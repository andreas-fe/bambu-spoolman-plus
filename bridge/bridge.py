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
from bridge.printer_telemetry import PrinterTelemetry, AMSCommandRejected
from bridge.ui import render_ui, SETTINGS_PAGE
from bridge.ams_ui import AMS_PANEL
from bridge.device_visuals import A1_VISUAL
from bridge.materials import materials_match, material_family, MATERIAL_FAMILY_PATTERN
from bridge.ams_control import filament_payload, slot_write_reason
from bridge.pressure_advance import calibration_choice
from bridge.ams_selection import selection_fingerprint, spool_matches
from bridge.auto_power import AutoPowerOff
from itsdangerous import URLSafeTimedSerializer, BadSignature
import hashlib

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
from zoneinfo import ZoneInfo
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
_console.setFormatter(logging.Formatter("[Bridge] %(asctime)s %(levelname)s: %(message)s", "%d.%m.%Y %H:%M:%S"))
logger.addHandler(_console)
_buffer = BufferLogHandler()
_buffer.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%d.%m.%Y %H:%M:%S"))
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
            con.execute("PRAGMA journal_mode=WAL")
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
                option = con.execute("SELECT value_json FROM settings WHERE key='flow_calibration_allowance'").fetchone()
                con.execute("INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?)",
                            ('flow_allowance_job:'+job_id, option['value_json'] if option else 'false', now))
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
        """Confirm selected trays and fill only missing role bindings for this job."""
        current_slots = load_mapping()
        current_stock = stock_snapshot(fetch_spoolman_spools())
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['kind'] != 'PRINT' or job['state'] not in ('RUNNING', 'FINISH_PENDING', 'WAITING_METADATA', 'WAITING_MAPPING'):
                return False
            if con.execute("SELECT 1 FROM deductions WHERE job_id=? AND (status NOT IN ('UNMAPPED','PENDING') OR attempts>0)", (job_id,)).fetchone():
                return False
            guard=con.execute('SELECT value FROM runtime WHERE key=?',('ams-job-review:'+job_id,)).fetchone()
            if guard:
                slots=json.loads(job['slot_mapping_json'] or '{}')
                for slot,change in json.loads(guard['value']).items():
                    slots[slot]=change['after']
                con.execute('UPDATE jobs SET slot_mapping_json=?,error=?,estimated=1,estimate_reason=? WHERE id=?',(json.dumps(slots),'','Rollenwechsel während Druck: manuell bestätigte Zuordnung; rollenbezogene Teilmengen nicht gemessen.',job_id))
                con.execute('DELETE FROM runtime WHERE key=?',('ams-job-review:'+job_id,))
            # A late tray confirmation must also resolve an originally empty
            # tray-to-role snapshot. Preserve every previously captured role.
            refreshed = con.execute('SELECT slot_mapping_json FROM jobs WHERE id=?', (job_id,)).fetchone()
            slots = json.loads(refreshed['slot_mapping_json'] or '{}')
            stock = json.loads(job['spools_snapshot_json'] or '{}')
            for tray in mapping:
                key = 'external' if tray == 254 else str(tray)
                if tray in (*range(4), 254) and not slots.get(key) and current_slots.get(key):
                    slots[key] = current_slots[key]
                    sid = str(slots[key])
                    if sid not in stock and sid in current_stock:
                        stock[sid] = current_stock[sid]
            con.execute('UPDATE jobs SET slot_mapping_json=?,spools_snapshot_json=?,error=? WHERE id=?',
                        (json.dumps(slots),json.dumps(stock),'',job_id))
            con.execute('DELETE FROM deductions WHERE job_id=?', (job_id,))
            con.execute("UPDATE jobs SET ams_mapping_json=?, ams_mapping2_json=NULL, mapping_source='USER', updated_at=? WHERE id=?",
                        (json.dumps(mapping), self.now(), job_id))
            return True

    def close_without_usage(self, job_id: str) -> bool:
        """Close an ended, unbooked metadata-less print without inventing usage."""
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            job=con.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            active=con.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            if not job or job['kind']!='PRINT' or job['state']!='WAITING_METADATA' or (active and active['value']==job_id):return False
            try:
                if json.loads(job['weights_json'] or '{}'):return False
            except (ValueError,TypeError):return False
            if con.execute('SELECT 1 FROM deductions WHERE job_id=?',(job_id,)).fetchone():return False
            if con.execute('SELECT 1 FROM consumption_entries WHERE job_id=?',(job_id,)).fetchone():return False
            if con.execute('SELECT 1 FROM inventory_job_plans WHERE job_id=?',(job_id,)).fetchone():return False
            note='Ohne Verbrauchsbuchung manuell geschlossen: Keine belastbaren Verbrauchsdaten vorhanden. Ein möglicher Materialverbrauch ist damit nicht erfasst.'
            con.execute('UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?',('CLOSED_NO_USAGE',note,self.now(),job_id))
            con.execute('DELETE FROM runtime WHERE key=?',('ams-job-review:'+job_id,))
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

    def list_deductions(self, limit: int = 30, before=None) -> list[dict[str, Any]]:
        # Page the combined journal before loading details: bounded work even
        # when years of consumption and correction records are retained.
        limit = max(1,min(int(limit),101))
        with self.connection() as con:
            con.execute('BEGIN')
            sql = """SELECT created_at,kind,id FROM (
                SELECT created_at,'deduction' AS kind,id FROM deductions
                UNION ALL
                SELECT created_at,'adjustment' AS kind,id FROM inventory_movements WHERE movement_type='adjustment'
                UNION ALL
                SELECT updated_at AS created_at,'closed' AS kind,rowid AS id FROM jobs WHERE state='CLOSED_NO_USAGE'
            )"""
            params=[]
            if before is not None:
                sql+=' WHERE (created_at,kind,id)<(?,?,?)'
                params.extend(before)
            sql+=' ORDER BY created_at DESC,kind DESC,id DESC LIMIT ?'
            keys=con.execute(sql,(*params,limit)).fetchall()
            ids=[r['id'] for r in keys if r['kind']=='deduction']
            correction_ids=[r['id'] for r in keys if r['kind']=='adjustment']
            closed_ids=[r['id'] for r in keys if r['kind']=='closed']
            marks=','.join('?' for _ in ids) or 'NULL'
            rows=[dict(r) for r in con.execute(
                "SELECT d.*,j.filename,j.subtask_name,j.created_at AS job_created_at,j.mapping_source,j.estimated,"
                "j.cancelled,j.progress AS job_progress,j.estimate_reason FROM deductions d JOIN jobs j ON j.id=d.job_id "
                f"WHERE d.id IN ({marks})",ids).fetchall()]
            correction_marks=','.join('?' for _ in correction_ids) or 'NULL'
            corrections=con.execute(f"SELECT * FROM inventory_movements WHERE id IN ({correction_marks})",correction_ids).fetchall()
            closed_marks=','.join('?' for _ in closed_ids) or 'NULL'
            closed_jobs=con.execute(f"SELECT rowid AS journal_id,* FROM jobs WHERE rowid IN ({closed_marks})",closed_ids).fetchall()
        for row in rows:
            row['journal_key']=[row['created_at'],'deduction',row['id']]
        for row in rows:
            if row['status'] == 'DONE':
                row['detail'] = re.sub(r'^HTTP 2\d\d(?=;|$)', 'Buchung durch Spoolman bestätigt', row['detail'])
                if row['cancelled']:
                    row['detail'] = f"Teilmenge bis Abbruch gebucht (Schätzung bei {row['job_progress']:g}% Fortschritt). " + row['detail']
            elif row['cancelled']:
                row['detail'] = 'Abgebrochener Druck · geschätzte Teilmenge. ' + row['detail']
        # Inventory corrections belong in the visible journal, but never in
        # consumption totals or the deduction worker's executable queue.
        for correction in corrections:
            data=dict(correction);before=data['before_weight_g'];after=data['after_weight_g'];note=data['note'] or ''
            weighed=note.startswith('Wiegekorrektur: ')
            title='Wiegekorrektur' if weighed else 'Bestandskorrektur'
            previous='unbekannt' if before is None else f'{before:g} g'
            current='unbekannt' if after is None else f'{after:g} g'
            detail=f'Bestand vorher: {previous}; danach: {current}.'
            if weighed:
                try:
                    measured=json.loads(note.removeprefix('Wiegekorrektur: '))
                    detail=f"Gewogen: {measured['gross_g']:g} g − Leerspule: {measured['tare_g']:g} g. "+detail
                    if measured.get('note'): detail+=' '+measured['note']
                except (ValueError,KeyError,TypeError):
                    detail+=' '+note
            elif note:
                detail+=' '+note
            rows.append(dict(journal_key=[data['created_at'],'adjustment',data['id']],id='adjustment:'+str(data['id']),entry_type='adjustment',created_at=data['created_at'],job_created_at=data['created_at'],
                             job_id='adjustment:'+str(data['id']),filename=title,subtask_name=title,tray_id=None,spool_id=data['spool_id'],
                             grams=data['delta_g'],amount_label=(current+' Bestand') if weighed else f"{data['delta_g']:+g} g",
                             status='CORRECTED',status_label='Korrigiert',estimated=0,cancelled=0,detail=detail))
        for job in closed_jobs:
            rows.append(dict(journal_key=[job['updated_at'],'closed',job['journal_id']],id='closed:'+job['id'],entry_type='closed_without_usage',
                             created_at=job['updated_at'],job_id=job['id'],filename=job['filename'],subtask_name=job['subtask_name'],
                             tray_id=None,spool_id=None,grams=None,amount_label='—',status='CLOSED_NO_USAGE',
                             status_label='Ohne Buchung geschlossen',estimated=0,cancelled=0,detail=job['error']))
        return sorted(rows,key=lambda row:tuple(row['journal_key']),reverse=True)

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
with inventory_connection(DB_PATH) as db:
    db.execute('CREATE INDEX IF NOT EXISTS jobs_closed_journal_idx ON jobs(state,updated_at DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS deductions_journal_time_idx ON deductions(created_at DESC,id DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS movements_journal_time_idx ON inventory_movements(movement_type,created_at DESC,id DESC)')
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
                result = inventory_service.apply_ledger_deduction(row['id'])
                if result.get('applied'):
                    label = 'Teilmenge bis Abbruch gebucht (Schätzung)' if job.get('cancelled') else 'Verbrauch gebucht'
                    log(f"{label}: {row['grams']:g} g, Spule #{row['spool_id']}.")
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
        if job.get('flow_calibration_allowance'):
            total += 200
            reasons.append('Flusskontrolle: pauschal 2 g je verwendeter Filamentfarbe zusätzlich (Schätzung)')
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
                        and materials_match(tray.get('material'), filament.get('material'))):
                    if slot not in candidates: candidates.append(slot)
            if primary is None and candidates: sources.add('AUTO_COLOR_MATERIAL')
        matched_slots = list(candidates)
        candidates = [slot for slot in candidates if slots.get('external' if slot == 254 else str(slot))]
        if not candidates:
            color = normalized_color(filament.get('color'))
            material = str(filament.get('material') or '').strip()
            if matched_slots:
                detail = 'Passender AMS-Slot hat keine Bestandsrolle im Auftrag. Bitte aktuelle Rolle zuordnen und die Auftragszuordnung erneut bestätigen.'
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
    if not job or job['state'] in ('PREPARING', 'FAILED', 'INTERRUPTED', 'DRY_RUN', 'CANCELLED_ZERO', 'CLOSED_NO_USAGE'):
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
    with inventory_connection(DB_PATH) as db:
        needs_review=db.execute('SELECT 1 FROM runtime WHERE key=?',('ams-job-review:'+job_id,)).fetchone()
    if needs_review:
        store.update_job(job_id,state='WAITING_MAPPING',error='Bestandsrolle in einem für diesen Druck relevanten Schacht während des Drucks geändert. Bitte die Zuordnung für diesen Auftrag prüfen und bestätigen; noch keine Abbuchung.')
        return
    if not weights:
        store.update_job(job_id, state='WAITING_METADATA', error='Warte auf eindeutige 3MF-Verbrauchsdaten.')
        return
    if any(k < 1 or not math.isfinite(v) or v <= 0 for k,v in weights.items()):
        store.update_job(job_id, state='REJECTED', error='Ungültige Verbrauchsdaten.')
        return
    slots = json.loads(job.get('slot_mapping_json') or json.dumps(load_mapping()))
    if not job.get('slot_mapping_json'): store.update_job(job_id, slot_mapping_json=json.dumps(slots))
    stock = json.loads(job.get('spools_snapshot_json') or '{}')
    with inventory_connection(DB_PATH) as db:
        allowance = db.execute('SELECT value_json FROM settings WHERE key=?', ('flow_allowance_job:'+job_id,)).fetchone()
    job['flow_calibration_allowance'] = bool(allowance and json.loads(allowance['value_json']) is True)
    rows, source, reason = plan_consumption(job, weights, filaments, slots, stock)
    if 'Rollenwechsel während Druck' in (job.get('estimate_reason') or ''):
        reason=(reason+'; ' if reason else '')+job['estimate_reason']
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

