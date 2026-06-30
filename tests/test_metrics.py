from app import metrics
from app.metrics import prometheus_metrics


def test_prometheus_metrics_include_model_config_labels() -> None:
    text = prometheus_metrics()

    assert "gpu_worker_model_config_info" in text
    assert 'model="portrait_hub/yolov8n.onnx"' in text
    assert 'task="detection"' in text
    assert 'status="active"' in text


def test_prometheus_metrics_include_latency_queue_and_gpu_memory(monkeypatch) -> None:
    metrics.observe("inference_seconds_sum", 0.02)
    metrics.observe("queue_seconds_sum", 0.003)
    metrics.observe_request_status(500)
    monkeypatch.setattr(
        metrics,
        "gpu_memory_metrics",
        lambda: [{"device": 0, "used": 10, "free": 20, "total": 30}],
    )

    text = prometheus_metrics()

    assert "gpu_worker_gpu_queue_depth" in text
    assert 'gpu_worker_requests_total{status="500",status_class="5xx"}' in text
    assert "gpu_worker_inference_seconds_bucket" in text
    assert "gpu_worker_queue_seconds_bucket" in text
    assert "gpu_worker_gpu_memory_used_bytes{device=\"0\"} 10" in text


def test_prometheus_metrics_include_provider_and_fallback_counters() -> None:
    metrics.record_session_provider("CPUExecutionProvider")
    metrics.record_session_provider("CUDAExecutionProvider")
    metrics.record_cpu_fallback("cuda_provider_unavailable")

    text = prometheus_metrics()

    assert 'gpu_worker_model_session_provider_total{provider="CPUExecutionProvider"}' in text
    assert 'gpu_worker_model_session_provider_total{provider="CUDAExecutionProvider"}' in text
    assert 'gpu_worker_cpu_fallback_total{reason="cuda_provider_unavailable"}' in text
    # Stream reconnects aggregate is always emitted as a counter (0 when no sessions).
    assert "gpu_worker_stream_reconnects_total" in text


def test_prometheus_metrics_include_media_pipeline_series() -> None:
    output = prometheus_metrics()
    assert "gpu_worker_stream_frames_sampled_total" in output
    assert "gpu_worker_stream_frames_processed_total" in output
    assert "gpu_worker_video_frames_considered_total" in output
    assert "gpu_worker_video_frames_selected_total" in output
    assert "gpu_worker_video_near_duplicate_drops_total" in output
    assert "gpu_worker_video_decode_backend_total" in output
    assert "gpu_worker_video_frame_quality_bucket" in output


def test_prometheus_metrics_use_live_gpu_queue_counters(monkeypatch) -> None:
    from app import runtime_state

    monkeypatch.setattr(metrics, "PROMETHEUS_METRICS_CACHE_SECONDS", 0)
    monkeypatch.setattr(runtime_state, "GPU_QUEUE_WAITERS", 2)
    monkeypatch.setattr(runtime_state, "GPU_DEVICE_QUEUE_WAITERS", {0: 1, 1: 3})

    text = prometheus_metrics()

    assert "gpu_worker_gpu_queue_depth 6" in text
    assert 'gpu_worker_gpu_device_queue_depth{device="0"} 1' in text
    assert 'gpu_worker_gpu_device_queue_depth{device="1"} 3' in text
