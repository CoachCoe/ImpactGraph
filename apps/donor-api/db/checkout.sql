-- name: StripeCheckout :one
SELECT * FROM donor_stripe_checkouts WHERE id=$1;

-- name: LockStripeCheckout :one
SELECT * FROM donor_stripe_checkouts WHERE id=$1 FOR UPDATE;

-- name: ValidReferralTouch :one
SELECT t.referrer_id,t.kind,t.expires_at FROM donor_referral_touches t JOIN donors d ON d.id=t.referrer_id
WHERE t.token_hash=$1 AND t.expires_at>$2 AND d.disabled=false AND d.id<>$3;

-- name: InsertCheckout :exec
INSERT INTO donor_checkouts (id,donor_id,amount_minor,currency,frequency,referrer_id,referral_kind,referral_expires_at,created_at)
VALUES ($1,$2,$3,'USD',$4,$5,$6,$7,$8);

-- name: InsertStripeCheckout :exec
INSERT INTO donor_stripe_checkouts (id,checkout_id,request_hash,request_params,status,created_at)
VALUES ($1,$2,$3,$4,'PENDING',$5);

-- name: ReserveRecurring :one
INSERT INTO donor_recurring_reservations (id,donor_id,attempt_id) VALUES ($1,$2,$3)
ON CONFLICT (donor_id) DO NOTHING RETURNING id;

-- name: AcknowledgeCheckout :exec
UPDATE donor_stripe_checkouts SET stripe_session_id=$2,hosted_url=$3,
status=CASE WHEN status='PENDING' THEN 'OPEN' ELSE status END WHERE id=$1;

-- name: ReservedAttempt :one
-- The checkout attempt holding the donor's recurring slot, locked.
SELECT a.* FROM donor_recurring_reservations r JOIN donor_stripe_checkouts a ON a.id=r.attempt_id
WHERE r.donor_id=$1 FOR UPDATE OF a;

-- name: CountRecentCheckoutAttempts :one
-- Hosted checkouts begun for the donor since the cutoff, the budget that
-- bounds how often anyone can make the website expire the donor's open one.
SELECT count(*)::bigint FROM donor_stripe_checkouts a JOIN donor_checkouts c ON c.id=a.checkout_id
WHERE c.donor_id=$1 AND a.created_at>$2;
