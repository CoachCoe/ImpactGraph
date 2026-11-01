-- Self-referral is blocked by the same payment fingerprint or device (PRD R-4).
-- donor_payment_methods records each card a donor's subscription has used, with
-- Stripe's card fingerprint, which is the same for one card across customers and
-- carries no card data. donor_device_marks records each app installation a donor
-- registered for notifications, as a SHA-256 of its push token, which survives
-- the token moving to another account's session.
--
-- Both are kept when an account is deleted. They are opaque, and they decide
-- referral credit on the gift records the law requires DAD to keep: removing
-- them would hand a deleted second account's referral credit back to its owner.
CREATE TABLE donor_payment_methods (
	stripe_payment_method_id VARCHAR(255) PRIMARY KEY CHECK (stripe_payment_method_id LIKE 'pm\_%'),
	donor_id UUID NOT NULL REFERENCES donors (id),
	fingerprint VARCHAR(64) NOT NULL CHECK (fingerprint <> ''),
	observed_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_donor_payment_methods_donor ON donor_payment_methods (donor_id);
CREATE INDEX ix_donor_payment_methods_fingerprint ON donor_payment_methods (fingerprint);

CREATE TABLE donor_device_marks (
	donor_id UUID NOT NULL REFERENCES donors (id),
	device_hash BYTEA NOT NULL CHECK (octet_length(device_hash) = 32),
	observed_at TIMESTAMPTZ NOT NULL,
	PRIMARY KEY (donor_id, device_hash)
);
CREATE INDEX ix_donor_device_marks_device ON donor_device_marks (device_hash);
