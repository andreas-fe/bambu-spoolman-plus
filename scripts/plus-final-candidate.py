"""Container helper: compare stopped Spoolman source and prepare cutover copy.

Mount Spoolman data at /spoolman (read-only), stopped bridge data at /legacy
(read-only), and the private backup directory at /work (writable).
"""
import json
import math
import os
import sqlite3
import shutil
from pathlib import Path
from bridge.inventory_db import connection, _now
from bridge.migrate_inventory import activate
from bridge.inventory_exchange import export_inventory

snapshot = json.loads(Path('/work/spoolman-snapshot.json').read_text())
databases = list(Path('/spoolman').glob('*.db'))
assert len(databases) == 1, 'Spoolman-Datenbank nicht eindeutig'
raw = Path('/work/raw-spoolman')
raw.mkdir()
for suffix in ('', '-wal', '-shm'):
    original = Path(str(databases[0]) + suffix)
    if original.exists():
        shutil.copy2(original, raw / original.name)
with sqlite3.connect(raw / databases[0].name) as source:
    source.row_factory = sqlite3.Row
    assert source.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with sqlite3.connect('/work/spoolman.sqlite3') as target:
        source.backup(target)
    for kind, table in [('vendors', 'vendor'), ('filaments', 'filament'), ('spools', 'spool')]:
        exported = {r['id']: r for r in snapshot[kind]}
        rows = source.execute(f'SELECT * FROM {table}').fetchall()
        assert set(exported) == {r['id'] for r in rows}, 'Quell-IDs während Export geändert'
        for row in rows:
            record = exported[row['id']]
            for key in row.keys():
                if key in ('registered', 'first_used', 'last_used'):
                    continue  # API formats SQLite timestamps differently.
                if key.endswith('_id') and key[:-3] in ('vendor', 'filament'):
                    value = (record.get(key[:-3]) or {}).get('id')
                elif key in record:
                    value = record[key]
                else:
                    continue  # Full SQLite backup preserves fields absent from API.
                if key == 'color_hex':
                    assert str(value or '').casefold() == str(row[key] or '').casefold(), 'Quellfarbe geändert'
                elif isinstance(value, (int, float)) and row[key] is not None:
                    assert math.isclose(value, row[key], abs_tol=1e-7), f'Quellwert geändert: {table}/{key}'
                else:
                    assert value == row[key], f'Quellwert geändert: {table}/{key}'
path = Path('/work/candidate.sqlite3')
assert not path.exists(), 'Kandidat existiert bereits'
with sqlite3.connect('/work/bridge.sqlite3') as source, sqlite3.connect(path) as target:
    source.backup(target)
with sqlite3.connect(path) as db:
    before = {t: db.execute(f'SELECT * FROM {t} ORDER BY id').fetchall() for t in ('jobs', 'deductions')}
result = activate(path, snapshot, json.loads(Path('/legacy/mapping.json').read_text()), os.environ['BAMBU_SERIAL'])
with connection(path) as db:
    for table, rows in before.items():
        assert [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY id')] == rows, 'Altjournal geändert'
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert db.execute('SELECT COUNT(*) FROM spools').fetchone()[0] == len(snapshot['spools'])
    expected = sum(s['remaining_weight'] for s in snapshot['spools'] if s.get('remaining_weight') is not None)
    assert math.isclose(db.execute('SELECT SUM(current_weight_g) FROM spools').fetchone()[0] or 0, expected, abs_tol=1e-7)
    assert db.execute('SELECT COUNT(*) FROM spools WHERE current_weight_g IS NULL').fetchone()[0] == sum(s.get('remaining_weight') is None for s in snapshot['spools'])
    db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES ('dry_run','true',?)", (_now(),))
Path('/work/native-export.json').write_text(json.dumps(export_inventory(path), ensure_ascii=False, indent=2))
print('FINAL_CANDIDATE_OK', json.dumps(result))
