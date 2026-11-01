package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"dad/donor-api/internal/store"
)

// captureDonor records the visitor token each capture is shown and answers
// with token, empty meaning nothing was captured.
type captureDonor struct {
	DonorSource
	token   string
	visitor string
	session string
	bound   bool
}

func (c *captureDonor) CaptureReferral(_ context.Context, _, _, visitor string, _ time.Time) (store.ReferralCapture, error) {
	c.visitor = visitor
	return store.ReferralCapture{Token: c.token}, nil
}

func (c *captureDonor) CaptureCompanyCode(_ context.Context, _, visitor string, _ time.Time) (store.CompanyCapture, error) {
	c.visitor = visitor
	return store.CompanyCapture{Token: c.token}, nil
}

func (c *captureDonor) BindCompanyTouch(_ context.Context, session, visitor string) (bool, error) {
	c.session, c.visitor = session, visitor
	return c.bound, nil
}

func appCapture(path, body string) *http.Request {
	r := httptest.NewRequest("POST", path, strings.NewReader(body))
	r.Header.Set("X-DAD-Client", "native")
	r.Header.Set("Content-Type", "application/json")
	return r
}

func TestAppLinkCaptureKeepsItsTokenInTheBody(t *testing.T) {
	kept, fresh := strings.Repeat("k", 43), strings.Repeat("f", 43)
	for _, path := range []string{"/donor/referrals/code", "/donor/company/ACME"} {
		donor := &captureDonor{token: fresh}
		response := httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, appCapture(path, `{"token":"`+kept+`"}`))
		var body map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil || response.Code != http.StatusOK ||
			body["token"] != fresh || body["captured"] != true || donor.visitor != kept {
			t.Errorf(
				"%s: got %d %v with visitor %q, want 200 returning the token and reading the kept one",
				path,
				response.Code,
				body,
				donor.visitor,
			)
		}
		if cookie := response.Header().Get("Set-Cookie"); cookie != "" {
			t.Errorf("%s: set a cookie for the app: %q", path, cookie)
		}

		donor = &captureDonor{}
		response = httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, appCapture(path, `{}`))
		if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil || response.Code != http.StatusOK ||
			body["token"] != nil || body["captured"] != false || donor.visitor != "" {
			t.Errorf("%s: nothing captured: got %d %v, want 200 with a null token", path, response.Code, body)
		}
	}
}

func TestLinkCaptureRefusesMisplacedTokens(t *testing.T) {
	for _, path := range []string{"/donor/referrals/code", "/donor/company/ACME"} {
		for name, r := range map[string]*http.Request{
			"short app token": appCapture(path, `{"token":"short"}`),
			"null app token":  appCapture(path, `{"token":null}`),
			"browser token": func() *http.Request {
				r := appCapture(path, `{"token":"`+strings.Repeat("k", 43)+`"}`)
				r.Header.Del("X-DAD-Client")
				r.Header.Set("Origin", "http://localhost:3000")
				return r
			}(),
		} {
			donor := &captureDonor{}
			response := httptest.NewRecorder()
			NewServer("http://localhost:3000", Services{Donor: donor}).Handler.ServeHTTP(response, r)
			if response.Code != http.StatusUnprocessableEntity || donor.visitor != "" {
				t.Errorf("%s %s: got %d, want 422 before the store", path, name, response.Code)
			}
		}
	}
}

func TestBrowserLinkCaptureStaysInItsCookie(t *testing.T) {
	fresh := strings.Repeat("f", 43)
	for path, cookie := range map[string]string{"/donor/referrals/code": referralCookie, "/donor/company/ACME": companyCookie} {
		donor := &captureDonor{token: fresh}
		r := httptest.NewRequest("POST", path, strings.NewReader(`{}`))
		r.Header.Set("Origin", "http://localhost:3000")
		r.AddCookie(&http.Cookie{Name: cookie, Value: "earlier"})
		response := httptest.NewRecorder()
		NewServer("http://localhost:3000", Services{Donor: donor}).Handler.ServeHTTP(response, r)
		set := response.Header().Get("Set-Cookie")
		if response.Code != http.StatusOK || donor.visitor != "earlier" || !strings.HasPrefix(set, cookie+"="+fresh) ||
			!strings.Contains(set, "HttpOnly") || strings.Contains(response.Body.String(), fresh) {
			t.Errorf(
				"%s: got %d with Set-Cookie %q and body %s, want the token only in an HttpOnly cookie",
				path,
				response.Code,
				set,
				response.Body,
			)
		}
	}
}

func TestAppBindsItsCompanyTouchAfterSignIn(t *testing.T) {
	kept := strings.Repeat("k", 43)
	donor := &captureDonor{bound: true}
	r := appRequest("POST", "/donor/me/company-touch", `{"token":"`+kept+`"}`)
	response := httptest.NewRecorder()
	NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, r)
	if response.Code != http.StatusOK || strings.TrimSpace(response.Body.String()) != `{"bound":true}` ||
		donor.session != appSession || donor.visitor != kept {
		t.Fatalf("got %d %s for session %q and visitor %q", response.Code, response.Body, donor.session, donor.visitor)
	}

	for name, body := range map[string]string{"missing token": `{}`, "short token": `{"token":"short"}`} {
		donor = &captureDonor{}
		response = httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, appRequest("POST", "/donor/me/company-touch", body))
		if response.Code != http.StatusUnprocessableEntity || donor.session != "" {
			t.Errorf("%s: got %d, want 422 before the store", name, response.Code)
		}
	}
}
