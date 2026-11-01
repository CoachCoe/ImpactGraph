package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"dad/donor-api/internal/store"
)

var appSession = strings.Repeat("s", 43)

// sessionDonor accepts only appSession and records every session it is shown.
type sessionDonor struct {
	DonorSource
	sessions []string
	revoked  pgtype.UUID
	err      error
}

func (s *sessionDonor) check(token string) error {
	s.sessions = append(s.sessions, token)
	if token != appSession {
		return store.ErrDenied
	}
	return s.err
}

func (s *sessionDonor) Profile(_ context.Context, token string) (store.Profile, error) {
	return store.Profile{Email: "donor@example.org"}, s.check(token)
}

func (s *sessionDonor) SavePreferences(_ context.Context, token string, _ store.PreferenceChange) (store.Profile, error) {
	return store.Profile{Email: "donor@example.org"}, s.check(token)
}

func (s *sessionDonor) SignOutEverywhere(_ context.Context, token string) error {
	return s.check(token)
}

func (s *sessionDonor) Sessions(_ context.Context, token string) (store.Sessions, error) {
	return store.Sessions{Sessions: []store.DeviceSession{{Label: "Web browser", Current: true}}}, s.check(token)
}

func (s *sessionDonor) SignOutSession(_ context.Context, token string, id pgtype.UUID) (store.Sessions, error) {
	s.revoked = id
	return store.Sessions{Sessions: []store.DeviceSession{}}, s.check(token)
}

func (s *sessionDonor) SignOutOthers(_ context.Context, token string) (store.Sessions, error) {
	return store.Sessions{Sessions: []store.DeviceSession{}}, s.check(token)
}

func appRequest(method, path, body string) *http.Request {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("X-DAD-Client", "native")
	r.Header.Set("Authorization", "Bearer "+appSession)
	return r
}

const preferences = `{"version":0,"referral_emails":true,"marketing_emails":false,"push_referral_converted":true,"push_streak_at_risk":false,"push_streak_milestone":true}`

func TestDonorSessionSource(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*http.Request)
		want   string
	}{
		{"app bearer", func(*http.Request) {}, appSession},
		{"lower-case scheme", func(r *http.Request) { r.Header.Set("Authorization", "bearer "+appSession) }, appSession},
		{"other scheme", func(r *http.Request) { r.Header.Set("Authorization", "Basic "+appSession) }, ""},
		{"short token", func(r *http.Request) { r.Header.Set("Authorization", "Bearer short") }, ""},
		{"no client marker", func(r *http.Request) { r.Header.Del("X-DAD-Client") }, ""},
		{"browser origin", func(r *http.Request) { r.Header.Set("Origin", "http://localhost:3000") }, ""},
		{"browser cookie wins", func(r *http.Request) { r.AddCookie(&http.Cookie{Name: donorCookie, Value: "cookie-session"}) }, "cookie-session"},
	} {
		r := appRequest("GET", "/donor/me/profile", "")
		tc.mutate(r)
		if got := donorSession(r); got != tc.want {
			t.Errorf("%s: got session %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestAppBearerSessionWorksWithoutOrigin(t *testing.T) {
	for _, tc := range []struct{ method, path, body string }{
		{"GET", "/donor/me/profile", ""},
		{"POST", "/donor/me/preferences", preferences},
		{"POST", "/donor/logout-all", ""},
	} {
		donor := &sessionDonor{}
		response := httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, appRequest(tc.method, tc.path, tc.body))
		if response.Code != http.StatusOK || len(donor.sessions) != 1 || donor.sessions[0] != appSession {
			t.Errorf("%s %s: got %d with sessions %q, want 200 with the bearer session", tc.method, tc.path, response.Code, donor.sessions)
		}
		if cookies := response.Result().Cookies(); len(cookies) != 0 {
			t.Errorf("%s %s: app response set cookies %v", tc.method, tc.path, cookies)
		}
	}
}

func TestBrowserSignalsDisqualifyBearerRequests(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*http.Request)
		status int
	}{
		// A cookie makes the request a browser request: Origin is required.
		{"cookie and bearer", func(r *http.Request) { r.AddCookie(&http.Cookie{Name: donorCookie, Value: appSession}) }, http.StatusForbidden},
		// A page elsewhere cannot add the marker without a preflight, but a request
		// that claims it alongside a foreign Origin is still a browser request.
		{"cross-origin marker", func(r *http.Request) { r.Header.Set("Origin", "https://evil.example.org") }, http.StatusForbidden},
		{"bearer without marker", func(r *http.Request) { r.Header.Del("X-DAD-Client") }, http.StatusForbidden},
		{"same-origin browser with bearer only", func(r *http.Request) { r.Header.Set("Origin", "http://localhost:3000") }, http.StatusUnauthorized},
	} {
		donor := &sessionDonor{}
		r := appRequest("POST", "/donor/me/preferences", preferences)
		tc.mutate(r)
		response := httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, r)
		if response.Code != tc.status || len(donor.sessions) != 0 {
			t.Errorf("%s: got %d after %d store calls, want %d before any", tc.name, response.Code, len(donor.sessions), tc.status)
		}
	}
}

