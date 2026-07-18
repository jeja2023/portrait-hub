// Package portraithub 提供 PortraitHub API 的 Go 客户端。
//
// 能力与 Python/Node SDK 对齐：Bearer / X-API-Key 双认证、租户头、
// 结构化 HTTP 错误、multipart 上传、路径段编码与请求超时。
package portraithub

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// SDKVersion 与仓库版本保持一致。
const SDKVersion = "0.11.2"

const userAgent = "portrait-hub-sdk-go/" + SDKVersion

// HTTPError 是非 2xx 响应或畸形成功响应的结构化错误。
type HTTPError struct {
	StatusCode int
	Payload    any
	Header     http.Header
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("PortraitHub 请求失败 with HTTP %d: %v", e.StatusCode, e.Payload)
}

// Option 配置 Client。
type Option func(*Client)

// WithAPIToken 设置凭证（平台 token 或应用 API key）。
func WithAPIToken(token string) Option {
	return func(c *Client) { c.apiToken = token }
}

// WithTenantID 设置 X-Tenant-ID 请求头。
func WithTenantID(tenantID string) Option {
	return func(c *Client) { c.tenantID = tenantID }
}

// WithAuthScheme 设置认证方式："bearer"（默认）或 "api_key"。
func WithAuthScheme(scheme string) Option {
	return func(c *Client) { c.authScheme = scheme }
}

// WithTimeout 设置请求超时（默认 30s）。
func WithTimeout(timeout time.Duration) Option {
	return func(c *Client) { c.httpClient.Timeout = timeout }
}

// WithHTTPClient 注入自定义 http.Client（测试或代理场景）。
func WithHTTPClient(client *http.Client) Option {
	return func(c *Client) { c.httpClient = client }
}

// Client 是 PortraitHub API 客户端。
type Client struct {
	baseURL    string
	apiToken   string
	tenantID   string
	authScheme string
	httpClient *http.Client
}

// NewClient 创建客户端。authScheme 非法时返回错误。
func NewClient(baseURL string, opts ...Option) (*Client, error) {
	client := &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		authScheme: "bearer",
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	for _, opt := range opts {
		opt(client)
	}
	normalized := strings.ReplaceAll(strings.ToLower(strings.TrimSpace(client.authScheme)), "-", "_")
	if normalized != "bearer" && normalized != "api_key" {
		return nil, fmt.Errorf("authScheme 必须是 'bearer' 或 'api_key'")
	}
	client.authScheme = normalized
	return client, nil
}

func (c *Client) headers(req *http.Request, contentType string) {
	req.Header.Set("User-Agent", userAgent)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if c.tenantID != "" {
		req.Header.Set("X-Tenant-ID", c.tenantID)
	}
	if c.apiToken != "" {
		if c.authScheme == "api_key" {
			req.Header.Set("X-API-Key", c.apiToken)
		} else {
			req.Header.Set("Authorization", "Bearer "+c.apiToken)
		}
	}
}

func pathSegment(value string) string {
	return url.PathEscape(value)
}

func pathWithQuery(path string, params map[string]any) string {
	query := url.Values{}
	for key, value := range params {
		if value == nil {
			continue
		}
		switch v := value.(type) {
		case bool:
			query.Set(key, fmt.Sprintf("%t", v))
		case string:
			if v == "" {
				continue
			}
			query.Set(key, v)
		case *int:
			if v != nil {
				query.Set(key, fmt.Sprintf("%d", *v))
			}
		case *string:
			if v != nil && *v != "" {
				query.Set(key, *v)
			}
		default:
			query.Set(key, fmt.Sprintf("%v", v))
		}
	}
	encoded := query.Encode()
	if encoded == "" {
		return path
	}
	return path + "?" + encoded
}

func decodeBody(body []byte, contentType string) any {
	text := string(body)
	if text == "" {
		return map[string]any{}
	}
	if !strings.Contains(contentType, "application/json") {
		return text
	}
	var payload any
	if err := json.Unmarshal(body, &payload); err != nil {
		return text
	}
	return payload
}

func (c *Client) do(req *http.Request) (map[string]any, error) {
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	payload := decodeBody(raw, resp.Header.Get("Content-Type"))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &HTTPError{StatusCode: resp.StatusCode, Payload: payload, Header: resp.Header}
	}
	object, ok := payload.(map[string]any)
	if !ok {
		return nil, &HTTPError{StatusCode: 502, Payload: payload, Header: resp.Header}
	}
	return object, nil
}

func (c *Client) json(method, path string, body any) (map[string]any, error) {
	var reader io.Reader
	contentType := ""
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(encoded)
		contentType = "application/json"
	}
	req, err := http.NewRequest(method, c.baseURL+path, reader)
	if err != nil {
		return nil, err
	}
	c.headers(req, contentType)
	return c.do(req)
}

func (c *Client) get(path string, params map[string]any) (map[string]any, error) {
	req, err := http.NewRequest(http.MethodGet, c.baseURL+pathWithQuery(path, params), nil)
	if err != nil {
		return nil, err
	}
	c.headers(req, "")
	return c.do(req)
}

