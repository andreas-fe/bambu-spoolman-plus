import pytest
from flask import Flask
from bridge.inventory_db import apply_migrations, connection
from bridge.inventory_service import InventoryService, InventoryError, IdempotencyConflict
from bridge.inventory_api import register_inventory


@pytest.fixture
def inventory(tmp_path):
    path=tmp_path/'inventory.db';apply_migrations(path)
    app=Flask(__name__);app.secret_key='test';register_inventory(app,path,True)
    client=app.test_client()
    assert client.post('/inventory/filaments/save',data={'name':'Gold','material':'PETG','empty_spool_weight_g':'210'}).status_code==302
    assert client.post('/inventory/spools/save',data={'filament_id':'1'}).status_code==302
    return path,client


def test_weigh_unknown_and_replay_absolute_measurement(inventory):
    path,client=inventory
    payload=dict(direction='weigh',operation_key='weigh-once',spool_id='1',gross_grams='1010',tare_grams='210')
    assert client.post('/inventory/adjust',data=payload).status_code==302
    assert client.post('/inventory/adjust',data=payload).status_code==302
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0]==800
        rows=db.execute('SELECT * FROM inventory_movements').fetchall()
        assert len(rows)==1 and rows[0]['before_weight_g'] is None and rows[0]['after_weight_g']==800
    assert client.post('/inventory/adjust',data=dict(payload,gross_grams='1020')).status_code==400
    assert client.post('/inventory/adjust',data=dict(payload,operation_key='empty',gross_grams='210')).status_code==302
    assert client.get('/api/inventory/spools').json[0]['current_weight_g']==0


@pytest.mark.parametrize('gross,tare',[('209','210'),('nan','210'),('inf','210'),('1000','-1')])
def test_weigh_rejects_invalid_weights_without_changes(inventory,gross,tare):
    path,client=inventory
    assert client.post('/inventory/adjust',data=dict(direction='weigh',operation_key='invalid',spool_id='1',gross_grams=gross,tare_grams=tare)).status_code==400
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] is None
        assert db.execute('SELECT count(*) FROM inventory_movements').fetchone()[0]==0


def test_weigh_product_default_and_spool_override(inventory):
    path,_=inventory
    with connection(path) as db:db.execute('UPDATE spools SET empty_spool_weight_g=NULL')
    service=InventoryService(path)
    service.weigh_stock(operation_key='fallback',spool_id=1,gross_grams=1000)
    service.weigh_stock(operation_key='override',spool_id=1,gross_grams=1000,tare_grams=200)
    with connection(path) as db:
        row=db.execute('SELECT current_weight_g,empty_spool_weight_g FROM spools').fetchone()
        assert tuple(row)==(800,200)


def test_delete_unused_and_protect_journal_and_product_references(inventory):
    path,client=inventory
    assert client.post('/inventory/filaments/1/delete',data={'confirm':'delete'}).status_code==400
    assert client.post('/inventory/spools/1/delete').status_code==400
    assert client.post('/inventory/spools/1/delete',data={'confirm':'delete'}).status_code==302
    assert client.post('/inventory/filaments/1/delete',data={'confirm':'delete'}).status_code==302
    client.post('/inventory/filaments/save',data={'name':'Red'})
    client.post('/inventory/spools/save',data={'filament_id':'1'})
    InventoryService(path).weigh_stock(operation_key='protected',spool_id=1,gross_grams=1000,tare_grams=200)
    assert client.post('/inventory/spools/1/delete',data={'confirm':'delete'}).status_code==400
    assert len(client.get('/api/inventory/spools').json)==1


