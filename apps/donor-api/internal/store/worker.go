package store

import (
	"context"
	"errors"
	"strings"
	"time"

	"dad/donor-api/internal/payments"
	"dad/donor-api/internal/recognition"
	"dad/donor-api/internal/store/queries"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

type EventWorker struct {
	DB      *pgxpool.Pool
	Gateway payments.FinancialGateway
}

// ProcessNext applies the next due Stripe event. A failure is recorded on
// the event, which is retried with backoff, rather than returned: only a
// failure to record the outcome stops the worker. It reports whether it found
// an event.
func (w EventWorker) ProcessNext(ctx context.Context) (bool, error) {
	worked := false
	err := pgx.BeginFunc(ctx, w.DB, func(tx pgx.Tx) error {
		q := queries.New(tx)
		event, err := q.NextStripeEvent(ctx)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		worked = true
		if err = q.IncrementEventAttempt(ctx, event.ID); err != nil {
			return err
		}
		now := time.Now().UTC()
		// The event's effects commit in a savepoint, so a failed event keeps its
		// attempt count and error while its partial effects roll back.
		applyErr := pgx.BeginFunc(ctx, tx, func(work pgx.Tx) error { return w.applyEvent(ctx, work, event, now) })
		if applyErr != nil {
			return q.FailStripeEvent(ctx, queries.FailStripeEventParams{ID: event.ID, LastError: text(eventFailure(applyErr))})
		}
		return q.CompleteStripeEvent(ctx, queries.CompleteStripeEventParams{ID: event.ID, ProcessedAt: timestamp(now)})
	})
	return worked && err == nil, err
}

// eventFailure classifies a failed event for operators without recording
// processor data that error text may carry.
func eventFailure(err error) string {
	switch {
	case errors.Is(err, payments.ErrProcessorUnavailable), errors.Is(err, payments.ErrUnconfigured):
		return "PaymentUnavailable"
	case errors.Is(err, pgx.ErrNoRows):
		return "MissingRecord"
	case errors.Is(err, payments.ErrFacts), errors.Is(err, recognition.ErrInvalid):
		return "InvalidFacts"
	}
	return "ProcessingError"
}

func parseID(value string) (pgtype.UUID, error) {
	var id pgtype.UUID
	if err := id.Scan(value); err != nil || !id.Valid {
		return id, payments.ErrFacts
	}
	return id, nil
}

// applyEvent applies a verified event and refreshes the affected donor's
// projection. Each kind fetches the processor state it needs before taking
// the donor lock, so a slow processor cannot block that donor's other work.
func (w EventWorker) applyEvent(ctx context.Context, tx pgx.Tx, event queries.DonorStripeEvent, now time.Time) error {
	data, err := payments.DecodeObject(event.Data)
	if err != nil {
		return err
	}
	q := queries.New(tx)
	// fetcher coalesces subscription reads for the span of this one step, so a
	// subscription resolved once is not fetched again within the step.
	fetcher := newSubFetcher(w.Gateway)
	var donor pgtype.UUID
	switch {
	case data.Text("payment_update_id") != "":
		donor, err = w.applyPaymentUpdateEvent(ctx, q, data, now)
	case strings.HasPrefix(event.EventType, "refund."):
		donor, err = w.applyRefundEvent(ctx, q, event, data, now)
	default:
		donor, err = w.applyCheckoutEvent(ctx, q, event, data, fetcher, now)
	}
	if err != nil {
		return err
	}
	_, err = refreshProjection(ctx, tx, donor, now)
	return err
}

// applyPaymentUpdateEvent settles a hosted card setup whose session ended.
func (w EventWorker) applyPaymentUpdateEvent(
	ctx context.Context,
	q *queries.Queries,
	data payments.Object,
	now time.Time,
) (pgtype.UUID, error) {
	id, err := parseID(data.Text("payment_update_id"))
	if err != nil {
		return pgtype.UUID{}, err
	}
	snapshot, err := q.PaymentUpdateSnapshot(ctx, id)
	if err != nil {
		return pgtype.UUID{}, err
	}
	gateway, ok := w.Gateway.(payments.PaymentMethodGateway)
	if !ok {
		return pgtype.UUID{}, payments.ErrUnconfigured
	}
	facts, err := fetchPaymentUpdate(ctx, q, gateway, snapshot, data.Text("id"), now)
	if err != nil {
		return pgtype.UUID{}, err
	}
	if _, err = q.LockDonor(ctx, snapshot.DonorID); err != nil {
		return pgtype.UUID{}, err
	}
	row, err := q.PaymentUpdateByID(ctx, id)
	if err != nil {
		return pgtype.UUID{}, err
	}
	status, err := applyPaymentUpdate(ctx, q, row, facts, now)
	if err != nil {
		return pgtype.UUID{}, err
	}
	if !terminalPaymentUpdate(status) {
		return pgtype.UUID{}, payments.ErrFacts
	}
	return snapshot.DonorID, nil
}

// applyRefundEvent records a refund's current processor state. It is read
// again under the lock if another refund state was recorded meanwhile, since
// webhook timestamps cannot order competing refund states.
func (w EventWorker) applyRefundEvent(
	ctx context.Context,
	q *queries.Queries,
	event queries.DonorStripeEvent,
	data payments.Object,
	now time.Time,
) (pgtype.UUID, error) {
	payment := text(data.Text("payment_intent"))
	snapshot, err := q.GiftSnapshotByPayment(ctx, payment)
	if err != nil {
		return pgtype.UUID{}, err
	}
	observations, err := q.GiftRefundObservations(ctx, snapshot.ID)
	if err != nil {
		return pgtype.UUID{}, err
	}
	current, err := w.retrieveRefund(ctx, data.Text("id"))
	if err != nil {
		return pgtype.UUID{}, err
	}
	if _, err = q.LockDonor(ctx, snapshot.DonorID); err != nil {
		return pgtype.UUID{}, err
	}
	gift, err := q.GiftByPayment(ctx, payment)
	if err != nil {
		return pgtype.UUID{}, err
	}
	recorded, err := q.GiftRefundObservations(ctx, gift.ID)
	if err != nil {
		return pgtype.UUID{}, err
	}
	if recorded != observations || gift.ID != snapshot.ID {
		if current, err = w.retrieveRefund(ctx, data.Text("id")); err != nil {
			return pgtype.UUID{}, err
		}
	}
	return snapshot.DonorID, recordRefund(ctx, q, gift, current, event.ID, pgtype.UUID{}, now)
}

// applyCheckoutEvent applies an event about a DAD checkout: its session, its
// subscription, or a payment it collected.
func (w EventWorker) applyCheckoutEvent(
	ctx context.Context,
	q *queries.Queries,
	event queries.DonorStripeEvent,
	data payments.Object,
	fetcher *subFetcher,
	now time.Time,
) (pgtype.UUID, error) {
	checkoutID, err := parseID(data.Text("checkout_id"))
	if err != nil {
		return pgtype.UUID{}, err
	}
	draft, err := q.CheckoutDraft(ctx, checkoutID)
	if err != nil {
		return pgtype.UUID{}, err
	}
	facts, err := w.prefetch(ctx, q, event, data, draft, fetcher)
	if err != nil {
		return pgtype.UUID{}, err
	}
	if _, err = q.LockDonor(ctx, draft.DonorID); err != nil {
		return pgtype.UUID{}, err
	}
	attempt, err := q.AttemptForCheckout(ctx, checkoutID)
	if err != nil {
		return pgtype.UUID{}, err
	}
	if err = recordCard(ctx, q, draft.DonorID, facts.card, now); err != nil {
		return pgtype.UUID{}, err
	}
	if err = w.applyLockedCheckoutEvent(ctx, q, event, data, draft, attempt, facts, fetcher, now); err != nil {
		return pgtype.UUID{}, err
	}
	return draft.DonorID, queueDeletedAccountReversals(ctx, q, checkoutID, now)
}

func (w EventWorker) retrieveRefund(ctx context.Context, id string) (payments.Object, error) {
	if w.Gateway == nil {
		return nil, payments.ErrUnconfigured
	}
	current, err := w.Gateway.RetrieveRefund(ctx, id)
	if err != nil {
		return nil, err
	}
	if current.Text("id") != id {
		return nil, payments.ErrFacts
	}
	return current, nil
}

// eventFacts is processor state for a checkout event, fetched before the
// donor lock is taken.
type eventFacts struct {
	subscription *subscriptionSnapshot
	card         *paymentCard
}

func (w EventWorker) prefetch(
	ctx context.Context,
	q *queries.Queries,
	event queries.DonorStripeEvent,
	data payments.Object,
	draft queries.DonorCheckout,
	fetcher *subFetcher,
) (eventFacts, error) {
	var facts eventFacts
	subscription := data.Text("subscription")
	switch kind := event.EventType; {
	case strings.HasPrefix(kind, "customer.subscription."):
		subscription = data.Text("id")
	case kind == "checkout.session.completed":
		if subscription == "" {
			return facts, nil
		}
	case strings.HasPrefix(kind, "invoice."):
		pending, err := q.PendingAmountChange(ctx, draft.ID)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return facts, err
		}
		if !pending {
			return facts, nil
		}
	default:
		return facts, nil
	}
	if !strings.HasPrefix(subscription, "sub_") {
		return facts, payments.ErrFacts
	}
	attempt, err := q.AttemptSnapshotForCheckout(ctx, draft.ID)
	if err != nil {
		return facts, err
	}
	if facts.subscription, err = fetchSubscription(ctx, q, fetcher, attempt.ID, subscription); err != nil {
		return facts, err
	}
	facts.card, err = subscriptionCard(ctx, q, w.Gateway, facts.subscription.remote)
	return facts, err
}

