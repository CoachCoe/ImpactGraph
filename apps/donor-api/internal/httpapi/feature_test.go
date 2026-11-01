package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgtype"

	"dad/donor-api/internal/payments"
	"dad/donor-api/internal/store"
)

// unconfiguredRecurring answers as the store does without a payment processor.
type unconfiguredRecurring struct{ RecurringSource }

func (unconfiguredRecurring) BeginPaymentUpdate(context.Context, string, pgtype.UUID) (store.PaymentUpdate, error) {
	return store.PaymentUpdate{}, payments.ErrUnconfigured
}

// unconfiguredClaims answers as the store does without a waitlist key.
type unconfiguredClaims struct{ FoundingClaimSource }

func (unconfiguredClaims) Authorize(context.Context, string) error {
	return store.ErrFoundingClaimsUnconfigured
}

func TestUnconfiguredFeaturesAnswerWithCodesWithoutErrorLogs(t *testing.T) {
	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })

	key := "8f4f52bb-99f6-490e-8313-5156cc4f8b45"
	for _, tc := range []struct {
		name     string
		services Services
		path     string
		body     string
		code     string
	}{
		{"checkout without payments", Services{Checkout: &checkoutStub{err: payments.ErrUnconfigured}}, "/donor/checkouts", `{"email":"donor@example.org","amount_minor":3000}`, "PAYMENTS_UNCONFIGURED"},
		{"card update without payments", Services{Recurring: unconfiguredRecurring{}}, "/donor/me/payment-method/setup", "", "PAYMENTS_UNCONFIGURED"},
		{"login without email", Services{Account: &accountStub{}}, "/donor/login", `{"email":"donor@example.org"}`, "EMAIL_UNCONFIGURED"},
		{"email change without email", Services{EmailChange: &emailChangeStub{}}, "/donor/me/email-change", `{"email":"new@example.org"}`, "EMAIL_UNCONFIGURED"},
		{"source upload without key", Services{ContentAdmin: &uploadStub{authorizeErr: store.ErrSourceStorageUnconfigured}}, "/donor/staff/content/sources?name=a.pdf", "", "SOURCE_STORAGE_UNCONFIGURED"},
		{"waitlist import without key", Services{FoundingClaims: unconfiguredClaims{}}, "/donor/staff/founding-claims", `{"claims_csv":""}`, "FOUNDING_CLAIMS_UNCONFIGURED"},
		{"passkey sign-in without a relying party", Services{}, "/donor/passkeys/sign-in/options", "", "PASSKEYS_UNCONFIGURED"},
		{"passkey registration without a relying party", Services{}, "/donor/me/passkeys/register/options", "", "PASSKEYS_UNCONFIGURED"},
	} {
		r := httptest.NewRequest("POST", tc.path, strings.NewReader(tc.body))
		r.Header.Set("Origin", originFor(tc.path))
		r.Header.Set("Idempotency-Key", key)
		r.AddCookie(&http.Cookie{Name: donorCookie, Value: "session"})
		r.AddCookie(&http.Cookie{Name: staffCookie, Value: "session"})
		response := httptest.NewRecorder()
		NewServer("", tc.services).Handler.ServeHTTP(response, r)
		var body problem
		_ = json.Unmarshal(response.Body.Bytes(), &body)
		if response.Code != http.StatusServiceUnavailable || body.Code != tc.code ||
			strings.Contains(strings.ToLower(body.Detail), "retry") {
			t.Errorf("%s: got %d %s, want 503 with code %s and no retry advice", tc.name, response.Code, response.Body, tc.code)
		}
	}
	if logs.Len() != 0 {
		t.Fatalf("configuration states were logged as failures: %s", logs.String())
	}
}

func TestConfiguredProcessorFailureStaysTransient(t *testing.T) {
	response := httptest.NewRecorder()
	services := Services{Checkout: &checkoutStub{err: payments.ErrProcessorUnavailable}}
	NewServer("", services).Handler.ServeHTTP(response, checkoutRequest(`{"email":"donor@example.org","amount_minor":3000}`))
	if response.Code != http.StatusServiceUnavailable || strings.Contains(response.Body.String(), `"code"`) {
		t.Fatalf("got %d %s, want an uncoded transient 503", response.Code, response.Body)
	}
}

func TestOversizedBodiesAre413(t *testing.T) {
	big := `{"email":"` + strings.Repeat("a", 3<<20) + `"}`
	for _, chunked := range []bool{false, true} {
		r := httptest.NewRequest("POST", "/donor/login", strings.NewReader(big))
		if chunked {
			r.ContentLength = -1
		}
		r.Header.Set("Origin", "http://localhost:3000")
		response := httptest.NewRecorder()
		NewServer("", Services{EmailConfigured: true, Account: &accountStub{}}).Handler.ServeHTTP(response, r)
		if response.Code != http.StatusRequestEntityTooLarge {
			t.Errorf("3 MB body (unknown length %v): got %d, want 413", chunked, response.Code)
		}
	}
}