def test_remove_archived_duplicate_keeps_history_and_active_twin(inventory):
    path,client=inventory
    service=InventoryService(path)
    service.adjust_stock(operation_key='old1',spool_id=1,delta_grams=1000)
    service.adjust_stock(operation_key='old2',spool_id=1,delta_grams=1000)
    with connection(path) as db:
        db.execute('UPDATE spools SET archived=1 WHERE id=1')
        db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,state TEXT,slot_mapping_json TEXT,spools_snapshot_json TEXT)")
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)',('old','DONE','{"3":"1"}','{"1":{}}'))
        db.execute("INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at,released_at) VALUES ('test','3',1,'2026-01-01','2026-01-02')")
        history=[tuple(r) for r in db.execute('SELECT * FROM inventory_movements')]
    assert client.post('/inventory/spools/save',data={'filament_id':'1','initial_weight_g':'1000'}).status_code==302
    with connection(path) as db:
        db.execute("INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at) VALUES ('test','3',2,'t')")
    html=client.get('/inventory').text
    assert 'Aus Inventar entfernen' in html
    assert client.post('/inventory/spools/1/delete',data={'confirm':'delete'}).status_code==302
    assert client.post('/inventory/spools/1/delete',data={'confirm':'delete'}).status_code==302
    assert [r['id'] for r in client.get('/api/inventory/spools').json]==[2]
    with connection(path) as db:
        assert db.execute('SELECT removed_at FROM spools WHERE id=1').fetchone()[0]
        assert [tuple(r) for r in db.execute('SELECT * FROM inventory_movements')]==history
        assert db.execute('SELECT current_weight_g FROM spools WHERE id=2').fetchone()[0]==1000
        assert db.execute('SELECT spool_id FROM ams_assignments WHERE released_at IS NULL').fetchone()[0]==2
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert client.post('/inventory/spools/save',data={'id':'1','filament_id':'1','archived':'0'}).status_code==400
    assert client.post('/inventory/adjust',data={'spool_id':'1','direction':'weigh','gross_grams':'1000','tare_grams':'200','operation_key':'no'}).status_code==400
    with pytest.raises(InventoryError):
        service.adjust_stock(operation_key='after-remove',spool_id=1,delta_grams=10)


@pytest.mark.parametrize('state',['RUNNING','WAITING_METADATA','WAITING_MAPPING','UNKNOWN','RETRY'])
def test_remove_blocks_open_job_even_for_archived_spool(inventory,state):
    path,client=inventory
    with connection(path) as db:
        db.execute('UPDATE spools SET archived=1')
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,state TEXT,slot_mapping_json TEXT,spools_snapshot_json TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)',('pending',state,'{}','{"1":{}}'))
    response=client.post('/inventory/spools/1/delete',data={'confirm':'delete'})
    assert response.status_code==400 and 'offenen Druckauftrag' in response.text
    assert len(client.get('/api/inventory/spools').json)==1


def test_remove_blocks_current_assignment(inventory):
    path,client=inventory
    with connection(path) as db:
        db.execute('UPDATE spools SET archived=1')
        db.execute("INSERT INTO ams_assignments(printer_id,slot_id,spool_id,assigned_at) VALUES ('test','0',1,'t')")
    response=client.post('/inventory/spools/1/delete',data={'confirm':'delete'})
    assert response.status_code==400 and 'aktuell einem Schacht' in response.text


def test_new_vendor_inside_product_form_is_atomic_and_reused(inventory):
    path,client=inventory
    payload=dict(name='Test PLA',material='PLA+',vendor_id='__new__',vendor_new='New Maker')
    assert client.post('/inventory/filaments/save',data=payload).status_code==302
    assert client.post('/inventory/filaments/save',data=dict(payload,vendor_new='new maker')).status_code==302
    with connection(path) as db:
        assert db.execute('SELECT count(*) FROM vendors').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM filaments WHERE vendor_id IS NOT NULL').fetchone()[0]==2
    assert client.post('/inventory/filaments/save',data=dict(payload,vendor_new='Not Created',empty_spool_weight_g='-1')).status_code==400
    with connection(path) as db:
        assert db.execute('SELECT count(*) FROM vendors').fetchone()[0]==1


def test_stock_balances_round_to_centigrams(inventory):
    path, _ = inventory
    service = InventoryService(path)
    service.weigh_stock(operation_key='precision-weigh', spool_id=1,
                       gross_grams=1101.307, tare_grams=210)
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 891.31
    service.adjust_stock(operation_key='precision-adjust', spool_id=1, delta_grams=-0.01)
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 891.30
    service.apply_consumption(operation_key='precision-use', job_id='precision-job',
                              source_key='slot-1', spool_id=1, grams=0.01)
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 891.29
