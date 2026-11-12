"""Container helper: capture an offline bridge and still-running Spoolman API.

Mount the stopped bridge data at /legacy and the private backup directory at
/work. This helper never writes to the source volumes.
"""
import json
import os
import sqlite3
import shutil
from pathlib import Path
import requests

raw = Path('/work/raw-bridge')
raw.mkdir()
assert Path('/legacy/bridge.sqlite3').is_file(), 'Bridge-Datenbank fehlt'
for suffix in ('', '-wal', '-shm'):
    original = Path('/legacy/bridge.sqlite3' + suffix)
    if original.exists():
        shutil.copy2(original, raw / original.name)
with sqlite3.connect(raw / 'bridge.sqlite3') as source:
    assert not source.execute("SELECT 1 FROM jobs WHERE state IN ('RUNNING','BOOKING','FINISH_PENDING','CANCEL_PENDING','MANUAL_PENDING','RETRY','WAITING_MAPPING','WAITING_METADATA')").fetchone(), 'Offene Jobs'
    assert not source.execute("SELECT 1 FROM deductions WHERE status IN ('PENDING','RETRY','IN_FLIGHT','UNMAPPED')").fetchone(), 'Offene Buchungen'
    assert not source.execute("SELECT 1 FROM runtime WHERE key='active_job_id' AND value IS NOT NULL").fetchone(), 'Aktiver Auftrag'
    with sqlite3.connect('/work/bridge.sqlite3') as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
base = os.environ['SPOOLMAN_URL'].rstrip('/')
snapshot = {}
for kind, endpoint in [('vendors', 'vendor'), ('filaments', 'filament'), ('spools', 'spool?allow_archived=true')]:
    response = requests.get(base + '/api/v1/' + endpoint, timeout=(5, 30))
    response.raise_for_status()
    snapshot[kind] = response.json()
Path('/work/spoolman-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
print('Finaler Snapshot:', {key: len(rows) for key, rows in snapshot.items()})
