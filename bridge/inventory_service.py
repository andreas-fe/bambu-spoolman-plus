"""Transactional inventory operations for the integrated application."""

from __future__ import annotations

import math
import json
from contextlib import nullcontext
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .inventory_db import connection


class InventoryError(ValueError):
    """Base error for rejected inventory operations."""


class IdempotencyConflict(InventoryError):
    """An idempotency key was reused with a different booking payload."""


class InsufficientStock(InventoryError):
    """A known spool balance cannot cover the requested consumption."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InventoryService:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def apply_consumption(
        self,
        *,
        operation_key: str,
        job_id: str,
        source_key: str,
        spool_id: int,
        grams: float,
        legacy_deduction_id: int | None = None,
        _db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Apply one consumption and its journal rows as a single transaction.

        Repeating the same logical operation is safe. Reusing either unique
        key for a different payload raises ``IdempotencyConflict``.
        """
        amount = float(grams)
        if not operation_key.strip() or not job_id.strip() or not source_key.strip():
            raise InventoryError("Buchungskennung, Job-ID und Quellkennung sind erforderlich.")
        if not math.isfinite(amount) or amount <= 0:
            raise InventoryError("Verbrauch muss eine endliche positive Grammzahl sein.")

        with (nullcontext(_db) if _db is not None else connection(self.path)) as db:
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    """SELECT * FROM consumption_entries
                       WHERE operation_key=? OR (job_id=? AND source_key=?)""",
                    (operation_key, job_id, source_key),
                ).fetchone()
                if existing:
                    same_request = (
                        existing["operation_key"] == operation_key
                        and existing["job_id"] == job_id
                        and existing["source_key"] == source_key
                        and existing["spool_id"] == spool_id
                        and math.isclose(existing["grams"], amount, rel_tol=0, abs_tol=1e-9)
                    )
                    if not same_request:
                        raise IdempotencyConflict("Buchungskennung wurde mit anderen Daten wiederverwendet.")
                    if _db is None:
                        db.commit()
                    return {"applied": False, "entry_id": existing["id"], "movement_id": existing["movement_id"]}

                spool = db.execute(
                    "SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)
                ).fetchone()
                if spool is None:
                    raise InventoryError(f"Spule {spool_id} existiert nicht.")

                before = spool["current_weight_g"]
                if before is not None and amount > before + 1e-9:
                    raise InsufficientStock(
                        f"Bekannter Restbestand {before:g} g reicht für {amount:g} g nicht aus."
                    )
                after = max(0.0, before - amount) if before is not None else None
                created_at = _now()
                movement_id = db.execute(
                    """INSERT INTO inventory_movements(
                           operation_key, spool_id, movement_type, delta_g,
                           before_weight_g, after_weight_g, source_job_id,
                           source_entry_id, created_at
                       ) VALUES (?, ?, 'consumption', ?, ?, ?, ?, ?, ?)""",
                    (operation_key, spool_id, -amount, before, after, job_id, source_key, created_at),
                ).lastrowid
                entry_id = db.execute(
                    """INSERT INTO consumption_entries(
                           operation_key, job_id, source_key, spool_id, grams,
                           movement_id, legacy_deduction_id, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (operation_key, job_id, source_key, spool_id, amount, movement_id,
                     legacy_deduction_id, created_at),
                ).lastrowid
                db.execute(
                    "UPDATE spools SET current_weight_g=?, last_used_at=?, updated_at=? WHERE id=?",
                    (after, created_at, created_at, spool_id),
                )
                if _db is None:
                    db.commit()
                return {"applied": True, "entry_id": entry_id, "movement_id": movement_id}
            except Exception:
                if _db is None:
                    db.rollback()
                raise

    def adjust_stock(
        self,
        *,
        operation_key: str,
        spool_id: int,
        delta_grams: float,
        note: str = "",
    ) -> dict[str, Any]:
        """Record an explicit weight correction atomically with the spool."""
        delta = float(delta_grams)
        if not operation_key.strip() or not math.isfinite(delta) or delta == 0:
            raise InventoryError("Korrektur benötigt eine Kennung und eine endliche, von null verschiedene Menge.")

        with connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT * FROM inventory_movements WHERE operation_key=?", (operation_key,)
                ).fetchone()
                if existing:
                    same_request = (
                        existing["movement_type"] == "adjustment"
                        and existing["spool_id"] == spool_id
                        and math.isclose(existing["delta_g"], delta, rel_tol=0, abs_tol=1e-9)
                        and existing["note"] == note
                    )
                    if not same_request:
                        raise IdempotencyConflict("Buchungskennung wurde mit anderen Korrekturdaten wiederverwendet.")
                    db.commit()
                    return {"applied": False, "movement_id": existing["id"]}

                spool = db.execute(
                    "SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)
                ).fetchone()
                if spool is None:
                    raise InventoryError(f"Spule {spool_id} existiert nicht.")
                before = spool["current_weight_g"]
                after = before + delta if before is not None else None
                if after is not None and after < -1e-9:
                    raise InsufficientStock("Die Korrektur würde einen negativen Restbestand erzeugen.")
                if after is not None:
                    after = max(0.0, after)
                now = _now()
                movement_id = db.execute(
                    """INSERT INTO inventory_movements(
                           operation_key, spool_id, movement_type, delta_g,
                           before_weight_g, after_weight_g, note, created_at
                       ) VALUES (?, ?, 'adjustment', ?, ?, ?, ?, ?)""",
                    (operation_key, spool_id, delta, before, after, note, now),
                ).lastrowid
                db.execute("UPDATE spools SET current_weight_g=?, updated_at=? WHERE id=?", (after, now, spool_id))
                db.commit()
                return {"applied": True, "movement_id": movement_id}
            except Exception:
                db.rollback()
                raise

    def apply_ledger_deduction(self, deduction_id: int) -> dict:
        """Commit the legacy journal status and local consumption together.

        No IN_FLIGHT claim or external request is needed. SQLite serializes
        competing workers. Historical rows at the cutover boundary are blocked.
        """
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                cutoff = db.execute("SELECT value_json FROM settings WHERE key='cutover_job_rowid'").fetchone()
                if cutoff is None:
                    raise InventoryError('Interner Bestand ist noch nicht freigeschaltet.')
                row = db.execute('SELECT d.*,j.rowid AS job_rowid FROM deductions d JOIN jobs j ON j.id=d.job_id WHERE d.id=?', (deduction_id,)).fetchone()
                if row is None or row['job_rowid'] <= json.loads(cutoff[0]):
                    raise InventoryError('Altbuchungen werden nicht im internen Bestand wiederholt.')
                if row['status'] == 'DONE':
                    db.commit()
                    return {'applied': False}
                if row['status'] not in ('PENDING', 'RETRY'):
                    raise InventoryError('Buchungszustand ist nicht automatisch wiederholbar.')
                plan = [dict(r) for r in db.execute('SELECT source_key,spool_id,grams FROM deductions WHERE job_id=? ORDER BY source_key', (row['job_id'],))]
                raw = json.dumps(plan, sort_keys=True, allow_nan=False)
                frozen = db.execute('SELECT plan_json FROM inventory_job_plans WHERE job_id=?', (row['job_id'],)).fetchone()
                if frozen and frozen[0] != raw:
                    raise IdempotencyConflict('Verbrauchsplan wurde nach Buchungsbeginn verändert.')
                db.execute('INSERT OR IGNORE INTO inventory_job_plans(job_id,plan_json,created_at) VALUES (?,?,?)', (row['job_id'], raw, _now()))
                result = self.apply_consumption(operation_key=f"deduction:{deduction_id}", job_id=row['job_id'], source_key=row['source_key'], spool_id=row['spool_id'], grams=row['grams'], legacy_deduction_id=deduction_id, _db=db)
                db.execute("UPDATE deductions SET status='DONE',detail='Intern atomar gebucht',attempts=attempts+1,updated_at=? WHERE id=?", (_now(), deduction_id))
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise
