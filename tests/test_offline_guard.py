from __future__ import annotations

import socket

import pytest

from schemagate.__main__ import main
from schemagate.app import create_app
from schemagate.config import DEFAULT_HOST, validate_bind_host


def _network_forbidden(*args, **kwargs):
    raise AssertionError("business path attempted a network connection")


def test_business_path_never_opens_a_socket(monkeypatch, tmp_path, cases_dir):
    monkeypatch.setattr(socket, "socket", _network_forbidden)
    monkeypatch.setattr(socket, "create_connection", _network_forbidden)
    app = create_app(
        fixtures_dir=cases_dir,
        db_path=tmp_path / "offline.db",
        reports_dir=tmp_path / "offline_reports",
        testing=True,
    )
    client = app.test_client()
    assert client.get("/healthz").status_code == 200
    response = client.post("/api/v1/runs", json={"case_id": "lake_events"})
    assert response.status_code == 200
    run_id = response.get_json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}/report?format=html").status_code == 200


def test_wildcard_bind_is_rejected_before_start(capsys):
    assert main(["serve", "--host", "0.0.0.0", "--port", "8765"]) == 2
    captured = capsys.readouterr()
    assert "SG-CONFIG-001" in captured.err
    assert "wildcard" in captured.err


def test_non_loopback_bind_is_rejected():
    with pytest.raises(ValueError):
        validate_bind_host("gateway.invalid")
    with pytest.raises(ValueError):
        validate_bind_host("")


def test_default_and_loopback_hosts():
    assert DEFAULT_HOST == "127.0.0.1"
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("localhost") == "localhost"
    assert validate_bind_host("::1") == "::1"
