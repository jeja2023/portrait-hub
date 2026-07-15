from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS") != "1",
    reason="设置 PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS=1 后运行外部服务 testcontainers 集成测试",
)


def _docker_container() -> Any:
    module = pytest.importorskip("testcontainers.core.container")
    return module.DockerContainer


def _wait_until(probe: Callable[[], Any], *, timeout: float = 60.0, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return probe()
        except Exception as exc:
            last_error = exc
            time.sleep(interval)
    if last_error is not None:
        raise AssertionError(f"外部服务未按时就绪: {last_error}") from last_error
    raise AssertionError("外部服务未按时就绪")


def _container_url(container: Any, port: int) -> str:
    host = container.get_container_host_ip()
    exposed = container.get_exposed_port(port)
    return f"http://{host}:{exposed}"


def _read_url(url: str, *, timeout: float = 5.0) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def _exec_ok(container: Any, command: list[str], *, timeout: float = 60.0) -> str:
    def probe() -> str:
        result = container.exec(command)
        output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
        if result.exit_code != 0:
            raise AssertionError(output.strip())
        return output

    return _wait_until(probe, timeout=timeout)


def _redis_command(host: str, port: int, *parts: str) -> bytes:
    encoded = [part.encode("utf-8") for part in parts]
    payload = b"*" + str(len(encoded)).encode("ascii") + b"\r\n"
    for part in encoded:
        payload += b"$" + str(len(part)).encode("ascii") + b"\r\n" + part + b"\r\n"
    with socket.create_connection((host, port), timeout=5) as client:
        client.sendall(payload)
        response = client.recv(4096)
    if not response:
        raise AssertionError("Redis 返回空响应")
    return response


def _aws_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    date_key = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _s3_request(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    method: str,
    path: str,
    body: bytes = b"",
    region: str = "us-east-1",
) -> tuple[int, bytes]:
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    amz_date = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_uri = urllib.parse.quote(path, safe="/")
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _aws_signing_key(secret_key, date_stamp, region, "s3"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Authorization": (
            "AWS4-HMAC-SHA256 "
            f"Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Host": host,
        "X-Amz-Content-Sha256": payload_hash,
        "X-Amz-Date": amz_date,
    }
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=body if method not in {"GET", "HEAD"} else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_postgres_container_gallery_round_trip() -> None:
    DockerContainer = _docker_container()

    with (
        DockerContainer("pgvector/pgvector:pg16")
        .with_env("POSTGRES_USER", "portrait")
        .with_env("POSTGRES_PASSWORD", "portrait-secret")
        .with_env("POSTGRES_DB", "portrait")
        .with_exposed_ports(5432) as container
    ):
        sql = """
        CREATE EXTENSION IF NOT EXISTS vector;
        DROP TABLE IF EXISTS portrait_gallery_probe;
        CREATE TABLE portrait_gallery_probe (image_id text PRIMARY KEY, embedding vector(3));
        INSERT INTO portrait_gallery_probe (image_id, embedding)
        VALUES ('portrait-a', '[1,0,0]'), ('portrait-b', '[0,1,0]');
        SELECT image_id FROM portrait_gallery_probe ORDER BY embedding <=> '[1,0,0]' LIMIT 1;
        """
        output = _exec_ok(
            container,
            [
                "psql",
                "-U",
                "portrait",
                "-d",
                "portrait",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ],
            timeout=90,
        )
        assert "portrait-a" in output


def test_qdrant_container_health_round_trip() -> None:
    DockerContainer = _docker_container()

    with DockerContainer("qdrant/qdrant:v1.9.7").with_exposed_ports(6333) as container:
        endpoint = _container_url(container, 6333)

        def collections() -> dict[str, Any]:
            status, body = _read_url(endpoint + "/collections")
            assert status == 200
            return json.loads(body.decode("utf-8"))

        payload = _wait_until(collections, timeout=90)
        assert "result" in payload


def test_redis_container_queue_round_trip() -> None:
    DockerContainer = _docker_container()

    with DockerContainer("redis:7-alpine").with_exposed_ports(6379) as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(6379))
        pong = _wait_until(lambda: _redis_command(host, port, "PING"), timeout=60)
        assert pong.startswith(b"+PONG")
        assert _redis_command(host, port, "LPUSH", "portrait:test", "message").startswith(b":1")
        assert b"message" in _redis_command(host, port, "RPOP", "portrait:test")


def test_minio_container_s3_round_trip() -> None:
    DockerContainer = _docker_container()

    access_key = "portrait"
    secret_key = "portrait-secret"
    with (
        DockerContainer("minio/minio:RELEASE.2025-04-22T22-12-26Z")
        .with_env("MINIO_ROOT_USER", access_key)
        .with_env("MINIO_ROOT_PASSWORD", secret_key)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000) as container
    ):
        endpoint = _container_url(container, 9000)
        _wait_until(lambda: _read_url(endpoint + "/minio/health/ready"), timeout=90)
        bucket_status, _ = _s3_request(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            method="PUT",
            path="/portrait-test",
        )
        assert bucket_status in {200, 409}
        put_status, _ = _s3_request(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            method="PUT",
            path="/portrait-test/probe.txt",
            body=b"ok",
        )
        assert put_status == 200
        get_status, body = _s3_request(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            method="GET",
            path="/portrait-test/probe.txt",
        )
        assert get_status == 200
        assert body == b"ok"
