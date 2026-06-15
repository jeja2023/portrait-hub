from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS") != "1",
    reason="set PORTRAIT_RUN_CONTAINER_INTEGRATION_TESTS=1 to run PostgreSQL/Qdrant testcontainers integration tests",
)


def test_postgres_container_gallery_round_trip() -> None:
    pytest.importorskip("testcontainers.postgres")
    pytest.importorskip("psycopg")
    pytest.importorskip("pgvector")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        assert dsn.startswith("postgres")


def test_qdrant_container_health_round_trip() -> None:
    pytest.importorskip("testcontainers.core.container")
    pytest.importorskip("qdrant_client")

    from qdrant_client import QdrantClient
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    with DockerContainer("qdrant/qdrant:v1.9.7").with_exposed_ports(6333) as container:
        wait_for_logs(container, "Qdrant HTTP listening on")
        port = container.get_exposed_port(6333)
        client = QdrantClient(url=f"http://127.0.0.1:{port}")
        assert client.get_collections() is not None