func bindSubscription(ctx context.Context, q *queries.Queries, attempt queries.DonorStripeCheckout, subscription string) error {
	if !strings.HasPrefix(subscription, "sub_") ||
		(attempt.StripeSubscriptionID.Valid && attempt.StripeSubscriptionID.String != subscription) {
		return payments.ErrFacts
	}
	return q.BindSubscription(ctx, queries.BindSubscriptionParams{ID: attempt.ID, StripeSubscriptionID: text(subscription)})
}

func (w EventWorker) applyLockedCheckoutEvent(
	ctx context.Context,
	q *queries.Queries,
	event queries.DonorStripeEvent,
	data payments.Object,
	draft queries.DonorCheckout,
	attempt queries.DonorStripeCheckout,
	facts eventFacts,
	fetcher *subFetcher,
	now time.Time,
) error {
	kind := event.EventType
	switch {
	case strings.HasPrefix(kind, "checkout.session."):
		if err := applySessionEvent(ctx, q, kind, data, attempt); err != nil || facts.subscription == nil {
			return err
		}
		// Syncing here shows a new monthly gift without waiting for a
		// subscription event, which Stripe may send before or after this one.
		attempt.StripeSubscriptionID = text(facts.subscription.id)
		_, err := reconcileSubscription(ctx, q, fetcher, draft, attempt, facts.subscription, now)
		return err
	case strings.HasPrefix(kind, "customer.subscription."):
		if err := bindSubscription(ctx, q, attempt, data.Text("id")); err != nil {
			return err
		}
		attempt.StripeSubscriptionID = text(data.Text("id"))
		_, err := reconcileSubscription(ctx, q, fetcher, draft, attempt, facts.subscription, now)
		return err
	}
	expected := draft.AmountMinor
	if strings.HasPrefix(kind, "invoice.") {
		var err error
		if expected, err = w.invoiceAmount(ctx, q, data, draft, attempt, facts.subscription, fetcher, now); err != nil {
			return err
		}
	}
	if kind == "invoice.payment_failed" {
		amount, err := data.Int("amount_due")
		if err != nil || draft.Frequency != "MONTHLY" || data.Text("status") != "open" || data.Text("currency") != "usd" ||
			amount != expected ||
			!billingReason(data) {
			return payments.ErrFacts
		}
		if err = bindSubscription(ctx, q, attempt, data.Text("subscription")); err != nil {
			return err
		}
		period, err := recordPeriod(ctx, q, draft, data, expected, false)
		if err != nil {
			return err
		}
		return recordBillingObservation(ctx, q, period, event, "FAILED", amount, data.Text("billing_reason"), event.OccurredAt.Time, now)
	}
	payment, err := paymentFacts(ctx, q, event, data, draft, attempt, expected)
	if err != nil {
		return err
	}
	if err = recordGift(ctx, q, draft, payment, now); err != nil {
		return err
	}
	if payment.period.Valid {
		return recordBillingObservation(
			ctx,
			q,
			payment.period,
			event,
			"PAID",
			payment.amount,
			data.Text("billing_reason"),
			payment.paid,
			now,
		)
	}
	return nil
}

