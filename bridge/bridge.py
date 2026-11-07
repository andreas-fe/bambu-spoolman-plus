#!/usr/bin/env python3
"""Bambu A1/A1 mini -> Spoolman bridge v2.1.0."""

from __future__ import annotations

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

APP_VERSION = "v2.2.0"
APP_BUILD_TIME = "07.11.2026"

PRINTER_IP = os.getenv("BAMBU_HOST", "").strip()
ACCESS_CODE = os.getenv("BAMBU_ACCESS_CODE", "").strip()
SERIAL = os.getenv("BAMBU_SERIAL", "").strip()
SPOOLMAN_URL = os.getenv("SPOOLMAN_URL", "http://spoolman:8000").rstrip("/")
SPOOLMAN_BROWSER_URL = os.getenv('SPOOLMAN_BROWSER_URL', SPOOLMAN_URL).rstrip('/')
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "7913"))
WEB_USERNAME = os.getenv("WEB_USERNAME", "").strip()
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", "/app/data/bridge.sqlite3"))
MAPPING_FILE = Path(os.getenv("MAPPING_FILE", "/app/data/mapping.json"))
MAX_3MF_BYTES = int(os.getenv("MAX_3MF_BYTES", str(200 * 1024 * 1024)))
MAX_XML_BYTES = int(os.getenv("MAX_XML_BYTES", str(2 * 1024 * 1024)))
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}

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
            for name, kind in [('slot_mapping_json', 'TEXT'), ('plate_index', 'INTEGER'), ('dry_run', 'INTEGER')]:
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
                con.execute("UPDATE jobs SET state='INTERRUPTED', error=?, updated_at=? WHERE id=?",
                            ("Neuer Dateiname vor FINISH erkannt.", now, job_id))
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
                con.execute('UPDATE jobs SET slot_mapping_json=?, dry_run=? WHERE id=?',
                            (json.dumps(load_mapping()), int(DRY_RUN), job_id))
                con.execute("UPDATE runtime SET value=? WHERE key='active_job_id'", (job_id,))
                log(f"Neuer Druckauftrag: {filename or subtask_name or job_id}")
            else:
                updates = ["updated_at=?", "state='RUNNING'"]
                values: list[Any] = [now]
                if filename: updates.append("filename=?"); values.append(filename)
                if subtask_name: updates.append("subtask_name=?"); values.append(subtask_name)
                if ams_mapping is not None: updates.append("ams_mapping_json=?"); values.append(json.dumps(ams_mapping))
                if ams_mapping2 is not None: updates.append("ams_mapping2_json=?"); values.append(json.dumps(ams_mapping2))
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
            if filename: fields.append("filename=?"); values.append(filename)
            if subtask_name: fields.append("subtask_name=?"); values.append(subtask_name)
            if ams_mapping is not None: fields.append("ams_mapping_json=?"); values.append(json.dumps(ams_mapping))
            if ams_mapping2 is not None: fields.append("ams_mapping2_json=?"); values.append(json.dumps(ams_mapping2))
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
            con.execute("UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?", (final_state, detail, now, job_id))
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
            return [dict(r) for r in con.execute(
                "SELECT d.*, j.filename, j.subtask_name, j.created_at AS job_created_at "
                "FROM deductions d JOIN jobs j ON j.id=d.job_id ORDER BY d.id DESC LIMIT ?", (limit,)
            ).fetchall()]

    def recoverable_jobs(self) -> list[str]:
        with self.connection() as con:
            return [str(r["id"]) for r in con.execute(
                "SELECT id FROM jobs WHERE state IN "
                "('FINISH_PENDING','WAITING_METADATA','WAITING_MAPPING','MANUAL_PENDING','BOOKING','RETRY') "
                "OR EXISTS (SELECT 1 FROM deductions d WHERE d.job_id=jobs.id AND d.status IN ('PENDING','RETRY','UNMAPPED')) "
                "AND jobs.state NOT IN ('DRY_RUN','FAILED','INTERRUPTED') ORDER BY created_at").fetchall()]

    def dry_run_jobs(self) -> list[str]:
        with self.connection() as con:
            return [str(r["id"]) for r in con.execute("SELECT id FROM jobs WHERE state='DRY_RUN' ORDER BY created_at").fetchall()]

store = Store(DB_PATH)

# ---------------------------------------------------------------------------
# Mapping Persistenz
# ---------------------------------------------------------------------------

DEFAULT_MAPPING = {**{str(i): None for i in range(4)}, "external": None}

def load_mapping() -> dict[str, str | None]:
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

def parse_slice_info_config(xml_content: bytes, plate_index: int | None = None) -> dict[int, float]:
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
    return {k: v for k, v in weights.items() if v > 0}

