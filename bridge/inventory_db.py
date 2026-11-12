"""Additive SQLite schema for the integrated inventory.

Migrations only add tables and never rewrite the legacy jobs/deductions ledger.
The internal backend additionally requires an explicit offline cutover marker.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a configured connection without changing the database schema."""
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    try:
        yield db
    finally:
        db.close()


_V1_STATEMENTS = (
    """CREATE TABLE vendors (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        source_system TEXT,
        source_id TEXT,
        source_payload_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source_system, source_id)
    )""",
    """CREATE TABLE locations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(parent_id, name)
    )""",
    """CREATE TABLE filaments (
        id INTEGER PRIMARY KEY,
        vendor_id INTEGER REFERENCES vendors(id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        material TEXT NOT NULL DEFAULT '',
        subtype TEXT NOT NULL DEFAULT '',
        color_json TEXT,
        color_name TEXT NOT NULL DEFAULT '',
        diameter_mm REAL CHECK(diameter_mm IS NULL OR diameter_mm > 0),
        density_g_cm3 REAL CHECK(density_g_cm3 IS NULL OR density_g_cm3 > 0),
        net_weight_g REAL CHECK(net_weight_g IS NULL OR net_weight_g >= 0),
        article_number TEXT NOT NULL DEFAULT '',
        identifiers_json TEXT NOT NULL DEFAULT '{}',
        slicer_profiles_json TEXT NOT NULL DEFAULT '{}',
        custom_fields_json TEXT NOT NULL DEFAULT '{}',
        source_system TEXT,
        source_id TEXT,
        source_payload_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source_system, source_id)
    )""",
    "CREATE INDEX filaments_vendor_material_idx ON filaments(vendor_id, material)",
    """CREATE TABLE spools (
        id INTEGER PRIMARY KEY,
        filament_id INTEGER REFERENCES filaments(id) ON DELETE RESTRICT,
        source_system TEXT,
        source_id TEXT,
        initial_weight_g REAL CHECK(initial_weight_g IS NULL OR initial_weight_g >= 0),
        current_weight_g REAL CHECK(current_weight_g IS NULL OR current_weight_g >= 0),
        empty_spool_weight_g REAL CHECK(empty_spool_weight_g IS NULL OR empty_spool_weight_g >= 0),
        location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1)),
        purchased_at TEXT,
        purchase_price_minor INTEGER CHECK(purchase_price_minor IS NULL OR purchase_price_minor >= 0),
        currency TEXT,
        batch TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        last_used_at TEXT,
        custom_fields_json TEXT NOT NULL DEFAULT '{}',
        source_payload_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source_system, source_id)
    )""",
    "CREATE INDEX spools_filament_active_idx ON spools(filament_id, archived)",
    "CREATE INDEX spools_location_idx ON spools(location_id)",
    """CREATE TABLE spool_tags (
        id INTEGER PRIMARY KEY,
        spool_id INTEGER NOT NULL REFERENCES spools(id) ON DELETE CASCADE,
        tag_type TEXT NOT NULL,
        tag_value TEXT NOT NULL,
        source_payload_json TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(tag_type, tag_value)
    )""",
    """CREATE TABLE ams_assignments (
        id INTEGER PRIMARY KEY,
        printer_id TEXT NOT NULL,
        slot_id TEXT NOT NULL,
        spool_id INTEGER NOT NULL REFERENCES spools(id) ON DELETE RESTRICT,
        assigned_at TEXT NOT NULL,
        released_at TEXT,
        source TEXT NOT NULL DEFAULT 'user',
        CHECK(released_at IS NULL OR released_at >= assigned_at)
    )""",
    """CREATE UNIQUE INDEX ams_assignments_one_active_slot
        ON ams_assignments(printer_id, slot_id) WHERE released_at IS NULL""",
    "CREATE INDEX ams_assignments_spool_idx ON ams_assignments(spool_id, released_at)",
    """CREATE TABLE inventory_movements (
        id INTEGER PRIMARY KEY,
        operation_key TEXT NOT NULL UNIQUE,
        spool_id INTEGER NOT NULL REFERENCES spools(id) ON DELETE RESTRICT,
        movement_type TEXT NOT NULL CHECK(movement_type IN ('opening', 'consumption', 'adjustment', 'return')),
        delta_g REAL NOT NULL,
        before_weight_g REAL CHECK(before_weight_g IS NULL OR before_weight_g >= 0),
        after_weight_g REAL CHECK(after_weight_g IS NULL OR after_weight_g >= 0),
        source_job_id TEXT,
        source_entry_id TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX inventory_movements_spool_time_idx ON inventory_movements(spool_id, created_at)",
    """CREATE TABLE consumption_entries (
        id INTEGER PRIMARY KEY,
        operation_key TEXT NOT NULL UNIQUE,
        job_id TEXT NOT NULL,
        source_key TEXT NOT NULL,
        spool_id INTEGER NOT NULL REFERENCES spools(id) ON DELETE RESTRICT,
        grams REAL NOT NULL CHECK(grams >= 0),
        movement_id INTEGER NOT NULL UNIQUE REFERENCES inventory_movements(id) ON DELETE RESTRICT,
        legacy_deduction_id INTEGER,
        created_at TEXT NOT NULL,
        UNIQUE(job_id, source_key)
    )""",
    "CREATE INDEX consumption_entries_spool_time_idx ON consumption_entries(spool_id, created_at)",
    """CREATE TABLE import_runs (
        id INTEGER PRIMARY KEY,
        source_system TEXT NOT NULL,
        source_version TEXT,
        status TEXT NOT NULL CHECK(status IN ('preview', 'validated', 'applied', 'failed')),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        source_snapshot_json TEXT NOT NULL,
        source_snapshot_sha256 TEXT NOT NULL,
        summary_json TEXT NOT NULL DEFAULT '{}',
        diagnostics_json TEXT NOT NULL DEFAULT '[]'
    )""",
    """CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        secret INTEGER NOT NULL DEFAULT 0 CHECK(secret IN (0, 1)),
        updated_at TEXT NOT NULL
    )""",
)


def apply_migrations(path: str | Path) -> int:
    """Apply pending additive migrations and return the resulting version.

    Existing bridge tables are never renamed or copied. Each version's DDL and
    version record commit atomically, so a failed migration can be retried.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connection(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )"""
            )
            row = db.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
            current = int(row["version"])
            if current < 1:
                for statement in _V1_STATEMENTS:
                    db.execute(statement)
                db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                    (1, _now()),
                )
                current = 1
            if current < 2:
                db.execute("CREATE TABLE inventory_job_plans (job_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, created_at TEXT NOT NULL)")
                db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(2,?)", (_now(),))
                current = 2
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Datenbankschema {current} ist neuer als diese Anwendung ({SCHEMA_VERSION})."
                )
            db.commit()
            return current
        except Exception:
            db.rollback()
            raise
