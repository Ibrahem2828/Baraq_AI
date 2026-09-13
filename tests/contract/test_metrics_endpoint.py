from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_metrics_endpoint_serves_prometheus_exposition_format() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # A HELP/TYPE pair is emitted for every metric family prometheus_client's
    # generate_latest() serializes, regardless of which app-specific counters
    # (see app.core.telemetry) have been incremented yet in this process --
    # proves this is real exposition output, not an empty/stub response.
    assert "# HELP " in response.text
    assert "# TYPE " in response.text


def test_metrics_endpoint_is_not_mounted_under_the_public_api_prefix() -> None:
    from app.core.config import get_settings

    with TestClient(app, base_url="http://localhost") as client:
        prefixed = client.get(f"{get_settings().public_api_prefix}/metrics")
    assert prefixed.status_code == 404
