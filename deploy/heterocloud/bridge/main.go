// The loopback bridge authenticates Terraform requests with rotating task IAM
// credentials. Tokens remain in memory and never enter Terraform state.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
)

const address = "127.0.0.1:7081"

var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

type credentials struct {
	mu        sync.Mutex
	endpoint  *url.URL
	org       string
	file      string
	client    *http.Client
	now       func() time.Time
	token     string
	refreshAt time.Time
	exchanges uint64
}

func (c *credentials) bearer(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.token != "" && c.now().Before(c.refreshAt) {
		return c.token, nil
	}
	// Read the path on every refresh, following Kubernetes' rotated symlink.
	info, err := os.Stat(c.file)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16384 {
		return "", errors.New("task identity file is unavailable or invalid")
	}
	subject, err := os.ReadFile(c.file)
	if err != nil || len(subject) > 16384 || len(strings.TrimSpace(string(subject))) == 0 {
		return "", errors.New("task identity file cannot be read")
	}
	form := url.Values{
		"grant_type":         {"urn:ietf:params:oauth:grant-type:token-exchange"},
		"subject_token_type": {"urn:ietf:params:oauth:token-type:jwt"},
		"subject_token":      {strings.TrimSpace(string(subject))},
	}
	endpoint := c.endpoint.ResolveReference(&url.URL{Path: "/api/v1/auth/workload/token"})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), strings.NewReader(form.Encode()))
	if err != nil {
		return "", errors.New("invalid task identity endpoint")
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response, err := c.client.Do(request)
	if err != nil {
		return "", errors.New("task identity exchange could not reach HeteroCloud")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("task identity exchange rejected (HTTP %d)", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 32769))
	if err != nil || len(body) > 32768 {
		return "", errors.New("invalid task identity response")
	}
	var value struct {
		AccessToken    string `json:"access_token"`
		TokenType      string `json:"token_type"`
		ExpiresIn      int64  `json:"expires_in"`
		OrganizationID string `json:"organization_id"`
	}
	if json.Unmarshal(body, &value) != nil || value.OrganizationID != c.org || value.TokenType != "Bearer" ||
		value.ExpiresIn < 1 || value.ExpiresIn > 900 || !strings.HasPrefix(value.AccessToken, "hcw_") ||
		len(value.AccessToken) > 256 || strings.ContainsAny(value.AccessToken, " \t\r\n") {
		return "", errors.New("task identity response does not match this organization")
	}
	refreshAfter := value.ExpiresIn - 60
	if refreshAfter < 1 {
		refreshAfter = 1
	}
	c.token = value.AccessToken
	c.refreshAt = c.now().Add(time.Duration(refreshAfter) * time.Second)
	c.exchanges++
	return c.token, nil
}

type authenticatedTransport struct {
	credentials *credentials
	base        http.RoundTripper
}

func (t authenticatedTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	token, err := t.credentials.bearer(request.Context())
	if err != nil {
		return nil, err
	}
	copy := request.Clone(request.Context())
	copy.Header = request.Header.Clone()
	copy.Header.Set("Authorization", "Bearer "+token)
	copy.Header.Del("Cookie")
	return t.base.RoundTrip(copy)
}

func handler(c *credentials, transport http.RoundTripper) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(c.endpoint)
	director := proxy.Director
	proxy.Director = func(request *http.Request) {
		director(request)
		request.Host = c.endpoint.Host
	}
	proxy.Transport = authenticatedTransport{credentials: c, base: transport}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		// Do not log requests, responses, headers, JWTs, or upstream error bodies.
		log.Print("HeteroCloud task IAM request failed")
		http.Error(w, "HeteroCloud task IAM request failed", http.StatusBadGateway)
	}
	collection := "/api/v1/organizations/" + c.org + "/flash/services"
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Method == http.MethodGet && request.URL.Path == "/healthz" {
			c.mu.Lock()
			status := struct {
				Exchanges uint64    `json:"exchanges"`
				RefreshAt time.Time `json:"refresh_at"`
			}{c.exchanges, c.refreshAt}
			c.mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(status)
			return
		}
		allowed := request.Method == http.MethodGet && request.URL.Path == "/api/v1/auth/identity"
		if request.URL.Path == collection {
			allowed = request.Method == http.MethodGet || request.Method == http.MethodPost
		} else if strings.HasPrefix(request.URL.Path, collection+"/") {
			identifier := strings.TrimPrefix(request.URL.Path, collection+"/")
			allowed = uuidPattern.MatchString(identifier) && (request.Method == http.MethodGet ||
				request.Method == http.MethodPut || request.Method == http.MethodDelete)
		}
		if !allowed {
			http.Error(w, "operation is outside the workspace API", http.StatusForbidden)
			return
		}
		request.Body = http.MaxBytesReader(w, request.Body, 2*1024*1024)
		proxy.ServeHTTP(w, request)
	})
}

func run() error {
	endpoint, err := url.Parse(os.Getenv("HETEROCLOUD_ENDPOINT"))
	org := os.Getenv("HETEROCLOUD_ORGANIZATION_ID")
	file := os.Getenv("HETEROCLOUD_WORKLOAD_TOKEN_FILE")
	if err != nil || endpoint.Scheme != "https" || endpoint.Host == "" || endpoint.User != nil ||
		(endpoint.Path != "" && endpoint.Path != "/") || endpoint.RawQuery != "" || endpoint.Fragment != "" ||
		!uuidPattern.MatchString(org) || file == "" {
		return errors.New("HeteroCloud task IAM environment is missing or invalid")
	}
	if os.Getenv("REST_API_BEARER") != "" || os.Getenv("HETEROCLOUD_API_KEY") != "" || os.Getenv("HETEROCLOUD_API_KEY_FILE") != "" {
		return errors.New("remove fixed cloud credentials before starting task IAM")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.ResponseHeaderTimeout = 55 * time.Second
	c := &credentials{endpoint: endpoint, org: org, file: file, now: time.Now,
		client: &http.Client{Transport: transport, Timeout: 30 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return errors.New("cannot start the local task IAM bridge")
	}
	server := &http.Server{Handler: handler(c, transport), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	serverFailure := make(chan error, 1)
	go func() { serverFailure <- server.Serve(listener) }()
	log.Print("HeteroCloud task IAM bridge started on loopback")
	child := exec.Command("/opt/coder", "server")
	child.Stdout, child.Stderr, child.Stdin = os.Stdout, os.Stderr, os.Stdin
	child.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err = child.Start(); err != nil {
		server.Close()
		return errors.New("cannot start Coder")
	}
	finished := make(chan error, 1)
	go func() { finished <- child.Wait() }()
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	defer signal.Stop(signals)
	select {
	case err = <-finished:
	case <-signals:
		_ = syscall.Kill(-child.Process.Pid, syscall.SIGTERM)
		select {
		case err = <-finished:
		case <-time.After(20 * time.Second):
			_ = syscall.Kill(-child.Process.Pid, syscall.SIGKILL)
			err = <-finished
		}
	case <-serverFailure:
		_ = syscall.Kill(-child.Process.Pid, syscall.SIGTERM)
		err = <-finished
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	return err
}

func main() {
	if err := run(); err != nil {
		log.Print("Coder task IAM launcher exited")
		os.Exit(1)
	}
}
