"""Versioned native JSON export and atomic restore into an empty database."""
import json
from .inventory_db import SCHEMA_VERSION, connection

TABLES = ('vendors', 'locations', 'filaments', 'spools', 'spool_tags', 'ams_assignments',
          'jobs', 'deductions', 'inventory_movements', 'consumption_entries', 'import_runs',
          'settings', 'inventory_job_plans', 'runtime')


def export_inventory(path):
    with connection(path) as db:
        db.execute('BEGIN')
        data = {t: [dict(r) for r in db.execute(f'SELECT rowid AS rowid,* FROM {t}' if t == 'jobs' else f'SELECT * FROM {t}')] for t in TABLES}
        data['settings'] = [r for r in data['settings'] if not r['secret']]
        data['runtime'] = [r for r in data['runtime'] if r['key'] == 'active_job_id']
    return dict(format='bambu-spoolman-plus', version=SCHEMA_VERSION, tables=data)


def restore_inventory(path, snapshot):
    if snapshot.get('format') != 'bambu-spoolman-plus' or snapshot.get('version') != SCHEMA_VERSION:
        raise ValueError('Nicht unterstütztes Austauschformat.')
    data = snapshot.get('tables')
    if not isinstance(data, dict) or set(data) != set(TABLES):
        raise ValueError('Unvollständiger Export.')
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if any(db.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() for t in TABLES if t != 'runtime'):
                raise ValueError('Wiederherstellung erfordert eine leere Datenbank.')
            db.execute('PRAGMA defer_foreign_keys=ON')
            db.execute("DELETE FROM runtime WHERE key='active_job_id'")
            for table in TABLES:
                columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                if table == 'jobs':
                    columns.add('rowid')
                if not isinstance(data[table], list):
                    raise ValueError('Ungültige Tabellenliste.')
                for row in data[table]:
                    if not isinstance(row, dict) or not row or not set(row) <= columns:
                        raise ValueError('Ungültige Spalten im Export.')
                    if table == 'runtime' and row.get('key') != 'active_job_id':
                        raise ValueError('Laufzeit-Geheimnisse sind kein Austauschformat.')
                    if table == 'settings' and row.get('secret'):
                        raise ValueError('Geheimnisse werden nicht importiert.')
                    keys = list(row)
                    db.execute(f'INSERT INTO {table}('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')', tuple(row[k] for k in keys))
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Ungültige Fremdschlüssel im Export.')
            db.commit()
        except Exception:
            db.rollback()
            raise
