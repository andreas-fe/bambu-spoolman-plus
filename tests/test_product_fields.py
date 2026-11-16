from flask import Flask

from bridge.inventory_api import register_inventory
from bridge.inventory_db import apply_migrations, connection, _V1_STATEMENTS


def test_v2_migration_adds_product_defaults_without_changing_existing_data(tmp_path):
    path = tmp_path / 'inventory.db'
    with connection(path) as db:
        for statement in _V1_STATEMENTS:
            db.execute(statement)
        db.execute('CREATE TABLE inventory_job_plans (job_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        db.execute("INSERT INTO schema_migrations VALUES (2,'before')")
        db.execute("INSERT INTO filaments(name,material,subtype,color_name,article_number,custom_fields_json,created_at,updated_at) VALUES ('Red','PETG','CF','Rot','SKU','{\"keep\":true}','t','t')")
        db.execute("INSERT INTO spools(filament_id,current_weight_g,empty_spool_weight_g,created_at,updated_at) VALUES (1,400,217,'t','t')")
        product_before = dict(db.execute('SELECT * FROM filaments').fetchone())
        spool_before = dict(db.execute('SELECT * FROM spools').fetchone())
    assert apply_migrations(path) == apply_migrations(path) == 4
    with connection(path) as db:
        product = dict(db.execute('SELECT * FROM filaments').fetchone())
        assert {key: product[key] for key in product_before} == product_before
        assert product['empty_spool_weight_g'] is None and product['notes'] == ''
        spool_after = dict(db.execute('SELECT * FROM spools').fetchone())
        assert spool_after.pop('removed_at') is None
        assert spool_after == spool_before


def test_product_fields_persist_and_new_spools_copy_default_only_once(tmp_path):
    path = tmp_path / 'inventory.db'
    apply_migrations(path)
    app = Flask(__name__)
    app.secret_key = 'test'
    register_inventory(app, path, True)
    client = app.test_client()
    payload = dict(name='Schwarz', material='PETG-CF', empty_spool_weight_g='210.5', notes='Pappspule; Nachfüllrolle möglich')
    assert client.post('/inventory/filaments/save', data=payload).status_code == 302
    with connection(path) as db:
        db.execute("UPDATE filaments SET article_number='keep', color_name='legacy',subtype='legacy'")
    for override in [None, '0', '199']:
        spool = dict(filament_id='1', initial_weight_g='1000')
        if override is not None:
            spool['empty_spool_weight_g'] = override
        assert client.post('/inventory/spools/save', data=spool).status_code == 302
    assert client.post('/inventory/filaments/save', data=dict(payload, id='1', empty_spool_weight_g='250')).status_code == 302
    with connection(path) as db:
        product = dict(db.execute('SELECT * FROM filaments').fetchone())
        assert product['material'] == 'PETG-CF' and product['notes'] == payload['notes']
        assert (product['article_number'], product['color_name'], product['subtype']) == ('keep', 'legacy', 'legacy')
        assert [r[0] for r in db.execute('SELECT empty_spool_weight_g FROM spools ORDER BY id')] == [210.5, 0, 199]
        assert db.execute('SELECT count(*) FROM inventory_movements').fetchone()[0] == 0
    assert client.post('/inventory/spools/save', data=dict(id='1', filament_id='1', empty_spool_weight_g='')).status_code == 302
    assert client.get('/api/inventory/spools').json[0]['empty_spool_weight_g'] is None
    for invalid in ['-1', 'nan', 'inf']:
        assert client.post('/inventory/filaments/save', data=dict(payload, empty_spool_weight_g=invalid)).status_code == 400


def test_product_editor_has_requested_fields_and_no_obsolete_inputs(tmp_path):
    path = tmp_path / 'inventory.db'
    apply_migrations(path)
    app = Flask(__name__)
    app.secret_key = 'test'
    register_inventory(app, path, True)
    html = app.test_client().get('/inventory').text
    editor = html.split('id="form-filaments"', 1)[1].split('</form>', 1)[0]
    assert 'name="empty_spool_weight_g"' in editor and 'name="notes"' in editor
    assert 'Sonstiges' in editor and 'Filamenttyp' in editor
    for field in ['article_number', 'color_name', 'subtype']:
        assert f'name="{field}"' not in editor
