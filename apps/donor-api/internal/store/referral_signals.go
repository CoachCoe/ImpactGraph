package store

import (
	"context"
	"crypto/sha256"
	"slices"
	"strings"
	"time"

	"dad/donor-api/internal/payments"
	"dad/donor-api/internal/recognition"
	"dad/donor-api/internal/store/queries"
	"github.com/jackc/pgx/v5/pgtype"
)

// paymentCard is a card a donor's subscription charges, named across
// customers by its fingerprint.
type paymentCard struct {
	method      string
	fingerprint string
}

// subscriptionCard reads the card behind subscription's default payment
// method, before the donor lock is taken. A card already recorded is not
// read again, so the processor is asked once per card rather than per event.
// A payment method that is not a card has no fingerprint and yields none.
func subscriptionCard(
	ctx context.Context,
	q *queries.Queries,
	gateway payments.FinancialGateway,
	subscription payments.Object,
) (*paymentCard, error) {
	method := subscription.Text("default_payment_method")
	if !strings.HasPrefix(method, "pm_") {
		return nil, nil
	}
	known, err := q.KnownPaymentMethod(ctx, method)
	if err != nil || known {
		return nil, err
	}
	remote, err := gateway.RetrievePaymentMethod(ctx, method)
	if err != nil {
		return nil, err
	}
	card, err := remote.Object("card")
	if err != nil || remote.Text("id") != method || remote.Text("type") != "card" || card.Text("fingerprint") == "" {
		return nil, nil
	}
	return &paymentCard{method: method, fingerprint: card.Text("fingerprint")}, nil
}

// recordCard notes that donor's subscription charges card. The caller holds
// the donor lock.
func recordCard(ctx context.Context, q *queries.Queries, donor pgtype.UUID, card *paymentCard, now time.Time) error {
	if card == nil {
		return nil
	}
	added, err := q.RecordPaymentMethod(ctx, queries.RecordPaymentMethodParams{
		StripePaymentMethodID: card.method, DonorID: donor, Fingerprint: card.fingerprint, ObservedAt: timestamp(now),
	})
	if err != nil || added == 0 {
		return err
	}
	return q.ScheduleReferralRecheck(ctx, queries.ScheduleReferralRecheckParams{AsOf: timestamp(now), DonorID: donor})
}

// recordDevice notes that donor uses the app installation whose push token
// is token.
func recordDevice(ctx context.Context, q *queries.Queries, donor pgtype.UUID, token string, now time.Time) error {
	hash := sha256.Sum256([]byte(token))
	added, err := q.RecordDeviceMark(ctx, queries.RecordDeviceMarkParams{DonorID: donor, DeviceHash: hash[:], ObservedAt: timestamp(now)})
	if err != nil || added == 0 {
		return err
	}
	return q.ScheduleReferralRecheck(ctx, queries.ScheduleReferralRecheckParams{AsOf: timestamp(now), DonorID: donor})
}

// withholdSharedReferrals drops the referral candidate from each of donor's
// gifts whose referrer has used one of donor's cards or app installations:
// self-referral is blocked by payment fingerprint or device (PRD R-4). The
// gift itself still counts toward the donor's own giving.
func withholdSharedReferrals(ctx context.Context, q *queries.Queries, donor pgtype.UUID, gifts []recognition.Gift) error {
	if !slices.ContainsFunc(gifts, func(g recognition.Gift) bool { return g.Candidate != nil }) {
		return nil
	}
	shared, err := q.SharedSignalDonors(ctx, donor)
	if err != nil || len(shared) == 0 {
		return err
	}
	blocked := make(map[string]bool, len(shared))
	for _, id := range shared {
		blocked[id.String()] = true
	}
	for i, g := range gifts {
		if g.Candidate != nil && blocked[g.Candidate.ReferrerID] {
			gifts[i].Candidate = nil
		}
	}
	return nil
}
