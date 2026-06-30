import threading
import time
from collections.abc import Mapping
from typing import Any, TypedDict

from app.settings import PROMETHEUS_METRICS_CACHE_SECONDS


METRICS: dict[str, float] = {
    "requests_total": 0,
    "predict_requests_total": 0,
    "predict_errors_total": 0,
    "model_loads_total": 0,
    "model_load_errors_total": 0,
    "model_load_cooldown_rejections_total": 0,
    "cache_hits_total": 0,
    "cache_misses_total": 0,
    "model_unloads_total": 0,
    "inference_seconds_sum": 0,
    "queue_seconds_sum": 0,
    "model_load_seconds_sum": 0,
    "persons_requests_total": 0,
    "persons_errors_total": 0,
    "persons_detected_total": 0,
    "persons_frames_total": 0,
    "embeddings_requests_total": 0,
    "embeddings_errors_total": 0,
    "tracks_requests_total": 0,
    "tracks_errors_total": 0,
    "vision_requests_total": 0,
    "vision_errors_total": 0,
    "vision_images_total": 0,
    "decode_seconds_sum": 0,
    "preprocess_seconds_sum": 0,
    "postprocess_seconds_sum": 0,
    "video_frames_considered_total": 0,
    "video_frames_selected_total": 0,
    "video_near_duplicate_drops_total": 0,
}
REQUEST_STATUS_COUNTS: dict[str, int] = {}
# 每个活跃的执行提供程序（例如 CUDA / CPU / TensorRT）所创建的会话数。
PROVIDER_SESSION_COUNTS: dict[str, int] = {}
# 按原因（cuda_provider_unavailable / session_init_failed / ...）分类的 CPU 回退次数。
# 在 GPU 主机上，非零率表示运行时状态下降；在 FORCE_CPU 主机上则保持为空。
CPU_FALLBACK_COUNTS: dict[str, int] = {}
# 按后端（opencv / pyav）分类的按帧号取帧次数。
VIDEO_DECODE_BACKEND_COUNTS: dict[str, int] = {}
METRICS_LOCK = threading.RLock()
PROMETHEUS_CACHE: dict[str, Any] = {"expires_at": 0.0, "text": ""}


class Histogram(TypedDict):
    buckets: tuple[float, ...]
    counts: list[int]
    inf: int
    count: int
    sum: float