// FileField 是 multipart 上传中的一个文件字段。
type FileField struct {
	Field string
	Path  string
}

func (c *Client) multipart(path string, fields map[string]any, files []FileField) (map[string]any, error) {
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	for key, value := range fields {
		if value == nil {
			continue
		}
		text := fmt.Sprintf("%v", value)
		if b, ok := value.(bool); ok {
			text = fmt.Sprintf("%t", b)
		}
		if err := writer.WriteField(key, text); err != nil {
			return nil, err
		}
	}
	for _, file := range files {
		data, err := os.ReadFile(file.Path)
		if err != nil {
			return nil, err
		}
		name := filepath.Base(file.Path)
		contentType := mime.TypeByExtension(filepath.Ext(name))
		if contentType == "" {
			contentType = "application/octet-stream"
		}
		part, err := writer.CreatePart(map[string][]string{
			"Content-Disposition": {fmt.Sprintf(`form-data; name=%q; filename=%q`, file.Field, name)},
			"Content-Type":        {contentType},
		})
		if err != nil {
			return nil, err
		}
		if _, err := part.Write(data); err != nil {
			return nil, err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, c.baseURL+path, &buf)
	if err != nil {
		return nil, err
	}
	c.headers(req, writer.FormDataContentType())
	return c.do(req)
}

// Health 调用 GET /health。
func (c *Client) Health() (map[string]any, error) {
	return c.get("/health", nil)
}

// CompareFaces 调用 POST /v1/compare/faces。
func (c *Client) CompareFaces(imageA, imageB, thresholdProfile string) (map[string]any, error) {
	if thresholdProfile == "" {
		thresholdProfile = "normal"
	}
	return c.multipart("/v1/compare/faces",
		map[string]any{"threshold_profile": thresholdProfile},
		[]FileField{{Field: "image_a", Path: imageA}, {Field: "image_b", Path: imageB}})
}

// ComparePersons 调用 POST /v1/compare/persons。
func (c *Client) ComparePersons(imageA, imageB, thresholdProfile string) (map[string]any, error) {
	if thresholdProfile == "" {
		thresholdProfile = "normal"
	}
	return c.multipart("/v1/compare/persons",
		map[string]any{"threshold_profile": thresholdProfile},
		[]FileField{{Field: "image_a", Path: imageA}, {Field: "image_b", Path: imageB}})
}

// Enroll 调用 POST /v1/gallery/enroll。
func (c *Client) Enroll(personID string, images []string, modality string) (map[string]any, error) {
	if modality == "" {
		modality = "body"
	}
	files := make([]FileField, 0, len(images))
	for _, image := range images {
		files = append(files, FileField{Field: "files", Path: image})
	}
	return c.multipart("/v1/gallery/enroll",
		map[string]any{"person_id": personID, "modality": modality}, files)
}

// Search 调用 POST /v1/gallery/search。
func (c *Client) Search(image, modality string, topK int, thresholdProfile string) (map[string]any, error) {
	if modality == "" {
		modality = "body"
	}
	if topK <= 0 {
		topK = 5
	}
	if thresholdProfile == "" {
		thresholdProfile = "normal"
	}
	return c.multipart("/v1/gallery/search",
		map[string]any{"modality": modality, "top_k": topK, "threshold_profile": thresholdProfile},
		[]FileField{{Field: "file", Path: image}})
}

// SearchBatch 调用 POST /v1/gallery/search/batch。
func (c *Client) SearchBatch(images []string, modality string, topK int, thresholdProfile string, asyncMode bool) (map[string]any, error) {
	if modality == "" {
		modality = "body"
	}
	if topK <= 0 {
		topK = 5
	}
	if thresholdProfile == "" {
		thresholdProfile = "normal"
	}
	files := make([]FileField, 0, len(images))
	for _, image := range images {
		files = append(files, FileField{Field: "files", Path: image})
	}
	return c.multipart("/v1/gallery/search/batch",
		map[string]any{
			"modality":          modality,
			"top_k":             topK,
			"threshold_profile": thresholdProfile,
			"async_mode":        asyncMode,
		}, files)
}

// CompareBatch 调用 POST /v1/compare/batch。
func (c *Client) CompareBatch(imagesA, imagesB []string, modality, thresholdProfile string, includeVectors, asyncMode bool) (map[string]any, error) {
	if modality == "" {
		modality = "body"
	}
	if thresholdProfile == "" {
		thresholdProfile = "normal"
	}
	files := make([]FileField, 0, len(imagesA)+len(imagesB))
	for _, image := range imagesA {
		files = append(files, FileField{Field: "image_a", Path: image})
	}
	for _, image := range imagesB {
		files = append(files, FileField{Field: "image_b", Path: image})
	}
	return c.multipart("/v1/compare/batch",
		map[string]any{
			"modality":          modality,
			"threshold_profile": thresholdProfile,
			"include_vectors":   includeVectors,
			"async_mode":        asyncMode,
		}, files)
}

// ReindexGallery 调用 POST /v1/gallery/reindex。
func (c *Client) ReindexGallery(modality, modelID string, dryRun bool) (map[string]any, error) {
	return c.json(http.MethodPost,
		pathWithQuery("/v1/gallery/reindex", map[string]any{"modality": modality, "model_id": modelID, "dry_run": dryRun}),
		nil)
}

// CreateVideoJob 调用 POST /v1/jobs/video。sampleIntervalSeconds/batchSize 传 nil 使用服务端默认。
func (c *Client) CreateVideoJob(video string, sampleIntervalSeconds *float64, batchSize *int) (map[string]any, error) {
	fields := map[string]any{}
	if sampleIntervalSeconds != nil {
		fields["sample_interval_seconds"] = *sampleIntervalSeconds
	}
	if batchSize != nil {
		fields["batch_size"] = *batchSize
	}
	return c.multipart("/v1/jobs/video", fields, []FileField{{Field: "file", Path: video}})
}

// GetJob 调用 GET /v1/jobs/{jobID}。
func (c *Client) GetJob(jobID string) (map[string]any, error) {
	return c.get("/v1/jobs/"+pathSegment(jobID), nil)
}

// JobResult 调用 GET /v1/jobs/{jobID}/result。
func (c *Client) JobResult(jobID string) (map[string]any, error) {
	return c.get("/v1/jobs/"+pathSegment(jobID)+"/result", nil)
}

// CancelJob 调用 POST /v1/jobs/{jobID}/cancel。
func (c *Client) CancelJob(jobID string) (map[string]any, error) {
	return c.json(http.MethodPost, "/v1/jobs/"+pathSegment(jobID)+"/cancel", nil)
}

// CreateStream 调用 POST /v1/streams。
func (c *Client) CreateStream(streamURL, name string, settings, metadata map[string]any) (map[string]any, error) {
	if settings == nil {
		settings = map[string]any{}
	}
	if metadata == nil {
		metadata = map[string]any{}
	}
	body := map[string]any{"stream_url": streamURL, "settings": settings, "metadata": metadata}
	if name != "" {
		body["name"] = name
	}
	return c.json(http.MethodPost, "/v1/streams", body)
}

// ListStreams 调用 GET /v1/streams。limit/offset/cursor 传 nil 使用服务端默认。
func (c *Client) ListStreams(limit, offset *int, cursor *string) (map[string]any, error) {
	return c.get("/v1/streams", map[string]any{"limit": limit, "offset": offset, "cursor": cursor})
}

// GetStream 调用 GET /v1/streams/{streamID}。
func (c *Client) GetStream(streamID string) (map[string]any, error) {
	return c.get("/v1/streams/"+pathSegment(streamID), nil)
}

// StartStream 调用 POST /v1/streams/{streamID}/start。
func (c *Client) StartStream(streamID string) (map[string]any, error) {
	return c.json(http.MethodPost, "/v1/streams/"+pathSegment(streamID)+"/start", nil)
}

// StopStream 调用 POST /v1/streams/{streamID}/stop。
func (c *Client) StopStream(streamID string) (map[string]any, error) {
	return c.json(http.MethodPost, "/v1/streams/"+pathSegment(streamID)+"/stop", nil)
}

// StreamStatus 调用 GET /v1/streams/{streamID}/status。
func (c *Client) StreamStatus(streamID string) (map[string]any, error) {
	return c.get("/v1/streams/"+pathSegment(streamID)+"/status", nil)
}

// StreamEvents 调用 GET /v1/streams/{streamID}/events。
func (c *Client) StreamEvents(streamID string, limit, offset *int, cursor *string) (map[string]any, error) {
	return c.get("/v1/streams/"+pathSegment(streamID)+"/events",
		map[string]any{"limit": limit, "offset": offset, "cursor": cursor})
}

// Models 调用 GET /v1/models。
func (c *Client) Models() (map[string]any, error) {
	return c.get("/v1/models", nil)
}

// GetModel 调用 GET /v1/models/{modelID}。
func (c *Client) GetModel(modelID string) (map[string]any, error) {
	return c.get("/v1/models/"+pathSegment(modelID), nil)
}

// LoadModel 调用 POST /v1/models/{modelID}/load。
func (c *Client) LoadModel(modelID string) (map[string]any, error) {
	return c.json(http.MethodPost, "/v1/models/"+pathSegment(modelID)+"/load", nil)
}

// UnloadModel 调用 POST /v1/models/{modelID}/unload。
func (c *Client) UnloadModel(modelID string) (map[string]any, error) {
	return c.json(http.MethodPost, "/v1/models/"+pathSegment(modelID)+"/unload", nil)
}

// Thresholds 调用 GET /v1/thresholds。
func (c *Client) Thresholds() (map[string]any, error) {
	return c.get("/v1/thresholds", nil)
}

// UpdateThresholds 调用 PUT /v1/thresholds/{profile}。
func (c *Client) UpdateThresholds(profile string, thresholds map[string]float64) (map[string]any, error) {
	return c.json(http.MethodPut, "/v1/thresholds/"+pathSegment(profile), thresholds)
}

// AdminStatus 调用 GET /v1/admin/status。
func (c *Client) AdminStatus() (map[string]any, error) {
	return c.get("/v1/admin/status", nil)
}