func TestRevokedOrExpiredBearerIsUnauthorized(t *testing.T) {
	donor := &sessionDonor{}
	server := NewServer("", Services{Donor: donor})
	for _, method := range []string{"GET", "POST"} {
		path, body := "/donor/me/profile", ""
		if method == "POST" {
			path, body = "/donor/me/preferences", preferences
		}
		r := appRequest(method, path, body)
		r.Header.Set("Authorization", "Bearer "+strings.Repeat("x", 43))
		response := httptest.NewRecorder()
		server.Handler.ServeHTTP(response, r)
		if response.Code != http.StatusUnauthorized {
			t.Errorf("%s %s with a session the store denies: got %d, want 401", method, path, response.Code)
		}
	}
}

func TestAppRequestsAreRateLimited(t *testing.T) {
	donor := &sessionDonor{}
	server := NewServer("", Services{Donor: donor})
	for range accountLimit.limit {
		server.Handler.ServeHTTP(httptest.NewRecorder(), appRequest("POST", "/donor/me/preferences", preferences))
	}
	response := httptest.NewRecorder()
	server.Handler.ServeHTTP(response, appRequest("POST", "/donor/me/preferences", preferences))
	if response.Code != http.StatusTooManyRequests || response.Header().Get("Retry-After") == "" {
		t.Fatalf("got %d with Retry-After %q, want 429 with a delay", response.Code, response.Header().Get("Retry-After"))
	}
	if len(donor.sessions) != accountLimit.limit {
		t.Fatalf("store saw %d requests, want %d", len(donor.sessions), accountLimit.limit)
	}
}

func TestClaimReturnsTokensOnlyToTheApp(t *testing.T) {
	proof := `{"token":"` + strings.Repeat("p", 43) + `"}`
	for _, tc := range []struct {
		name        string
		request     func() *http.Request
		status      int
		bodyTokens  bool
		cookieToken bool
	}{
		{"app", func() *http.Request {
			r := appRequest("POST", "/donor/claim", proof)
			r.Header.Del("Authorization")
			return r
		}, http.StatusOK, true, false},
		{"browser", func() *http.Request {
			r := httptest.NewRequest("POST", "/donor/claim", strings.NewReader(proof))
			r.Header.Set("Origin", "http://localhost:3000")
			return r
		}, http.StatusOK, false, true},
		{"browser claiming the marker", func() *http.Request {
			r := httptest.NewRequest("POST", "/donor/claim", strings.NewReader(proof))
			r.Header.Set("Origin", "http://localhost:3000")
			r.Header.Set("X-DAD-Client", "native")
			return r
		}, http.StatusOK, false, true},
		{"marker with cookie and no origin", func() *http.Request {
			r := appRequest("POST", "/donor/claim", proof)
			r.AddCookie(&http.Cookie{Name: donorCookie, Value: "old"})
			return r
		}, http.StatusForbidden, false, false},
	} {
		response := httptest.NewRecorder()
		NewServer("", Services{Account: &accountStub{}}).Handler.ServeHTTP(response, tc.request())
		var body map[string]any
		_ = json.Unmarshal(response.Body.Bytes(), &body)
		_, access := body["access_token"]
		_, refresh := body["refresh_token"]
		cookieToken := strings.Contains(response.Header().Get("Set-Cookie"), browserTokens.Access)
		if response.Code != tc.status || access != tc.bodyTokens || refresh != tc.bodyTokens || cookieToken != tc.cookieToken {
			t.Errorf("%s: got %d, tokens in body %v/%v, in cookie %v; want %d, %v, %v",
				tc.name, response.Code, access, refresh, cookieToken, tc.status, tc.bodyTokens, tc.cookieToken)
		}
		want := map[string]any{
			"access_token":       appTokens.Access,
			"expires_in":         float64(15 * 60),
			"refresh_token":      appTokens.Refresh,
			"refresh_expires_in": float64(60 * 24 * 60 * 60),
		}
		if tc.bodyTokens && !reflect.DeepEqual(body, want) {
			t.Errorf("%s: native session body %v, want %v", tc.name, body, want)
		}
	}
}

