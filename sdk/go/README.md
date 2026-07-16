# PortraitHub Go SDK

PortraitHub API 的 Go 客户端，零第三方依赖（仅标准库），Go 1.22+。

能力与 Python/Node SDK 对齐：

- Bearer / X-API-Key 双认证与 `X-Tenant-ID` 租户头
- 结构化 HTTP 错误（`*HTTPError` 携带状态码与解码后的响应体）
- multipart 文件上传（比对、注册、检索、视频任务）
- 路径段编码（`url.PathEscape`）防注入
- 请求超时（默认 30s，`WithTimeout` 可调）与 `User-Agent: portrait-hub-sdk-go/x.y.z`

## 快速开始

```go
package main

import (
    "fmt"
    "log"

    "github.com/portrait-hub/portrait-hub-sdk-go/portraithub"
)

func main() {
    client, err := portraithub.NewClient(
        "http://127.0.0.1:8000",
        portraithub.WithAPIToken("phk_your_application_key"),
        portraithub.WithAuthScheme("api_key"),
        portraithub.WithTenantID("tenant-a"),
    )
    if err != nil {
        log.Fatal(err)
    }

    health, err := client.Health()
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(health)

    result, err := client.Search("query.jpg", "face", 5, "strict")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(result["data"])
}
```

## 错误处理

```go
result, err := client.GetJob(jobID)
var httpErr *portraithub.HTTPError
if errors.As(err, &httpErr) {
    fmt.Println(httpErr.StatusCode, httpErr.Payload)
}
```

## 测试

```bash
cd sdk/go && go test ./...
```
