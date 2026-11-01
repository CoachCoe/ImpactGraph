package store

import (
	"context"
	"testing"
	"time"

	"dad/donor-api/internal/payments"
	"dad/donor-api/internal/recognition"
)

// referredGift gives ben a monthly gift alice referred, inside h's transaction.
func referredGift(h *historyFixture, at time.Time) {
	h.gift(recognition.Gift{
		ID:        "ben-first",
		DonorID:   "ben",
		Amount:    3000,
		Currency:  "USD",
		Frequency: "MONTHLY",
		PaidAt:    at,
		Candidate: &recognition.Candidate{ReferrerID: "alice", Kind: "LINK", ExpiresAt: at.AddDate(0, 1, 0)},
	})
}

func TestSharedCardOrDeviceWithholdsReferralCredit(t *testing.T) {
	paid := time.Date(2026, 12, 15, 0, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		name     string
		share    func(*historyFixture)
		credited bool
	}{
		{"same card on two customers", func(h *historyFixture) {
			mustRecordCard(h, "alice", "pm_alice", "fp_shared", paid)
			mustRecordCard(h, "ben", "pm_ben", "fp_shared", paid)
		}, false},
		{"same app installation", func(h *historyFixture) {
			mustRecordDevice(h, "alice", "push-token-shared", paid)
			mustRecordDevice(h, "ben", "push-token-shared", paid)
		}, false},
		{"different cards and installations", func(h *historyFixture) {
			mustRecordCard(h, "alice", "pm_alice", "fp_alice", paid)
			mustRecordCard(h, "ben", "pm_ben", "fp_ben", paid)
			mustRecordDevice(h, "alice", "push-token-alice", paid)
			mustRecordDevice(h, "ben", "push-token-ben", paid)
		}, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			ctx := context.Background()
			tx, err := database(t).Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			h := newHistoryFixture(t, tx)
			referredGift(h, paid)
			c.share(h)
			got, err := projectFacts(ctx, h.q, h.donor("ben"), paid.Add(time.Hour))
			if err != nil {
				t.Fatal(err)
			}
			if got.ReferrerID.Valid != c.credited || got.NetMinor.Int64 != 3000 {
				t.Fatalf(
					"credited=%v net=%d, want credited=%v and the gift still counted",
					got.ReferrerID.Valid,
					got.NetMinor.Int64,
					c.credited,
				)
			}
			if totals := oldReferralAggregate(t, tx, h.donor("alice")); (totals.Link == 1) != c.credited {
				t.Fatalf("referrer totals %+v, want credited=%v", totals, c.credited)
			}
		})
	}
}

// A referrer who adds the referred donor's card after the gift was credited
// loses the credit at the referred donor's next projection, which recording
// the card brings forward to now.
func TestLaterSharedCardWithdrawsReferralCredit(t *testing.T) {
	ctx := context.Background()
	tx, err := database(t).Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	h := newHistoryFixture(t, tx)
	paid := time.Date(2026, 12, 15, 0, 0, 0, 0, time.UTC)
	referredGift(h, paid)
	mustRecordCard(h, "ben", "pm_ben", "fp_shared", paid)
	first, err := projectFacts(ctx, h.q, h.donor("ben"), paid)
	if err != nil || first.ReferrerID != h.donor("alice") {
		t.Fatalf("first projection: %+v %v", first, err)
	}
	later := paid.Add(24 * time.Hour)
	mustRecordCard(h, "alice", "pm_alice", "fp_shared", later)
	var due time.Time
	if err := tx.QueryRow(ctx, "SELECT next_refresh_at FROM donor_metric_projections WHERE donor_id=$1", h.donor("ben")).Scan(&due); err != nil {
		t.Fatal(err)
	}
	if due.After(later) {
		t.Fatalf("referred donor next refreshes at %v, after the shared card was recorded at %v", due, later)
	}
	second, err := projectFacts(ctx, h.q, h.donor("ben"), later)
	if err != nil || second.ReferrerID.Valid {
		t.Fatalf("credit survived the shared card: %+v %v", second, err)
	}
	if totals := oldReferralAggregate(t, tx, h.donor("alice")); totals.Link != 0 || totals.LinkMinor != 0 {
		t.Fatalf("referrer kept totals %+v", totals)
	}
}

// A webhook about a subscription records the card it charges, against the
// donor whose checkout opened it.
func TestSubscriptionWebhookRecordsItsCard(t *testing.T) {
	f := newWorkerFixture(t, "MONTHLY")
	ctx := context.Background()
	result := f.checkout(t)
	subID := "sub_" + result.CheckoutID
	if _, err := f.db.Exec(ctx, "UPDATE donor_stripe_checkouts SET stripe_subscription_id=$2,status='COMPLETE' WHERE checkout_id=$1",
		result.CheckoutID, subID); err != nil {
		t.Fatal(err)
	}
	remote := subscription(result.CheckoutID, time.Now().UTC().Truncate(time.Second).Add(-time.Hour))
	remote["default_payment_method"] = "pm_" + result.CheckoutID
	f.gateway.subscriptions[subID] = paymentObject(t, remote)
	f.gateway.paymentMethods = map[string]payments.Object{"pm_" + result.CheckoutID: paymentObject(t, map[string]any{
		"id":   "pm_" + result.CheckoutID,
		"type": "card",
		"card": map[string]any{"fingerprint": "fp_checkout", "last4": "4242"},
	})}
	f.syncSubscription(t, result.CheckoutID)
	var donorID, fingerprint string
	if err := f.db.QueryRow(ctx, "SELECT donor_id::text,fingerprint FROM donor_payment_methods WHERE stripe_payment_method_id=$1",
		"pm_"+result.CheckoutID).Scan(&donorID, &fingerprint); err != nil {
		t.Fatal(err)
	}
	if donorID != f.donorID.String() || fingerprint != "fp_checkout" {
		t.Fatal("the subscription card was recorded against the wrong donor or fingerprint")
	}
}

func mustRecordCard(h *historyFixture, name, method, fingerprint string, at time.Time) {
	h.t.Helper()
	if err := recordCard(context.Background(), h.q, h.donor(name), &paymentCard{method: method, fingerprint: fingerprint}, at); err != nil {
		h.t.Fatal(err)
	}
}

func mustRecordDevice(h *historyFixture, name, token string, at time.Time) {
	h.t.Helper()
	if err := recordDevice(context.Background(), h.q, h.donor(name), token, at); err != nil {
		h.t.Fatal(err)
	}
}