func recordBillingObservation(
	ctx context.Context,
	q *queries.Queries,
	period pgtype.UUID,
	event queries.DonorStripeEvent,
	status string,
	amount int64,
	reason string,
	occurred, recorded time.Time,
) error {
	id, err := NewID()
	if err != nil {
		return err
	}
	return q.InsertBillingObservation(ctx, queries.InsertBillingObservationParams{
		ID: id, PeriodID: period, SourceEventID: event.ID, Status: status, AmountMinor: amount,
		BillingReason: reason, OccurredAt: timestamp(occurred), EventCreatedAt: event.OccurredAt, RecordedAt: timestamp(recorded),
	})
}

func applySessionEvent(
	ctx context.Context,
	q *queries.Queries,
	kind string,
	data payments.Object,
	attempt queries.DonorStripeCheckout,
) error {
	if attempt.StripeSessionID.Valid && attempt.StripeSessionID.String != data.Text("id") {
		return payments.ErrFacts
	}
	status := attempt.Status
	if kind == "checkout.session.expired" {
		if data.Text("status") != "expired" {
			return payments.ErrFacts
		}
		if status != "COMPLETE" && status != "CANCELLED" {
			status = "EXPIRED"
			if err := q.ReleaseReservation(ctx, attempt.ID); err != nil {
				return err
			}
		}
	} else {
		if data.Text("status") != "complete" {
			return payments.ErrFacts
		}
		if status != "CANCELLED" {
			status = "COMPLETE"
		}
		if subscription := data.Text("subscription"); subscription != "" {
			if err := bindSubscription(ctx, q, attempt, subscription); err != nil {
				return err
			}
		}
	}
	return q.RecordCheckoutSession(
		ctx,
		queries.RecordCheckoutSessionParams{ID: attempt.ID, StripeSessionID: text(data.Text("id")), Status: status},
	)
}

