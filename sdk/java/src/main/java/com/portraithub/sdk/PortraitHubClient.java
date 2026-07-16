package com.portraithub.sdk;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.StringJoiner;

/**
 * PortraitHub API 的 Java 客户端（零第三方依赖，基于 java.net.http，需要 Java 11+）。
 *
 * <p>能力与 Python/Node SDK 对齐：Bearer / X-API-Key 双认证、租户头、结构化 HTTP 错误、
 * multipart 上传、路径段编码与请求超时。响应以原始 JSON 字符串返回，由调用方选择
 * JSON 库解析（保持零依赖）。</p>
 */
public final class PortraitHubClient {

    /** 与仓库版本保持一致。 */
    public static final String SDK_VERSION = "0.8.4";

    private static final String USER_AGENT = "portrait-hub-sdk-java/" + SDK_VERSION;

    /** 非 2xx 响应的结构化异常。 */
    public static final class PortraitHubHttpException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        private final int statusCode;
        private final String payload;

        public PortraitHubHttpException(int statusCode, String payload) {
            super("PortraitHub 请求失败 with HTTP " + statusCode + ": " + payload);
            this.statusCode = statusCode;
            this.payload = payload;
        }

        public int statusCode() {
            return statusCode;
        }

        public String payload() {
            return payload;
        }
    }

    /** multipart 上传中的一个文件字段。 */
    public static final class FileField {
        final String field;
        final Path path;

        public FileField(String field, Path path) {
            this.field = field;
            this.path = path;
        }
    }

    /** 构建器。 */
    public static final class Builder {
        private final String baseUrl;
        private String apiToken;
        private String tenantId;
        private String authScheme = "bearer";
        private Duration timeout = Duration.ofSeconds(30);

        public Builder(String baseUrl) {
            this.baseUrl = baseUrl;
        }

        public Builder apiToken(String value) {
            this.apiToken = value;
            return this;
        }

        public Builder tenantId(String value) {
            this.tenantId = value;
            return this;
        }

        public Builder authScheme(String value) {
            this.authScheme = value;
            return this;
        }

        public Builder timeout(Duration value) {
            this.timeout = value;
            return this;
        }

        public PortraitHubClient build() {
            return new PortraitHubClient(this);
        }
    }

    private final String baseUrl;
    private final String apiToken;
    private final String tenantId;
    private final String authScheme;
    private final Duration timeout;
    private final HttpClient httpClient;

    private PortraitHubClient(Builder builder) {
        this.baseUrl = builder.baseUrl.replaceAll("/+$", "");
        this.apiToken = builder.apiToken;
        this.tenantId = builder.tenantId;
        String normalized = builder.authScheme.trim().toLowerCase(Locale.ROOT).replace('-', '_');
        if (!normalized.equals("bearer") && !normalized.equals("api_key")) {
            throw new IllegalArgumentException("authScheme 必须是 'bearer' 或 'api_key'");
        }
        this.authScheme = normalized;
        this.timeout = builder.timeout;
        this.httpClient = HttpClient.newBuilder().connectTimeout(builder.timeout).build();
    }

    public static Builder builder(String baseUrl) {
        return new Builder(baseUrl);
    }

    private HttpRequest.Builder requestBuilder(String path) {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(timeout)
                .header("User-Agent", USER_AGENT);
        if (tenantId != null && !tenantId.isEmpty()) {
            request.header("X-Tenant-ID", tenantId);
        }
        if (apiToken != null && !apiToken.isEmpty()) {
            if (authScheme.equals("api_key")) {
                request.header("X-API-Key", apiToken);
            } else {
                request.header("Authorization", "Bearer " + apiToken);
            }
        }
        return request;
    }

    static String pathSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    static String pathWithQuery(String path, Map<String, Object> params) {
        if (params == null || params.isEmpty()) {
            return path;
        }
        StringJoiner query = new StringJoiner("&");
        boolean any = false;
        for (Map.Entry<String, Object> entry : params.entrySet()) {
            Object value = entry.getValue();
            if (value == null) {
                continue;
            }
            String text = value instanceof Boolean
                    ? value.toString().toLowerCase(Locale.ROOT)
                    : value.toString();
            if (text.isEmpty()) {
                continue;
            }
            query.add(URLEncoder.encode(entry.getKey(), StandardCharsets.UTF_8)
                    + "=" + URLEncoder.encode(text, StandardCharsets.UTF_8));
            any = true;
        }
        return any ? path + "?" + query : path;
    }

    /** 极简 JSON 字符串序列化（键值均按 JSON 规则转义），用于零依赖发送请求体。 */
    static String toJson(Map<String, Object> payload) {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> entry : payload.entrySet()) {
            if (!first) {
                out.append(',');
            }
            first = false;
            out.append(jsonString(entry.getKey())).append(':').append(jsonValue(entry.getValue()));
        }
        return out.append('}').toString();
    }

    private static String jsonValue(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Boolean || value instanceof Number) {
            return value.toString();
        }
        if (value instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> nested = (Map<String, Object>) value;
            return toJson(nested);
        }
        if (value instanceof Iterable) {
            StringJoiner items = new StringJoiner(",", "[", "]");
            for (Object item : (Iterable<?>) value) {
                items.add(jsonValue(item));
            }
            return items.toString();
        }
        return jsonString(value.toString());
    }

    private static String jsonString(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.append('"').toString();
    }

    private String send(HttpRequest request) {
        HttpResponse<String> response;
        try {
            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException exc) {
            throw new UncheckedIOException(exc);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("请求被中断", exc);
        }
        String body = response.body() == null ? "" : response.body();
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new PortraitHubHttpException(response.statusCode(), body);
        }
        String trimmed = body.trim();
        if (!trimmed.startsWith("{")) {
            throw new PortraitHubHttpException(502, body);
        }
        return body;
    }

    private String getJson(String path, Map<String, Object> params) {
        HttpRequest request = requestBuilder(pathWithQuery(path, params)).GET().build();
        return send(request);
    }

    private String sendJson(String method, String path, Map<String, Object> payload) {
        HttpRequest.BodyPublisher body = payload == null
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofString(toJson(payload), StandardCharsets.UTF_8);
        HttpRequest.Builder request = requestBuilder(path).method(method, body);
        if (payload != null) {
            request.header("Content-Type", "application/json");
        }
        return send(request.build());
    }

    private static String multipartHeaderValue(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\r", " ").replace("\n", " ");
    }

    private String multipart(String path, Map<String, Object> fields, List<FileField> files) {
        String boundary = "portrait-hub-" + UUID.randomUUID().toString().replace("-", "");
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        try {
            if (fields != null) {
                for (Map.Entry<String, Object> entry : fields.entrySet()) {
                    if (entry.getValue() == null) {
                        continue;
                    }
                    String value = entry.getValue() instanceof Boolean
                            ? entry.getValue().toString().toLowerCase(Locale.ROOT)
                            : entry.getValue().toString();
                    body.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
                    body.write(("Content-Disposition: form-data; name=\""
                            + multipartHeaderValue(entry.getKey()) + "\"\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                    body.write(value.getBytes(StandardCharsets.UTF_8));
                    body.write("\r\n".getBytes(StandardCharsets.UTF_8));
                }
            }
            if (files != null) {
                for (FileField file : files) {
                    String fileName = file.path.getFileName().toString();
                    String contentType = Files.probeContentType(file.path);
                    if (contentType == null) {
                        contentType = "application/octet-stream";
                    }
                    body.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
                    body.write(("Content-Disposition: form-data; name=\""
                            + multipartHeaderValue(file.field) + "\"; filename=\""
                            + multipartHeaderValue(fileName) + "\"\r\n"
                            + "Content-Type: " + contentType + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                    body.write(Files.readAllBytes(file.path));
                    body.write("\r\n".getBytes(StandardCharsets.UTF_8));
                }
            }
            body.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        } catch (IOException exc) {
            throw new UncheckedIOException(exc);
        }
        HttpRequest request = requestBuilder(path)
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.ofByteArray(body.toByteArray()))
                .build();
        return send(request);
    }

    // ---- API 方法（与 Python/Node SDK 一致） ----

    public String health() {
        return getJson("/health", null);
    }

    public String compareFaces(Path imageA, Path imageB, String thresholdProfile) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("threshold_profile", thresholdProfile == null ? "normal" : thresholdProfile);
        List<FileField> files = new ArrayList<>();
        files.add(new FileField("image_a", imageA));
        files.add(new FileField("image_b", imageB));
        return multipart("/v1/compare/faces", fields, files);
    }

    public String comparePersons(Path imageA, Path imageB, String thresholdProfile) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("threshold_profile", thresholdProfile == null ? "normal" : thresholdProfile);
        List<FileField> files = new ArrayList<>();
        files.add(new FileField("image_a", imageA));
        files.add(new FileField("image_b", imageB));
        return multipart("/v1/compare/persons", fields, files);
    }

    public String enroll(String personId, List<Path> images, String modality) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("person_id", personId);
        fields.put("modality", modality == null ? "body" : modality);
        List<FileField> files = new ArrayList<>();
        for (Path image : images) {
            files.add(new FileField("files", image));
        }
        return multipart("/v1/gallery/enroll", fields, files);
    }

    public String search(Path image, String modality, int topK, String thresholdProfile) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("modality", modality == null ? "body" : modality);
        fields.put("top_k", topK <= 0 ? 5 : topK);
        fields.put("threshold_profile", thresholdProfile == null ? "normal" : thresholdProfile);
        List<FileField> files = new ArrayList<>();
        files.add(new FileField("file", image));
        return multipart("/v1/gallery/search", fields, files);
    }

    public String searchBatch(List<Path> images, String modality, int topK, String thresholdProfile, boolean asyncMode) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("modality", modality == null ? "body" : modality);
        fields.put("top_k", topK <= 0 ? 5 : topK);
        fields.put("threshold_profile", thresholdProfile == null ? "normal" : thresholdProfile);
        fields.put("async_mode", asyncMode);
        List<FileField> files = new ArrayList<>();
        for (Path image : images) {
            files.add(new FileField("files", image));
        }
        return multipart("/v1/gallery/search/batch", fields, files);
    }

    public String compareBatch(List<Path> imagesA, List<Path> imagesB, String modality,
                               String thresholdProfile, boolean includeVectors, boolean asyncMode) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("modality", modality == null ? "body" : modality);
        fields.put("threshold_profile", thresholdProfile == null ? "normal" : thresholdProfile);
        fields.put("include_vectors", includeVectors);
        fields.put("async_mode", asyncMode);
        List<FileField> files = new ArrayList<>();
        for (Path image : imagesA) {
            files.add(new FileField("image_a", image));
        }
        for (Path image : imagesB) {
            files.add(new FileField("image_b", image));
        }
        return multipart("/v1/compare/batch", fields, files);
    }

    public String reindexGallery(String modality, String modelId, boolean dryRun) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("modality", modality);
        params.put("model_id", modelId);
        params.put("dry_run", dryRun);
        return sendJson("POST", pathWithQuery("/v1/gallery/reindex", params), null);
    }

    public String createVideoJob(Path video, Double sampleIntervalSeconds, Integer batchSize) {
        Map<String, Object> fields = new LinkedHashMap<>();
        if (sampleIntervalSeconds != null) {
            fields.put("sample_interval_seconds", sampleIntervalSeconds);
        }
        if (batchSize != null) {
            fields.put("batch_size", batchSize);
        }
        List<FileField> files = new ArrayList<>();
        files.add(new FileField("file", video));
        return multipart("/v1/jobs/video", fields, files);
    }

    public String getJob(String jobId) {
        return getJson("/v1/jobs/" + pathSegment(jobId), null);
    }

    public String jobResult(String jobId) {
        return getJson("/v1/jobs/" + pathSegment(jobId) + "/result", null);
    }

    public String cancelJob(String jobId) {
        return sendJson("POST", "/v1/jobs/" + pathSegment(jobId) + "/cancel", null);
    }

    public String createStream(String streamUrl, String name, Map<String, Object> settings, Map<String, Object> metadata) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("stream_url", streamUrl);
        if (name != null && !name.isEmpty()) {
            payload.put("name", name);
        }
        payload.put("settings", settings == null ? new LinkedHashMap<String, Object>() : settings);
        payload.put("metadata", metadata == null ? new LinkedHashMap<String, Object>() : metadata);
        return sendJson("POST", "/v1/streams", payload);
    }

    public String listStreams(Integer limit, Integer offset, String cursor) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("limit", limit);
        params.put("offset", offset);
        params.put("cursor", cursor);
        return getJson("/v1/streams", params);
    }

    public String getStream(String streamId) {
        return getJson("/v1/streams/" + pathSegment(streamId), null);
    }

    public String startStream(String streamId) {
        return sendJson("POST", "/v1/streams/" + pathSegment(streamId) + "/start", null);
    }

    public String stopStream(String streamId) {
        return sendJson("POST", "/v1/streams/" + pathSegment(streamId) + "/stop", null);
    }

    public String streamStatus(String streamId) {
        return getJson("/v1/streams/" + pathSegment(streamId) + "/status", null);
    }

    public String streamEvents(String streamId, Integer limit, Integer offset, String cursor) {
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("limit", limit);
        params.put("offset", offset);
        params.put("cursor", cursor);
        return getJson("/v1/streams/" + pathSegment(streamId) + "/events", params);
    }

    public String models() {
        return getJson("/v1/models", null);
    }

    public String getModel(String modelId) {
        return getJson("/v1/models/" + pathSegment(modelId), null);
    }

    public String loadModel(String modelId) {
        return sendJson("POST", "/v1/models/" + pathSegment(modelId) + "/load", null);
    }

    public String unloadModel(String modelId) {
        return sendJson("POST", "/v1/models/" + pathSegment(modelId) + "/unload", null);
    }

    public String thresholds() {
        return getJson("/v1/thresholds", null);
    }

    public String updateThresholds(String profile, Map<String, Object> thresholds) {
        return sendJson("PUT", "/v1/thresholds/" + pathSegment(profile), thresholds);
    }

    public String adminStatus() {
        return getJson("/v1/admin/status", null);
    }
}
