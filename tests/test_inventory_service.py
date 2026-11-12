import sqlite3

import pytest

from bridge.inventory_db import apply_migrations, connection
from bridge.inventory_service import (
    IdempotencyConflict,
    InsufficientStock,
    InventoryService,
)


def inventory(tmp_path, weight=100.0):
    path = tmp_path / "inventory.sqlite3"
    apply_migrations(path)
    with connection(path) as db:
        spool_id = db.execute(
            "INSERT INTO spools(current_weight_g, created_at, updated_at) VALUES (?, 't', 't')",
            (weight,),
        ).lastrowid
    return path, InventoryService(path), spool_id


def test_consumption_updates_balance_and_journal_atomically(tmp_path):
    path, service, spool_id = inventory(tmp_path)

    result = service.apply_consumption(
        operation_key="job-1:filament:1:spool:1",
        job_id="job-1",
        source_key="filament:1",
        spool_id=spool_id,
        grams=12.5,
        legacy_deduction_id=17,
    )

    assert result["applied"] is True
    with connection(path) as db:
        assert db.execute("SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)).fetchone()[0] == 87.5
        entry = db.execute("SELECT * FROM consumption_entries").fetchone()
        movement = db.execute("SELECT * FROM inventory_movements").fetchone()
        assert entry["legacy_deduction_id"] == 17
        assert entry["movement_id"] == movement["id"]
        assert movement["delta_g"] == -12.5
        assert movement["before_weight_g"] == 100
        assert movement["after_weight_g"] == 87.5


def test_repeated_consumption_is_idempotent_and_conflicts_are_rejected(tmp_path):
    _, service, spool_id = inventory(tmp_path)
    request = dict(
        operation_key="job-1:filament:1:spool:1",
        job_id="job-1",
        source_key="filament:1",
        spool_id=spool_id,
        grams=12.5,
    )
    first = service.apply_consumption(**request)
    second = service.apply_consumption(**request)
    assert first["applied"] is True
    assert second == {"applied": False, "entry_id": first["entry_id"], "movement_id": first["movement_id"]}

    with pytest.raises(IdempotencyConflict):
        service.apply_consumption(**{**request, "grams": 13})


def test_unknown_weight_stays_unknown_after_consumption(tmp_path):
    path, service, spool_id = inventory(tmp_path, weight=None)
    service.apply_consumption(
        operation_key="job-1:filament:1:spool:1",
        job_id="job-1",
        source_key="filament:1",
        spool_id=spool_id,
        grams=2,
    )
    with connection(path) as db:
        assert db.execute("SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)).fetchone()[0] is None
        movement = db.execute("SELECT before_weight_g, after_weight_g FROM inventory_movements").fetchone()
        assert tuple(movement) == (None, None)


def test_failed_journal_insert_rolls_back_balance_and_movement(tmp_path):
    path, service, spool_id = inventory(tmp_path)
    with connection(path) as db:
        db.execute(
            """CREATE TRIGGER reject_consumption BEFORE INSERT ON consumption_entries
               BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        service.apply_consumption(
            operation_key="job-1:filament:1:spool:1",
            job_id="job-1",
            source_key="filament:1",
            spool_id=spool_id,
            grams=12.5,
        )

    with connection(path) as db:
        assert db.execute("SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)).fetchone()[0] == 100
        assert db.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM consumption_entries").fetchone()[0] == 0


def test_adjustment_is_idempotent_and_cannot_make_balance_negative(tmp_path):
    path, service, spool_id = inventory(tmp_path, weight=10)
    request = dict(operation_key="count-1", spool_id=spool_id, delta_grams=-2, note="Wiegen")
    first = service.adjust_stock(**request)
    second = service.adjust_stock(**request)
    assert first["applied"] is True
    assert second == {"applied": False, "movement_id": first["movement_id"]}
    with pytest.raises(InsufficientStock):
        service.adjust_stock(operation_key="count-2", spool_id=spool_id, delta_grams=-9)
    with connection(path) as db:
        assert db.execute("SELECT current_weight_g FROM spools WHERE id=?", (spool_id,)).fetchone()[0] == 8
        assert db.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 1