// invoiceAmount returns the amount the invoice's period must charge: a
// confirmed amount change applies from its effective renewal, never prorated.
func (w EventWorker) invoiceAmount(
	ctx context.Context,
	q *queries.Queries,
	data payments.Object,
	draft queries.DonorCheckout,
	attempt queries.DonorStripeCheckout,
	snapshot *subscriptionSnapshot,
	fetcher *subFetcher,
	now time.Time,
) (int64, error) {
	pending, err := q.PendingAmountChange(ctx, draft.ID)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return 0, err
	}
	if pending {
		if err = bindSubscription(ctx, q, attempt, data.Text("subscription")); err != nil {
			return 0, err
		}
		attempt.StripeSubscriptionID = text(data.Text("subscription"))
		if _, err = reconcileSubscription(ctx, q, fetcher, draft, attempt, snapshot, now); err != nil {
			return 0, err
		}
	}
	start, _, err := invoiceBounds(data)
	if err != nil {
		return 0, err
	}
	amount, err := q.InvoiceAmountChange(ctx, queries.InvoiceAmountChangeParams{CheckoutID: draft.ID, EffectiveAt: timestamp(start)})
	if errors.Is(err, pgx.ErrNoRows) {
		return draft.AmountMinor, nil
	}
	return amount, err
}

type paidGift struct {
	objectID      string
	amount        int64
	paid          time.Time
	paymentIntent string
	period        pgtype.UUID
}

func paymentFacts(
	ctx context.Context,
	q *queries.Queries,
	event queries.DonorStripeEvent,
	data payments.Object,
	draft queries.DonorCheckout,
	attempt queries.DonorStripeCheckout,
	expected int64,
) (paidGift, error) {
	out := paidGift{objectID: data.Text("id")}
	var err error
	switch event.EventType {
	case "invoice.payment_succeeded":
		before, e1 := data.IntDefault("starting_balance")
		after, e2 := data.IntDefault("ending_balance")
		total, e3 := data.Int("total")
		due, e4 := data.Int("amount_due")
		out.amount, err = data.Int("amount_paid")
		if draft.Frequency != "MONTHLY" || data.Text("status") != "paid" || !billingReason(data) || e1 != nil || e2 != nil || e3 != nil ||
			e4 != nil ||
			err != nil ||
			before != 0 ||
			after != 0 ||
			out.amount != total ||
			out.amount != due {
			return out, payments.ErrFacts
		}
		if err = bindSubscription(ctx, q, attempt, data.Text("subscription")); err != nil {
			return out, err
		}
		if out.paid, err = data.Unix("paid_at"); err != nil {
			return out, err
		}
		if out.paymentIntent, err = invoicePaymentIntent(data, out.amount); err != nil {
			return out, err
		}
		if out.period, err = recordPeriod(ctx, q, draft, data, expected, true); err != nil {
			return out, err
		}
	default:
		return out, payments.ErrFacts
	}
	if out.amount != expected || out.amount <= 0 || data.Text("currency") != "usd" {
		return out, payments.ErrFacts
	}
	return out, nil
}