HISTOGRAM_BUCKETS: dict[str, tuple[float, ...]] = {
    "inference_seconds": (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    "queue_seconds": (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    "model_load_seconds": (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    "decode_seconds": (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    "preprocess_seconds": (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    "postprocess_seconds": (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    # 抽帧质量分（0~1），用于观察被选中帧的质量分布。
    "video_frame_quality": (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
}
HISTOGRAMS: dict[str, Histogram] = {
    name: {"buckets": buckets, "counts": [0 for _ in buckets], "inf": 0, "count": 0, "sum": 0.0}
    for name, buckets in HISTOGRAM_BUCKETS.items()
}


def observe(metric: str, value: float = 1) -> None:
    with METRICS_LOCK:
        METRICS[metric] = METRICS.get(metric, 0) + value
        PROMETHEUS_CACHE["expires_at"] = 0.0
        if metric.endswith("_seconds_sum"):
            observe_histogram(metric[: -len("_sum")], value)


def observe_request_status(status_code: int) -> None:
    with METRICS_LOCK:
        status = str(status_code)
        REQUEST_STATUS_COUNTS[status] = REQUEST_STATUS_COUNTS.get(status, 0) + 1
        PROMETHEUS_CACHE["expires_at"] = 0.0


def record_session_provider(provider: str) -> None:
    """记录一次 ONNX 会话最终落在哪个执行 provider 上（CUDA/CPU/TensorRT）。"""
    with METRICS_LOCK:
        PROVIDER_SESSION_COUNTS[provider] = PROVIDER_SESSION_COUNTS.get(provider, 0) + 1
        PROMETHEUS_CACHE["expires_at"] = 0.0


def record_cpu_fallback(reason: str) -> None:
    """记录一次“本想用 GPU 却回退到 CPU”的事件及其原因。FORCE_CPU 的主动选择不计入。"""
    with METRICS_LOCK:
        CPU_FALLBACK_COUNTS[reason] = CPU_FALLBACK_COUNTS.get(reason, 0) + 1
        PROMETHEUS_CACHE["expires_at"] = 0.0


def record_video_decode_backend(backend: str) -> None:
    """记录一次按帧号取帧所用的解码后端（opencv / pyav）。"""
    with METRICS_LOCK:
        VIDEO_DECODE_BACKEND_COUNTS[backend] = VIDEO_DECODE_BACKEND_COUNTS.get(backend, 0) + 1
        PROMETHEUS_CACHE["expires_at"] = 0.0


def observe_video_sampling_metrics(metadata: dict[str, Any]) -> None:
    """从视频/流抽帧的 metadata 记录抽帧质量与丢帧相关指标。

    candidate_frames_considered=候选总数；extracted_frames=最终选中数；
    near_duplicate_candidate_count=因近重复被剔除的候选数；frame_qualities=选中帧质量分布。
    """
    considered = int(metadata.get("candidate_frames_considered", 0) or 0)
    selected = int(metadata.get("extracted_frames", 0) or 0)
    near_dupes = int(metadata.get("near_duplicate_candidate_count", 0) or 0)
    observe("video_frames_considered_total", considered)
    observe("video_frames_selected_total", selected)
    observe("video_near_duplicate_drops_total", near_dupes)
    with METRICS_LOCK:
        for quality in metadata.get("frame_qualities", []) or []:
            if isinstance(quality, dict) and "score" in quality:
                observe_histogram("video_frame_quality", float(quality["score"]))
        PROMETHEUS_CACHE["expires_at"] = 0.0


def observe_histogram(metric: str, value: float) -> None:
    histogram = HISTOGRAMS.get(metric)
    if histogram is None:
        return
    numeric_value = max(0.0, value)
    histogram["count"] += 1
    histogram["sum"] += numeric_value
    matched = False
    for index, bucket in enumerate(histogram["buckets"]):
        if numeric_value <= bucket:
            histogram["counts"][index] += 1
            matched = True
    if not matched:
        histogram["inf"] += 1


def gpu_memory_metrics() -> list[dict[str, int]]:
    try:  # pragma: no cover - 需要 NVIDIA 运行环境
        import pynvml  # pyright: ignore[reportMissingImports]  # 可选，来自 nvidia-ml-py (requirements-prod-optional.txt)
    except Exception:
        return []
    try:  # pragma: no cover - 需要 NVIDIA 运行环境
        pynvml.nvmlInit()
        devices = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            devices.append(
                {
                    "device": index,
                    "used": int(memory.used),
                    "free": int(memory.free),
                    "total": int(memory.total),
                }
            )
        return devices
    except Exception:
        return []


def metric_label(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def model_labels(model: str, config: Mapping[str, Any], extra: dict[str, object] | None = None) -> str:
    raw_rollout = config.get("rollout")
    rollout = raw_rollout if isinstance(raw_rollout, dict) else {}
    labels = {
        "model": model,
        "task": config.get("task") or config.get("type") or "",
        "version": config.get("version") or "",
        "status": rollout.get("status") or "",
    }
    if extra:
        labels.update(extra)
    return ",".join(f'{key}="{metric_label(value)}"' for key, value in labels.items())


def append_histogram(lines: list[str], metric: str, help_text: str) -> None:
    histogram = HISTOGRAMS[metric]
    prometheus_name = f"gpu_worker_{metric}"
    lines.extend(
        [
            f"# HELP {prometheus_name} {help_text}",
            f"# TYPE {prometheus_name} histogram",
        ]
    )
    for bucket, count in zip(histogram["buckets"], histogram["counts"]):
        lines.append(f'{prometheus_name}_bucket{{le="{bucket:g}"}} {count}')
    lines.append(f'{prometheus_name}_bucket{{le="+Inf"}} {histogram["count"]}')
    lines.append(f'{prometheus_name}_count {histogram["count"]}')
    lines.append(f'{prometheus_name}_sum {histogram["sum"]}')


def prometheus_metrics() -> str:
    now = time.monotonic()
    with METRICS_LOCK:
        if PROMETHEUS_METRICS_CACHE_SECONDS > 0 and PROMETHEUS_CACHE.get("text") and now < float(PROMETHEUS_CACHE["expires_at"]):
            return str(PROMETHEUS_CACHE["text"])
        text = build_prometheus_metrics()
        PROMETHEUS_CACHE["text"] = text
        PROMETHEUS_CACHE["expires_at"] = now + max(0.0, PROMETHEUS_METRICS_CACHE_SECONDS)
        return text


def build_prometheus_metrics() -> str:
    from app.model_config import MODEL_CONFIGS
    from app.portrait_stream_worker import STREAM_WORKER_SESSIONS
    from app import runtime_state

    loaded_models = len(runtime_state.MODEL_REGISTRY)
    active_stream_sessions = sum(1 for item in STREAM_WORKER_SESSIONS.values() if item.get("status") == "running")
    stream_backpressure_drops = sum(int(item.get("backpressure_drops", 0)) for item in STREAM_WORKER_SESSIONS.values())
    stream_reconnects_total = sum(int(item.get("restart_count", 0)) for item in STREAM_WORKER_SESSIONS.values())
    stream_frames_processed = sum(int(item.get("frames_processed", 0)) for item in STREAM_WORKER_SESSIONS.values())
    stream_frames_sampled = sum(int(item.get("frames_sampled", 0)) for item in STREAM_WORKER_SESSIONS.values())
    queue_depth = max(0, int(runtime_state.GPU_QUEUE_WAITERS)) + sum(
        max(0, int(depth)) for depth in runtime_state.GPU_DEVICE_QUEUE_WAITERS.values()
    )
    device_queue_depths = {
        device_id: max(0, int(depth))
        for device_id, depth in runtime_state.GPU_DEVICE_QUEUE_WAITERS.items()
    }
    lines = [
        "# HELP gpu_worker_requests_total 应用中间件接收到的 HTTP 请求总数。",
        "# TYPE gpu_worker_requests_total counter",
        f"gpu_worker_requests_total {METRICS.get('requests_total', 0)}",
        "# HELP gpu_worker_predict_requests_total 预测请求总数。",
        "# TYPE gpu_worker_predict_requests_total counter",
        f"gpu_worker_predict_requests_total {METRICS.get('predict_requests_total', 0)}",
        "# HELP gpu_worker_predict_errors_total 预测错误总数。",
        "# TYPE gpu_worker_predict_errors_total counter",
        f"gpu_worker_predict_errors_total {METRICS.get('predict_errors_total', 0)}",
        "# HELP gpu_worker_model_loads_total 成功的模型加载总数。",
        "# TYPE gpu_worker_model_loads_total counter",
        f"gpu_worker_model_loads_total {METRICS.get('model_loads_total', 0)}",
        "# HELP gpu_worker_model_load_errors_total 失败的模型加载总数。",
        "# TYPE gpu_worker_model_load_errors_total counter",
        f"gpu_worker_model_load_errors_total {METRICS.get('model_load_errors_total', 0)}",
        "# HELP gpu_worker_model_load_cooldown_rejections_total Requests fast-failed while a model load is cooling down after failure.",
        "# TYPE gpu_worker_model_load_cooldown_rejections_total counter",
        f"gpu_worker_model_load_cooldown_rejections_total {METRICS.get('model_load_cooldown_rejections_total', 0)}",
        "# HELP gpu_worker_cache_hits_total 模型缓存命中总数。",
        "# TYPE gpu_worker_cache_hits_total counter",
        f"gpu_worker_cache_hits_total {METRICS.get('cache_hits_total', 0)}",
        "# HELP gpu_worker_cache_misses_total 模型缓存未命中总数。",
        "# TYPE gpu_worker_cache_misses_total counter",
        f"gpu_worker_cache_misses_total {METRICS.get('cache_misses_total', 0)}",
        "# HELP gpu_worker_model_unloads_total 模型卸载或逐出的总数。",
        "# TYPE gpu_worker_model_unloads_total counter",
        f"gpu_worker_model_unloads_total {METRICS.get('model_unloads_total', 0)}",
        "# HELP gpu_worker_loaded_models 当前加载的模型数。",
        "# TYPE gpu_worker_loaded_models gauge",
        f"gpu_worker_loaded_models {loaded_models}",
        "# HELP gpu_worker_gpu_queue_depth 当前等待全局 GPU 访问的协程数。",
        "# TYPE gpu_worker_gpu_queue_depth gauge",
        f"gpu_worker_gpu_queue_depth {queue_depth}",
        "# HELP gpu_worker_gpu_device_queue_depth 当前等待 GPU 设备队列的协程数。",
        "# TYPE gpu_worker_gpu_device_queue_depth gauge",
        "# HELP gpu_worker_stream_active_sessions 当前运行的流处理工作器会话数。",
        "# TYPE gpu_worker_stream_active_sessions gauge",
        f"gpu_worker_stream_active_sessions {active_stream_sessions}",
        "# HELP gpu_worker_stream_backpressure_drops_total 因流背压丢弃的总帧数。",
        "# TYPE gpu_worker_stream_backpressure_drops_total counter",
        f"gpu_worker_stream_backpressure_drops_total {stream_backpressure_drops}",
        "# HELP gpu_worker_stream_reconnects_total 所有会话中流处理工作器的重连总次数。",
        "# TYPE gpu_worker_stream_reconnects_total counter",
        f"gpu_worker_stream_reconnects_total {stream_reconnects_total}",
        "# HELP gpu_worker_stream_frames_sampled_total Total frames sampled by stream workers across sessions.",
        "# TYPE gpu_worker_stream_frames_sampled_total counter",
        f"gpu_worker_stream_frames_sampled_total {stream_frames_sampled}",
        "# HELP gpu_worker_stream_frames_processed_total Total frames processed by stream workers across sessions.",
        "# TYPE gpu_worker_stream_frames_processed_total counter",
        f"gpu_worker_stream_frames_processed_total {stream_frames_processed}",
        "# HELP gpu_worker_inference_seconds_sum 推理执行时间累计秒数。",
        "# TYPE gpu_worker_inference_seconds_sum counter",
        f"gpu_worker_inference_seconds_sum {METRICS.get('inference_seconds_sum', 0)}",
        "# HELP gpu_worker_queue_seconds_sum 队列等待时间累计秒数。",
        "# TYPE gpu_worker_queue_seconds_sum counter",
        f"gpu_worker_queue_seconds_sum {METRICS.get('queue_seconds_sum', 0)}",
        "# HELP gpu_worker_model_load_seconds_sum 模型加载时间累计秒数。",
        "# TYPE gpu_worker_model_load_seconds_sum counter",
        f"gpu_worker_model_load_seconds_sum {METRICS.get('model_load_seconds_sum', 0)}",
        "# HELP gpu_worker_persons_requests_total /infer/persons 请求总数。",
        "# TYPE gpu_worker_persons_requests_total counter",
        f"gpu_worker_persons_requests_total {METRICS.get('persons_requests_total', 0)}",
        "# HELP gpu_worker_persons_errors_total /infer/persons 错误总数。",
        "# TYPE gpu_worker_persons_errors_total counter",
        f"gpu_worker_persons_errors_total {METRICS.get('persons_errors_total', 0)}",
        "# HELP gpu_worker_persons_detected_total 检测到的总人数。",
        "# TYPE gpu_worker_persons_detected_total counter",
        f"gpu_worker_persons_detected_total {METRICS.get('persons_detected_total', 0)}",
        "# HELP gpu_worker_persons_frames_total 行人检测处理的总帧数。",
        "# TYPE gpu_worker_persons_frames_total counter",
        f"gpu_worker_persons_frames_total {METRICS.get('persons_frames_total', 0)}",
        "# HELP gpu_worker_embeddings_requests_total /infer/person-embeddings 请求总数。",
        "# TYPE gpu_worker_embeddings_requests_total counter",
        f"gpu_worker_embeddings_requests_total {METRICS.get('embeddings_requests_total', 0)}",
        "# HELP gpu_worker_embeddings_errors_total /infer/person-embeddings 错误总数。",
        "# TYPE gpu_worker_embeddings_errors_total counter",
        f"gpu_worker_embeddings_errors_total {METRICS.get('embeddings_errors_total', 0)}",
        "# HELP gpu_worker_tracks_requests_total /infer/person-tracks 请求总数。",
        "# TYPE gpu_worker_tracks_requests_total counter",
        f"gpu_worker_tracks_requests_total {METRICS.get('tracks_requests_total', 0)}",
        "# HELP gpu_worker_tracks_errors_total /infer/person-tracks 错误总数。",
        "# TYPE gpu_worker_tracks_errors_total counter",
        f"gpu_worker_tracks_errors_total {METRICS.get('tracks_errors_total', 0)}",
        "# HELP gpu_worker_vision_requests_total 通用 /vision 推理请求总数。",
        "# TYPE gpu_worker_vision_requests_total counter",
        f"gpu_worker_vision_requests_total {METRICS.get('vision_requests_total', 0)}",
        "# HELP gpu_worker_vision_errors_total 通用 /vision 推理错误总数。",
        "# TYPE gpu_worker_vision_errors_total counter",
        f"gpu_worker_vision_errors_total {METRICS.get('vision_errors_total', 0)}",
        "# HELP gpu_worker_vision_images_total 通用 /vision 推理处理的总图像数。",
        "# TYPE gpu_worker_vision_images_total counter",
        f"gpu_worker_vision_images_total {METRICS.get('vision_images_total', 0)}",
        "# HELP gpu_worker_decode_seconds_sum 图像解码时间累计秒数。",
        "# TYPE gpu_worker_decode_seconds_sum counter",
        f"gpu_worker_decode_seconds_sum {METRICS.get('decode_seconds_sum', 0)}",
        "# HELP gpu_worker_preprocess_seconds_sum 预处理时间累计秒数。",
        "# TYPE gpu_worker_preprocess_seconds_sum counter",
        f"gpu_worker_preprocess_seconds_sum {METRICS.get('preprocess_seconds_sum', 0)}",
        "# HELP gpu_worker_postprocess_seconds_sum 后处理时间累计秒数。",
        "# TYPE gpu_worker_postprocess_seconds_sum counter",
        f"gpu_worker_postprocess_seconds_sum {METRICS.get('postprocess_seconds_sum', 0)}",
        "# HELP gpu_worker_video_frames_considered_total Total candidate frames considered during video/stream sampling.",
        "# TYPE gpu_worker_video_frames_considered_total counter",
        f"gpu_worker_video_frames_considered_total {METRICS.get('video_frames_considered_total', 0)}",
        "# HELP gpu_worker_video_frames_selected_total Total frames selected after quality/scene/dedup sampling.",
        "# TYPE gpu_worker_video_frames_selected_total counter",
        f"gpu_worker_video_frames_selected_total {METRICS.get('video_frames_selected_total', 0)}",
        "# HELP gpu_worker_video_near_duplicate_drops_total Total candidate frames dropped as near-duplicates.",
        "# TYPE gpu_worker_video_near_duplicate_drops_total counter",
        f"gpu_worker_video_near_duplicate_drops_total {METRICS.get('video_near_duplicate_drops_total', 0)}",
        "# HELP gpu_worker_model_config_info 已配置的模型元数据。值始终为 1。",
        "# TYPE gpu_worker_model_config_info gauge",
    ]
    for model, config in sorted(MODEL_CONFIGS.items()):
        lines.append(f"gpu_worker_model_config_info{{{model_labels(model, config)}}} 1")

    for status_code, count in sorted(REQUEST_STATUS_COUNTS.items()):
        status_class = f"{status_code[0]}xx" if status_code else "unknown"
        lines.append(f'gpu_worker_requests_total{{status="{metric_label(status_code)}",status_class="{status_class}"}} {count}')

    for device_id, depth in sorted(device_queue_depths.items()):
        lines.append(f'gpu_worker_gpu_device_queue_depth{{device="{device_id}"}} {depth}')

    lines.extend(
        [
            "# HELP gpu_worker_model_session_provider_total 每个活动执行提供程序创建的 ONNX 会话总数。",
            "# TYPE gpu_worker_model_session_provider_total counter",
        ]
    )
    for provider, count in sorted(PROVIDER_SESSION_COUNTS.items()):
        lines.append(f'gpu_worker_model_session_provider_total{{provider="{metric_label(provider)}"}} {count}')

    lines.extend(
        [
            "# HELP gpu_worker_cpu_fallback_total 预期 GPU 运行时回退到 CPU 的总次数，按原因分类。",
            "# TYPE gpu_worker_cpu_fallback_total counter",
        ]
    )
    for reason, count in sorted(CPU_FALLBACK_COUNTS.items()):
        lines.append(f'gpu_worker_cpu_fallback_total{{reason="{metric_label(reason)}"}} {count}')

    lines.extend(
        [
            "# HELP gpu_worker_video_decode_backend_total Frame-index decode operations per backend (opencv/pyav).",
            "# TYPE gpu_worker_video_decode_backend_total counter",
        ]
    )
    for backend, count in sorted(VIDEO_DECODE_BACKEND_COUNTS.items()):
        lines.append(f'gpu_worker_video_decode_backend_total{{backend="{metric_label(backend)}"}} {count}')

    lines.extend(
        [
            "# HELP gpu_worker_model_loaded_info 已加载的模型元数据。值始终为 1。",
            "# TYPE gpu_worker_model_loaded_info gauge",
            "# HELP gpu_worker_model_file_bytes 模型构件文件大小（字节）。",
            "# TYPE gpu_worker_model_file_bytes gauge",
            "# HELP gpu_worker_model_load_count_total 该工作器中模型加载总次数。",
            "# TYPE gpu_worker_model_load_count_total counter",
            "# HELP gpu_worker_model_inference_count_total 该工作器中模型推理总次数。",
            "# TYPE gpu_worker_model_inference_count_total counter",
            "# HELP gpu_worker_model_last_used_at_seconds 最后一次模型使用时间的 Unix 时间戳。",
            "# TYPE gpu_worker_model_last_used_at_seconds gauge",
        ]
    )
    for model, bundle in sorted(runtime_state.MODEL_REGISTRY.items()):
        config = MODEL_CONFIGS.get(model, {})
        labels = model_labels(
            model,
            config,
            {
                "gpu_device_id": bundle.get("gpu_device_id", ""),
                "execution_provider": bundle.get("execution_provider", ""),
            },
        )
        lines.append(f"gpu_worker_model_loaded_info{{{labels}}} 1")
        lines.append(f"gpu_worker_model_file_bytes{{{labels}}} {bundle.get('file_size', 0)}")
        lines.append(f"gpu_worker_model_load_count_total{{{labels}}} {bundle.get('load_count', 0)}")
        lines.append(f"gpu_worker_model_inference_count_total{{{labels}}} {bundle.get('inference_count', 0)}")
        lines.append(f"gpu_worker_model_last_used_at_seconds{{{labels}}} {bundle.get('last_used_at', 0)}")
    append_histogram(lines, "inference_seconds", "推理执行延迟（秒）。")
    append_histogram(lines, "queue_seconds", "推理队列等待延迟（秒）。")
    append_histogram(lines, "model_load_seconds", "模型加载延迟（秒）。")
    append_histogram(lines, "decode_seconds", "图像或视频解码延迟（秒）。")
    append_histogram(lines, "video_frame_quality", "Quality score distribution of selected video frames.")
    append_histogram(lines, "preprocess_seconds", "预处理延迟（秒）。")
    append_histogram(lines, "postprocess_seconds", "后处理延迟（秒）。")
    lines.extend(
        [
            "# HELP gpu_worker_gpu_memory_used_bytes 按设备分类的当前已用 GPU 内存字节数。",
            "# TYPE gpu_worker_gpu_memory_used_bytes gauge",
            "# HELP gpu_worker_gpu_memory_free_bytes 按设备分类的当前空闲 GPU 内存字节数。",
            "# TYPE gpu_worker_gpu_memory_free_bytes gauge",
            "# HELP gpu_worker_gpu_memory_total_bytes 按设备分类的 GPU 总内存字节数。",
            "# TYPE gpu_worker_gpu_memory_total_bytes gauge",
        ]
    )
    for item in gpu_memory_metrics():
        device_label = f'device="{item["device"]}"'
        lines.append(f"gpu_worker_gpu_memory_used_bytes{{{device_label}}} {item['used']}")
        lines.append(f"gpu_worker_gpu_memory_free_bytes{{{device_label}}} {item['free']}")
        lines.append(f"gpu_worker_gpu_memory_total_bytes{{{device_label}}} {item['total']}")
    return "\n".join(lines) + "\n"
