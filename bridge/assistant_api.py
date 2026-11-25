"""An opt-in, authenticated MCP bridge API and human-approved power requests.

No raw logs, access secrets or arbitrary commands are exposed. The MCP client
cannot approve its own action: approval takes place in the existing web UI.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
import threading
import time
from flask import jsonify, request

MAX_PENDING = 12
LIFETIME = 120


def authorized_request(req, token=None):
    configured = os.getenv("MCP_BRIDGE_TOKEN", "") if token is None else token
    supplied = req.headers.get("X-MCP-Bridge-Token", "")
    try:
        loopback = ipaddress.ip_address(req.remote_addr or "").is_loopback
    except ValueError:
        loopback = False
    return bool(loopback and len(configured) >= 32 and supplied and
                secrets.compare_digest(supplied.encode(), configured.encode()))


def register_assistant(app, *, status, stock, journal, costs, preflight, files, power):
    lock = threading.Lock()
    pending = {}

    def clean():
        now = time.monotonic()
        for key, value in list(pending.items()):
            if now - value["created"] > LIFETIME:
                pending.pop(key, None)

    def result_from_pending(ident):
        item = pending.get(ident)
        if item is None:
            return {"state": "expired_or_unknown"}
        return {"state": item["state"], "action": item["action"],
                "result": item.get("result"), "error": item.get("error")}

    @app.get("/api/assistant/status")
    def assistant_status():
        return jsonify(status())

    @app.get("/api/assistant/stock")
    def assistant_stock():
        return jsonify(items=stock())

    @app.get("/api/assistant/journal")
    def assistant_journal():
        return jsonify(entries=journal())

    @app.get("/api/assistant/costs")
    def assistant_costs():
        return jsonify(entries=costs())

    @app.get("/api/assistant/files")
    def assistant_files():
        try:
            return jsonify(files=files())
        except Exception:
            return jsonify(error="Druckerspeicher momentan nicht erreichbar."), 503

    @app.post("/api/assistant/preflight")
    def assistant_preflight():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {"file", "plate", "mapping"}:
            return jsonify(error="Ungültige Druckvorbereitung."), 400
        try:
            return jsonify(preflight(data.get("file"), data.get("plate"), data.get("mapping")))
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify(error=str(exc)), 409

    @app.post("/api/assistant/power/request")
    def assistant_power_request():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"action"} or data["action"] not in ("on", "off"):
            return jsonify(error="Nur Ein-/Ausschalten erlaubt."), 400
        with lock:
            clean()
            if len(pending) >= MAX_PENDING:
                return jsonify(error="Zu viele offene Bestätigungen."), 429
            ident = secrets.token_urlsafe(24)
            pending[ident] = {"created": time.monotonic(), "action": data["action"],
                              "state": "pending"}
        return jsonify(request_id=ident, state="pending", expires_in=LIFETIME,
                       message="Keine Schaltung ausgeführt. Bitte in der Steckdosenansicht ausdrücklich freigeben."), 202

    @app.get("/api/assistant/power/result")
    def assistant_power_result():
        with lock:
            clean()
            return jsonify(result_from_pending(request.args.get("request_id", "")))

    @app.get("/assistant/power/pending")
    def assistant_pending():
        with lock:
            clean()
            return jsonify(requests=[
                {"request_id": key, "action": item["action"], "state": item["state"],
                 "result": item.get("result"), "error": item.get("error")}
                for key, item in pending.items() if item["state"] == "pending"
            ])

    @app.post("/assistant/power/decision")
    def assistant_power_decision():
        ident = request.form.get("request_id", "")
        decision = request.form.get("decision", "")
        with lock:
            clean()
            item = pending.get(ident)
            if not item or item["state"] != "pending":
                return jsonify(error="Anfrage ist abgelaufen oder bereits bearbeitet."), 409
            if decision == "reject":
                item["state"] = "rejected"
                return jsonify(state="rejected")
            if decision != "approve" or request.form.get("confirm") != "power-" + item["action"]:
                return jsonify(error="Schaltaktion ausdrücklich bestätigen."), 400
            item["state"] = "executing"
            action = item["action"]
        try:
            value = power(action == "on")
        except Exception:
            # Never claim that a failed/ambiguous action succeeded; no automatic retry.
            with lock:
                item["state"] = "unknown"
                item["error"] = "Schaltung nicht bestätigt. Steckdosen- und Druckerstatus prüfen."
            return jsonify(error=item["error"]), 409
        with lock:
            item["state"] = "done"
            item["result"] = {"on": bool(value["on"]), "observed_at": value.get("observed_at")}
        return jsonify(result=item["result"], state="done")
