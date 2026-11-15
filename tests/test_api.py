from __future__ import annotations

import json


def _run(client, case_id="lake_events"):
    response = client.post("/api/v1/runs", json={"case_id": case_id})
    assert response.status_code == 200
    return response.get_json()


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.data == b'{"status":"ok"}\n'


def test_cases_endpoint(client):
    payload = client.get("/api/v1/cases").get_json()
    assert payload["count"] == 3
    assert [item["case_id"] for item in payload["items"]] == [
        "lake_events", "lake_orders", "lake_pricing"
    ]


def test_create_run_returns_decision_and_run_id(client):
    payload = _run(client)
    assert payload["run_id"].startswith("run-")
    assert payload["decision"] == "BLOCK"
    assert payload["summary"]["change_count"] == 17
    assert "report_html" in payload["links"]


def test_get_run_detail_matches_json_report(client):
    created = _run(client)
    run_id = created["run_id"]
    detail = client.get(f"/api/v1/runs/{run_id}")
    report = client.get(f"/api/v1/runs/{run_id}/report?format=json")
    assert detail.status_code == 200
    assert report.status_code == 200
    assert detail.get_json() == report.get_json()
    assert detail.get_json()["decision"] == "BLOCK"


def test_markdown_and_html_reports(client):
    run_id = _run(client)["run_id"]
    markdown = client.get(f"/api/v1/runs/{run_id}/report?format=markdown")
    html = client.get(f"/api/v1/runs/{run_id}/report?format=html")
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.content_type
    assert "Consumer compatibility matrix" in markdown.get_data(as_text=True)
    assert html.status_code == 200
    assert "text/html" in html.content_type
    body = html.get_data(as_text=True)
    assert "http://" not in body.lower()
    assert "https://" not in body.lower()
    assert "Migration steps" in body


def test_unknown_case_is_404(client):
    response = client.post("/api/v1/runs", json={"case_id": "not_a_case"})
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "SG-API-002"


def test_invalid_body_and_format(client):
    malformed = client.post(
        "/api/v1/runs", data="{", content_type="application/json"
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "SG-API-001"
    run_id = _run(client)["run_id"]
    unsupported = client.get(f"/api/v1/runs/{run_id}/report?format=xml")
    assert unsupported.status_code == 400
    assert unsupported.get_json()["error"]["code"] == "SG-API-004"


def test_unknown_run_is_404(client):
    response = client.get("/api/v1/runs/run-missing")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "SG-API-003"


def test_ledger_endpoint_filters_and_limits(client):
    _run(client)
    _run(client, "lake_orders")
    payload = client.get("/api/v1/ledger?case_id=lake_events&limit=20").get_json()
    assert payload["count"] == 1
    assert payload["items"][0]["case_id"] == "lake_events"
    injection = client.get("/api/v1/ledger?case_id=lake_events%27%20OR%201=1--")
    assert injection.status_code == 404
    bad_limit = client.get("/api/v1/ledger?limit=abc")
    assert bad_limit.status_code == 400
    assert bad_limit.get_json()["error"]["code"] == "SG-API-006"


def test_repeated_run_reuses_identity(client):
    first = _run(client)
    second = _run(client)
    assert first["run_id"] == second["run_id"]
    assert second["reused"] is True
    assert second["attempt_count"] == 2


def test_index_lists_cases_and_runs(client):
    _run(client)
    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "lake_events" in body
    assert "lake_orders" in body
    assert "/api/v1/runs/" in body


def test_unknown_api_route_has_stable_error(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "SG-API-005"


def test_run_detail_json_is_canonical(client):
    run_id = _run(client)["run_id"]
    raw = client.get(f"/api/v1/runs/{run_id}").get_data(as_text=True)
    assert raw.endswith("\n")
    payload = json.loads(raw)
    assert raw.startswith("{\n")
    assert payload["case"]["case_id"] == "lake_events"