def extract_weights_via_ftps(filename: str, plate_index: int | None = None) -> dict[int, float]:
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
                    return parse_slice_info_config(archive.read(item), plate_index)
    except Exception as exc:
        log(f"XML Parsing Fehler: {exc}", logging.ERROR)
    return {}

# ---------------------------------------------------------------------------
# Spoolman API Client
# ---------------------------------------------------------------------------

spoolman_status = {'connected': None, 'error': ''}

def fetch_spoolman_spools() -> list[dict[str, Any]]:
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
        if 200 <= r.status_code < 300: return "DONE", f"HTTP {r.status_code}"
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
    for row in store.get_pending_deductions(job_id):
        if simulate:
            store.set_deduction_status(row["id"], "DRY_RUN", "Nur simuliert; keine spätere automatische Nachbuchung")
            log(f"[DRY-RUN] {row['grams']}g von Spule #{row['spool_id']}")
        elif store.claim_deduction(row["id"]):
            status, detail = send_spoolman_deduction(row)
            store.set_deduction_status(row["id"], status, detail)
            log(f"Buchung #{row['id']}: {row['grams']}g, Spule #{row['spool_id']}: {status}")
    settle_job(job_id)


def process_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if not job or job["state"] in ("RUNNING", "PREPARING", "FAILED", "INTERRUPTED", "DRY_RUN"):
        return
    if job["kind"] == "MANUAL":
        book_pending(job_id)
        return

    weights = {}
    if job.get("weights_json"):
        weights = {int(k): float(v) for k, v in json.loads(job["weights_json"]).items()}
    if not weights:
        weights = extract_weights_via_ftps(job.get("filename") or job.get("subtask_name", ""), job.get("plate_index"))
        if not weights:
            store.update_job(job_id, state="WAITING_METADATA", error="Warte auf eindeutige 3MF-Verbrauchsdaten.")
            return
        store.update_job(job_id, weights_json=json.dumps(weights))
    if any(k < 1 or not math.isfinite(v) or v <= 0 for k, v in weights.items()):
        store.update_job(job_id, state="REJECTED", error="Ungültige Verbrauchsdaten.")
        return

    raw_mapping = json.loads(job["ams_mapping_json"]) if job.get("ams_mapping_json") else None
    raw_mapping2 = json.loads(job["ams_mapping2_json"]) if job.get("ams_mapping2_json") else None
    # Freeze spool assignments for the job so a later spool change cannot redirect retries.
    if job.get("slot_mapping_json"):
        slot_mapping = json.loads(job["slot_mapping_json"])
    else:
        slot_mapping = load_mapping()
        store.update_job(job_id, slot_mapping_json=json.dumps(slot_mapping))

    for f_id, grams in sorted(weights.items()):
        tray_id = resolve_tray_for_filament(f_id, raw_mapping, raw_mapping2)
        slot_key = "external" if tray_id == 254 else (str(tray_id) if tray_id is not None else None)
        spool_id = slot_mapping.get(slot_key) if slot_key is not None else None
        if not spool_id:
            store.upsert_deduction(job_id, f"filament:{f_id}", f_id, tray_id, None, grams, "UNMAPPED", "AMS-Mapping oder Spulenzuordnung fehlt")
        else:
            store.upsert_deduction(job_id, f"filament:{f_id}", f_id, tray_id, int(spool_id), grams, "PENDING")
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

