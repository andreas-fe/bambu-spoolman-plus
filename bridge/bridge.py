#!/usr/bin/env python3
"""Bambu A1/A1 mini -> Spoolman bridge v2.1.0."""

from __future__ import annotations

import sys

# Support both module execution in Docker and the legacy script command.
if __package__ in (None, ""):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from bridge.inventory_db import apply_migrations, connection as inventory_connection
from bridge.inventory_service import InventoryService, InventoryError
from bridge.inventory_api import register_inventory, spoolman_view
from bridge.devices import register_devices
from bridge.printer_telemetry import PrinterTelemetry
from bridge.ui import render_ui, SETTINGS_PAGE

import ftplib
import io
import json
import logging
import math
import os
import queue
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import requests
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, url_for

# ---------------------------------------------------------------------------
# Metadaten & Konfiguration
# ---------------------------------------------------------------------------

APP_VERSION = "v3.0.0-plus"
APP_BUILD_TIME = "11.11.2026"

PRINTER_IP = os.getenv("BAMBU_HOST", "").strip()
ACCESS_CODE = os.getenv("BAMBU_ACCESS_CODE", "").strip()
SERIAL = os.getenv("BAMBU_SERIAL", "").strip()
SPOOLMAN_URL = os.getenv("SPOOLMAN_URL", "http://spoolman:8000").rstrip("/")
SPOOLMAN_BROWSER_URL = os.getenv('SPOOLMAN_BROWSER_URL', SPOOLMAN_URL).rstrip('/')
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "7913"))
WEB_USERNAME = os.getenv("WEB_USERNAME", "").strip()
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
INVENTORY_BACKEND = os.getenv("INVENTORY_BACKEND", "spoolman").strip().lower()
if INVENTORY_BACKEND not in ("spoolman", "internal"):
    raise RuntimeError("INVENTORY_BACKEND muss spoolman oder internal sein.")
DB_PATH = Path(os.getenv("DB_PATH", "/app/data/bridge.sqlite3"))
MAPPING_FILE = Path(os.getenv("MAPPING_FILE", "/app/data/mapping.json"))
MAX_3MF_BYTES = int(os.getenv("MAX_3MF_BYTES", str(200 * 1024 * 1024)))
MAX_XML_BYTES = int(os.getenv("MAX_XML_BYTES", str(2 * 1024 * 1024)))
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
AUTO_MATCH_FILAMENTS = os.getenv('AUTO_MATCH_FILAMENTS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}

if not PRINTER_IP or not ACCESS_CODE or not SERIAL:
    raise RuntimeError("BAMBU_HOST, BAMBU_ACCESS_CODE und BAMBU_SERIAL müssen gesetzt sein.")
if bool(WEB_USERNAME) != bool(WEB_PASSWORD):
    raise RuntimeError('WEB_USERNAME und WEB_PASSWORD müssen gemeinsam gesetzt sein.')

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_buffer: deque[str] = deque(maxlen=300)
log_lock = threading.Lock()

class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with log_lock:
            log_buffer.append(line)

logger = logging.getLogger("bambu_spoolman_bridge")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("[Bridge] %(asctime)s %(levelname)s: %(message)s", "%H:%M:%S"))
logger.addHandler(_console)
_buffer = BufferLogHandler()
_buffer.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%H:%M:%S"))
logger.addHandler(_buffer)

def log(message: str, level: int = logging.INFO) -> None:
    logger.log(level, message)