def record_ams_changes():
    """Release stale current assignments, never modify captured print snapshots."""
    if INVENTORY_BACKEND != 'internal':
        return
    key = 'ams_fingerprints:' + SERIAL
    with inventory_connection(DB_PATH) as db:
        row = db.execute('SELECT value FROM runtime WHERE key=?', (key,)).fetchone()
        previous = json.loads(row['value']) if row and row['value'] else {}
        signatures = {str(slot): [bool(tray.get('available')), str(tray.get('material') or '').upper(), normalized_color(tray.get('color'))]
                      for slot,tray in ams_state.items() if tray.get('known')}
        if all(previous.get(slot) == signature for slot,signature in signatures.items()):
            return
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM runtime WHERE key=?', (key,)).fetchone()
        previous = json.loads(row['value']) if row and row['value'] else {}
        current = dict(previous)
        changed = []
        for slot, tray in ams_state.items():
            if not tray.get('known'):
                continue
            signature = [bool(tray.get('available')), str(tray.get('material') or '').upper(), normalized_color(tray.get('color'))]
            name = str(slot)
            if name in previous and previous[name] != signature:
                db.execute('UPDATE ams_assignments SET released_at=? WHERE printer_id=? AND slot_id=? AND released_at IS NULL', (store.now(), SERIAL, name))
                changed.append(slot)
            current[name] = signature
        if current != previous:
            db.execute('INSERT INTO runtime(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,json.dumps(current)))
        db.commit()
    for slot in changed:
        log(f'AMS Slot {slot+1}: Wechsel von Material/Farbe oder Belegung erkannt. Spule für künftige Aufträge neu zuordnen.', logging.WARNING)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        p = data.get("print") or {}
        if not p: return

        state, filename, subtask, ams_map, ams_map2 = telemetry.update(p, normalized_color)
        record_ams_changes()

        if state in ("RUNNING", "PREPARE"):
            previous = store.active_job_id()
            started = store.create_or_update_running_job(filename, subtask, ams_map, ams_map2)
            if started != previous:
                if 'mc_percent' not in p: printer_status['progress'] = 0
                if 'layer_num' not in p: printer_status['layer'] = 0
                if 'stg_cur' not in p: printer_status['stage'] = None
                if 'mc_remaining_time' not in p:
                    printer_status.update(remaining_minutes=None, remaining_seen_at=0)
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


@app.template_filter('date_time')
def display_date_time(value):
    if not value:
        return '—'
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo('Europe/Berlin')).strftime('%d.%m.%Y %H:%M:%S')
    except (ValueError, TypeError):
        return value