ams_state = {i: {"type": "-", "color": "#888888", "remain": -1} for i in range(4)}
printer_status = {"state": "OFFLINE", "progress": 0, "subtask_name": "", "active_slot": None, "active_slot_label": "Kein Slot erkannt"}
mqtt_client: mqtt.Client | None = None
seq_counter = 1

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
    global seq_counter
    seq_counter += 1
    client.publish(f"device/{SERIAL}/request", json.dumps({"pushing": {"command": "pushall", "sequence_id": str(seq_counter)}}), qos=1)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        p = data.get("print") or {}
        if not p: return

        ams_data = p.get("ams") or {}
        if isinstance(ams_data, dict):
            for unit in ams_data.get("ams", []):
                for tray in unit.get("tray", []):
                    tid = int(tray.get("id", -1))
                    if tid in range(4):
                        c = str(tray.get("tray_color") or "888888")[:6]
                        ams_state[tid] = {
                            "type": tray.get("tray_sub_brands") or tray.get("tray_type") or "Generic",
                            "color": f"#{c}",
                            "remain": tray.get("remain", -1),
                        }

        ams_map = p.get("ams_mapping") or (ams_data.get("ams_mapping") if isinstance(ams_data, dict) else None)
        ams_map2 = p.get("ams_mapping2") or (ams_data.get("ams_mapping2") if isinstance(ams_data, dict) else None)

        raw_tray = ams_data.get("tray_now", p.get("tray_now")) if isinstance(ams_data, dict) else p.get("tray_now")
        try: rt = int(raw_tray) if raw_tray is not None else None
        except: rt = 255

        if rt in range(4):
            printer_status["active_slot"] = rt
            printer_status["active_slot_label"] = f"AMS Slot {rt + 1}"
        elif rt == 254:
            printer_status["active_slot"] = 254
            printer_status["active_slot_label"] = "Externe Spule"
        elif rt == 255:
            printer_status["active_slot"] = None
            printer_status["active_slot_label"] = "Kein Slot erkannt"

        state = str(p.get("gcode_state") or "").upper()
        filename = str(p.get("gcode_file") or "")
        subtask = str(p.get("subtask_name") or "")

        if state:
            printer_status["state"] = state
        if 'mc_percent' in p:
            printer_status["progress"] = int(float(p['mc_percent'] or 0))
        if subtask: printer_status["subtask_name"] = subtask

        if state in ("RUNNING", "PREPARE"):
            store.create_or_update_running_job(filename, subtask, ams_map, ams_map2)
        # Mapping and file metadata can arrive in MQTT delta messages without a state.
        active_id = store.active_job_id()
        if active_id:
            fields = {}
            if filename: fields['filename'] = filename
            if subtask: fields['subtask_name'] = subtask
            if ams_map is not None: fields['ams_mapping_json'] = json.dumps(ams_map)
            if ams_map2 is not None: fields['ams_mapping2_json'] = json.dumps(ams_map2)
            plate = re.search(r'plate_(\d+)\.gcode', str(p.get('param', '')))
            if plate: fields['plate_index'] = int(plate.group(1))
            if fields: store.update_job(active_id, **fields)
        if state == "FINISH":
            j_id = store.finish_active_job(filename, subtask, ams_map, ams_map2)
            if j_id: enqueue_job(j_id)
        elif state in ("FAILED", "CANCEL", "CANCELLED", "CANCELED", "STOPPED", "ERROR"):
            store.close_active_job("FAILED", f"Status: {state}")
        elif state == "IDLE":
            store.close_active_job("INTERRUPTED", "Unerwartet IDLE vor FINISH")
    except Exception as exc:
        logger.exception("MQTT Fehler")

# ---------------------------------------------------------------------------
# Flask Web Interface
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

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
            return Response('Ungültiges Formular. Seite neu laden.', 403)