func TestClaimRecordsOnlyTheAppsDeviceDescription(t *testing.T) {
	proof := strings.Repeat("p", 43)
	name := strings.Repeat("é", store.MaxDeviceName)
	for _, tc := range []struct {
		name, fields string
		app          bool
		status       int
		client       store.Client
	}{
		{"app device", `,"device_name":"` + name + `","platform":"IOS"`, true, http.StatusOK, store.Client{App: true, Device: name, Platform: "IOS"}},
		{"app without description", ``, true, http.StatusOK, store.Client{App: true}},
		{"browser description ignored", `,"device_name":"Laptop","platform":"ANDROID"`, false, http.StatusOK, store.Client{}},
		{"unknown platform", `,"platform":"WINDOWS"`, true, http.StatusUnprocessableEntity, store.Client{}},
		{"null platform", `,"platform":null`, true, http.StatusUnprocessableEntity, store.Client{}},
		{"long device name", `,"device_name":"` + name + `x"`, true, http.StatusUnprocessableEntity, store.Client{}},
	} {
		body := `{"token":"` + proof + `"` + tc.fields + `}`
		r := httptest.NewRequest("POST", "/donor/claim", strings.NewReader(body))
		if tc.app {
			r.Header.Set("X-DAD-Client", "native")
		} else {
			r.Header.Set("Origin", "http://localhost:3000")
		}
		stub := &accountStub{}
		response := httptest.NewRecorder()
		NewServer("", Services{Account: stub}).Handler.ServeHTTP(response, r)
		if response.Code != tc.status || stub.client != tc.client {
			t.Errorf("%s: got %d issuing to %+v, want %d issuing to %+v", tc.name, response.Code, stub.client, tc.status, tc.client)
		}
	}
}

func TestRefreshServesOnlyTheApp(t *testing.T) {
	refresh := strings.Repeat("f", 43) + "." + strings.Repeat("s", 43)
	body := `{"refresh_token":"` + refresh + `"}`
	app := func() *http.Request {
		r := httptest.NewRequest("POST", "/donor/refresh", strings.NewReader(body))
		r.Header.Set("X-DAD-Client", "native")
		return r
	}
	stub := &accountStub{}
	response := httptest.NewRecorder()
	NewServer("", Services{Account: stub}).Handler.ServeHTTP(response, app())
	var tokens map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &tokens); err != nil || response.Code != http.StatusOK || stub.refresh != refresh ||
		tokens["access_token"] != appTokens.Access || tokens["refresh_token"] != appTokens.Refresh || response.Header().Get("Set-Cookie") != "" {
		t.Fatalf("app refresh: got %d %s exchanging %q, want 200 with new tokens and no cookie", response.Code, response.Body, stub.refresh)
	}
	for _, tc := range []struct {
		name   string
		mutate func(*http.Request)
	}{
		{"same-origin browser", func(r *http.Request) { r.Header.Set("Origin", "http://localhost:3000") }},
		{"browser without origin", func(r *http.Request) { r.Header.Del("X-DAD-Client") }},
		{"marker with cookie", func(r *http.Request) { r.AddCookie(&http.Cookie{Name: donorCookie, Value: appSession}) }},
	} {
		stub := &accountStub{}
		r := app()
		tc.mutate(r)
		response := httptest.NewRecorder()
		NewServer("", Services{Account: stub}).Handler.ServeHTTP(response, r)
		if response.Code != http.StatusForbidden || stub.refresh != "" || strings.Contains(response.Body.String(), "token") {
			t.Errorf("%s: got %d %s after exchanging %q, want 403 before the store", tc.name, response.Code, response.Body, stub.refresh)
		}
	}
}