@app.template_filter('remaining_time')
def display_remaining_time(printer):
    if printer.get('state') == 'FINISH':
        return 'Abgeschlossen'
    if printer.get('state') not in ('RUNNING','PREPARE','PAUSE'):
        return '—'
    minutes = printer.get('remaining_minutes')
    if minutes is None or not 0 <= time.time()-printer.get('remaining_seen_at',0) <= 180:
        return 'Warte auf Zeitangabe'
    hours,rest = divmod(int(minutes),60)
    text = f'{hours} Std. {rest} Min.' if hours else f'{rest} Min.' if rest else '< 1 Min.'
    return ('Pausiert · noch ' if printer.get('state')=='PAUSE' else 'ca. ') + text


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


register_devices(app, PRINTER_IP, ACCESS_CODE, printer_status, set_printer_light, refresh_printer_status,
                 shutdown_job_guard=lambda job_id:auto_power.permitted(job_id))


def auto_power_safe(fresh=False):
    from bridge.devices import safe_to_power_off
    if INVENTORY_BACKEND!='internal' or DRY_RUN or not mqtt_client or not mqtt_client.is_connected():
        return False
    if fresh:
        sequence=telemetry.state_sequence
        send_pushall(mqtt_client)
        if not telemetry.wait_for_state(sequence,timeout=6):return False
    if fresh:
        return safe_to_power_off(printer_status)
    # A minute-based heartbeat must not restart a two-minute countdown every
    # thirty seconds. The final challenge and Tapo worker still demand fresh
    # explicit idle reports immediately before switching.
    return printer_status.get('state') in ('IDLE','FINISH') and 0<=time.time()-printer_status.get('state_seen_at',0)<=180


def auto_power_switch(job_id):
    from bridge.devices import TapoSwitch
    return TapoSwitch().run(False,lambda:auto_power.permitted(job_id),expected_job_id=job_id)


auto_power=AutoPowerOff(DB_PATH,auto_power_safe,auto_power_switch,log=log,
                       connected=lambda:bool(mqtt_client and mqtt_client.is_connected()))


@app.post('/settings/auto-power-off')
def configure_auto_power():
    try:
        enabled=request.form.get('enabled')=='on'
        if enabled and (INVENTORY_BACKEND!='internal' or DRY_RUN or not (os.getenv('TAPO_HOST') or os.getenv('TAPO_HOST_FILE'))):
            raise ValueError('Automatisches Ausschalten benötigt Produktivbetrieb und eine konfigurierte Steckdose.')
        auto_power.configure(enabled,request.form.get('job_id'),int(request.form.get('delay','120')))
        return redirect('/#auto-power-off')
    except (ValueError,KeyError) as exc:
        from bridge.ui import render_form_error
        return render_form_error(str(exc))

ams_write_lock = threading.Lock()


class AMSSelectionChanged(ValueError):
    pass


def ams_review(slot, spool_id):
    from bridge.devices import safe_to_power_off
    if slot not in ('0','1','2','3','external'):
        raise ValueError('Ungültiger Schacht.')
    spool = next((s for s in fetch_spoolman_spools() if str(s['id'])==str(spool_id)),None) if spool_id else None
    if spool_id and spool is None:
        raise ValueError('Diese Bestandsspule ist nicht mehr verfügbar. Bitte neu auswählen.')
    tray = ams_state[int(slot)] if slot != 'external' else {}
    payload,error = None,None
    if slot == 'external':
        error = 'Externe Spulen werden hier nur im Bestand zugeordnet.'
    elif not spool:
        error = 'Bitte eine Bestandsspule auswählen.'
    elif not tray.get('known') or not tray.get('available'):
        error = 'Im Schacht ist noch kein Filament bestätigt. Bitte Rolle einlegen und Status aktualisieren.'
    else:
        try:
            chosen=request.values.get('calibration','-1')
            known_profile=next((p for p in printer_status.get('calibration_profiles',[]) if str(p['cali_idx'])==chosen),None)
            payload = filament_payload(int(slot),spool,ams_state,known_profile['filament_id'] if known_profile else None)
            if request.values.get('mode')=='write' or request.form.get('confirm')=='write':
                payload['calibration'] = calibration_choice(request.values.get('calibration','-1'),printer_status,payload)
        except ValueError as exc: error = str(exc)
    policy = slot_write_reason(printer_status,int(slot),bool(mqtt_client and mqtt_client.is_connected())) if slot!='external' else None
    reason = error or policy
    return dict(spool=spool,tray=tray,payload=payload,matches=spool_matches(spool,tray),
                write_allowed=not reason,write_reason=reason,
                fingerprint=selection_fingerprint(slot,spool,tray,load_mapping(),payload))


def ams_signer():
    return URLSafeTimedSerializer(app.secret_key,salt='ams-selection-v1')