// recordGift writes the payment with the checkout's attribution as it stood
// when charged; an expired referral window leaves the gift unattributed.
func recordGift(ctx context.Context, q *queries.Queries, draft queries.DonorCheckout, p paidGift, now time.Time) error {
	donor, err := q.LockDonor(ctx, draft.DonorID)
	if err != nil {
		return err
	}
	hadGift, err := q.HasGift(ctx, donor.ID)
	if err != nil {
		return err
	}
	id, err := NewID()
	if err != nil {
		return err
	}
	var referrer pgtype.UUID
	var kind pgtype.Text
	if draft.ReferrerID.Valid && p.paid.Before(draft.ReferralExpiresAt.Time) {
		referrer, kind = draft.ReferrerID, draft.ReferralKind
	}
	pi := text(p.paymentIntent)
	if err = q.InsertGift(ctx, queries.InsertGiftParams{ID: id, DonorID: donor.ID, CheckoutID: draft.ID, StripeObjectID: p.objectID, AmountMinor: p.amount, Frequency: draft.Frequency, PaidAt: timestamp(p.paid), StripePaymentIntentID: pi, PeriodID: p.period, ReferrerID: referrer, ReferralKind: kind}); err != nil {
		return err
	}
	gift, err := q.GiftByStripeObject(ctx, p.objectID)
	if err != nil {
		return err
	}
	if gift.DonorID != donor.ID || gift.CheckoutID != draft.ID || gift.AmountMinor != p.amount || gift.Frequency != draft.Frequency ||
		gift.ReferrerID != referrer ||
		gift.ReferralKind != kind ||
		(gift.StripePaymentIntentID.Valid && gift.StripePaymentIntentID != pi) ||
		(gift.PeriodID.Valid && gift.PeriodID != p.period) {
		return payments.ErrFacts
	}
	if err = q.EnrichGift(ctx, queries.EnrichGiftParams{ID: gift.ID, StripePaymentIntentID: pi, PeriodID: p.period}); err != nil {
		return err
	}
	if !hadGift && !donor.ClaimedAt.Valid {
		return queueLoginEmail(ctx, q, donor.Email, "", now)
	}
	return nil
}

func billingReason(data payments.Object) bool {
	return data.Text("billing_reason") == "subscription_create" || data.Text("billing_reason") == "subscription_cycle"
}

// invoiceBounds returns the period of an invoice's single, complete line.
func invoiceBounds(data payments.Object) (time.Time, time.Time, error) {
	lines, err := data.Array("lines")
	if err != nil || !data.BoolIs("lines_complete", true) || len(lines) != 1 {
		return time.Time{}, time.Time{}, payments.ErrFacts
	}
	bounds, err := lines[0].Object("period")
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	return monthlyPeriod(bounds, "start", "end")
}

func recordPeriod(
	ctx context.Context,
	q *queries.Queries,
	draft queries.DonorCheckout,
	data payments.Object,
	expected int64,
	paid bool,
) (pgtype.UUID, error) {
	empty := pgtype.UUID{}
	start, end, err := invoiceBounds(data)
	if err != nil {
		return empty, err
	}
	lines, err := data.Array("lines")
	if err != nil {
		return empty, err
	}
	line := lines[0]
	amount, err := line.Int("amount")
	if err != nil || amount != expected || line.Text("parent_type") != "subscription_item_details" || !line.BoolIs("proration", false) ||
		line.Text("subscription") != data.Text("subscription") ||
		line.Text("currency") != "usd" {
		return empty, payments.ErrFacts
	}
	id, err := NewID()
	if err != nil {
		return empty, err
	}
	if err = q.InsertBillingPeriod(ctx, queries.InsertBillingPeriodParams{ID: id, DonorID: draft.DonorID, CheckoutID: draft.ID, StripeInvoiceID: data.Text("id"), StripeSubscriptionID: data.Text("subscription"), DueAt: timestamp(start), EndsAt: timestamp(end)}); err != nil {
		return empty, err
	}
	p, err := q.BillingPeriodByInvoice(ctx, data.Text("id"))
	if err != nil {
		return empty, err
	}
	if p.CheckoutID != draft.ID || p.StripeSubscriptionID != data.Text("subscription") || !p.DueAt.Time.Equal(start) ||
		!p.EndsAt.Time.Equal(end) {
		return empty, payments.ErrFacts
	}
	if paid {
		if err = q.MarkPeriodPaid(ctx, p.ID); err != nil {
			return empty, err
		}
	}
	return p.ID, nil
}

func invoicePaymentIntent(data payments.Object, amount int64) (string, error) {
	rows, err := data.Array("payments")
	if err != nil || !data.BoolIs("payments_complete", true) || len(rows) != 1 {
		return "", payments.ErrFacts
	}
	p := rows[0]
	paid, err := p.Int("amount_paid")
	if err != nil || paid != amount || p.Text("status") != "paid" || p.Text("currency") != "usd" || p.Text("invoice") != data.Text("id") ||
		!p.BoolIs("livemode", false) ||
		p.Text("type") != "payment_intent" ||
		!strings.HasPrefix(p.Text("payment_intent"), "pi_") {
		return "", payments.ErrFacts
	}
	return p.Text("payment_intent"), nil
}
