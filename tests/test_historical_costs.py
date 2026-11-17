import json
from tests.test_bridge import bridge,enable_internal
from bridge.inventory_db import connection,apply_migrations
from bridge.inventory_exchange import export_inventory,restore_inventory
from bridge.historical_costs import backfill_history


def finished(b):
    sid=enable_internal(b)
    with connection(b.DB_PATH) as db:
        db.execute("UPDATE spools SET purchase_price_minor=2000,price_weight_g=1000,currency='EUR' WHERE id=?",(sid,))
        db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES ('electricity_eur_per_kwh','\"0.35\"','t')")
    job=b.store.create_or_update_running_job('Example.3mf','Example',[0])
    b.inventory_service.apply_consumption(operation_key='use',job_id=job,source_key='one',spool_id=sid,grams=50)
    with connection(b.DB_PATH) as db:
        db.execute("UPDATE consumption_entries SET purchase_price_minor=NULL,price_weight_g=NULL,price_currency=NULL")
    b.store.finish_active_job('','',None);b.store.update_job(job,state='DONE')
    return job


def test_historical_backfill_is_financial_only_explicit_and_idempotent(bridge):
    b=bridge;job=finished(b);before=export_inventory(b.DB_PATH)
    hints={job:dict(seconds=3600,source='Slicer-Dateicache')}
    preview=backfill_history(b.DB_PATH,duration_hints=hints)
    assert len(preview)==1 and preview[0]['estimate']['total_eur']=='1.03'
    assert export_inventory(b.DB_PATH)['tables']==before['tables']
    assert len(backfill_history(b.DB_PATH,apply=True,duration_hints=hints))==1
    assert not backfill_history(b.DB_PATH,apply=True,duration_hints=hints)
    after=export_inventory(b.DB_PATH)
    for table,rows in before['tables'].items():
        if table!='job_cost_estimates':assert after['tables'][table]==rows,table
    summary=b.print_costs.summary(job)
    assert summary['historical_estimate'] and summary['total_label']=='1,03 €' and summary['coverage_percent']==0
    assert any('80 W' in s for s in summary['estimate_details'])
    with connection(b.DB_PATH) as db:db.execute('UPDATE spools SET purchase_price_minor=9900')
    assert b.print_costs.summary(job)['total_label']=='1,03 €'


def test_backfill_fallbacks_skips_running_and_simulated_and_roundtrips(bridge,tmp_path):
    b=bridge;job=finished(b)
    with connection(b.DB_PATH) as db:
        db.execute('DELETE FROM settings WHERE key=?',('electricity_eur_per_kwh',))
        db.execute('UPDATE spools SET purchase_price_minor=NULL')
    running=b.store.create_or_update_running_job('auto_filament_cali.gcode','Calibration',None)
    assert [p['job_id'] for p in backfill_history(b.DB_PATH)]==[job]
    b.store.finish_active_job('','',None);b.store.update_job(running,state='CLOSED_NO_USAGE')
    assert len(backfill_history(b.DB_PATH,apply=True))==2
    summary=b.print_costs.summary(running)
    assert summary['material_label']=='0,04 €' and any('2 g' in n for n in summary['estimate_details'])
    assert any('0,35' in n for n in summary['estimate_details'])
    snapshot=export_inventory(b.DB_PATH);target=tmp_path/'restore.sqlite3';b.Store(target);apply_migrations(target);restore_inventory(target,snapshot)
    assert export_inventory(target)['tables']==snapshot['tables']
    with connection(b.DB_PATH) as db:db.execute('UPDATE jobs SET dry_run=1 WHERE id=?',(running,))
    assert not b.print_costs.summary(running)['historical_estimate']


def test_spool_creation_defaults_to_1000_but_explicit_unknown_and_zero_survive(bridge):
    b=bridge;enable_internal(b)
    from flask import Flask
    from bridge.inventory_api import register_inventory
    app=Flask(__name__);app.secret_key='test';register_inventory(app,b.DB_PATH,True);client=app.test_client();client.get('/inventory')
    with client.session_transaction() as session:token=session['csrf_token']
    for value,expected in [(None,1000),('',None),('0',0),('750',750)]:
        payload=dict(csrf_token=token,filament_id='1')
        if value is not None:payload['initial_weight_g']=value
        assert client.post('/inventory/spools/save',data=payload).status_code==302
        with connection(b.DB_PATH) as db:
            row=db.execute('SELECT initial_weight_g,current_weight_g FROM spools ORDER BY id DESC LIMIT 1').fetchone()
            assert tuple(row)==(expected,expected)
