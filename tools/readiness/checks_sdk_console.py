"""SDK 与 Console Next 契约门禁：HTTP 错误、路径编码、multipart、调试台和 SLO 面板。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_sdk_and_console(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    python_sdk = src["python_sdk"]
    node_sdk = src["node_sdk"]
    deploy_check = src["deploy_check"]
    console_module_sources = src["console_module_sources"]
    return [
        {
            "name": "sdk:http_error_contract",
            "ok": (
                "class PortraitHubHTTPError" in python_sdk
                and "except HTTPError as exc" in python_sdk
                and "raise PortraitHubHTTPError(exc.code" in python_sdk
                and "class PortraitHubHTTPError extends Error" in node_sdk
                and "async decodeBody(response)" in node_sdk
                and "JSON.parse(text)" in node_sdk
                and "return text;" in node_sdk
                and "if (!response.ok)" in node_sdk
                and "module.exports = { PortraitHubClient, PortraitHubHTTPError, SDK_VERSION }" in node_sdk
            ),
        },
        {
            "name": "sdk:path_segment_encoding",
            "ok": (
                "from urllib.parse import quote" in python_sdk
                and "def _path_segment" in python_sdk
                and 'quote(str(value), safe="")' in python_sdk
                and 'f"/v1/thresholds/{self._path_segment(profile)}"' in python_sdk
                and "pathSegment(value)" in node_sdk
                and "encodeURIComponent(String(value))" in node_sdk
                and "updateThresholds(profile, thresholds)" in node_sdk
            ),
        },
        {
            "name": "sdk:multipart_header_escaping",
            "ok": (
                "def _multipart_header_value" in python_sdk
                and "self._multipart_header_value(key)" in python_sdk
                and "safe_field_name = self._multipart_header_value(field_name)" in python_sdk
                and "safe_filename = self._multipart_header_value(path_obj.name)" in python_sdk
                and 'name="{safe_field_name}"; ' in python_sdk
                and 'filename="{safe_filename}"' in python_sdk
                and 'name="{field_name}"; ' not in python_sdk
                and 'filename="{path_obj.name}"' not in python_sdk
            ),
        },
        {
            "name": "sdk:node_contract_deploy_check",
            "ok": (
                "def check_node_sdk_tests" in deploy_check
                and 'tests" / "test_node_sdk.js' in deploy_check
                and 'shutil.which("node")' in deploy_check
                and "subprocess.run(" in deploy_check
                and "node_sdk_contract_tests" in deploy_check
            ),
        },
        {
            "name": "sdk:batch_async_and_video_examples",
            "ok": (
                "def search_batch" in python_sdk
                and "def compare_batch" in python_sdk
                and '"/v1/gallery/search/batch"' in python_sdk
                and '"/v1/compare/batch"' in python_sdk
                and "searchBatch(images" in node_sdk
                and "compareBatch(" in node_sdk
                and '"/v1/gallery/search/batch"' in node_sdk
                and '"/v1/compare/batch"' in node_sdk
                and "asyncMode" in node_sdk
                and 'id: "batch-python"' in console_module_sources
                and 'id: "batch-node"' in console_module_sources
                and 'id: "video-python"' in console_module_sources
                and 'id: "video-node"' in console_module_sources
                and "client.search_batch(images, async_mode=True)" in console_module_sources
                and "client.create_video_job" in console_module_sources
                and "createVideoJob" in console_module_sources
                and "client.jobResult" in console_module_sources
                and "navigator.clipboard.writeText(example.code)" in console_module_sources
            ),
        },
        {
            "name": "frontend:api_playground_stage_two_coverage",
            "ok": (
                'value: "/v1/gallery/search/batch"' in console_module_sources
                and 'value: "/v1/compare/batch"' in console_module_sources
                and 'value: "/v1/streams"' in console_module_sources
                and 'value: "/v1/streams/{stream_id}/events"' in console_module_sources
                and "const streamId = ref" in console_module_sources
                and "const streamUrl = ref" in console_module_sources
                and "const asyncMode = ref" in console_module_sources
                and "export async function apiRaw" in console_module_sources
                and "const selectedEndpoint" in console_module_sources
                and 'appendFiles(form, "files"' in console_module_sources
                and "endpoint_template" in console_module_sources
                and "http_status" in console_module_sources
                and "error_code" in console_module_sources
                and "controlled_use" in console_module_sources
            ),
        },
        {
            "name": "frontend:slo_panel_operational_contract",
            "ok": (
                "export function summarizeSloCallLogs" in console_module_sources
                and "call_logs_30d" in console_module_sources
                and '"/v1/access/call-logs?limit=500&created_since="' in console_module_sources
                and "queue_p95_seconds" in console_module_sources
                and "queue_p99_seconds" in console_module_sources
                and "gpu_queue_depth" in console_module_sources
                and "gpu_device_queue_depths" in console_module_sources
                and "error_budget_burn_rate" in console_module_sources
                and "error_budget_remaining" in console_module_sources
                and "success_rate_source" in console_module_sources
                and "call_log_window_seconds" in console_module_sources
                and '"gpu_worker_queue_seconds"' in console_module_sources
                and '"gpu_worker_gpu_device_queue_depth"' in console_module_sources
            ),
        },
    ]