def ams_validate_review(slot, spool_id, mode):
    try:
        signed = ams_signer().loads(request.form.get('review_token',''),max_age=120)
    except BadSignature:
        raise AMSSelectionChanged('Die Vorschau ist abgelaufen oder ungültig. Bitte die Auswahl erneut prüfen.')
    current = ams_review(slot,spool_id)
    if mode=='assign': current['fingerprint']['payload']=None
    csrf_hash = hashlib.sha256(session.get('csrf_token','').encode()).hexdigest()
    if signed.get('session')!=csrf_hash or signed.get('mode')!=mode or signed.get('fingerprint')!=current['fingerprint']:
        raise AMSSelectionChanged('Druckerangaben, Bestand oder Zuordnung haben sich geändert. Bitte erneut prüfen.')
    if mode=='write' and not current['write_allowed']:
        raise AMSSelectionChanged(current['write_reason'])
    if mode=='assign' and slot!='external' and current['spool'] and not current['matches'] and request.form.get('override')!='yes':
        raise ValueError('Bitte die abweichende oder ungeprüfte Zuordnung ausdrücklich bestätigen.')
    return current,signed


def ams_consume_review(signed):
    # One reviewed operation, one attempt. An uncertain result needs a fresh
    # review instead of an automatic repeat of a hardware command.
    with inventory_connection(DB_PATH) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("DELETE FROM runtime WHERE key LIKE 'ams-selection:%' AND CAST(value AS REAL)<?",(time.time()-600,))
        key='ams-selection:'+signed['nonce']
        if db.execute('SELECT 1 FROM runtime WHERE key=?',(key,)).fetchone():
            raise AMSSelectionChanged('Diese Änderung wurde bereits gesendet. Bitte den Status prüfen und eine neue Vorschau öffnen.')
        db.execute('INSERT INTO runtime(key,value) VALUES (?,?)',(key,str(time.time())))
        db.commit()


def ams_job_requires_review(job, slot, spool):
    if not job or job['state']!='RUNNING' or slot=='external':
        return False
    before=json.loads(job.get('slot_mapping_json') or '{}').get(slot)
    after=str(spool['id']) if spool else None
    if before==after:
        return False
    if int(slot) in json.loads(job.get('observed_slots_json') or '[]'):
        return True
    raw=json.loads(job.get('ams_mapping_json') or 'null');raw2=json.loads(job.get('ams_mapping2_json') or 'null')
    ids=[int(k) for k in json.loads(job.get('weights_json') or '{}')]
    explicit=[resolve_tray_for_filament(i,raw,raw2) for i in ids]
    if explicit and all(tray is not None for tray in explicit):
        return int(slot) in explicit
    metadata=json.loads(job.get('filaments_json') or '{}')
    if not metadata:
        return True
    old=json.loads(job.get('ams_snapshot_json') or '{}').get(slot,{})
    candidates=[old,dict(material=spool.get('material'),color=spool.get('clean_color'))] if spool else [old]
    return any(normalized_color(fil.get('color')) and normalized_color(fil.get('color'))==normalized_color(tray.get('color')) and materials_match(fil.get('material'),tray.get('material')) for fil in metadata.values() for tray in candidates)


