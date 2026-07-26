# Compatibility

| SDK version | API contract | Python | Support level |
| --- | --- | --- | --- |
| 0.18.x | `/v1` | 3.10-3.13 | Stable |
| 0.17.x | `/v1` | 3.10-3.13 | Compatible, maintenance only |
| 0.14.x-0.16.x | `/v1` | 3.10-3.13 | Compatible, security fixes only |

The client calls `GET /v1/meta` to verify the supported range. A release may add fields to responses without a major API contract change. Removing or changing an existing field, status transition, or error code requires a reviewed compatibility migration and deprecation window.

Node is maintenance-only. Go and Java are experimental and are not part of the formal SDK support SLA. The reviewed compatibility baseline for the 0.18.x release remains the 0.17.0 OpenAPI snapshot.