func TestRefreshAnswersWithoutLeakingCauses(t *testing.T) {
	refresh := `{"refresh_token":"` + strings.Repeat("f", 43) + "." + strings.Repeat("s", 43) + `"}`
	for _, tc := range []struct {
		body   string
		err    error
		status int
	}{
		{refresh, store.ErrDenied, http.StatusUnauthorized},
		{refresh, errors.New("private failure"), http.StatusServiceUnavailable},
		{`{"refresh_token":"short"}`, nil, http.StatusUnprocessableEntity},
		{`{}`, nil, http.StatusUnprocessableEntity},
	} {
		r := httptest.NewRequest("POST", "/donor/refresh", strings.NewReader(tc.body))
		r.Header.Set("X-DAD-Client", "native")
		response := httptest.NewRecorder()
		NewServer("", Services{Account: &accountStub{err: tc.err}}).Handler.ServeHTTP(response, r)
		if response.Code != tc.status || strings.Contains(response.Body.String(), "private") ||
			strings.Contains(response.Body.String(), appTokens.Refresh) {
			t.Errorf("%s with %v: got %d %s, want %d without causes or tokens", tc.body, tc.err, response.Code, response.Body, tc.status)
		}
	}
}

func TestRefreshUsesTheAccountBudget(t *testing.T) {
	server := NewServer("", Services{Account: &accountStub{}})
	refresh := func() int {
		r := httptest.NewRequest("POST", "/donor/refresh", strings.NewReader(`{"refresh_token":"`+strings.Repeat("r", 87)+`"}`))
		r.Header.Set("X-DAD-Client", "native")
		response := httptest.NewRecorder()
		server.Handler.ServeHTTP(response, r)
		return response.Code
	}
	for i := range accountLimit.limit {
		if status := refresh(); status != http.StatusOK {
			t.Fatalf("refresh %d: got %d, want 200 within the account budget", i+1, status)
		}
	}
	if status := refresh(); status != http.StatusTooManyRequests {
		t.Fatalf("refresh over budget: got %d, want 429", status)
	}
}

func TestAppClaimRevokesThePresentedSession(t *testing.T) {
	stub := &accountStub{}
	r := appRequest("POST", "/donor/claim", `{"token":"`+strings.Repeat("p", 43)+`"}`)
	NewServer("", Services{Account: stub}).Handler.ServeHTTP(httptest.NewRecorder(), r)
	if stub.old != appSession {
		t.Fatalf("claim revoked %q, want the presented bearer session", stub.old)
	}
}

func TestAppLogoutRevokesBearerWithoutCookies(t *testing.T) {
	stub := &accountStub{}
	response := httptest.NewRecorder()
	NewServer("", Services{Account: stub}).Handler.ServeHTTP(response, appRequest("POST", "/donor/logout", ""))
	if response.Code != http.StatusOK || stub.old != appSession || response.Header().Get("Set-Cookie") != "" {
		t.Fatalf("got %d revoking %q with Set-Cookie %q, want 200 revoking the bearer session and no cookie",
			response.Code, stub.old, response.Header().Get("Set-Cookie"))
	}
}