# ---------------------------------------------------------------------------
# SQLite Ledger
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, path: Path):
        self.path = path
        self._init_db()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connection() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'PRINT',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    subtask_name TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    ams_mapping_json TEXT,
                    ams_mapping2_json TEXT,
                    weights_json TEXT,
                    slot_mapping_json TEXT,
                    plate_index INTEGER,
                    dry_run INTEGER,
                    filaments_json TEXT,
                    ams_snapshot_json TEXT,
                    mapping_source TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS deductions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    source_key TEXT NOT NULL,
                    filament_id INTEGER,
                    tray_id INTEGER,
                    spool_id INTEGER,
                    grams REAL NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, source_key)
                );

                CREATE TABLE IF NOT EXISTS runtime (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                INSERT OR IGNORE INTO runtime(key, value) VALUES ('active_job_id', NULL);
                """
            )
            columns = {row['name'] for row in con.execute('PRAGMA table_info(jobs)')}
            for name, kind in [('slot_mapping_json', 'TEXT'), ('plate_index', 'INTEGER'), ('dry_run', 'INTEGER'),
                               ('filaments_json', 'TEXT'), ('ams_snapshot_json', 'TEXT'),
                               ('mapping_source', "TEXT NOT NULL DEFAULT ''"),
                               ('progress', 'REAL NOT NULL DEFAULT 0'), ('observed_slots_json', "TEXT NOT NULL DEFAULT '[]'"),
                               ('spools_snapshot_json', 'TEXT'), ('cancelled', 'INTEGER NOT NULL DEFAULT 0'),
                               ('estimated', 'INTEGER NOT NULL DEFAULT 0'), ('estimate_reason', "TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:
                    con.execute(f'ALTER TABLE jobs ADD COLUMN {name} {kind}')
            con.execute("UPDATE jobs SET dry_run=1 WHERE dry_run IS NULL AND "
                        "(state='DRY_RUN' OR EXISTS (SELECT 1 FROM deductions d WHERE d.job_id=jobs.id AND d.status='DRY_RUN'))")
            con.execute(
                "UPDATE deductions SET status='UNKNOWN', detail='Prozess-Neustart während API-Anfrage.' "
                "WHERE status='IN_FLIGHT'"
            )
            # Correct misleading DONE states left by v2.1 without replaying uncertain requests.
            for status in ('RETRY', 'REJECTED', 'UNKNOWN'):
                con.execute("UPDATE jobs SET state=? WHERE state='DONE' AND EXISTS "
                            "(SELECT 1 FROM deductions d WHERE d.job_id=jobs.id AND d.status=?)", (status, status))

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def create_or_update_running_job(self, filename: str, subtask_name: str, ams_mapping: list[int] | None, ams_mapping2: list[dict[str, Any]] | None = None) -> str:
        now = self.now()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            job_id = active["value"] if active else None
            job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
            old_file = str(job["filename"] or "").replace("\\", "/").rsplit("/", 1)[-1] if job else ""
            new_file = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]

            if job and old_file and new_file and old_file != new_file and job["state"] in ("RUNNING", "PREPARING"):
                con.execute("UPDATE jobs SET state='CANCEL_PENDING', cancelled=1, estimated=1, error=?, updated_at=? WHERE id=?",
                            ("Neuer Dateiname vor FINISH erkannt.", now, job_id))
                enqueue_job(str(job_id))
                con.execute("UPDATE runtime SET value=NULL WHERE key='active_job_id'")
                job = None

            if not job or job["state"] not in ("RUNNING", "PREPARING"):
                job_id = str(uuid.uuid4())
                con.execute(
                    "INSERT INTO jobs(id,kind,created_at,updated_at,filename,subtask_name,state,ams_mapping_json,ams_mapping2_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, "PRINT", now, now, filename or "", subtask_name or "", "RUNNING",
                     json.dumps(ams_mapping) if ams_mapping is not None else None,
                     json.dumps(ams_mapping2) if ams_mapping2 is not None else None),
                )
                con.execute('UPDATE jobs SET slot_mapping_json=?, dry_run=?, ams_snapshot_json=?, mapping_source=? WHERE id=?',
                            (json.dumps(load_mapping()), int(DRY_RUN), json.dumps(ams_state),
                             'MQTT' if ams_mapping is not None or ams_mapping2 is not None else '', job_id))
                con.execute("UPDATE runtime SET value=? WHERE key='active_job_id'", (job_id,))
                log(f"Neuer Druckauftrag: {filename or subtask_name or job_id}")
            else:
                updates = ["updated_at=?", "state='RUNNING'"]
                values: list[Any] = [now]
                if filename: updates.append("filename=?"); values.append(filename)
                if subtask_name: updates.append("subtask_name=?"); values.append(subtask_name)
                if job['mapping_source'] != 'USER':
                    if ams_mapping is not None: updates.append("ams_mapping_json=?"); values.append(json.dumps(ams_mapping))
                    if ams_mapping2 is not None: updates.append("ams_mapping2_json=?"); values.append(json.dumps(ams_mapping2))
                    if ams_mapping is not None or ams_mapping2 is not None:
                        updates.append("mapping_source='MQTT'")
                values.append(job_id)
                con.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id=?", values)
            con.commit()
            return str(job_id)

    def finish_active_job(self, filename: str, subtask_name: str, ams_mapping: list[int] | None, ams_mapping2: list[dict[str, Any]] | None = None) -> str | None:
        now = self.now()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            job_id = row["value"] if row else None
            if not job_id:
                con.commit()
                return None
            fields = ["state='FINISH_PENDING'", "updated_at=?"]
            values: list[Any] = [now]
            allow_mapping = con.execute('SELECT mapping_source FROM jobs WHERE id=?', (job_id,)).fetchone()['mapping_source'] != 'USER'
            if filename: fields.append("filename=?"); values.append(filename)
            if subtask_name: fields.append("subtask_name=?"); values.append(subtask_name)
            if allow_mapping:
                if ams_mapping is not None: fields.append("ams_mapping_json=?"); values.append(json.dumps(ams_mapping))
                if ams_mapping2 is not None: fields.append("ams_mapping2_json=?"); values.append(json.dumps(ams_mapping2))
                if ams_mapping is not None or ams_mapping2 is not None: fields.append("mapping_source='MQTT'")
            values.append(job_id)
            con.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)
            con.execute("UPDATE runtime SET value=NULL WHERE key='active_job_id'")
            con.commit()
        log(f"FINISH erkannt. Job {job_id[:8]} wird abgerechnet.")
        return str(job_id)

    def close_active_job(self, final_state: str, detail: str = "") -> str | None:
        now = self.now()
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            job_id = row["value"] if row else None
            if not job_id:
                con.commit()
                return None
            con.execute("UPDATE jobs SET state=?, error=?, cancelled=?, updated_at=? WHERE id=?",
                        (final_state, detail, int(final_state == 'CANCEL_PENDING'), now, job_id))
            con.execute("UPDATE runtime SET value=NULL WHERE key='active_job_id'")
            con.commit()
        return str(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def active_job_id(self) -> str | None:
        with self.connection() as con:
            row = con.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            return row['value'] if row else None

    def session_secret(self) -> str:
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT OR IGNORE INTO runtime(key,value) VALUES (?,?)',
                        ('flask_secret_key', secrets.token_hex(32)))
            return con.execute('SELECT value FROM runtime WHERE key=?', ('flask_secret_key',)).fetchone()['value']

    def replace_unattempted_plan(self, job_id: str, rows: list[dict]) -> bool:
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM deductions WHERE job_id=? AND (attempts>0 OR status NOT IN ('PENDING','UNMAPPED'))", (job_id,)).fetchone():
                return False
            con.execute('DELETE FROM deductions WHERE job_id=?', (job_id,))
            for row in rows:
                con.execute('INSERT INTO deductions(job_id,source_key,filament_id,tray_id,spool_id,grams,status,detail,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (job_id, row['source_key'], row['filament_id'], row['tray_id'], row['spool_id'], row['grams'], row['status'], row.get('detail',''), self.now(), self.now()))
            return True

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields: return
        fields["updated_at"] = self.now()
        assignments = ", ".join(f"{k}=?" for k in fields)
        with self.connection() as con:
            con.execute(f"UPDATE jobs SET {assignments} WHERE id=?", list(fields.values()) + [job_id])

    def upsert_deduction(self, job_id: str, source_key: str, filament_id: int | None, tray_id: int | None, spool_id: int | None, grams: float, status: str, detail: str = "") -> None:
        now = self.now()
        with self.connection() as con:
            existing = con.execute("SELECT * FROM deductions WHERE job_id=? AND source_key=?", (job_id, source_key)).fetchone()
            if not existing:
                con.execute(
                    "INSERT INTO deductions(job_id,source_key,filament_id,tray_id,spool_id,grams,status,detail,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (job_id, source_key, filament_id, tray_id, spool_id, grams, status, detail, now, now),
                )
            elif existing["status"] == "UNMAPPED":
                next_status = status if status == "UNMAPPED" else "PENDING"
                con.execute(
                    "UPDATE deductions SET filament_id=?,tray_id=?,spool_id=?,grams=?,status=?,detail=?,updated_at=? WHERE id=?",
                    (filament_id, tray_id, spool_id, grams, next_status, detail, now, existing["id"]),
                )

    def get_pending_deductions(self, job_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM deductions WHERE job_id=? AND status IN ('PENDING','RETRY') ORDER BY id", (job_id,)).fetchall()]

    def set_deduction_status(self, deduction_id: int, status: str, detail: str = "") -> None:
        with self.connection() as con:
            con.execute(
                "UPDATE deductions SET status=?,detail=?,updated_at=?,attempts=attempts+? WHERE id=?",
                (status, detail[:1000], self.now(), 1 if status == "IN_FLIGHT" else 0, deduction_id),
            )

    def claim_deduction(self, deduction_id: int) -> bool:
        with self.connection() as con:
            result = con.execute(
                "UPDATE deductions SET status='IN_FLIGHT', attempts=attempts+1, updated_at=? "
                "WHERE id=? AND status IN ('PENDING','RETRY')", (self.now(), deduction_id))
            return result.rowcount == 1

    def deductions_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [dict(row) for row in con.execute('SELECT * FROM deductions WHERE job_id=?', (job_id,))]

    def set_job_mapping(self, job_id: str, mapping: list[int]) -> bool:
        """A user-confirmed mapping applies to one job, never to subsequent prints."""
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['kind'] != 'PRINT' or job['state'] not in ('RUNNING', 'FINISH_PENDING', 'WAITING_METADATA', 'WAITING_MAPPING'):
                return False
            if con.execute("SELECT 1 FROM deductions WHERE job_id=? AND (status NOT IN ('UNMAPPED','PENDING') OR attempts>0)", (job_id,)).fetchone():
                return False
            con.execute('DELETE FROM deductions WHERE job_id=?', (job_id,))
            con.execute("UPDATE jobs SET ams_mapping_json=?, ams_mapping2_json=NULL, mapping_source='USER', updated_at=? WHERE id=?",
                        (json.dumps(mapping), self.now(), job_id))
            return True

    def create_manual_job(self, spool_id: int, slot: int | None, grams: float) -> str:
        job_id = str(uuid.uuid4())
        now = self.now()
        with self.connection() as con:
            con.execute(
                "INSERT INTO jobs(id,kind,created_at,updated_at,filename,subtask_name,state) VALUES(?,?,?,?,?,?,?)",
                (job_id, "MANUAL", now, now, "", "Manuelle Abbuchung", "MANUAL_PENDING"),
            )
            con.execute('UPDATE jobs SET dry_run=? WHERE id=?', (int(DRY_RUN), job_id))
            con.execute(
                "INSERT INTO deductions(job_id,source_key,filament_id,tray_id,spool_id,grams,status,detail,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, "manual", None, slot, spool_id, grams, "PENDING", "Manuelle Buchung", now, now),
            )
        return job_id

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    def list_deductions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT d.*, j.filename, j.subtask_name, j.created_at AS job_created_at, j.mapping_source, j.estimated "
                "FROM deductions d JOIN jobs j ON j.id=d.job_id ORDER BY d.id DESC LIMIT ?", (limit,)
            ).fetchall()]
        for row in rows:
            if row['status'] == 'DONE':
                row['detail'] = re.sub(r'^HTTP 2\d\d(?=;|$)', 'Buchung durch Spoolman bestätigt', row['detail'])
        return rows

    def recoverable_jobs(self) -> list[str]:
        with self.connection() as con:
            return [str(r["id"]) for r in con.execute(
                "SELECT id FROM jobs WHERE state IN "
                "('FINISH_PENDING','CANCEL_PENDING','WAITING_METADATA','WAITING_MAPPING','MANUAL_PENDING','BOOKING','RETRY') "
                "OR (state='RUNNING' AND (weights_json IS NULL OR spools_snapshot_json IS NULL)) "
                "OR EXISTS (SELECT 1 FROM deductions d WHERE d.job_id=jobs.id AND d.status IN ('PENDING','RETRY','UNMAPPED')) "
                "AND jobs.state NOT IN ('DRY_RUN','FAILED','INTERRUPTED') ORDER BY created_at").fetchall()]

    def dry_run_jobs(self) -> list[str]:
        with self.connection() as con:
            return [str(r["id"]) for r in con.execute("SELECT id FROM jobs WHERE state='DRY_RUN' ORDER BY created_at").fetchall()]

store = Store(DB_PATH)
apply_migrations(DB_PATH)
inventory_service = InventoryService(DB_PATH)
with inventory_connection(DB_PATH) as db:
    saved_settings = {r['key']: json.loads(r['value_json']) for r in db.execute("SELECT key,value_json FROM settings WHERE key IN ('dry_run','auto_match_filaments')")}
DRY_RUN = bool(saved_settings.get('dry_run', DRY_RUN))
AUTO_MATCH_FILAMENTS = bool(saved_settings.get('auto_match_filaments', AUTO_MATCH_FILAMENTS))
if INVENTORY_BACKEND == 'internal':
    with inventory_connection(DB_PATH) as db:
        if not db.execute("SELECT 1 FROM settings WHERE key='cutover_job_rowid'").fetchone():
            raise RuntimeError('Interner Bestand benötigt den geprüften Umschaltlauf.')

# ---------------------------------------------------------------------------
# Mapping Persistenz
# ---------------------------------------------------------------------------

DEFAULT_MAPPING = {**{str(i): None for i in range(4)}, "external": None}

def load_mapping() -> dict[str, str | None]:
    if INVENTORY_BACKEND == 'internal':
        with inventory_connection(DB_PATH) as db:
            rows = db.execute('SELECT slot_id,spool_id FROM ams_assignments WHERE printer_id=? AND released_at IS NULL', (SERIAL,)).fetchall()
        return {**DEFAULT_MAPPING, **{r['slot_id']: str(r['spool_id']) for r in rows}}
    if MAPPING_FILE.exists():
        try:
            raw = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
            res = {str(i): (str(raw.get(str(i))) if raw.get(str(i)) not in (None, "") else None) for i in range(4)}
            res["external"] = str(raw.get("external")) if raw.get("external") not in (None, "") else None
            return res
        except Exception as exc:
            log(f"Mapping-Fehler: {exc}", logging.ERROR)
    return DEFAULT_MAPPING.copy()

def save_mapping(mapping: dict[str, str | None]) -> None:
    if INVENTORY_BACKEND == 'internal':
        with inventory_connection(DB_PATH) as db:
            db.execute('BEGIN IMMEDIATE')
            for spool in mapping.values():
                if spool and not db.execute('SELECT 1 FROM spools WHERE id=? AND archived=0', (int(spool),)).fetchone():
                    raise InventoryError('AMS-Zuordnung benötigt eine aktive Spule.')
            now = store.now()
            db.execute('UPDATE ams_assignments SET released_at=? WHERE printer_id=? AND released_at IS NULL', (now, SERIAL))
            for slot, spool in mapping.items():
                if spool:
                    db.execute('INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at) VALUES (?,?,?,?)', (SERIAL, slot, int(spool), now))
            db.commit()
        return
    tmp = MAPPING_FILE.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    tmp.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    os.replace(tmp, MAPPING_FILE)

# ---------------------------------------------------------------------------
# 3MF & FTPS
# ---------------------------------------------------------------------------

class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def connect(self, host: str = "", port: int = 990, timeout: float = 15, source_address=None):
        if host: self.host = host
        self.port = port
        self.timeout = timeout
        raw_sock = socket.create_connection((self.host, self.port), self.timeout, source_address)
        self.af = raw_sock.family
        self.sock = self.context.wrap_socket(raw_sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def makepasv(self):
        _, port = super().makepasv()
        return self.host, port

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

def parse_slice_info_config(xml_content: bytes, plate_index: int | None = None,
                            filament_metadata: dict | None = None) -> dict[int, float]:
    if len(xml_content) > MAX_XML_BYTES:
        raise ValueError('slice_info.config überschreitet MAX_XML_BYTES')
    root = ET.fromstring(xml_content)
    plates = [node for node in root.iter() if str(node.tag).rsplit('}', 1)[-1] == 'plate']
    if plates:
        if plate_index is not None:
            plates = [plate for plate in plates if any(
                node.get('key') in ('index', 'plater_id') and node.get('value') == str(plate_index)
                for node in plate)]
        if len(plates) != 1:
            raise ValueError('Druckplatte nicht eindeutig; keine automatische Buchung')
        root = plates[0]
    weights: dict[int, float] = {}
    for filament in root.iter():
        if str(filament.tag).rsplit("}", 1)[-1] != "filament": continue
        raw_id, raw_grams = filament.get("id"), filament.get("used_g")
        if raw_id is None or raw_grams is None: continue
        try:
            f_id, grams = int(raw_id), float(raw_grams)
        except (TypeError, ValueError): continue
        if f_id < 1 or not math.isfinite(grams) or grams <= 0: continue
        weights[f_id] = round(weights.get(f_id, 0.0) + grams, 2)
        if filament_metadata is not None:
            filament_metadata[str(f_id)] = {'color': filament.get('color', ''), 'material': filament.get('type', '')}
    return {k: v for k, v in weights.items() if v > 0}

def extract_weights_via_ftps(filename: str, plate_index: int | None = None,
                             filament_metadata: dict | None = None) -> dict[int, float]:
    if not filename: return {}
    name = filename.strip().replace("\\", "/").split("?")[0].rstrip("/").split("/")[-1]
    if '\r' in name or '\n' in name:
        return {}
    names = [name]
    if name.endswith('.3mf') and not name.endswith('.gcode.3mf'):
        names.append(name[:-4] + '.gcode.3mf')
    if not name.endswith('.3mf'):
        names.extend([name + '.3mf', name + '.gcode.3mf'])
    candidates = [path for n in names for path in (n, f'/cache/{n}')]

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftps = ImplicitFTP_TLS(context=ctx)
    archive_data = None

    try:
        ftps.connect(PRINTER_IP, 990, timeout=15)
        ftps.login("bblp", ACCESS_CODE)
        ftps.prot_p()
        ftps.set_pasv(True)

        for p in candidates:
            try:
                buf = io.BytesIO()
                def bounded_write(block):
                    if buf.tell() + len(block) > MAX_3MF_BYTES:
                        raise ValueError('3MF überschreitet MAX_3MF_BYTES')
                    buf.write(block)
                ftps.retrbinary(f"RETR {p}", bounded_write)
                data = buf.getvalue()
                if data and len(data) <= MAX_3MF_BYTES:
                    archive_data = data
                    break
            except ftplib.all_errors:
                continue
    except Exception as exc:
        log(f"FTPS Fehler: {exc}", logging.WARNING)
    finally:
        try: ftps.quit()
        except Exception: ftps.close()

    if not archive_data: return {}

    try:
        with zipfile.ZipFile(io.BytesIO(archive_data), "r") as archive:
            for item in archive.namelist():
                if item.lower().endswith("slice_info.config"):
                    if archive.getinfo(item).file_size > MAX_XML_BYTES:
                        raise ValueError('slice_info.config überschreitet MAX_XML_BYTES')
                    return parse_slice_info_config(archive.read(item), plate_index, filament_metadata)
    except Exception as exc:
        log(f"XML Parsing Fehler: {exc}", logging.ERROR)
    return {}

# ---------------------------------------------------------------------------
# Spoolman API Client
# ---------------------------------------------------------------------------

spoolman_status = {'connected': None, 'error': ''}

def fetch_spoolman_spools() -> list[dict[str, Any]]:
    if INVENTORY_BACKEND == 'internal':
        spools = spoolman_view(DB_PATH)
        spoolman_status.update(connected=True, error='')
        return spools
    try:
        r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool", timeout=(3, 8))
        r.raise_for_status()
        spools = []
        for s in r.json():
            if s.get("archived", False): continue
            fil = s.get("filament") or {}
            color = str(fil.get("color_hex") or "888888").replace("#", "")[:6]
            s["clean_color"] = f"#{color or '888888'}"
            s["material"] = fil.get("material") or "-"
            spools.append(s)
        spoolman_status.update(connected=True, error='')
        return spools
    except Exception as exc:
        log(f"Spoolman nicht erreichbar: {exc}", logging.WARNING)
        spoolman_status.update(connected=False, error='Spoolman nicht erreichbar; URL und Netzwerk prüfen.')
        return []

def send_spoolman_deduction(row: dict[str, Any]) -> tuple[str, str]:
    try:
        probe = requests.get(f"{SPOOLMAN_URL}/api/v1/spool", timeout=(3, 5))
        probe.raise_for_status()
    except requests.RequestException as exc:
        return "RETRY", f"Spoolman vor Buchung nicht erreichbar: {exc}"

    try:
        r = requests.put(
            f"{SPOOLMAN_URL}/api/v1/spool/{int(row['spool_id'])}/use",
            json={"use_weight": round(float(row["grams"]), 2)},
            timeout=(3, 12),
        )
        if 200 <= r.status_code < 300: return "DONE", "Buchung durch Spoolman bestätigt"
        if r.status_code == 429: return "RETRY", "Rate Limit"
        if 400 <= r.status_code < 500: return "REJECTED", f"HTTP {r.status_code}: {r.text[:200]}"
        return "UNKNOWN", f"HTTP {r.status_code}"
    except requests.ConnectTimeout as exc:
        return "RETRY", f"Timeout beim Aufbau: {exc}"
    except requests.RequestException as exc:
        return "UNKNOWN", f"Unklarer Timeout: {exc}"

# ---------------------------------------------------------------------------
# Queue Worker & State Machine
# ---------------------------------------------------------------------------

job_queue: queue.Queue[str] = queue.Queue()
queued_jobs: set[str] = set()
queued_lock = threading.Lock()

def enqueue_job(job_id: str) -> None:
    with queued_lock:
        if job_id not in queued_jobs:
            queued_jobs.add(job_id)
            job_queue.put(job_id)

def resolve_tray_for_filament(filament_id: int, raw_mapping, raw_mapping2) -> int | None:
    # Slicer IDs start at 1; physical AMS Lite trays start at 0.
    filament_id = int(filament_id)
    if filament_id < 1:
        return None
    if isinstance(raw_mapping, list) and len(raw_mapping) >= filament_id:
        try:
            val = int(raw_mapping[filament_id - 1])
            if val in range(4) or val == 254:
                return val
        except (TypeError, ValueError):
            pass
    if isinstance(raw_mapping2, list) and len(raw_mapping2) >= filament_id:
        entry = raw_mapping2[filament_id - 1]
        if isinstance(entry, dict):
            try:
                ams_id, slot_id = int(entry.get("ams_id", -1)), int(entry.get("slot_id", -1))
            except (TypeError, ValueError):
                return None
            if ams_id == 255 and slot_id in (0, 254, 255):
                return 254
            if ams_id == 0 and slot_id in range(4):
                return slot_id
    return None


def normalized_color(value) -> str | None:
    color = str(value or '').strip().lstrip('#').upper()
    if not re.fullmatch(r'[0-9A-F]{6}(?:[0-9A-F]{2})?', color):
        return None
    return color[:6]


def settle_job(job_id: str) -> None:
    statuses = {r["status"] for r in store.deductions_for_job(job_id)}
    if "UNKNOWN" in statuses or "IN_FLIGHT" in statuses:
        state, error = "UNKNOWN", "Buchung unklar. Bestand in Spoolman prüfen; keine automatische Wiederholung."
    elif "REJECTED" in statuses:
        state, error = "REJECTED", "Spoolman hat eine Buchung abgelehnt; Journal prüfen."
    elif statuses & {"PENDING", "RETRY"}:
        state, error = "RETRY", "Spoolman vorübergehend nicht erreichbar."
    elif "UNMAPPED" in statuses:
        state, error = "WAITING_MAPPING", "Filament oder Spule nicht eindeutig zugeordnet."
    elif "DRY_RUN" in statuses:
        state, error = "DRY_RUN", "Simulation; wird beim Wechsel zum Echtbetrieb nicht nachgebucht."
    elif statuses == {"DONE"}:
        state, error = "DONE", ""
    else:
        state, error = "WAITING_METADATA", "Keine gültigen Verbrauchsdaten."
    store.update_job(job_id, state=state, error=error)


def book_pending(job_id: str) -> None:
    job = store.get_job(job_id)
    simulate = DRY_RUN or (job.get('dry_run') == 1)
    source = {'AUTO_COLOR_MATERIAL': 'Farbe und Material automatisch zugeordnet',
              'USER': 'Zuordnung durch Benutzer bestätigt', 'MQTT': 'Zuordnung vom Drucker'}.get(job.get('mapping_source'), '')
    if job.get('estimate_reason'):
        source = (source + '; ' if source else '') + 'SCHÄTZUNG: ' + job['estimate_reason']
    for row in store.get_pending_deductions(job_id):
        if simulate:
            detail = 'Nur simuliert; keine spätere automatische Nachbuchung'
            store.set_deduction_status(row["id"], "DRY_RUN", detail + ('; ' + source if source else ''))
            log(f"[DRY-RUN] {row['grams']}g von Spule #{row['spool_id']}")
        elif INVENTORY_BACKEND == 'internal':
            try:
                inventory_service.apply_ledger_deduction(row['id'])
            except InventoryError as exc:
                store.set_deduction_status(row['id'], 'REJECTED', str(exc))
                log(f"Interne Buchung abgelehnt: {exc}", logging.WARNING)
        elif store.claim_deduction(row["id"]):
            status, detail = send_spoolman_deduction(row)
            store.set_deduction_status(row["id"], status, detail + ('; ' + source if source else ''))
            log(f"Buchung #{row['id']}: {row['grams']}g, Spule #{row['spool_id']}: {status}")
    settle_job(job_id)


def stock_snapshot(spools: list[dict]) -> dict:
    result = {}
    for spool in spools:
        filament = spool.get('filament') or {}
        weight = spool.get('remaining_weight')
        try:
            weight = float(weight)
            if not math.isfinite(weight): weight = None
        except (TypeError, ValueError): weight = None
        result[str(spool['id'])] = {'remaining_weight': weight,
            'material': filament.get('material', ''), 'color': filament.get('color_hex', '')}
    return result


def plan_consumption(job: dict, weights: dict, filaments: dict, slots: dict, stock: dict):
    """Plan in centigrams. Freeze this plan before the first API attempt."""
    inventory = json.loads(job.get('ams_snapshot_json') or '{}')
    observed = json.loads(job.get('observed_slots_json') or '[]')
    raw = json.loads(job.get('ams_mapping_json') or 'null')
    raw2 = json.loads(job.get('ams_mapping2_json') or 'null')
    remaining = {key: max(0, round(value['remaining_weight'] * 100))
                 for key, value in stock.items() if value.get('remaining_weight') is not None}
    rows, reasons, sources = [], [], set()
    factor = max(0, min(100, float(job.get('progress') or 0))) / 100 if job.get('cancelled') else 1
    if job.get('cancelled'):
        reasons.append(f"Abbruchschätzung: Slicer-Mengen × {factor * 100:g}% Fortschritt")

    for filament_id, original in sorted(weights.items()):
        total = round(original * factor * 100)
        if total <= 0: continue
        filament = filaments.get(str(filament_id), {})
        primary = resolve_tray_for_filament(filament_id, raw, raw2)
        candidates = []
        if primary is not None:
            sources.add(job.get('mapping_source') or 'MQTT')
            candidates.append(primary)
        if AUTO_MATCH_FILAMENTS and job.get('mapping_source') != 'USER':
            for slot in range(4):
                tray = inventory.get(str(slot), {})
                if (tray.get('available') is True and normalized_color(filament.get('color'))
                        and normalized_color(tray.get('color')) == normalized_color(filament.get('color'))
                        and str(tray.get('material') or '').strip().upper() == str(filament.get('material') or '').strip().upper()):
                    if slot not in candidates: candidates.append(slot)
            if primary is None and candidates: sources.add('AUTO_COLOR_MATERIAL')
        matched_slots = list(candidates)
        candidates = [slot for slot in candidates if slots.get('external' if slot == 254 else str(slot))]
        if not candidates:
            color = normalized_color(filament.get('color'))
            material = str(filament.get('material') or '').strip()
            if matched_slots:
                detail = 'Passender AMS-Slot hat keine Spoolman-Spulenzuordnung.'
            elif job.get('mapping_source') == 'USER':
                detail = f'Filament {filament_id} wurde keinem AMS-Slot zugeordnet.'
            elif not color or not material:
                detail = f'Slicer-Farbe oder Material für Filament {filament_id} fehlt.'
            elif not any(tray.get('known') for tray in inventory.values()):
                detail = 'AMS-Daten zum Druckbeginn fehlen.'
            else:
                detail = f'Keine AMS-Übereinstimmung für Slicer-Farbe #{color} und Material {material}.'
            rows.append(dict(source_key=f'filament:{filament_id}', filament_id=filament_id, tray_id=None,
                             spool_id=None, grams=total/100, status='UNMAPPED', detail=detail))
            continue
        def spool_for(slot): return str(slots['external' if slot == 254 else str(slot)])
        def order(slot):
            sid = spool_for(slot)
            if primary == slot: return (0, 0, 0)
            if slot in observed: return (1, observed.index(slot), 0)
            weight = remaining.get(sid)
            return (2, 0 if weight and weight > 0 else 1, weight if weight is not None else float('inf'))
        candidates.sort(key=order)
        if len(candidates) > 1:
            if not any(slot in observed for slot in candidates) and not any(spool_for(slot) in remaining for slot in candidates):
                rows.append(dict(source_key=f'filament:{filament_id}', filament_id=filament_id, tray_id=None,
                                 spool_id=None, grams=total/100, status='UNMAPPED', detail='Gleiche Farbe/Material; Restgewichte fehlen'))
                continue
            reasons.append('Gewichtsbasierte Aufteilung gleicher Farbe und gleichen Materials; beobachtete Slots haben Vorrang')
        pool, used_spools = [], set()
        for slot in candidates:
            sid = spool_for(slot)
            if sid not in used_spools:
                pool.append((slot, sid)); used_spools.add(sid)
        # Off-printer reserves use Spoolman's color/material for the primary stock item.
        reference = stock.get(pool[0][1], filament)
        color = normalized_color(reference.get('color'))
        material = str(reference.get('material') or '').strip().upper()
        reserves = [(sid, item) for sid, item in stock.items() if sid not in used_spools
                    and color and normalized_color(item.get('color')) == color
                    and material and str(item.get('material') or '').strip().upper() == material
                    and remaining.get(sid, 0) > 0]
        reserves.sort(key=lambda pair: (remaining[pair[0]], int(pair[0])))
        pool.extend((None, sid) for sid, _ in reserves)
        left, parts = total, []
        for slot, sid in pool:
            if left <= 0: break
            capacity = remaining.get(sid)
            amount = left if capacity is None else min(left, capacity)
            if amount <= 0: continue
            parts.append([slot, sid, amount])
            left -= amount
            if capacity is not None: remaining[sid] -= amount
            if slot is None:
                reasons.append('Rechnerische Aufteilung auf passende Spoolman-Reservespule; physische Verwendung nicht bestätigt')
        if left > 0:
            slot, sid = pool[-1]
            if parts and parts[-1][1] == sid: parts[-1][2] += left
            else: parts.append([slot, sid, left])
            remaining[sid] = remaining.get(sid, 0) - left
            reasons.append('Verbrauch übersteigt gepflegten Restbestand; Differenz auf letzte passende Spule gebucht')
        if len(parts) > 1:
            reasons.append('Spulenaufteilung anhand gespeicherter Restgewichte geschätzt')
        for number, (slot, sid, amount) in enumerate(parts, 1):
            key = f'filament:{filament_id}' + (f':part:{number}' if number > 1 else '')
            rows.append(dict(source_key=key, filament_id=filament_id, tray_id=slot,
                             spool_id=int(sid), grams=amount/100, status='PENDING'))
    source = 'USER' if job.get('mapping_source') == 'USER' else '+'.join(sorted(sources))
    return rows, source, '; '.join(dict.fromkeys(reasons))


def process_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if not job or job['state'] in ('PREPARING', 'FAILED', 'INTERRUPTED', 'DRY_RUN', 'CANCELLED_ZERO'):
        return
    if job['kind'] == 'MANUAL':
        book_pending(job_id)
        return
    existing = store.deductions_for_job(job_id)
    # Keep original plans, including v2.x positions, immutable after any booking attempt.
    if existing and all(row['status'] != 'UNMAPPED' for row in existing):
        book_pending(job_id)
        return
    if existing and any(row['attempts'] or row['status'] not in ('UNMAPPED','PENDING') for row in existing):
        store.update_job(job_id, state='UNKNOWN', error='Teilweise verarbeiteter Altauftrag; keine automatische Neuaufteilung.')
        return
    if not job.get('spools_snapshot_json'):
        spools = fetch_spoolman_spools()
        if spools:
            snapshot = json.dumps(stock_snapshot(spools))
            store.update_job(job_id, spools_snapshot_json=snapshot)
            job['spools_snapshot_json'] = snapshot
    weights = {int(k): float(v) for k, v in json.loads(job.get('weights_json') or '{}').items()}
    filaments = json.loads(job.get('filaments_json') or '{}')
    if not weights or (AUTO_MATCH_FILAMENTS and not filaments and job.get('mapping_source') != 'USER'):
        downloaded = extract_weights_via_ftps(job.get('filename') or job.get('subtask_name',''), job.get('plate_index'), filaments)
        if not weights: weights = downloaded
        if weights:
            store.update_job(job_id, weights_json=json.dumps(weights), filaments_json=json.dumps(filaments))
    # Start workers capture metadata and stock only; they never deduct during printing.
    if job['state'] == 'RUNNING': return
    if not weights:
        store.update_job(job_id, state='WAITING_METADATA', error='Warte auf eindeutige 3MF-Verbrauchsdaten.')
        return
    if any(k < 1 or not math.isfinite(v) or v <= 0 for k,v in weights.items()):
        store.update_job(job_id, state='REJECTED', error='Ungültige Verbrauchsdaten.')
        return
    slots = json.loads(job.get('slot_mapping_json') or json.dumps(load_mapping()))
    if not job.get('slot_mapping_json'): store.update_job(job_id, slot_mapping_json=json.dumps(slots))
    stock = json.loads(job.get('spools_snapshot_json') or '{}')
    rows, source, reason = plan_consumption(job, weights, filaments, slots, stock)
    store.update_job(job_id, mapping_source=source or job.get('mapping_source',''), estimated=int(bool(reason)), estimate_reason=reason)
    if not rows:
        store.update_job(job_id, state='CANCELLED_ZERO' if job.get('cancelled') else 'REJECTED', error=reason or 'Keine positive Verbrauchsmenge.')
        return
    if not store.replace_unattempted_plan(job_id, rows): return
    if any(row['status'] == 'UNMAPPED' for row in rows):
        details = '; '.join(dict.fromkeys(row['detail'] for row in rows if row['status'] == 'UNMAPPED'))
        store.update_job(job_id, state='WAITING_MAPPING', error=(details + ' Gesamter Auftrag wartet.')[:1000])
        return
    book_pending(job_id)


def job_worker():
    while True:
        j_id = job_queue.get()
        try: process_job(j_id)
        except Exception as exc: logger.exception(f"Job-Fehler: {exc}")
        finally:
            with queued_lock: queued_jobs.discard(j_id)
            job_queue.task_done()

# ---------------------------------------------------------------------------
# MQTT Listener
# ---------------------------------------------------------------------------

telemetry = PrinterTelemetry()
ams_state = telemetry.ams
printer_status = telemetry.status
mqtt_client: mqtt.Client | None = None
seq_counter = 1
sequence_lock = threading.Lock()

def next_sequence():
    global seq_counter
    with sequence_lock:
        seq_counter = seq_counter % 2147483646 + 1
        return str(seq_counter)

def on_connect(client, userdata, flags, reason_code, properties=None):
    code = getattr(reason_code, "value", reason_code)
    if code == 0:
        log("MQTT-Verbindung zum Bambu-Drucker hergestellt.")
        client.subscribe(f"device/{SERIAL}/report", qos=0)
        send_pushall(client)
    else:
        log(f'MQTT-Anmeldung abgelehnt: {reason_code}', logging.ERROR)

def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    printer_status['state'] = 'OFFLINE'

def send_pushall(client):
    result = client.publish(f"device/{SERIAL}/request", json.dumps({"pushing": {"command": "pushall", "sequence_id": next_sequence()}}), qos=0)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise ConnectionError("MQTT-Statusabfrage abgewiesen.")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        p = data.get("print") or {}
        if not p: return

        state, filename, subtask, ams_map, ams_map2 = telemetry.update(p, normalized_color)

        if state in ("RUNNING", "PREPARE"):
            previous = store.active_job_id()
            started = store.create_or_update_running_job(filename, subtask, ams_map, ams_map2)
            if started != previous:
                if 'mc_percent' not in p: printer_status['progress'] = 0
                if 'layer_num' not in p: printer_status['layer'] = 0
                if 'stg_cur' not in p: printer_status['stage'] = None
                enqueue_job(started)
        # Mapping and file metadata can arrive in MQTT delta messages without a state.
        active_id = store.active_job_id()
        if active_id:
            fields = {}
            job = store.get_job(active_id)
            fields['progress'] = max(float(job.get('progress') or 0), printer_status['progress'])
            observed = json.loads(job.get('observed_slots_json') or '[]')
            slot = printer_status['active_slot']
            if (printer_status['layer'] > 0 and printer_status['stage'] == 0
                    and slot is not None and slot not in observed):
                observed.append(slot)
            fields['observed_slots_json'] = json.dumps(observed)
            snapshot = json.loads(job.get('ams_snapshot_json') or '{}')
            for tid, tray in ams_state.items():
                if not snapshot.get(str(tid), {}).get('known') and tray.get('known'):
                    snapshot[str(tid)] = dict(tray)
            fields['ams_snapshot_json'] = json.dumps(snapshot)
            if filename: fields['filename'] = filename
            if subtask: fields['subtask_name'] = subtask
            if job['mapping_source'] != 'USER':
                if ams_map is not None: fields['ams_mapping_json'] = json.dumps(ams_map)
                if ams_map2 is not None: fields['ams_mapping2_json'] = json.dumps(ams_map2)
                if ams_map is not None or ams_map2 is not None: fields['mapping_source'] = 'MQTT'
            plate = re.search(r'plate_(\d+)\.gcode', str(p.get('param', '')))
            if plate: fields['plate_index'] = int(plate.group(1))
            if fields: store.update_job(active_id, **fields)
        if state == "FINISH":
            j_id = store.finish_active_job(filename, subtask, ams_map, ams_map2)
            if j_id: enqueue_job(j_id)
        elif state in ("FAILED", "CANCEL", "CANCELLED", "CANCELED", "STOPPED", "ERROR"):
            j_id = store.close_active_job('CANCEL_PENDING', f'Status: {state}')
            if j_id: enqueue_job(j_id)
        elif state == "IDLE":
            j_id = store.close_active_job('CANCEL_PENDING', 'Unerwartet IDLE vor FINISH')
            if j_id: enqueue_job(j_id)
    except Exception as exc:
        logger.exception("MQTT Fehler")

# ---------------------------------------------------------------------------
# Flask Web Interface
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or store.session_secret()
register_inventory(app, DB_PATH, INVENTORY_BACKEND == "internal")
def set_printer_light(action):
    client = mqtt_client
    if client is None or not client.is_connected():
        raise ConnectionError('Drucker nicht verbunden.')
    sequence = telemetry.light_sequence
    result = client.publish(f"device/{SERIAL}/request", json.dumps({"system": {
        "sequence_id": next_sequence(), "command": "ledctrl",
        "led_node": "chamber_light", "led_mode": action,
        "led_on_time": 500, "led_off_time": 500, "loop_times": 0, "interval_time": 0,
    }}), qos=0)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise ConnectionError('MQTT-Lichtbefehl abgewiesen.')
    send_pushall(client)
    if not telemetry.wait_for_light(sequence, action, timeout=6):
        raise TimeoutError('Der Drucker hat die Lichtstellung nicht bestätigt. LAN-Steuerung und Entwicklermodus prüfen.')


def refresh_printer_status():
    client = mqtt_client
    if client is None or not client.is_connected():
        return False
    # Fresh explicit state reports remain valid; do not demand a second full
    # report when the printer streams deltas or rate-limits pushall.
    if 0 <= time.time() - printer_status.get("state_seen_at", 0) <= 30:
        return True
    sequence = telemetry.state_sequence
    send_pushall(client)
    return telemetry.wait_for_state(sequence, timeout=6)


register_devices(app, PRINTER_IP, ACCESS_CODE, printer_status, set_printer_light, refresh_printer_status)

def job_mapping_forms():
    forms = []
    for job in store.list_jobs():
        if job['kind'] != 'PRINT' or job['state'] not in ('RUNNING', 'FINISH_PENDING', 'WAITING_METADATA', 'WAITING_MAPPING'):
            continue
        if any(row['status'] not in ('UNMAPPED', 'PENDING') or row['attempts'] > 0 for row in store.deductions_for_job(job['id'])):
            continue
        weights = json.loads(job['weights_json'] or '{}')
        mapping = json.loads(job['ams_mapping_json'] or '[]')
        mapping2 = json.loads(job['ams_mapping2_json'] or 'null')
        metadata = json.loads(job.get('filaments_json') or '{}')
        inventory = json.loads(job.get('ams_snapshot_json') or '{}')
        planned = store.deductions_for_job(job['id'])
        ids = sorted(int(k) for k in weights) if weights else list(range(1, max(len(mapping), 4) + 1))
        job['filaments'] = []
        for i in ids:
            tray = resolve_tray_for_filament(i, mapping, mapping2)
            if tray is None:
                planned_trays = {row['tray_id'] for row in planned if row['filament_id'] == i and row['tray_id'] is not None}
                if len(planned_trays) == 1: tray = planned_trays.pop()
            fil = metadata.get(str(i), {})
            job['filaments'].append({'id': i, 'grams': weights.get(str(i)), 'tray': tray,
                                     'color': normalized_color(fil.get('color')), 'material': fil.get('material','')})
        job['assignment_summary'] = (f"{sum(f['tray'] is not None for f in job['filaments'])} von {len(ids)} Filamenten zugeordnet" if weights else '')
        job['slot_labels'] = {slot: f"AMS Slot {slot + 1}" + (f" · {inventory[str(slot)].get('material','')} · {inventory[str(slot)].get('color','')}" if inventory.get(str(slot),{}).get('known') else '') for slot in range(4)}
        forms.append(job)
    return forms

@app.before_request
def protect_requests():
    if request.path == '/health':
        return None
    if WEB_USERNAME:
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.username or '', WEB_USERNAME) or not secrets.compare_digest(auth.password or '', WEB_PASSWORD):
            return Response('Anmeldung erforderlich.', 401, {'WWW-Authenticate': 'Basic realm="Bambu Bridge"'})
    if request.method == 'POST':
        expected = session.get('csrf_token')
        if not expected or not secrets.compare_digest(request.form.get('csrf_token', ''), expected):
            target = '/camera' if request.path == '/devices/light' else '/inventory' if request.path.startswith('/inventory') else '/devices' if request.path.startswith('/devices') else '/settings' if request.path.startswith('/settings') else '/'
            active = {'/inventory':'inventory','/camera':'camera','/devices':'devices','/settings':'settings','/':'overview'}[target]
            return Response(render_ui('''<div class="page-heading"><div><h1>Sitzung abgelaufen</h1><p>Diese Eingabe wurde nicht gespeichert.</p></div></div><section class="panel"><p>Öffne die Seite neu und wiederhole anschließend deine Eingabe.</p><a class="button" href="{{target}}">Seite neu öffnen</a></section>''', active=active, title='Sitzung abgelaufen', target=target), 403, mimetype='text/html')

HTML_TEMPLATE = r"""

{% if dry_run %}
<div class="warn-banner">
  Simulation aktiv · Verbräuche werden protokolliert, der Bestand bleibt unverändert. Den Betriebsmodus kannst du unter Einstellungen ändern.
</div>
{% endif %}

<section class="card">
  <div class="top">
    <div>
      <div class="eyebrow">Dein Drucker</div><h1>Drucker & Verbrauch</h1>
  <div class="muted">A1 / A1 mini · AMS Lite · Automatische Verbrauchsbuchung</div>
    </div>
    <div>
      <button class="secondary" onclick="window.location.reload()">Aktualisieren</button>

    </div>
  </div>
  <div class="status">
    <div><span class="muted">Spoolman:</span> <strong id="spoolman">{{ 'Verbunden' if spoolman.connected else 'Nicht verbunden' }}</strong></div>
    <div><span class="muted">Drucker:</span> <span id="state" class="pill">{{ printer.state }}</span> <span id="progress">{{ printer.progress }}%</span></div>
    <div><span class="muted">Datei:</span> <strong id="subtask">{{ printer.subtask_name or '—' }}</strong></div>
    <div><span class="muted">Aktiver Schacht:</span> <strong id="active">{{ printer.active_slot_label }}</strong></div>
  </div>
</section>

<section class="card">
  <h2>Spulen im AMS Lite</h2>
    <p class="muted">Wähle für jeden Schacht die eingelegte Spule aus. Aktualisiere die Zuordnung nach einem Rollenwechsel.</p>
    <details class="diagnostics"><summary class="muted small">Automatischer Abgleich: {{'aktiv' if auto_match else 'deaktiviert'}}</summary><p class="muted small">Farbe und Material werden mit den Slicer-Angaben verglichen. Bei mehreren passenden Spulen helfen der beobachtete Slot und das Restgewicht bei der Zuordnung.</p></details>
  <form method="post" action="/save">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    {% for slot_id in range(4) %}
      <div class="slot">
        <div>
          <span class="swatch" style="background:{{ ams[slot_id].color }}"></span>
          <strong>AMS Slot {{ slot_id + 1 }}</strong>
          <div class="muted small">{{ ams[slot_id].type }} · Rest: {% if ams[slot_id].get('remain', -1)|int(-1)>=0 %}{{ ams[slot_id].remain }}%{% else %}unbekannt{% endif %}</div>
          <div class="muted small">Material/Farbcode: {% if ams[slot_id].known and ams[slot_id].available %}{{ ams[slot_id].material }} / <code>{{ ams[slot_id].color }}</code>{% else %}—{% endif %}</div>
        </div>
        <select name="slot_{{ slot_id }}">
          <option value="">Keine Spule zugeordnet</option>
          {% for s in spools %}
            <option value="{{ s.id }}" {% if mapping.get(slot_id|string) == s.id|string %}selected{% endif %}>
              {{ s.filament.vendor.name ~ ' · ' if s.filament and s.filament.vendor and s.filament.vendor.name else '' }}{{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g
            </option>
          {% endfor %}
        </select>
      </div>
    {% endfor %}
    <div class="slot">
      <div><strong>Externe Spule</strong><div class="muted small">Spulenhalter / vt_tray</div></div>
      <select name="slot_external">
        <option value="">Keine Spule zugeordnet</option>
        {% for s in spools %}
          <option value="{{ s.id }}" {% if mapping.get('external') == s.id|string %}selected{% endif %}>
            {{ s.filament.vendor.name ~ ' · ' if s.filament and s.filament.vendor and s.filament.vendor.name else '' }}{{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g
          </option>
        {% endfor %}
      </select>
    </div>
    <div style="text-align:right;margin-top:12px;"><button type="submit">Zuordnung speichern</button></div>
  </form>
</section>

<section class="card">
  <h2>Verbrauch manuell erfassen</h2>
  <form method="post" action="/manual_deduct" class="manual-grid">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div>
      <label class="muted small">Spule auswählen</label>
      <select name="spool_id" required>
        <option value="">— Spule wählen —</option>
        {% for s in spools %}
          <option value="{{ s.id }}">{{ s.filament.vendor.name ~ ' · ' if s.filament and s.filament.vendor and s.filament.vendor.name else '' }}{{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="muted small">Menge (Gramm)</label>
      <input type="number" step="0.01" min="0.01" max="1000" name="grams" placeholder="Gramm" required>
    </div>
    <button type="submit" >Verbrauch buchen</button>
  </form>
</section>

<section class="card">
  <h2>Druckaufträge und Filamentzuordnung</h2>
  <p class="muted">Slicer-Filamentnummern und AMS-Slots können verschieden sein. Fehlt die Druckerzuordnung, hier für diesen Auftrag bestätigen. Bereits gebuchte Positionen werden nicht verändert.</p>
  {% for job in mapping_jobs %}
  <form method="post" action="{{ url_for('set_job_mapping', job_id=job.id) }}" style="margin-bottom:18px">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <strong>{{ job.subtask_name or job.filename or job.id[:8] }}</strong> · {{ job.state }}
    {% if job.mapping_source %}<span class="muted"> · Zuordnung: {{ job.mapping_source }}</span>{% endif %}
    {% if job.assignment_summary %}<p><strong>{{ job.assignment_summary }}</strong></p>{% endif %}
    {% if job.error %}<p class="muted">{{ job.error }}</p>{% endif %}
    {% for filament in job.filaments %}
    <div class="slot">
      <label for="{{ job.id }}-f{{ filament.id }}">Filament {{ filament.id }}{% if filament.grams is not none %} · {{ '%.2f'|format(filament.grams) }} g{% endif %}
        <span class="muted small" style="display:block">{% if filament.color %}<span class="swatch" style="background:#{{ filament.color }}"></span>#{{ filament.color }}{% endif %} {{ filament.material }}</span>
      </label>
      <select id="{{ job.id }}-f{{ filament.id }}" name="filament_{{ filament.id }}">
        <option value="-1">— Nicht zugeordnet —</option>
        {% for slot in range(4) %}<option value="{{ slot }}" {% if filament.tray == slot %}selected{% endif %}>{{ job.slot_labels[slot] }}</option>{% endfor %}
        <option value="254" {% if filament.tray == 254 %}selected{% endif %}>Externe Spule</option>
      </select>
    </div>
    {% endfor %}
    <button type="submit" style="margin-top:10px">Zuordnung für diesen Auftrag bestätigen</button>
  </form>
  {% else %}<p class="muted">Kein Auftrag mit offener Filamentzuordnung.</p>{% endfor %}
</section>

<section class="card">
  <h2>Buchungsjournal (Letzte Positionen)</h2>
  <div style="overflow:auto;">
    <table>
      <thead><tr><th>Zeit</th><th>Auftrag</th><th>Schacht</th><th>Spule</th><th>Verbrauch</th><th>Status</th><th>Details</th></tr></thead>
      <tbody id="deductions">
        {% for d in deductions %}
          <tr>
            <td>{{ d.created_at[11:19] }}</td>
            <td>{{ d.subtask_name or d.filename or d.job_id[:8] }}</td>
            <td>{{ 'Extern' if d.tray_id == 254 else (('Slot ' ~ (d.tray_id + 1)) if d.tray_id is not none else '—') }}</td>
            <td>{{ ('#' ~ d.spool_id) if d.spool_id else '—' }}</td>
            <td>{{ '%.2f'|format(d.grams) }} g</td>
            <td><strong>{{ d.status }}{% if d.estimated %} · SCHÄTZUNG{% endif %}</strong></td>
            <td class="muted small">{{ d.detail }}</td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">Noch keine Buchungen erfasst.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="card"><details class="diagnostics">
  <summary>Diagnose & Live-Logs</summary>
  <div class="logs" id="logs">{% for line in logs %}{{ line }}
{% endfor %}</div></details>
</section>


<script>
let userScrolled=false;const logBox=document.getElementById('logs');logBox.addEventListener('scroll',()=>{userScrolled=(logBox.scrollHeight-logBox.scrollTop-logBox.clientHeight)>30;});
function cell(row,val){const td=document.createElement('td');td.textContent=val??'';row.appendChild(td);}
function refreshStatus(){fetch('/api/status').then(r=>r.json()).then(data=>{
 document.getElementById('spoolman').textContent=data.spoolman.connected?'Verbunden':'Nicht verbunden';
 document.getElementById('state').textContent=data.printer.state;
 document.getElementById('progress').textContent=data.printer.progress+'%';
 document.getElementById('subtask').textContent=data.printer.subtask_name||'—';
 document.getElementById('active').textContent=data.printer.active_slot_label;
 logBox.textContent=data.logs.join('\n');
 if(!userScrolled)logBox.scrollTop=logBox.scrollHeight;
 const tbody=document.getElementById('deductions');tbody.replaceChildren();
 data.deductions.forEach(d=>{
   const tr=document.createElement('tr');
   cell(tr,(d.created_at||'').slice(11,19));
   cell(tr,d.subtask_name||d.filename||d.job_id.slice(0,8));
   cell(tr,d.tray_id===null?'—':(d.tray_id===254?'Extern':'Slot '+(d.tray_id+1)));
   cell(tr,d.spool_id?'#'+d.spool_id:'—');
   cell(tr,Number(d.grams).toFixed(2)+' g');
   cell(tr,d.status+(d.estimated?' · SCHÄTZUNG':''));
   cell(tr,d.detail);
   tbody.appendChild(tr);
 });
});}
setInterval(refreshStatus,5000);
</script>
"""

@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "bambu-spoolman-bridge", "version": APP_VERSION, "dry_run": DRY_RUN, "inventory_backend": INVENTORY_BACKEND})

@app.route("/")
def index():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_ui(
        HTML_TEMPLATE.replace("Spoolman", "Inventar") if INVENTORY_BACKEND == "internal" else HTML_TEMPLATE,
        active='overview', title='Drucker & Verbrauch',
        version=APP_VERSION,
        build_time=APP_BUILD_TIME,
        csrf_token=session["csrf_token"],
        printer=printer_status,
        spoolman=spoolman_status,
        ams=ams_state,
        mapping=load_mapping(),
        mapping_jobs=job_mapping_forms(),
        spools=fetch_spoolman_spools(),
        deductions=store.list_deductions(30),
        logs=list(log_buffer),
        spoolman_url=SPOOLMAN_BROWSER_URL,
        dry_run=DRY_RUN,
        auto_match=AUTO_MATCH_FILAMENTS,
    )

@app.route("/api/status")
def status_api():
    with log_lock: logs = list(log_buffer)
    return jsonify({
        "printer": printer_status,
        "spoolman": spoolman_status,
        "jobs": store.list_jobs(),
        "dry_run": DRY_RUN,
        "auto_match_filaments": AUTO_MATCH_FILAMENTS,
        "deductions": store.list_deductions(30),
        "logs": logs,
    })

@app.route("/save", methods=["POST"])
def save():
    mapping = {str(i): (request.form.get(f"slot_{i}") or None) for i in range(4)}
    mapping["external"] = request.form.get("slot_external") or None
    if any(v is not None and (not v.isdigit() or int(v) <= 0) for v in mapping.values()):
        return Response('Ungültige Spulen-ID.', 400)
    try:
        save_mapping(mapping)
    except InventoryError as exc:
        return Response(str(exc), 400, mimetype='text/plain')
    # Only fill assignments that were missing; retain all already recorded spool IDs.
    for job_id in store.recoverable_jobs():
        job = store.get_job(job_id)
        if job and job['state'] == 'WAITING_MAPPING':
            snapshot = json.loads(job['slot_mapping_json'] or '{}')
            for key, value in mapping.items():
                if not snapshot.get(key): snapshot[key] = value
            store.update_job(job_id, slot_mapping_json=json.dumps(snapshot))
            enqueue_job(job_id)
    log("AMS-Zuordnungen gespeichert.")
    return redirect(url_for("index"))

@app.route("/manual_deduct", methods=["POST"])
def manual_deduct():
    try:
        grams = float(request.form.get("grams", 0))
        spool_id = int(request.form.get("spool_id", 0))
        if not math.isfinite(grams) or not 0.01 <= grams <= 1000 or spool_id <= 0:
            return Response("Ungültige Eingabewerte.", 400)

        slot_mapping = load_mapping()
        tray_id = None
        for k, v in slot_mapping.items():
            if v == str(spool_id):
                tray_id = 254 if k == "external" else int(k)
                break

        job_id = store.create_manual_job(spool_id, tray_id, round(grams, 2))
        enqueue_job(job_id)
        log(f"Manuelle Buchung: {grams:.2f}g auf Spule #{spool_id} vorgemerkt.")
    except (TypeError, ValueError):
        return Response('Ungültige Eingabewerte.', 400)
    except Exception as exc:
        log(f"Fehler bei manueller Buchung: {exc}", logging.ERROR)
        return Response('Buchung konnte nicht vorgemerkt werden.', 500)
    return redirect(url_for("index"))

@app.route('/jobs/<job_id>/mapping', methods=['POST'])
def set_job_mapping(job_id):
    try:
        entries = {int(key.removeprefix('filament_')): int(value) for key, value in request.form.items()
                   if key.startswith('filament_')}
        if not entries or any(not 1 <= i <= 256 or tray not in (-1, 0, 1, 2, 3, 254) for i, tray in entries.items()):
            raise ValueError('Invalid mapping')
        mapping = [-1] * max(entries)
        for i, tray in entries.items():
            mapping[i - 1] = tray
    except (TypeError, ValueError):
        return Response('Ungültige Filamentzuordnung.', 400)
    with queued_lock:
        if job_id in queued_jobs or not store.set_job_mapping(job_id, mapping):
            return Response('Auftrag wird verarbeitet, ist unbekannt oder enthält bereits Buchungen.', 409)
    job = store.get_job(job_id)
    if job['state'] != 'RUNNING':
        enqueue_job(job_id)
    log(f'Filamentzuordnung für Auftrag {job_id[:8]} durch Benutzer bestätigt.')
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET', 'POST'])
def app_settings():
    global DRY_RUN, AUTO_MATCH_FILAMENTS
    session.setdefault('csrf_token', secrets.token_urlsafe(32))
    if request.method == 'POST':
        dry_run = request.form.get('dry_run') == 'on'
        auto_match = request.form.get('auto_match') == 'on'
        with inventory_connection(DB_PATH) as db:
            db.execute('BEGIN IMMEDIATE')
            for key, value in [('dry_run', dry_run), ('auto_match_filaments', auto_match)]:
                db.execute('INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at', (key, json.dumps(value), store.now()))
            db.commit()
        DRY_RUN, AUTO_MATCH_FILAMENTS = dry_run, auto_match
        return redirect('/settings?saved=1')
    return render_ui(SETTINGS_PAGE, active='settings', title='Einstellungen', backend=INVENTORY_BACKEND, token=session['csrf_token'], dry_run=DRY_RUN, auto_match=AUTO_MATCH_FILAMENTS, saved=request.args.get('saved') == '1')


def main():
    threading.Thread(target=job_worker, daemon=True).start()

    for job_id in store.recoverable_jobs(): enqueue_job(job_id)

    def mqtt_runner():
        global mqtt_client
        while True:
            client = None
            try:
                client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=f"bambu_bridge_{SERIAL[-6:]}", protocol=mqtt.MQTTv311)
                mqtt_client = client
                client.username_pw_set("bblp", ACCESS_CODE)
                client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
                client.tls_insecure_set(True)
                client.on_connect = on_connect
                client.on_disconnect = on_disconnect
                client.on_message = on_message
                client.connect(PRINTER_IP, 8883, keepalive=60)
                client.loop_forever(retry_first_connection=True)
            except Exception as exc:
                log(f"MQTT-Schleife: {exc}; neuer Versuch in 10 Sekunden.", logging.ERROR)
            finally:
                mqtt_client = None
                if client:
                    try: client.disconnect()
                    except: pass
            time.sleep(10)

    threading.Thread(target=mqtt_runner, daemon=True).start()

    def retry_worker():
        while True:
            time.sleep(60)
            try:
                for job_id in store.recoverable_jobs():
                    enqueue_job(job_id)
            except Exception:
                logger.exception("Wiederaufnahme fehlgeschlagen")
    threading.Thread(target=retry_worker, daemon=True).start()

    def heartbeat():
        while True:
            fetch_spoolman_spools()
            time.sleep(60)
            if mqtt_client and mqtt_client.is_connected():
                try: send_pushall(mqtt_client)
                except Exception: logger.exception('MQTT-Heartbeat fehlgeschlagen')
    threading.Thread(target=heartbeat, daemon=True).start()

    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()
