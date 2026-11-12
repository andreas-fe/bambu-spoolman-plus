import json
import pytest
from flask import Flask
from bridge.filament_catalog import FilamentCatalog, CatalogUnavailable
from bridge.inventory_db import apply_migrations, connection
from bridge.inventory_api import register_inventory

RECORD = dict(id='example_pla_red_1000_175', manufacturer='Example Brand', name='PLA Red', material='PLA', weight=1000, diameter=1.75, density=1.24, color_hex='FF0000', future_field={'keep':True})


def test_public_catalog_is_cached_and_search_does_not_write_inventory(tmp_path):
    calls=[]
    catalog=FilamentCatalog(loader=lambda:calls.append(1) or [RECORD])
    path=tmp_path/'inventory.db';apply_migrations(path)
    app=Flask(__name__);app.secret_key='test'
    register_inventory(app,path,True,catalog=catalog)
    client=app.test_client()
    assert client.get('/api/filament-catalog?q=example+pla').json['total']==1
    assert client.get('/api/filament-catalog?q=red').json['total']==1
    assert len(calls)==1
    with connection(path) as db:
        assert db.execute('select count(*) from filaments').fetchone()[0]==0
        assert db.execute('select count(*) from vendors').fetchone()[0]==0
    payload=dict(catalog_id=RECORD['id'],vendor_id='__catalog__',name='PLA Red',material='PLA',color_json='FF0000',diameter_mm='1.75',net_weight_g='1000')
    assert client.post('/inventory/filaments/save',data=payload).status_code==302
    with connection(path) as db:
        product=db.execute('select * from filaments').fetchone()
        assert product['source_system']=='spoolmandb' and product['source_id']==RECORD['id']
        assert json.loads(product['source_payload_json'])['future_field']=={'keep':True}
        assert db.execute('select name from vendors').fetchone()[0]=='Example Brand'
        assert db.execute('select count(*) from spools').fetchone()[0]==0
        assert db.execute('select count(*) from inventory_movements').fetchone()[0]==0
    assert client.post('/inventory/filaments/save',data=payload).status_code==400
    with connection(path) as db:
        assert db.execute('select count(*) from filaments').fetchone()[0]==1
        assert db.execute('select count(*) from vendors').fetchone()[0]==1


def test_offline_catalog_does_not_prevent_manual_creation(tmp_path):
    def unavailable():raise OSError('offline')
    catalog=FilamentCatalog(loader=unavailable)
    path=tmp_path/'inventory.db';apply_migrations(path)
    app=Flask(__name__);app.secret_key='test';register_inventory(app,path,True,catalog=catalog)
    client=app.test_client()
    assert client.get('/api/filament-catalog?q=pla').status_code==503
    assert client.get('/inventory').status_code==200
    assert client.post('/inventory/filaments/save',data={'name':'Manual PLA','material':'PLA'}).status_code==302


def test_catalog_rejects_ambiguous_records_and_too_short_queries():
    catalog=FilamentCatalog(loader=lambda:[RECORD,RECORD])
    with pytest.raises(CatalogUnavailable):catalog.search('pla')
    with pytest.raises(ValueError):catalog.search('p')
