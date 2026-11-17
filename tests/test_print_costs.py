import json
from decimal import Decimal
import pytest
from bridge.inventory_db import connection,apply_migrations
from bridge.inventory_service import InventoryService
from bridge.print_costs import PrintCosts,decimal_input


@pytest.fixture
def cost_data(tmp_path):
    path=tmp_path/'costs.sqlite3'
    with connection(path) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,created_at TEXT,state TEXT,filename TEXT,subtask_name TEXT,dry_run INTEGER)')
        db.execute("INSERT INTO jobs VALUES ('job','PRINT','1970-01-01T00:16:40+00:00','RUNNING','Demo.3mf','Demo',0)")
    apply_migrations(path)
    with connection(path) as db:
        db.execute("INSERT INTO filaments(id,name,net_weight_g,created_at,updated_at) VALUES(1,'PLA',1000,'t','t')")
        db.execute("INSERT INTO spools(id,filament_id,initial_weight_g,current_weight_g,purchase_price_minor,currency,created_at,updated_at) VALUES(1,1,500,500,2000,'EUR','t','t')")
        db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES('electricity_eur_per_kwh','\"0.30\"','t')")
    clock=[1000.0];costs=PrintCosts(path,clock=lambda:clock[0]);return path,costs,clock


def book(path,grams=60):
    return InventoryService(path).apply_consumption(operation_key='use',job_id='job',source_key='one',spool_id=1,grams=grams)


def test_prices_and_energy_are_frozen_without_changing_inventory_accounting(cost_data):
    path,costs,clock=cost_data;costs.start('job')
    with connection(path) as db:db.execute("UPDATE settings SET value_json='\"0.99\"'")
    costs.start('job')
    first=book(path);assert not book(path)['applied']
    with connection(path) as db:
        db.execute("UPDATE spools SET purchase_price_minor=9900,price_weight_g=500")
        db.execute("UPDATE jobs SET state='DONE'")
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0]==440
        assert db.execute('SELECT COUNT(*) FROM consumption_entries').fetchone()[0]==1
    for stamp in range(1000,4601,60):
        clock[0]=stamp;costs.sample('job',dict(observed_at=stamp,power_w=100))
    costs.stop('job');costs.stop('job');summary=costs.summary('job')
    assert summary['material_label']=='1,20 €' # nominal 1000 g, never current/initial 500 g
    assert summary['energy_kwh']==.1 and summary['electricity_label']=='0,03 €'
    assert summary['total_label']=='1,23 €' and summary['coverage_percent']==100 and summary['complete']
    assert summary['rate']=='0.30'
    clock[0]+=30;costs.sample('job',dict(observed_at=clock[0],power_w=999));assert costs.summary('job')==summary


def test_missing_prices_gaps_and_foreign_currency_remain_unknown(cost_data):
    path,costs,clock=cost_data
    with connection(path) as db:db.execute("UPDATE spools SET currency='USD'")
    book(path);costs.start('job');costs.sample('job',dict(observed_at=1000,power_w=100))
    clock[0]=1030;costs.sample('job',{}) # broken read clears continuity
    clock[0]=4600;costs.sample('job',dict(observed_at=4600,power_w=100));costs.stop('job')
    summary=costs.summary('job');assert summary['material_label']=='Nicht erfasst' and summary['total_label']=='Unvollständig'
    assert summary['energy_kwh']==0 and summary['coverage_percent']==0
    with connection(path) as db:db.execute("UPDATE spools SET currency='EUR',purchase_price_minor=0")
    assert costs.summary('job')['material_label']=='Nicht erfasst' # no retrospective repricing


def test_free_spool_explicit_weight_and_partial_booking(cost_data):
    path,costs,clock=cost_data
    with connection(path) as db:db.execute('UPDATE spools SET purchase_price_minor=0,price_weight_g=750')
    book(path);summary=costs.summary('job')
    assert summary['material_label']=='0,00 €' and not summary['material_complete']
    with connection(path) as db:db.execute("UPDATE jobs SET state='DONE'")
    assert costs.summary('job')['material_complete']


def test_reconcile_does_not_invent_energy_across_restarts(cost_data):
    path,costs,clock=cost_data;costs.start('job');costs.sample('job',dict(observed_at=1000,power_w=200))
    clock[0]=2000;costs.reconcile(None)
    assert costs.summary('job')['energy_kwh']==0 and not costs.summary('job')['active']
    with connection(path) as db:
        db.execute("INSERT INTO jobs SELECT 'dry',kind,created_at,state,filename,subtask_name,1 FROM jobs WHERE id='job'")
    costs.start('dry');assert costs.summary('dry')['energy_kwh'] is None


@pytest.mark.parametrize('bad',['nan','inf','-1','100001','2.001','bad'])
def test_price_validation_rejects_invalid_prices(bad):
    with pytest.raises(ValueError):decimal_input(bad)


def test_local_decimal_zero_and_missing_price():
    assert decimal_input('19,99')==Decimal('19.99')
    assert decimal_input('0')==0 and decimal_input('') is None


def test_spool_price_editor_validation_and_foreign_currency_preservation(cost_data):
    from flask import Flask
    from bridge.inventory_api import register_inventory
    path,costs,clock=cost_data;app=Flask(__name__);app.secret_key='test';register_inventory(app,path,True);client=app.test_client()
    assert 'Kaufpreis der Rolle' in client.get('/inventory').text
    payload=dict(id=1,filament_id=1,purchase_price_eur='19,95',price_weight_g='750')
    assert client.post('/inventory/spools/save',data=payload).status_code==302
    with connection(path) as db:
        row=db.execute('SELECT * FROM spools').fetchone()
        assert row['purchase_price_minor']==1995 and row['price_weight_g']==750 and row['current_weight_g']==500 and row['currency']=='EUR'
    for key,value in [('purchase_price_eur','nan'),('purchase_price_eur','-2'),('price_weight_g','0')]:
        assert client.post('/inventory/spools/save',data={**payload,key:value}).status_code==400
    with connection(path) as db:db.execute("UPDATE spools SET currency='USD'")
    assert client.post('/inventory/spools/save',data={**payload,'purchase_price_eur':''}).status_code==302
    with connection(path) as db:
        assert db.execute('SELECT currency FROM spools').fetchone()[0]=='USD'


def test_total_matches_displayed_rounded_components_and_multiple_spools(cost_data):
    path,costs,clock=cost_data
    with connection(path) as db:
        db.execute("UPDATE settings SET value_json='\"0.35\"'")
        db.execute('UPDATE spools SET purchase_price_minor=1999')
        db.execute("INSERT INTO spools(id,filament_id,current_weight_g,purchase_price_minor,price_weight_g,currency,created_at,updated_at) VALUES(2,1,500,0,1000,'EUR','t','t')")
    costs.start('job');book(path,42)
    InventoryService(path).apply_consumption(operation_key='free',job_id='job',source_key='two',spool_id=2,grams=5)
    for stamp in range(1000,4601,60):
        clock[0]=stamp;costs.sample('job',dict(observed_at=stamp,power_w=100))
    costs.stop('job')
    with connection(path) as db:db.execute("UPDATE jobs SET state='DONE'")
    summary=costs.summary('job');assert summary['entries']==2 and summary['material_label']=='0,84 €' and summary['electricity_label']=='0,04 €' and summary['total_label']=='0,88 €'
