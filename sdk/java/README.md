# PortraitHub Java SDK

PortraitHub API 的 Java 客户端，零第三方运行时依赖（基于 `java.net.http`），Java 11+。

能力与 Python/Node SDK 对齐：

- Bearer / X-API-Key 双认证与 `X-Tenant-ID` 租户头
- 结构化 HTTP 异常（`PortraitHubHttpException` 携带状态码与响应体）
- multipart 文件上传（比对、注册、检索、视频任务），header 值转义防注入
- 路径段编码防注入
- 请求超时（默认 30s，`builder.timeout(...)` 可调）与 `User-Agent: portrait-hub-sdk-java/x.y.z`

响应以原始 JSON 字符串返回，由调用方选择 JSON 库（Jackson/Gson/…）解析，保持零依赖。

## 快速开始

```java
import com.portraithub.sdk.PortraitHubClient;
import java.nio.file.Path;
import java.time.Duration;

PortraitHubClient client = PortraitHubClient.builder("http://127.0.0.1:8000")
        .apiToken("phk_your_application_key")
        .authScheme("api_key")
        .tenantId("tenant-a")
        .timeout(Duration.ofSeconds(30))
        .build();

String health = client.health();
String search = client.search(Path.of("query.jpg"), "face", 5, "strict");
```

## 错误处理

```java
try {
    client.getJob(jobId);
} catch (PortraitHubClient.PortraitHubHttpException error) {
    System.out.println(error.statusCode() + ": " + error.payload());
}
```

## 构建与测试

```bash
cd sdk/java && mvn -q test
```
