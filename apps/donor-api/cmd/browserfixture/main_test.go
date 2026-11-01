package main

import (
	"io"
	"strings"
	"testing"
)

func TestRefusesAnythingButTheBrowserTestDatabase(t *testing.T) {
	for _, tc := range []struct {
		args []string
		dsn  string
		want string
	}{
		{[]string{"-kind", "staff", "-origin", "http://127.0.0.1:3000"}, "postgresql://u@localhost/donor_go", "-kind"},
		{[]string{"-kind", "donor"}, "postgresql://u@localhost/donor_go", "-origin"},
		{[]string{"-kind", "donor", "-origin", "http://127.0.0.1:3000"}, "postgresql://u@localhost/dad", "donor_go"},
		{[]string{"-kind", "partner", "-origin", "http://127.0.0.1:3000"}, "", "donor_go"},
	} {
		getenv := func(name string) string {
			if name == "DONOR_GO_DATABASE_URL" {
				return tc.dsn
			}
			return ""
		}
		if err := run(tc.args, getenv, io.Discard); err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%v with %q: got %v, want an error naming %s", tc.args, tc.dsn, err, tc.want)
		}
	}
}
