import sqlite3

import pytest

from bridge.inventory_db import apply_migrations, connection


def test_schema_migration_preserves_existing_bridge_ledger(tmp_path):
    path = tmp_path / "bridge.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE deductions (
                id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                spool_id INTEGER,
                grams REAL NOT NULL
            );
            INSERT INTO jobs VALUES ('job-1', 'DONE');
            INSERT INTO deductions VALUES (17, 'job-1', 924, 12.5);
            """
        )

    assert apply_migrations(path) == 6
    assert apply_migrations(path) == 6

    with connection(path) as db:
        assert dict(db.execute("SELECT * FROM jobs WHERE id='job-1'").fetchone()) == {
            "id": "job-1",
            "state": "DONE",
        }
        assert dict(db.execute("SELECT * FROM deductions WHERE id=17").fetchone()) == {
            "id": 17,
            "job_id": "job-1",
            "spool_id": 924,
            "grams": 12.5,
        }
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 6


def test_spool_keeps_source_id_and_unknown_weights_as_unknown(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    apply_migrations(path)
    with connection(path) as db:
        vendor_id = db.execute(
            "INSERT INTO vendors(name, source_system, source_id, created_at, updated_at) "
            "VALUES ('Example', 'spoolman', '5', 't', 't')"
        ).lastrowid
        filament_id = db.execute(
            "INSERT INTO filaments(vendor_id, name, source_system, source_id, created_at, updated_at) "
            "VALUES (?, 'PLA White', 'spoolman', '42', 't', 't')",
            (vendor_id,),
        ).lastrowid
        spool_id = db.execute(
            "INSERT INTO spools(filament_id, source_system, source_id, created_at, updated_at) "
            "VALUES (?, 'spoolman', '924', 't', 't')",
            (filament_id,),
        ).lastrowid
        spool = db.execute("SELECT * FROM spools WHERE id=?", (spool_id,)).fetchone()

    assert spool["id"] != 924
    assert spool["source_system"] == "spoolman"
    assert spool["source_id"] == "924"
    assert spool["initial_weight_g"] is None
    assert spool["current_weight_g"] is None


def test_external_spoolman_id_is_unique_per_source(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    apply_migrations(path)
    with connection(path) as db:
        db.execute(
            "INSERT INTO spools(source_system, source_id, created_at, updated_at) "
            "VALUES ('spoolman', '924', 't', 't')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO spools(source_system, source_id, created_at, updated_at) "
                "VALUES ('spoolman', '924', 't', 't')"
            )


def test_active_ams_slot_is_unique_but_history_is_kept(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    apply_migrations(path)
    with connection(path) as db:
        first = db.execute(
            "INSERT INTO spools(created_at, updated_at) VALUES ('t', 't')"
        ).lastrowid
        second = db.execute(
            "INSERT INTO spools(created_at, updated_at) VALUES ('t', 't')"
        ).lastrowid
        db.execute(
            "INSERT INTO ams_assignments(printer_id, slot_id, spool_id, assigned_at) "
            "VALUES ('a1', '0', ?, 't1')",
            (first,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO ams_assignments(printer_id, slot_id, spool_id, assigned_at) "
                "VALUES ('a1', '0', ?, 't2')",
                (second,),
            )
        db.execute("UPDATE ams_assignments SET released_at='t2'")
        db.execute(
            "INSERT INTO ams_assignments(printer_id, slot_id, spool_id, assigned_at) "
            "VALUES ('a1', '0', ?, 't3')",
            (second,),
        )
        assert db.execute("SELECT COUNT(*) FROM ams_assignments").fetchone()[0] == 2


def test_failed_migration_rolls_back_version_and_partial_schema(tmp_path):
    path = tmp_path / "bad.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE vendors (id INTEGER PRIMARY KEY)")

    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(path)

    with sqlite3.connect(path) as db:
        table_names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "schema_migrations" not in table_names
    assert "locations" not in table_names
