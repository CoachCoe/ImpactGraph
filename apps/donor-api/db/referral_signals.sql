-- name: KnownPaymentMethod :one
SELECT EXISTS(SELECT 1 FROM donor_payment_methods WHERE stripe_payment_method_id=$1);

-- name: RecordPaymentMethod :execrows
INSERT INTO donor_payment_methods (stripe_payment_method_id,donor_id,fingerprint,observed_at)
VALUES ($1,$2,$3,$4) ON CONFLICT (stripe_payment_method_id) DO NOTHING;

-- name: RecordDeviceMark :execrows
INSERT INTO donor_device_marks (donor_id,device_hash,observed_at)
VALUES ($1,$2,$3) ON CONFLICT (donor_id,device_hash) DO NOTHING;

-- name: SharedSignalDonors :many
-- Other donors who have used one of this donor's cards or app installations.
SELECT other.donor_id FROM donor_payment_methods mine
JOIN donor_payment_methods other ON other.fingerprint=mine.fingerprint AND other.donor_id<>mine.donor_id
WHERE mine.donor_id=$1
UNION
SELECT other.donor_id FROM donor_device_marks mine
JOIN donor_device_marks other ON other.device_hash=mine.device_hash AND other.donor_id<>mine.donor_id
WHERE mine.donor_id=$1;

-- name: ScheduleReferralRecheck :exec
-- A new card or installation can withdraw credit between this donor and the
-- donor who referred them, or the donors they referred, so their projections
-- are refreshed now rather than at their next scheduled refresh.
UPDATE donor_metric_projections SET next_refresh_at=sqlc.arg(as_of)::timestamptz
WHERE (donor_id=sqlc.arg(donor_id)::uuid OR referrer_id=sqlc.arg(donor_id)::uuid)
  AND next_refresh_at>sqlc.arg(as_of)::timestamptz;
