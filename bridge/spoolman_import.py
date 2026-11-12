"""Offline, validated Spoolman snapshots; never contacts or modifies Spoolman."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .inventory_db import connection, _now


class ImportValidationError(ValueError):
    pass


def preview(snapshot: dict) -> dict:
    errors = []
    indices = {}
    for kind in ('vendors', 'filaments', 'spools'):
        rows = snapshot.get(kind)
        if not isinstance(rows, list):
            errors.append(f'{kind}: Liste erforderlich')
            rows = []
        index = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), int) or isinstance(row.get('id'), bool) or row['id'] <= 0:
                errors.append(f'{kind}: ungültige ID')
                continue
            if row['id'] in index:
                errors.append(f'{kind}: doppelte ID {row["id"]}')
            index[row['id']] = row
        indices[kind] = index
    for row in indices['filaments'].values():
        vendor = row.get('vendor') or {}
        if vendor and vendor.get('id') not in indices['vendors']:
            errors.append(f'filament {row["id"]}: Hersteller fehlt')
        for field in ('diameter', 'density', 'weight'):
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or (field != 'weight' and value == 0)):
                errors.append(f'filament {row["id"]}: ungültiges {field}')
    known = []
    for row in indices['spools'].values():
        if (row.get('filament') or {}).get('id') not in indices['filaments']:
            errors.append(f'spool {row["id"]}: Produkt fehlt')
        for field in ('remaining_weight', 'initial_weight', 'spool_weight'):
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0):
                errors.append(f'spool {row["id"]}: ungültiges {field}')
        if row.get('location') is not None and not isinstance(row['location'], str):
            errors.append(f'spool {row["id"]}: ungültiger Lagerort')
        weight = row.get('remaining_weight')
        if isinstance(weight, (float, int)) and not isinstance(weight, bool) and math.isfinite(weight) and weight >= 0:
            known.append(weight)
    return dict(valid=not errors, errors=errors,
                counts={k: len(v) for k, v in indices.items()},
                archived=sum(bool(s.get('archived')) for s in indices['spools'].values()),
                known_weight_g=sum(known), unknown_weights=len(indices['spools']) - len(known))


def apply_snapshot(path: str | Path, snapshot: dict) -> dict:
    """Import once into an empty inventory; archive the complete original JSON.

    A repeated identical snapshot is a no-op, even after consumption. A changed
    snapshot cannot overwrite live balances or silently merge source revisions.
    The caller must migrate a backup/test database before production cutover.
    """
    report = preview(snapshot)
    if not report['valid']:
        raise ImportValidationError('; '.join(report['errors']))
    raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    now = _now()
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            previous = db.execute("SELECT id FROM import_runs WHERE source_snapshot_sha256=? AND status='applied'", (digest,)).fetchone()
            if previous:
                db.commit()
                return dict(report, applied=False, run_id=previous['id'])
            if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in ('spools', 'filaments', 'vendors')):
                raise ImportValidationError('Import erfordert ein leeres Inventar; bestehende Bestände werden nicht überschrieben.')
            run = db.execute("INSERT INTO import_runs(source_system,status,started_at,source_snapshot_json,source_snapshot_sha256,summary_json) VALUES ('spoolman','validated',?,?,?,?)", (now, raw, digest, json.dumps(report))).lastrowid
            maps = {'vendors': {}, 'filaments': {}}
            for row in snapshot['vendors']:
                maps['vendors'][row['id']] = db.execute('INSERT INTO vendors(name,source_system,source_id,source_payload_json,created_at,updated_at) VALUES (?,\'spoolman\',?,?,?,?)', (row.get('name') or '', str(row['id']), json.dumps(row), now, now)).lastrowid
            for row in snapshot['filaments']:
                vendor_id = maps['vendors'].get((row.get('vendor') or {}).get('id'))
                maps['filaments'][row['id']] = db.execute('INSERT INTO filaments(vendor_id,name,material,color_json,custom_fields_json,diameter_mm,density_g_cm3,net_weight_g,source_system,source_id,source_payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,\'spoolman\',?,?,?,?)', (vendor_id, row.get('name') or '', row.get('material') or '', json.dumps(row.get('color_hex')), json.dumps(row.get('extra') or {}), row.get('diameter'), row.get('density'), row.get('weight'), str(row['id']), json.dumps(row), now, now)).lastrowid
            for row in snapshot['spools']:
                location_id = None
                if row.get('location'):
                    name = row['location']
                    existing = db.execute('SELECT id FROM locations WHERE parent_id IS NULL AND name=?', (name,)).fetchone()
                    location_id = existing['id'] if existing else db.execute('INSERT INTO locations(name,created_at,updated_at) VALUES (?,?,?)', (name, now, now)).lastrowid
                db.execute('INSERT INTO spools(filament_id,source_system,source_id,initial_weight_g,current_weight_g,empty_spool_weight_g,location_id,archived,notes,custom_fields_json,source_payload_json,created_at,updated_at) VALUES (?,\'spoolman\',?,?,?,?,?,?,?,?,?,?,?)', (maps['filaments'][row['filament']['id']], str(row['id']), row.get('initial_weight'), row.get('remaining_weight'), row.get('spool_weight'), location_id, int(bool(row.get('archived'))), row.get('comment') or '', json.dumps(row.get('extra') or {}), json.dumps(row), now, now))
            db.execute("UPDATE import_runs SET status='applied',finished_at=? WHERE id=?", (now, run))
            db.commit()
            return dict(report, applied=True, run_id=run)
        except Exception:
            db.rollback()
            raise
