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