HTML_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bambu → Spoolman Bridge {{ version }}</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;padding:22px;background:#17191d;color:#e8eaed;font:14px system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1050px;margin:auto}.card{background:#242830;border:1px solid #353b46;border-radius:12px;padding:18px;margin:0 0 16px;box-shadow:0 4px 15px #0002}
h1{font-size:22px;margin:0;color:#86efac}h2{font-size:16px;margin:0 0 12px}.muted{color:#aab2bf}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
a{color:#93c5fd;text-decoration:none}a:hover{text-decoration:underline}.status{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-top:15px;padding:12px;background:#191d24;border-radius:8px}
.pill{padding:4px 9px;border-radius:20px;background:#374151;font-weight:700}.warn-banner{background:#854d0e;color:#fef08a;padding:12px 16px;border-radius:8px;font-weight:bold;margin-bottom:16px;border:1px solid #ca8a04}
.slot{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.2fr);gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid #3a404b}.slot:last-child{border:0}
.swatch{display:inline-block;width:15px;height:15px;border-radius:50%;border:1px solid #ddd;vertical-align:middle;margin-right:8px}select,input{width:100%;background:#171b22;color:#eee;border:1px solid #4b5563;border-radius:6px;padding:9px}
button{background:#16a34a;color:white;border:0;padding:9px 14px;border-radius:7px;font-weight:650;cursor:pointer}button:hover{background:#15803d}.secondary{background:#374151}.secondary:hover{background:#4b5563}
.manual-grid{display:grid;grid-template-columns:minmax(240px,1.5fr) 120px auto;gap:10px;align-items:end}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #3a404b}th{color:#aab2bf;font-size:12px}
.logs{height:220px;overflow:auto;background:#111419;border:1px solid #343b46;border-radius:8px;padding:10px;font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap}.small{font-size:12px}
</style>
</head><body><main>

{% if dry_run %}
<div class="warn-banner">
  ⚠️ DRY-RUN AKTIV: Alle Verbräuche werden im Journal simuliert und NICHT in Spoolman abgebucht. (Echtbetrieb: DRY_RUN=false in .env)
</div>
{% endif %}

<section class="card">
  <div class="top">
    <div>
      <h1>Bambu Lab → Spoolman Bridge <span style="font-size:14px;color:#aab2bf;font-weight:normal;">{{ version }} (Stand: {{ build_time }})</span></h1>
      <div class="muted">A1 / A1 mini · AMS Lite · FINISH-only Verbuchung</div>
    </div>
    <div>
      <button class="secondary" onclick="refreshStatus()">Aktualisieren</button>
      <a href="{{ spoolman_url }}" target="_blank" rel="noopener" style="margin-left:8px;">Spoolman öffnen ↗</a>
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
  <h2>AMS Lite Zuordnung</h2>
  <form method="post" action="/save">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    {% for slot_id in range(4) %}
      <div class="slot">
        <div>
          <span class="swatch" style="background:{{ ams[slot_id].color }}"></span>
          <strong>AMS Slot {{ slot_id + 1 }}</strong>
          <div class="muted small">{{ ams[slot_id].type }} · Rest: {{ ams[slot_id].remain }}%</div>
        </div>
        <select name="slot_{{ slot_id }}">
          <option value="">— Keine Spoolman-Spule —</option>
          {% for s in spools %}
            <option value="{{ s.id }}" {% if mapping.get(slot_id|string) == s.id|string %}selected{% endif %}>
              #{{ s.id }} · {{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g
            </option>
          {% endfor %}
        </select>
      </div>
    {% endfor %}
    <div class="slot">
      <div><strong>Externe Spule</strong><div class="muted small">Spulenhalter / vt_tray</div></div>
      <select name="slot_external">
        <option value="">— Keine Spoolman-Spule —</option>
        {% for s in spools %}
          <option value="{{ s.id }}" {% if mapping.get('external') == s.id|string %}selected{% endif %}>
            #{{ s.id }} · {{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g
          </option>
        {% endfor %}
      </select>
    </div>
    <div style="text-align:right;margin-top:12px;"><button type="submit">Zuordnung speichern</button></div>
  </form>
</section>

<section class="card">
  <h2>Manuelle Abbuchung</h2>
  <form method="post" action="/manual_deduct" class="manual-grid">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div>
      <label class="muted small">Spule auswählen</label>
      <select name="spool_id" required>
        <option value="">— Spule wählen —</option>
        {% for s in spools %}
          <option value="{{ s.id }}">#{{ s.id }} · {{ s.filament.name if s.filament else 'Unbekannt' }} ({{ s.material }}) · {{ (s.remaining_weight|round|int) if s.remaining_weight is not none else '?' }} g</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="muted small">Menge (Gramm)</label>
      <input type="number" step="0.01" min="0.01" max="1000" name="grams" placeholder="Gramm" required>
    </div>
    <button type="submit" style="background:#2563eb;">Jetzt buchen</button>
  </form>
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
            <td><strong>{{ d.status }}</strong></td>
            <td class="muted small">{{ d.detail }}</td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">Noch keine Buchungen erfasst.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="card">
  <h2>Live-Logs</h2>
  <div class="logs" id="logs">{% for line in logs %}{{ line }}
{% endfor %}</div>
</section>
</main>

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
   cell(tr,d.status);
   cell(tr,d.detail);
   tbody.appendChild(tr);
 });
});}
setInterval(refreshStatus,5000);
</script>
</body></html>"""

@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "bambu-spoolman-bridge", "version": APP_VERSION, "dry_run": DRY_RUN})

@app.route("/")
def index():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template_string(
        HTML_TEMPLATE,
        version=APP_VERSION,
        build_time=APP_BUILD_TIME,
        csrf_token=session["csrf_token"],
        printer=printer_status,
        spoolman=spoolman_status,
        ams=ams_state,
        mapping=load_mapping(),
        spools=fetch_spoolman_spools(),
        deductions=store.list_deductions(30),
        logs=list(log_buffer),
        spoolman_url=SPOOLMAN_BROWSER_URL,
        dry_run=DRY_RUN,
    )

@app.route("/api/status")
def status_api():
    with log_lock: logs = list(log_buffer)
    return jsonify({
        "printer": printer_status,
        "spoolman": spoolman_status,
        "jobs": store.list_jobs(),
        "dry_run": DRY_RUN,
        "deductions": store.list_deductions(30),
        "logs": logs,
    })

@app.route("/save", methods=["POST"])
def save():
    mapping = {str(i): (request.form.get(f"slot_{i}") or None) for i in range(4)}
    mapping["external"] = request.form.get("slot_external") or None
    if any(v is not None and (not v.isdigit() or int(v) <= 0) for v in mapping.values()):
        return Response('Ungültige Spulen-ID.', 400)
    save_mapping(mapping)
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
