package portraithub

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func newTestServer(t *testing.T, handler func(w http.ResponseWriter, r *http.Request)) (*httptest.Server, *Client) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(handler))
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL,
		WithAPIToken("token"),
		WithTenantID("tenant-a"),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return server, client
}

func TestHeadersSupportBearerAndApplicationAPIKey(t *testing.T) {
	var got http.Header
	_, client := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})

	if _, err := client.Health(); err != nil {
		t.Fatalf("Health: %v", err)
	}
	if got.Get("Authorization") != "Bearer token" {
		t.Fatalf("Authorization = %q", got.Get("Authorization"))
	}
	if got.Get("X-Tenant-ID") != "tenant-a" {
		t.Fatalf("X-Tenant-ID = %q", got.Get("X-Tenant-ID"))
	}
	if got.Get("User-Agent") != "portrait-hub-sdk-go/"+SDKVersion {
		t.Fatalf("User-Agent = %q", got.Get("User-Agent"))
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer server.Close()
	apiKeyClient, err := NewClient(server.URL, WithAPIToken("phk_secret"), WithAuthScheme("api-key"))
	if err != nil {
		t.Fatalf("NewClient(api-key): %v", err)
	}
	if _, err := apiKeyClient.Health(); err != nil {
		t.Fatalf("Health: %v", err)
	}
	if got.Get("X-API-Key") != "phk_secret" {
		t.Fatalf("X-API-Key = %q", got.Get("X-API-Key"))
	}
	if got.Get("Authorization") != "" {
		t.Fatalf("Authorization should be empty, got %q", got.Get("Authorization"))
	}

	if _, err := NewClient("http://testserver", WithAuthScheme("basic")); err == nil {
		t.Fatal("expected error for invalid auth scheme")
	}
}

func TestHTTPErrorKeepsStructuredPayload(t *testing.T) {
	_, client := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"error":{"code":"rate_limited"}}`))
	})

	_, err := client.Health()
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) {
		t.Fatalf("expected HTTPError, got %v", err)
	}
	if httpErr.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("StatusCode = %d", httpErr.StatusCode)
	}
	payload, _ := json.Marshal(httpErr.Payload)
	if string(payload) != `{"error":{"code":"rate_limited"}}` {
		t.Fatalf("Payload = %s", payload)
	}
}

func TestNonObjectSuccessBecomesBadGateway(t *testing.T) {
	_, client := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[1,2,3]`))
	})

	_, err := client.Health()
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) {
		t.Fatalf("expected HTTPError, got %v", err)
	}
	if httpErr.StatusCode != 502 {
		t.Fatalf("StatusCode = %d", httpErr.StatusCode)
	}
}

func TestPathSegmentEncoding(t *testing.T) {
	var gotPath string
	_, client := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})

	if _, err := client.GetModel("portrait/arcface r100"); err != nil {
		t.Fatalf("GetModel: %v", err)
	}
	if gotPath != "/v1/models/portrait%2Farcface%20r100" {
		t.Fatalf("path = %q", gotPath)
	}
}

func TestQueryParametersSkipNilAndFormatBooleans(t *testing.T) {
	var gotQuery string
	_, client := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})

	limit := 10
	if _, err := client.ListStreams(&limit, nil, nil); err != nil {
		t.Fatalf("ListStreams: %v", err)
	}
	if gotQuery != "limit=10" {
		t.Fatalf("query = %q", gotQuery)
	}
}
