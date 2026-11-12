"""Explicit offline cutover. Run only with the writing bridge stopped."""
import argparse
import json
from pathlib import Path
from .inventory_db import apply_migrations, connection, _now
from .spoolman_import import apply_snapshot


def activate(path, snapshot, mapping, printer):
    apply_migrations(path)
    # Validation and import run on a backup/test database before production.
    result = apply_snapshot(path, snapshot)
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute("SELECT 1 FROM settings WHERE key='cutover_job_rowid'").fetchone():
                raise ValueError('Datenbank ist bereits umgeschaltet.')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('RUNNING','BOOKING','FINISH_PENDING','CANCEL_PENDING','MANUAL_PENDING','RETRY','WAITING_MAPPING','WAITING_METADATA')").fetchone():
                raise ValueError('Offene Jobs verhindern die Umschaltung.')
            if db.execute("SELECT 1 FROM deductions WHERE status IN ('PENDING','RETRY','IN_FLIGHT','UNMAPPED')").fetchone():
                raise ValueError('Offene Buchungen verhindern die Umschaltung.')
            if db.execute("SELECT value FROM runtime WHERE key='active_job_id' AND value IS NOT NULL").fetchone():
                raise ValueError('Aktiver Auftrag verhindert die Umschaltung.')
            now = _now()
            cutoff = db.execute('SELECT COALESCE(MAX(rowid),0) FROM jobs').fetchone()[0]
            for slot, external in mapping.items():
                if external in (None, ''):
                    continue
                row = db.execute("SELECT id FROM spools WHERE source_system='spoolman' AND source_id=? AND archived=0", (str(external),)).fetchone()
                if not row:
                    raise ValueError(f'Zuordnung für Slot {slot} hat keine aktive importierte Spule.')
                db.execute('INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at,source) VALUES (?,?,?,?,\'migration\')', (printer, slot, row['id'], now))
            db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES ('cutover_job_rowid',?,?)", (json.dumps(cutoff), now))
            db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES ('cutover_at',?,?)", (json.dumps(now), now))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--mapping', required=True)
    parser.add_argument('--printer', required=True)
    parser.add_argument('--activate', action='store_true', help='Bestätigt: Bridge gestoppt, Vollbackup und Testimport geprüft')
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text())
    mapping = json.loads(Path(args.mapping).read_text())
    if args.activate:
        print(json.dumps(activate(args.database, snapshot, mapping, args.printer)))
    else:
        apply_migrations(args.database)
        print(json.dumps(apply_snapshot(args.database, snapshot)))


if __name__ == '__main__':
    main()