// The app may call donor account, partner portal, and link capture routes
// only; guest giving, partner applications, and the back office stay on the web.
func TestOnlyDonorAccountRoutesAdmitTheApp(t *testing.T) {
	entries := map[string]bool{
		"POST /donor/signup":                   true,
		"POST /donor/password/sign-in":         true,
		"POST /donor/login":                    true,
		"POST /donor/claim":                    true,
		"POST /donor/refresh":                  true,
		"POST /donor/email-change/confirm":     true,
		"POST /donor/passkeys/sign-in/options": true,
		"POST /donor/passkeys/sign-in":         true,
		"POST /donor/phone/sign-in/options":    true,
		"POST /donor/phone/sign-in":            true,
		"POST /donor/oidc/{provider}/sign-in":  true,
		"POST /donor/logout":                   true,
		"POST /donor/logout-all":               true,
		"POST /analytics/events":               true,
		"POST /donor/referrals/{code}":         true,
		"POST /donor/company/{code}":           true,
		"POST /donor/partner/sign-in":          true,
		"POST /donor/partner/accept-invite":    true,
	}
	server := NewServer("", Services{})
	for _, route := range allRoutes() {
		if !strings.HasPrefix(route.pattern, "POST ") || route.auth == stripeAuth {
			continue
		}
		admitted := entries[route.pattern] || strings.HasPrefix(route.pattern, "POST /donor/me/") || route.auth == partnerAuth
		response := httptest.NewRecorder()
		server.Handler.ServeHTTP(response, appRequest("POST", routeRequest(route.pattern, "").URL.Path, "{}"))
		if got := response.Code != http.StatusForbidden; got != admitted {
			t.Errorf("%s from the app: got %d, want admitted=%v", route.pattern, response.Code, admitted)
		}
	}
	response := httptest.NewRecorder()
	server.Handler.ServeHTTP(response, appRequest("GET", "/donor/staff/me", ""))
	if response.Code != http.StatusUnauthorized {
		t.Errorf("staff read with a bearer token: got %d, want 401", response.Code)
	}
}

func TestSessionRoutesServeCookieAndBearerCallers(t *testing.T) {
	browser := func(method, path string) *http.Request {
		r := httptest.NewRequest(method, path, nil)
		r.Header.Set("Origin", "http://localhost:3000")
		r.AddCookie(&http.Cookie{Name: donorCookie, Value: appSession})
		return r
	}
	for _, caller := range []struct {
		name    string
		request func(method, path string) *http.Request
	}{
		{"cookie", browser},
		{"bearer", func(method, path string) *http.Request { return appRequest(method, path, "") }},
	} {
		for _, path := range []string{"GET /donor/me/sessions", "POST /donor/me/sessions/" + testID + "/revoke", "POST /donor/me/sessions/revoke-others"} {
			method, path, _ := strings.Cut(path, " ")
			donor := &sessionDonor{}
			response := httptest.NewRecorder()
			NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, caller.request(method, path))
			var body struct {
				Sessions []store.DeviceSession `json:"sessions"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil || response.Code != http.StatusOK || body.Sessions == nil ||
				len(donor.sessions) != 1 ||
				donor.sessions[0] != appSession {
				t.Errorf(
					"%s %s %s: got %d %s with sessions %q, want 200 with a session list for the caller",
					caller.name,
					method,
					path,
					response.Code,
					response.Body,
					donor.sessions,
				)
			}
			if strings.HasSuffix(path, "/revoke") && donor.revoked.String() != testID {
				t.Errorf("%s: revoked %s, want %s", caller.name, donor.revoked.String(), testID)
			}
		}
	}
}

func TestSessionRevocationErrors(t *testing.T) {
	for _, tc := range []struct {
		path   string
		err    error
		status int
		calls  int
	}{
		{"/donor/me/sessions/" + testID + "/revoke", store.ErrCurrentSession, http.StatusConflict, 1},
		{"/donor/me/sessions/" + testID + "/revoke", pgx.ErrNoRows, http.StatusNotFound, 1},
		{"/donor/me/sessions/not-a-session/revoke", nil, http.StatusNotFound, 0},
		{"/donor/me/sessions/revoke-others", errors.New("private failure"), http.StatusServiceUnavailable, 1},
	} {
		donor := &sessionDonor{err: tc.err}
		response := httptest.NewRecorder()
		NewServer("", Services{Donor: donor}).Handler.ServeHTTP(response, appRequest("POST", tc.path, ""))
		if response.Code != tc.status || len(donor.sessions) != tc.calls || strings.Contains(response.Body.String(), "private") {
			t.Errorf(
				"%s with %v: got %d %s after %d store calls, want %d after %d",
				tc.path,
				tc.err,
				response.Code,
				response.Body,
				len(donor.sessions),
				tc.status,
				tc.calls,
			)
		}
	}
}
