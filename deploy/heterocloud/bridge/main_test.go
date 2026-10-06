package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testOrg = "019fbda8-eff3-7610-bdd5-8fe89e746c14"

func TestRefreshReadsRotatedProjectionAndCachesBeforeDeadline(t *testing.T) {
	dir := t.TempDir()
	first, second, file := filepath.Join(dir, "first"), filepath.Join(dir, "second"), filepath.Join(dir, "identity")
	os.WriteFile(first, []byte("synthetic-first-jwt"), 0600)
	os.WriteFile(second, []byte("synthetic-rotated-jwt"), 0600)
	if err := os.Symlink(first, file); err != nil {
		t.Fatal(err)
	}
	var subjects []string
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseForm(); err != nil {
			t.Fatal(err)
		}
		if r.Form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:token-exchange" {
			t.Error("wrong grant")
		}
		subjects = append(subjects, r.Form.Get("subject_token"))
		json.NewEncoder(w).Encode(map[string]interface{}{"access_token": "hcw_synthetic_" + r.Form.Get("subject_token"),
			"token_type": "Bearer", "expires_in": 900, "organization_id": testOrg})
	}))
	defer server.Close()
	u, _ := url.Parse(server.URL)
	now := time.Now()
	c := &credentials{endpoint: u, org: testOrg, file: file, client: server.Client(), now: func() time.Time { return now }}
	one, err := c.bearer(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if again, err := c.bearer(context.Background()); err != nil || again != one || len(subjects) != 1 {
		t.Fatal("credential was not cached")
	}
	if err := os.Remove(file); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(second, file); err != nil {
		t.Fatal(err)
	}
	now = now.Add(841 * time.Second)
	two, err := c.bearer(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if two == one || len(subjects) != 2 || subjects[1] != "synthetic-rotated-jwt" {
		t.Fatal("rotated projection was not used")
	}
}

func TestExchangeErrorsDoNotExposeCredentials(t *testing.T) {
	file := filepath.Join(t.TempDir(), "jwt")
	os.WriteFile(file, []byte("synthetic-secret-subject"), 0600)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"access_token": "hcw_synthetic-secret-access", "token_type": "Bearer",
			"expires_in": 900, "organization_id": "foreign-org"})
	}))
	defer server.Close()
	u, _ := url.Parse(server.URL)
	c := &credentials{endpoint: u, org: testOrg, file: file, client: server.Client(), now: time.Now}
	_, err := c.bearer(context.Background())
	if err == nil || strings.Contains(err.Error(), "synthetic-secret") {
		t.Fatal("unsafe exchange error")
	}
}

func TestProxyAuthenticatesOnlyWorkspaceRoutes(t *testing.T) {
	file := filepath.Join(t.TempDir(), "jwt")
	os.WriteFile(file, []byte("synthetic-jwt"), 0600)
	var forwarded bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/auth/workload/token" {
			json.NewEncoder(w).Encode(map[string]interface{}{"access_token": "hcw_synthetic-proxy", "token_type": "Bearer",
				"expires_in": 900, "organization_id": testOrg})
			return
		}
		forwarded = true
		if r.Header.Get("Authorization") != "Bearer hcw_synthetic-proxy" || r.Header.Get("Cookie") != "" {
			t.Error("incorrect upstream authentication")
		}
		io.WriteString(w, `{"items":[]}`)
	}))
	defer server.Close()
	u, _ := url.Parse(server.URL)
	c := &credentials{endpoint: u, org: testOrg, file: file, client: server.Client(), now: time.Now}
	h := handler(c, server.Client().Transport)
	r := httptest.NewRequest("GET", "/api/v1/organizations/"+testOrg+"/flash/services", nil)
	r.Header.Set("Authorization", "Bearer untrusted-client-header")
	r.Header.Set("Cookie", "untrusted-client-cookie")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 || !forwarded {
		t.Fatal("workspace request failed")
	}
	forwarded = false
	for _, path := range []string{"/api/v1/organizations/foreign/flash/services", "/api/v1/organizations/" + testOrg + "/iam/principals",
		"/api/v1/organizations/" + testOrg + "/flash/services/019fbda8-eff3-7610-bdd5-8fe89e746c14/secrets"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != 403 || forwarded {
			t.Fatal("out-of-scope request was forwarded")
		}
	}
}
