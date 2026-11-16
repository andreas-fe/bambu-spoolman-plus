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

    def remove_archived_spool(self, spool_id, _db=None):
        """Remove from inventory while preserving all historical identities."""
        with (nullcontext(_db) if _db is not None else connection(self.path)) as db:
            if _db is None:
                db.execute('BEGIN IMMEDIATE')
            spool = db.execute('SELECT archived,removed_at FROM spools WHERE id=?', (spool_id,)).fetchone()
            if spool is None:
                raise InventoryError('Spule existiert nicht.')
            if spool['removed_at']:
                return
            if not spool['archived']:
                raise InventoryError('Bitte die Spule zuerst archivieren.')
            if db.execute('SELECT 1 FROM ams_assignments WHERE spool_id=? AND released_at IS NULL', (spool_id,)).fetchone():
                raise InventoryError('Die Spule ist aktuell einem Schacht zugeordnet. Bitte zuerst die Zuordnung lösen.')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'jobs' in tables:
                for guard in db.execute("SELECT value FROM runtime WHERE key LIKE 'ams-job-review:%'") if 'runtime' in tables else []:
                    if any(str(change.get('after'))==str(spool_id) for change in json.loads(guard['value']).values()):
                        raise InventoryError('Diese Rolle benötigt noch eine Zuordnungsprüfung für einen Druckauftrag.')
                closed = ('DONE','DRY_RUN','CANCELLED_ZERO','REJECTED','FAILED','INTERRUPTED','CLOSED_NO_USAGE')
                for job in db.execute('SELECT id,state,slot_mapping_json,spools_snapshot_json FROM jobs'):
                    if job['state'] in closed:
                        continue
                    in_mapping = str(spool_id) in {str(v) for v in json.loads(job['slot_mapping_json'] or '{}').values()}
                    in_stock = str(spool_id) in json.loads(job['spools_snapshot_json'] or '{}')
                    booked = db.execute('SELECT 1 FROM deductions WHERE job_id=? AND spool_id=?', (job['id'],spool_id)).fetchone() if 'deductions' in tables else None
                    if in_mapping or in_stock or booked:
                        raise InventoryError('Die Spule wird noch von einem offenen Druckauftrag benötigt. Bitte den Auftrag zuerst abschließen oder klären.')
            now = _now()
            db.execute('UPDATE spools SET removed_at=?,updated_at=? WHERE id=?', (now,now,spool_id))
            if _db is None:
                db.commit()

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
                    "SELECT current_weight_g FROM spools WHERE id=? AND removed_at IS NULL", (spool_id,)
                ).fetchone()
                if spool is None:
                    raise InventoryError(f"Spule {spool_id} existiert nicht.")

                before = spool["current_weight_g"]
                if before is not None and amount > before + 1e-9:
                    raise InsufficientStock(
                        f"Bekannter Restbestand {before:g} g reicht für {amount:g} g nicht aus."
                    )
                after = round(max(0.0, before - amount), 2) if before is not None else None
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
                    "SELECT current_weight_g FROM spools WHERE id=? AND removed_at IS NULL", (spool_id,)
                ).fetchone()
                if spool is None:
                    raise InventoryError(f"Spule {spool_id} existiert nicht.")
                before = spool["current_weight_g"]
                after = before + delta if before is not None else None
                if after is not None and after < -1e-9:
                    raise InsufficientStock("Die Korrektur würde einen negativen Restbestand erzeugen.")
                if after is not None:
                    after = round(max(0.0, after), 2)
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

    def weigh_stock(self, *, operation_key, spool_id, gross_grams, tare_grams=None, note=''):
        """Set an absolute measured balance, including previously unknown stock."""
        gross = float(gross_grams)
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            spool = db.execute('SELECT s.*,f.empty_spool_weight_g AS product_tare FROM spools s LEFT JOIN filaments f ON f.id=s.filament_id WHERE s.id=?', (spool_id,)).fetchone()
            if spool is None:
                raise InventoryError('Spule existiert nicht.')
            if spool['removed_at'] is not None:
                raise InventoryError('Diese Spule wurde aus dem Inventar entfernt.')
            tare = tare_grams if tare_grams is not None else (spool['empty_spool_weight_g'] if spool['empty_spool_weight_g'] is not None else spool['product_tare'])
            if tare is None:
                raise InventoryError('Bitte das Leerspulengewicht eintragen.')
            tare = float(tare)
            if not operation_key or not all(math.isfinite(v) and v >= 0 for v in (gross, tare)) or gross < tare:
                raise InventoryError('Gesamtgewicht muss mindestens dem Leerspulengewicht entsprechen; beide Gewichte müssen endlich und nicht negativ sein.')
            after = round(gross - tare, 2)
            payload = 'Wiegekorrektur: ' + json.dumps(dict(gross_g=gross, tare_g=tare, note=note), sort_keys=True, ensure_ascii=False)
            existing = db.execute('SELECT * FROM inventory_movements WHERE operation_key=?', (operation_key,)).fetchone()
            if existing:
                if existing['spool_id'] != spool_id or existing['note'] != payload or existing['movement_type'] != 'adjustment':
                    raise IdempotencyConflict('Buchungskennung wurde mit anderen Wiegedaten wiederverwendet.')
                return {'applied': False, 'movement_id': existing['id']}
            before = spool['current_weight_g']
            delta = round(after - before, 2) if before is not None else after
            now = _now()
            ident = db.execute("INSERT INTO inventory_movements(operation_key,spool_id,movement_type,delta_g,before_weight_g,after_weight_g,note,created_at) VALUES (?,?,'adjustment',?,?,?,?,?)", (operation_key,spool_id,delta,before,after,payload,now)).lastrowid
            db.execute('UPDATE spools SET current_weight_g=?,empty_spool_weight_g=?,updated_at=? WHERE id=?', (after,tare,now,spool_id))
            db.commit()
            return {'applied': True, 'movement_id': ident}

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
