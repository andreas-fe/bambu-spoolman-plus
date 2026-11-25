import json
import time

import pytest
from flask import Flask

from bridge.assistant_api import authorized_request, register_assistant
from bridge.preflight import review_plate


def sample(grams=100, remaining=250, color="#FFFFFF", material="PLA"):
    metadata = dict(model="Bambu Lab A1", plates=[
        dict(index=1, nozzle="0.4", outside=False,
             filaments=[dict(id=1, material=material, color=color, grams=grams)])
    ])
    context = dict(model="Bambu Lab A1", nozzle="0.4", state="IDLE", connected=True,
                   inventory_available=True,
                   slots=[dict(slot="0", available=True, spool_id=10, material="PLA",
                               color="#FFFFFF", spool_material="PLA", spool_color="#FFFFFF")],
                   spools=[dict(id=10, remaining_weight=remaining, material="PLA",
                                clean_color="#FFFFFF", archived=False)])
    return metadata, context


def test_preflight_good_snapshot_is_not_a_start_permission():
    m, c = sample()
    result = review_plate(m, 1, c, {"1": "0"})
    assert result["status"] == "plausible"
    assert result["start_authorized"] is False and result["simulation"] is True
    assert result["filaments"][0]["remaining_g"] == 250


def test_preflight_never_guesses_unknown_or_short_stock():
    m, c = sample(grams=300)
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "blocked"
    c["spools"][0]["remaining_weight"] = None
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "review"
    m["plates"][0]["filaments"][0]["grams"] = float("nan")
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "review"


def test_preflight_requires_exact_material_and_color_even_same_family():
    m, c = sample(material="PLA+")
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "blocked"
    m, c = sample(color="#000000")
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "blocked"


def test_preflight_rejects_invalid_selection_and_model():
    m, c = sample()
    with pytest.raises(ValueError):
        review_plate(m, 0, c)
    with pytest.raises(ValueError):
        review_plate(m, 1, c, {"200": "0"})
    c["model"] = "A1 mini"
    assert review_plate(m, 1, c, {"1": "0"})["status"] == "blocked"


def test_preflight_unavailable_slot_or_offline_is_not_plausible():
    m, c = sample()
    c["slots"][0]["available"] = False
    assert review_plate(m, 1, c)["status"] == "review"
    c["slots"][0]["available"] = True
    c["connected"] = False
    assert review_plate(m, 1, c)["status"] == "review"


def test_preflight_aggregates_duplicate_spool_demand():
    m, c = sample(grams=90, remaining=150)
    m["plates"][0]["filaments"].append(dict(id=2, material="PLA", color="#FFFFFF", grams=90))
    assert review_plate(m, 1, c, {"1": "0", "2": "0"})["status"] == "blocked"


def test_preflight_reports_candidate_inventory_without_changing_mapping():
    m, c = sample(grams=90)
    c["slots"][0]["color"] = "#111111"
    c["spools"].append(dict(id=11, remaining_weight=120, material="PLA", clean_color="#FFFFFF", archived=False))
    result = review_plate(m, 1, c)
    assert result["status"] == "review"
    candidate = next(x for x in result["recommendations"] if x["action"] == "check_spool_swap")
    assert any(x["spool_id"] == 11 for x in candidate["candidates"])
    assert result["filaments"][0]["selected_slot"] is None


def test_assistant_secret_missing_short_or_wrong_does_not_authorize(monkeypatch):
    from flask import Request
    from werkzeug.test import EnvironBuilder
    builder = EnvironBuilder(path="/api/assistant/status", headers={"X-MCP-Bridge-Token": "a" * 32})
    with Flask(__name__).request_context(builder.get_environ()):
        from flask import request
        monkeypatch.delenv("MCP_BRIDGE_TOKEN", raising=False)
        assert not authorized_request(request)
        monkeypatch.setenv("MCP_BRIDGE_TOKEN", "short")
        assert not authorized_request(request)
        monkeypatch.setenv("MCP_BRIDGE_TOKEN", "b" * 32)
        assert not authorized_request(request)
        monkeypatch.setenv("MCP_BRIDGE_TOKEN", "a" * 32)
        assert authorized_request(request)


def assistant_test_app():
    app = Flask(__name__)
    calls = []
    register_assistant(app, status=lambda: {"state": "IDLE"},
                       stock=lambda: [], journal=lambda: [], costs=lambda: [],
                       files=lambda: [], preflight=lambda *args: {"simulation": True},
                       power=lambda value: calls.append(value) or {"on": value},
                       power_status=lambda: {"on": False, "power_w": 0.0})
    return app.test_client(), calls


def test_power_request_never_switches_before_web_approval():
    client, calls = assistant_test_app()
    response = client.post("/api/assistant/power/request", json={"action": "off"})
    assert response.status_code == 202
    ident = response.json["request_id"]
    assert not calls
    assert client.get("/api/assistant/power/status").json["on"] is False
    assert client.get("/api/assistant/power/result", query_string={"request_id": ident}).json["state"] == "pending"
    assert client.post("/assistant/power/decision", data={"request_id": ident, "decision": "approve", "confirm": "power-on"}).status_code == 400
    assert not calls
    result = client.post("/assistant/power/decision", data={"request_id": ident, "decision": "approve", "confirm": "power-off"})
    assert result.json["state"] == "done" and calls == [False]
    assert client.post("/assistant/power/decision", data={"request_id": ident, "decision": "approve", "confirm": "power-off"}).status_code == 409
    assert calls == [False]


def test_power_request_can_be_rejected_without_switching():
    client, calls = assistant_test_app()
    ident = client.post("/api/assistant/power/request", json={"action": "on"}).json["request_id"]
    assert client.post("/assistant/power/decision", data={"request_id": ident, "decision": "reject"}).json["state"] == "rejected"
    assert calls == []
    assert client.post("/api/assistant/power/request", json={"action": "toggle"}).status_code == 400