def ams_mark_job_review(db,slot,spool,expected):
    active=db.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
    jobrow=db.execute('SELECT * FROM jobs WHERE id=?',(active['value'],)).fetchone() if active and active['value'] else None
    if not jobrow or jobrow['state']!='RUNNING':
        return
    key='ams-job-review:'+jobrow['id']
    existing=db.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()
    changes=json.loads(existing['value']) if existing else {}
    if slot in changes or ams_job_requires_review(dict(jobrow),slot,spool):
        changes[slot]=dict(before=changes.get(slot,{}).get('before',expected),after=str(spool['id']) if spool else None)
        db.execute('INSERT INTO runtime(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(changes)))
        db.execute('UPDATE jobs SET error=? WHERE id=?',('Rolle während des Drucks gewechselt: Zuordnung und rollenbezogene Teilmengen vor der Buchung prüfen.',jobrow['id']))


def ams_assign_one(slot, spool, expected, after_write=False):
    with inventory_connection(DB_PATH) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT spool_id FROM ams_assignments WHERE printer_id=? AND slot_id=? AND released_at IS NULL',(SERIAL,slot)).fetchone()
        actual=str(row['spool_id']) if row else None
        if actual != expected and not (after_write and actual is None):
            raise AMSSelectionChanged('Die Schachtzuordnung wurde inzwischen geändert. Bitte den Status prüfen.')
        if spool and not db.execute('SELECT 1 FROM spools WHERE id=? AND archived=0 AND removed_at IS NULL',(spool['id'],)).fetchone():
            raise ValueError('Die Bestandsspule ist inzwischen nicht mehr aktiv.')
        if actual != (str(spool['id']) if spool else None):
            now=store.now()
            db.execute('UPDATE ams_assignments SET released_at=? WHERE printer_id=? AND slot_id=? AND released_at IS NULL',(now,SERIAL,slot))
            if spool:
                db.execute('INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at) VALUES (?,?,?,?)',(SERIAL,slot,spool['id'],now))
        ams_mark_job_review(db,slot,spool,expected)
        db.commit()


def ams_result(message):
    return jsonify(message=message,ams=ams_state,mapping=load_mapping(),printer=printer_status)


def refresh_calibration_profiles():
    nozzle=str(printer_status.get('nozzle_diameter') or '')
    if nozzle not in ('0.2','0.4','0.6','0.8'):raise AMSSelectionChanged('Düsendurchmesser noch unbekannt.')
    sequence=next_sequence();client=mqtt_client
    if not client or not client.is_connected():raise AMSSelectionChanged('Drucker nicht verbunden.')
    payload=dict(command='extrusion_cali_get',sequence_id=sequence,filament_id='',nozzle_diameter=nozzle)
    if client.publish(f'device/{SERIAL}/request',json.dumps({'print':payload}),qos=0).rc!=mqtt.MQTT_ERR_SUCCESS or not telemetry.wait_for_profiles(sequence):
        raise AMSSelectionChanged('Keine aktuelle Profilliste erhalten. Später erneut laden.')


@app.get('/api/ams/calibrations')
def ams_calibrations():
    try:
        if not refresh_printer_status():raise AMSSelectionChanged('Druckerstatus nicht erreichbar.')
        refresh_calibration_profiles()
        return jsonify(profiles=printer_status.get('calibration_profiles',[]),nozzle_diameter=printer_status['nozzle_diameter'])
    except AMSSelectionChanged as exc:
        return jsonify(error=str(exc)),409


@app.get('/api/ams/selection')
def ams_selection_preview():
    if INVENTORY_BACKEND!='internal':
        return jsonify(error='Interne Bestandsverwaltung erforderlich.'),409
    slot=request.args.get('slot','');spool_id=request.args.get('spool_id','');mode=request.args.get('mode','assign')
    try:
        if mode not in ('assign','write'):
            raise ValueError('Ungültige Aktion.')
        if mode=='write':
            refresh_printer_status()
            if request.args.get('calibration','-1')!='-1':refresh_calibration_profiles()
        review=ams_review(slot,spool_id)
        if mode=='write' and not review['write_allowed']:
            raise AMSSelectionChanged(review['write_reason'])
        session.setdefault('csrf_token',secrets.token_urlsafe(32))
        if mode=='assign': review['fingerprint']['payload']=None
        signed=dict(fingerprint=review['fingerprint'],mode=mode,nonce=secrets.token_urlsafe(20),
                    session=hashlib.sha256(session['csrf_token'].encode()).hexdigest())
        profile=review['payload']
        job=store.get_job(store.active_job_id()) if store.active_job_id() else None
        needs_review=ams_job_requires_review(job,slot,review['spool'])
        return jsonify(review_required=needs_review,token=ams_signer().dumps(signed),expires_in=120,matches=review['matches'],
                       write_allowed=review['write_allowed'],write_reason=review['write_reason'],
                       spool=review['spool'],tray=review['tray'],printer=printer_status,
                       profile=dict(material=profile['tray_type'],min=profile['nozzle_temp_min'],max=profile['nozzle_temp_max']) if profile else None,
                       calibration=profile.get('calibration') if profile else None)
    except AMSSelectionChanged as exc:
        return jsonify(error=str(exc)),409
    except (ValueError,KeyError) as exc:
        return jsonify(error=str(exc)),400


@app.post('/devices/ams/assign')
def assign_ams_spool():
    if INVENTORY_BACKEND!='internal' or request.form.get('confirm')!='assign':
        return jsonify(error='Bitte die Bestandszuordnung bestätigen.'),400
    if not ams_write_lock.acquire(blocking=False):
        return jsonify(error='Eine AMS-Änderung läuft bereits. Bitte kurz warten.'),409
    try:
        slot=request.form.get('slot','');spool_id=request.form.get('spool_id','')
        review,signed=ams_validate_review(slot,spool_id,'assign')
        ams_consume_review(signed)
        ams_assign_one(slot,review['spool'],review['fingerprint']['previous'])
        log(f"Bestandszuordnung für {'externe Spule' if slot=='external' else 'AMS Slot '+str(int(slot)+1)} aktualisiert; keine Druckerangaben geändert.")
        return ams_result('Bestandszuordnung gespeichert. Druckerangaben unverändert.')
    except AMSSelectionChanged as exc:
        return jsonify(error=str(exc)),409
    except (ValueError,KeyError) as exc:
        return jsonify(error=str(exc)),400
    except Exception:
        return jsonify(error='Zuordnung nicht gespeichert. Bitte den Status prüfen.'),503
    finally:
        ams_write_lock.release()


@app.post('/devices/ams/filament')
def write_ams_filament():
    from bridge.devices import safe_to_power_off
    if request.form.get('confirm')!='write' or INVENTORY_BACKEND!='internal':
        return jsonify(error='Bitte die Übertragung an den Drucker ausdrücklich bestätigen.'),400
    if not ams_write_lock.acquire(blocking=False):
        return jsonify(error='Eine AMS-Änderung läuft bereits. Bitte kurz warten.'),409
    metadata_confirmed=False
    try:
        slot=str(int(request.form['slot']));spool_id=request.form['spool_id']
        if not refresh_printer_status():
            return jsonify(error='Aktueller Druckerstatus fehlt. Bitte Status aktualisieren.'),409
        policy=slot_write_reason(printer_status,int(slot),bool(mqtt_client and mqtt_client.is_connected()))
        if policy: return jsonify(error=policy),409
        if request.form.get('calibration','-1')!='-1':refresh_calibration_profiles()
        review,signed=ams_validate_review(slot,spool_id,'write')
        payload=dict(review['payload'])
        calibration=payload.pop('calibration')
        policy=slot_write_reason(printer_status,int(slot),bool(mqtt_client and mqtt_client.is_connected()))
        if policy: raise AMSSelectionChanged(policy)
        ams_consume_review(signed)
        with inventory_connection(DB_PATH) as db:
            db.execute('BEGIN IMMEDIATE')
            ams_mark_job_review(db,slot,review['spool'],review['fingerprint']['previous'])
            db.commit()
        sequence=telemetry.ams_sequence(int(slot))
        payload['sequence_id']=next_sequence()
        client=mqtt_client
        if client is None or not client.is_connected() or client.publish(f'device/{SERIAL}/request',json.dumps({'print':payload}),qos=0).rc!=mqtt.MQTT_ERR_SUCCESS:
            return jsonify(error='AMS-Befehl konnte nicht gesendet werden. Bitte den Status prüfen.'),503
        send_pushall(client)
        if not telemetry.wait_for_ams_write(int(slot),sequence,payload['sequence_id'],payload['tray_type'],'#'+payload['tray_color'][:6],timeout=25):
            return jsonify(error='Keine passende neue Druckerbestätigung. Ergebnis unklar: Bitte den Status prüfen und vor einem neuen Versuch erneut bestätigen.'),504
        metadata_confirmed=True
        # Metadata and PA selection are two commands, with separate confirmations.
        policy=slot_write_reason(printer_status,int(slot),bool(client and client.is_connected()))
        if policy: raise AMSSelectionChanged(policy)
        command=dict(calibration['payload']); command['sequence_id']=next_sequence()
        cal_sequence=telemetry.calibration_sequence(int(slot))
        if client.publish(f'device/{SERIAL}/request',json.dumps({'print':command}),qos=0).rc!=mqtt.MQTT_ERR_SUCCESS:
            return jsonify(error='Material/Farbe möglicherweise geändert, Drucksteuerung nicht bestätigt. Bitte Zustand prüfen; Bestandsrolle wurde nicht übernommen.'),503
        send_pushall(client)
        if not telemetry.wait_for_calibration(int(slot),cal_sequence,command['sequence_id'],command['cali_idx'],calibration['k_value']):
            return jsonify(error='Material/Farbe bestätigt, Drucksteuerung jedoch nicht bestätigt. Bitte am Drucker prüfen. Keine automatische Wiederholung und keine neue Bestandsbindung.'),504
        record_ams_changes()
        ams_assign_one(slot,review['spool'],review['fingerprint']['previous'],after_write=True)
        log(f'AMS Slot {int(slot)+1}: Material/Farbe bestätigt und Bestandsspule zugeordnet.')
        return ams_result('Drucker bestätigt. Material, Farbe, Drucksteuerung und Bestandsspule übernommen.')
    except AMSCommandRejected as exc:
        return jsonify(error=('Material/Farbe bereits übertragen; Drucksteuerung nicht bestätigt. Keine neue Bestandszuordnung. ' if metadata_confirmed else '')+str(exc)),422
    except AMSSelectionChanged as exc:
        return jsonify(error=('Material/Farbe bereits übertragen; bitte Drucksteuerung und Bestandszuordnung prüfen. ' if metadata_confirmed else '')+str(exc)),409
    except (ValueError,KeyError) as exc:
        return jsonify(error=str(exc)),400
    except Exception:
        return jsonify(error='Ergebnis der AMS-Übertragung unklar. Bitte aktuellen Druckerstatus prüfen; nicht ungeprüft erneut senden.'),503
    finally:
        ams_write_lock.release()


def job_mapping_forms():
    forms = []
    for job in store.list_jobs():
        if job['kind'] != 'PRINT' or job['state'] not in ('RUNNING', 'FINISH_PENDING', 'WAITING_METADATA', 'WAITING_MAPPING'):
            continue
        if any(row['status'] not in ('UNMAPPED', 'PENDING') or row['attempts'] > 0 for row in store.deductions_for_job(job['id'])):
            continue
        weights = json.loads(job['weights_json'] or '{}')
        job['can_close_without_usage']=job['state']=='WAITING_METADATA' and not weights and not store.deductions_for_job(job['id'])
        mapping = json.loads(job['ams_mapping_json'] or '[]')
        mapping2 = json.loads(job['ams_mapping2_json'] or 'null')
        metadata = json.loads(job.get('filaments_json') or '{}')
        inventory = json.loads(job.get('ams_snapshot_json') or '{}')
        planned = store.deductions_for_job(job['id'])
        preview = []
        if weights and AUTO_MATCH_FILAMENTS and job.get('mapping_source') != 'USER':
            preview, _, _ = plan_consumption(
                job, {int(k): float(v) for k, v in weights.items()}, metadata,
                json.loads(job.get('slot_mapping_json') or '{}'),
                json.loads(job.get('spools_snapshot_json') or '{}'))
        ids = sorted(int(k) for k in weights) if weights else list(range(1, max(len(mapping), 4) + 1))
        job['filaments'] = []
        for i in ids:
            suggested = False
            tray = resolve_tray_for_filament(i, mapping, mapping2)
            if tray is None:
                planned_trays = {row['tray_id'] for row in planned if row['filament_id'] == i and row['tray_id'] is not None}
                if len(planned_trays) == 1: tray = planned_trays.pop()
            fil = metadata.get(str(i), {})
            if tray is None:
                parts = [row for row in preview if row['filament_id'] == i]
                if len(parts) == 1 and parts[0]['status'] == 'PENDING' and parts[0]['tray_id'] is not None:
                    tray = parts[0]['tray_id']
                    suggested = True
            job['filaments'].append({'id': i, 'grams': weights.get(str(i)), 'tray': tray,
                                     'suggested': suggested, 'color': normalized_color(fil.get('color')), 'material': fil.get('material','')})
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
  <div class="top printer-heading">
    <div>
      <div class="eyebrow">Dein Drucker</div><h1>Drucker & Verbrauch</h1>
  <div class="muted">A1 / A1 mini · AMS Lite · Automatische Verbrauchsbuchung</div>
    </div>
    <div>
      <button class="secondary" onclick="window.location.reload()">Aktualisieren</button>

    </div>
    """ + A1_VISUAL + r"""
  </div>
  <div class="status">
    <div><span class="muted">Spoolman:</span> <strong id="spoolman">{{ 'Verbunden' if spoolman.connected else 'Nicht verbunden' }}</strong></div>
    <div><span class="muted">Drucker:</span> <span id="state" class="pill">{{ printer.state }}</span> <span id="progress">{{ printer.progress }}%</span></div>
    <div><span class="muted">Datei:</span> <strong id="subtask">{{ printer.subtask_name or '—' }}</strong></div>
    <div><span class="muted">Voraussichtliche Restzeit:</span> <strong id="remaining-time">{{printer|remaining_time}}</strong><div class="muted small">Schätzung des Druckers</div></div>
    <div><span class="muted">Aktiver Schacht:</span> <strong id="active">{{ printer.active_slot_label }}</strong></div>
  </div>
</section>

""" + AMS_PANEL + r"""

<section class="card" id="auto-power-off"><h2>Nach diesem Druck ausschalten</h2><p class="muted">Erst nach erfolgreichem Druckende und abgeschlossener Verbrauchsbuchung. Bei Abbruch, neuem Druck oder ungeklärter Zuordnung wird nicht automatisch ausgeschaltet.</p><form method="post" action="/settings/auto-power-off"><input type="hidden" name="csrf_token" value="{{csrf_token}}"><input type="hidden" name="job_id" value="{{active_job_id or auto_power.job_id or ''}}"><label style="display:flex;gap:10px;align-items:center"><input style="width:auto" type="checkbox" name="enabled" {% if auto_power.enabled %}checked{% endif %} {% if not active_job_id and not auto_power.enabled %}disabled{% endif %}> Steckdose nach diesem Druck automatisch ausschalten</label><div class="actions"><label>Wartezeit nach abgeschlossener Buchung<select name="delay">{% for seconds,label in [(60,'1 Minute'),(120,'2 Minuten (empfohlen)'),(180,'3 Minuten')] %}<option value="{{seconds}}" {% if auto_power.delay==seconds %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label><button>Automatik speichern</button></div><p id="auto-power-status" class="muted small" role="status">{{auto_power.message}}</p></form></section>

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
    <p class="muted small">Automatische Vorschau und spätere Rückmeldungen erscheinen nach dem Neuladen. Verbrauch wird erst nach Druckende gebucht.</p>
    {% if job.assignment_summary %}<p><strong>{{ job.assignment_summary }}</strong></p>{% endif %}
    {% if job.error %}<p class="muted">{{ job.error }}</p>{% endif %}
    {% if job.can_close_without_usage %}<p class="notice">Für diesen Auftrag fehlen Verbrauchsmengen. Eine Filamentzuordnung allein kann das nicht beheben. Du kannst ihn unten ohne Buchung schließen; die Entscheidung bleibt im Journal sichtbar.</p>{% else %}
    {% for filament in job.filaments %}
    <div class="slot">
      <label for="{{ job.id }}-f{{ filament.id }}">Filament {{ filament.id }}{% if filament.grams is not none %} · {{ '%.2f'|format(filament.grams) }} g{% endif %}
        <span class="muted small" style="display:block">{% if filament.color %}<span class="swatch" style="background:#{{ filament.color }}"></span>#{{ filament.color }}{% endif %} {{ filament.material }}{% if filament.suggested %} · Automatische Vorschau (Farbe & Material), noch nicht gebucht{% endif %}</span>
      </label>
      <select id="{{ job.id }}-f{{ filament.id }}" name="filament_{{ filament.id }}">
        <option value="-1">— Nicht zugeordnet —</option>
        {% for slot in range(4) %}<option value="{{ slot }}" {% if filament.tray == slot %}selected{% endif %}>{{ job.slot_labels[slot] }}</option>{% endfor %}
        <option value="254" {% if filament.tray == 254 %}selected{% endif %}>Externe Spule</option>
      </select>
    </div>
    {% endfor %}
    <button type="submit" style="margin-top:10px">Zuordnung für diesen Auftrag bestätigen</button>{% endif %}
  </form>
  {% if job.can_close_without_usage %}<form method="post" action="{{ url_for('close_job_without_usage',job_id=job.id) }}" class="close-job-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="confirm" value="close-without-usage">
    <label style="display:flex;flex-direction:row;gap:10px;align-items:center"><input type="checkbox" name="acknowledge" value="yes" required style="width:auto">Ohne Verbrauchsbuchung schließen. Möglichen Materialverbrauch korrigiere ich bei Bedarf separat.</label>
    <button type="submit" class="secondary" style="margin:10px 0 24px">Auftrag ohne Buchung schließen</button>
  </form>{% endif %}
  {% else %}<p class="muted">Kein Auftrag mit offener Filamentzuordnung.</p>{% endfor %}
</section>

<section class="card" id="journal-panel"><details class="diagnostics" id="journal-details" open>
  <summary>Buchungsjournal <span class="muted small" id="journal-count">{{deductions|length}} Einträge angezeigt</span></summary>
  <p class="muted small">Die letzten 10 Einträge. Ältere Buchungen bleiben gespeichert und können schrittweise nachgeladen werden.</p>
  <div style="overflow:auto;">
    <table>
      <thead><tr><th>Datum & Uhrzeit</th><th>Auftrag</th><th>Schacht</th><th>Spule</th><th>Menge</th><th>Status</th><th>Details</th></tr></thead>
      <tbody id="deductions">
        {% for d in deductions %}
          <tr>
            <td>{{ d.created_at|date_time }}</td>
            <td>{{ d.subtask_name or d.filename or d.job_id[:8] }}</td>
            <td>{{ 'Extern' if d.tray_id == 254 else (('Slot ' ~ (d.tray_id + 1)) if d.tray_id is not none else '—') }}</td>
            <td>{{ ('#' ~ d.spool_id) if d.spool_id else '—' }}</td>
            <td>{{ d.amount_label if d.amount_label is defined else ('%.2f'|format(d.grams))+' g' }}</td>
            <td><strong>{{ d.status_label or d.status }}{% if d.estimated %} · SCHÄTZUNG{% endif %}</strong></td>
            <td class="muted small">{{ d.detail }}</td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">Noch keine Buchungen erfasst.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="actions"><button type="button" class="secondary" id="journal-more" onclick="loadMoreJournal()" {% if not journal.has_more %}hidden{% endif %}>10 weitere Einträge laden</button><button type="button" class="secondary" id="journal-latest" onclick="resetJournal()" hidden>Aktuelle Einträge anzeigen</button></div>
  <p id="journal-message" class="muted small" role="status" aria-live="polite"></p>
</details></section>

<section class="card"><details class="diagnostics">
  <summary>Diagnose & Live-Logs</summary>
  <div class="logs" id="logs">{% for line in logs %}{{ line }}
{% endfor %}</div></details>
</section>


<script>
let userScrolled=false;const logBox=document.getElementById('logs');logBox.addEventListener('scroll',()=>{userScrolled=(logBox.scrollHeight-logBox.scrollTop-logBox.clientHeight)>30;});
function formatRemaining(printer){if(printer.state==='FINISH')return 'Abgeschlossen';if(!['RUNNING','PREPARE','PAUSE'].includes(printer.state))return '—';const age=Date.now()/1000-(printer.remaining_seen_at||0),minutes=printer.remaining_minutes;if(minutes==null||age<0||age>180)return 'Warte auf Zeitangabe';const hours=Math.floor(minutes/60),rest=minutes%60;const text=hours?hours+' Std. '+rest+' Min.':rest?rest+' Min.':'< 1 Min.';return (printer.state==='PAUSE'?'Pausiert · noch ':'ca. ')+text}
function formatDateTime(value){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?value:new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date)}
function cell(row,val){const td=document.createElement('td');td.textContent=val??'';row.appendChild(td);}
function refreshStatus(){fetch('/api/status').then(r=>r.json()).then(data=>{
 document.getElementById('spoolman').textContent=data.spoolman.connected?'Verbunden':'Nicht verbunden';
 document.getElementById('state').textContent=data.printer.state;
 if(data.ams)updateAMS(data.ams,data.ams_mapping,data.printer);
 const illustration=document.querySelector('.printer-illustration');if(illustration)illustration.classList.toggle('printing',data.printer.state==='RUNNING');
 document.getElementById('progress').textContent=data.printer.progress+'%';
 document.getElementById('remaining-time').textContent=formatRemaining(data.printer);
 const powerStatus=document.getElementById('auto-power-status');if(powerStatus&&data.auto_power){let text=data.auto_power.message;if(data.auto_power.due_at&&data.auto_power.enabled)text+=' Noch ca. '+Math.max(0,Math.ceil((data.auto_power.due_at-Date.now()/1000)/60))+' Min.';powerStatus.textContent=text;}
 document.getElementById('subtask').textContent=data.printer.subtask_name||'—';
 document.getElementById('active').textContent=data.printer.active_slot_label;
 logBox.textContent=data.logs.join('\n');
 if(!userScrolled)logBox.scrollTop=logBox.scrollHeight;
 if(data.journal)updateJournal(data.journal);
});}
let journalRows={{deductions|tojson}},journalCursor={{journal.next_cursor|tojson}},journalHasMore={{journal.has_more|tojson}},journalExpanded=false,journalLoading=false,latestJournal={{journal|tojson}};
function renderJournal(){const tbody=document.getElementById('deductions');tbody.replaceChildren();for(const d of journalRows){const tr=document.createElement('tr');cell(tr,formatDateTime(d.created_at));cell(tr,d.subtask_name||d.filename||d.job_id.slice(0,8));cell(tr,d.tray_id==null?'—':d.tray_id===254?'Extern':'Slot '+(d.tray_id+1));cell(tr,d.spool_id?'#'+d.spool_id:'—');cell(tr,d.amount_label||Number(d.grams).toFixed(2)+' g');cell(tr,(d.status_label||d.status)+(d.estimated?' · SCHÄTZUNG':''));cell(tr,d.detail);tbody.append(tr)}if(!journalRows.length){const tr=document.createElement('tr');cell(tr,'Noch keine Buchungen erfasst.');tr.firstChild.colSpan=7;tbody.append(tr)}document.getElementById('journal-count').textContent=journalRows.length+' Einträge angezeigt';const more=document.getElementById('journal-more');more.hidden=!journalHasMore;more.disabled=journalLoading;more.textContent=journalLoading?'Wird geladen …':'10 weitere Einträge laden'}
function journalRowID(row){return String(row.id)}
function updateJournal(data){latestJournal=data;if(!journalExpanded&&!journalLoading){journalRows=data.entries;journalCursor=data.next_cursor;journalHasMore=data.has_more;document.getElementById('journal-latest').hidden=true;renderJournal();return}const latest=new Map(data.entries.map(r=>[journalRowID(r),r]));journalRows=journalRows.map(r=>latest.get(journalRowID(r))||r);const changed=data.entries[0]&&journalRowID(data.entries[0])!==journalRowID(journalRows[0]||{});document.getElementById('journal-latest').hidden=!changed;renderJournal()}
async function loadMoreJournal(){if(journalLoading||!journalHasMore)return;journalLoading=true;const cursor=journalCursor;renderJournal();const message=document.getElementById('journal-message');message.textContent='';try{const response=await fetch('/api/journal?before='+encodeURIComponent(cursor),{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'Journal konnte nicht geladen werden.');const ids=new Set(journalRows.map(journalRowID));journalRows.push(...data.entries.filter(r=>!ids.has(journalRowID(r))));journalCursor=data.next_cursor;journalHasMore=data.has_more;journalExpanded=true;message.textContent=data.has_more?'':'Alle verfügbaren Einträge geladen.'}catch(error){message.textContent=error.message+' Bitte erneut versuchen.'}finally{journalLoading=false;renderJournal()}}
function resetJournal(){if(journalLoading)return;journalExpanded=false;document.getElementById('journal-message').textContent='';document.getElementById('journal-latest').hidden=true;updateJournal(latestJournal)}
const journalDetails=document.getElementById('journal-details');try{const saved=localStorage.getItem('plus-journal-open');if(saved!==null)journalDetails.open=saved==='true'}catch{}journalDetails.addEventListener('toggle',()=>{try{localStorage.setItem('plus-journal-open',String(journalDetails.open))}catch{}});
setInterval(refreshStatus,5000);
</script>
"""

def journal_page(before=None):
    rows=store.list_deductions(11,before=before)
    more=len(rows)>10
    entries=rows[:10]
    cursor=URLSafeTimedSerializer(app.secret_key,salt='journal-cursor-v1').dumps(entries[-1]['journal_key']) if more and entries else None
    return dict(entries=entries,has_more=more,next_cursor=cursor)


@app.get('/api/journal')
def journal_api():
    before=None
    token=request.args.get('before')
    if token:
        try:
            if len(token)>512:raise ValueError()
            before=URLSafeTimedSerializer(app.secret_key,salt='journal-cursor-v1').loads(token)
            if not isinstance(before,list) or len(before)!=3 or not isinstance(before[0],str) or before[1] not in ('deduction','adjustment','closed') or type(before[2]) is not int or before[2]<1:raise ValueError()
        except (BadSignature,ValueError,TypeError):
            return jsonify(error='Ungültige Journalposition. Bitte die Seite neu laden.'),400
    return jsonify(journal_page(before))


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "bambu-spoolman-bridge", "version": APP_VERSION, "dry_run": DRY_RUN, "inventory_backend": INVENTORY_BACKEND})

@app.route("/")
def index():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    journal=journal_page()
    return render_ui(
        HTML_TEMPLATE.replace("Spoolman", "Inventar") if INVENTORY_BACKEND == "internal" else HTML_TEMPLATE,
        active='overview', title='Drucker & Verbrauch',
        version=APP_VERSION,
        material_family_pattern=MATERIAL_FAMILY_PATTERN,
        build_time=APP_BUILD_TIME,
        csrf_token=session["csrf_token"],
        printer=printer_status,
        spoolman=spoolman_status,
        ams=ams_state,
        mapping=load_mapping(),
        mapping_jobs=job_mapping_forms(),
        auto_power=auto_power.status(),active_job_id=store.active_job_id(),
        spools=fetch_spoolman_spools(),
        deductions=journal['entries'],journal=journal,
        logs=list(log_buffer),
        spoolman_url=SPOOLMAN_BROWSER_URL,
        dry_run=DRY_RUN,
        auto_match=AUTO_MATCH_FILAMENTS,
    )

@app.route("/api/status")
def status_api():
    with log_lock: logs = list(log_buffer)
    journal=journal_page()
    return jsonify({
        "printer": printer_status,
        "ams": ams_state,
        "ams_mapping": load_mapping(),
        "spoolman": spoolman_status,
        "jobs": store.list_jobs(),
        "dry_run": DRY_RUN,
        "auto_match_filaments": AUTO_MATCH_FILAMENTS,
        "auto_power": auto_power.status(),
        "deductions": journal["entries"],
        "journal": journal,
        "logs": logs,
    })

@app.route("/save", methods=["POST"])
def save():
    mapping = {str(i): (request.form.get(f"slot_{i}") or None) for i in range(4)}
    mapping["external"] = request.form.get("slot_external") or None
    if any(v is not None and (not v.isdigit() or int(v) <= 0) for v in mapping.values()):
        return Response('Ungültige Spulen-ID.', 400)
    if not ams_write_lock.acquire(blocking=False):
        return Response('Eine AMS-Änderung läuft bereits. Bitte kurz warten.',409)
    try:
        save_mapping(mapping)
    except InventoryError as exc:
        return Response(str(exc), 400, mimetype='text/plain')
    finally:
        ams_write_lock.release()
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

@app.post('/jobs/<job_id>/close-without-usage')
def close_job_without_usage(job_id):
    if request.form.get('confirm')!='close-without-usage' or request.form.get('acknowledge')!='yes':
        return Response('Bitte das Schließen ohne Verbrauchsbuchung ausdrücklich bestätigen.',400)
    with queued_lock:
        if job_id in queued_jobs or not store.close_without_usage(job_id):
            return Response('Auftrag wird verarbeitet, ist aktiv, bereits geschlossen oder enthält Verbrauchs-/Buchungsdaten. Bitte Status aktualisieren.',409)
    log('Auftrag ohne Verbrauchsbuchung manuell geschlossen; fehlende Verbrauchsdaten. Historie bleibt erhalten.')
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
            for key, value in [('dry_run', dry_run), ('auto_match_filaments', auto_match), ('flow_calibration_allowance', request.form.get('flow_allowance') == 'on')]:
                db.execute('INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at', (key, json.dumps(value), store.now()))
            db.commit()
        DRY_RUN, AUTO_MATCH_FILAMENTS = dry_run, auto_match
        return redirect('/settings?saved=1')
    with inventory_connection(DB_PATH) as db:
        option = db.execute("SELECT value_json FROM settings WHERE key='flow_calibration_allowance'").fetchone()
    return render_ui(SETTINGS_PAGE, flow_allowance=bool(option and json.loads(option['value_json']) is True), active='settings', title='Einstellungen', backend=INVENTORY_BACKEND, token=session['csrf_token'], dry_run=DRY_RUN, auto_match=AUTO_MATCH_FILAMENTS, saved=request.args.get('saved') == '1')


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

    def auto_power_runner():
        while True:
            try:auto_power.poll()
            except Exception:logger.warning('Automatische Abschaltprüfung fehlgeschlagen; es wird nicht ausgeschaltet.')
            time.sleep(5)
    threading.Thread(target=auto_power_runner,daemon=True).start()

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
