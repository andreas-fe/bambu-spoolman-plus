import json,os
from pathlib import Path
import pytest
from flask import Flask,render_template_string
from bridge.runtime_config import KEYS,CONFIG_UI,register_config,read_overrides,load_overrides,write_overrides


def fixture(monkeypatch,tmp_path):
    values=dict(BAMBU_HOST='192.0.2.50',BAMBU_ACCESS_CODE='0'*8,BAMBU_SERIAL='demo-printer',BAMBU_MODEL='Bambu Lab A1',TAPO_HOST='192.0.2.60',TAPO_USERNAME='example-account',TAPO_PASSWORD='fixture-tapo-password')
    for key in KEYS:monkeypatch.setenv(key,values[key]);monkeypatch.delenv(key+'_FILE',raising=False)
    app=Flask(__name__);app.secret_key='fixture';busy=[False];restarts=[]
    def guard(v):
        if busy[0]:raise ValueError('Druck läuft.')
    view=register_config(app,tmp_path/'private.json',guard,lambda:restarts.append(True))
    return app,view,busy,restarts,tmp_path/'private.json',values


def test_local_configuration_private_values_never_returned(monkeypatch,tmp_path):
    app,view,busy,restarts,path,values=fixture(monkeypatch,tmp_path);c=app.test_client()
    assert view()['values']['BAMBU_HOST']==values['BAMBU_HOST']
    with app.test_request_context():html=render_template_string(CONFIG_UI,connections=view(),token='test')
    assert 'Web-Anmeldung' not in html
    for secret in (values['TAPO_PASSWORD'],values['BAMBU_ACCESS_CODE']):assert secret not in html
    r=c.post('/settings/connections',data=dict(BAMBU_HOST='192.0.2.51',idle_confirm='yes'))
    assert r.status_code==200 and restarts==[True]
    for secret in (values['TAPO_PASSWORD'],values['BAMBU_ACCESS_CODE']):assert secret not in r.text
    assert path.stat().st_mode&0o777==0o600 and read_overrides(path)['BAMBU_HOST']=='192.0.2.51'
    assert c.post('/settings/connections',data={'idle_confirm':'yes'}).status_code==409


def test_blank_secret_retained_and_guarded_device_change(monkeypatch,tmp_path):
    app,view,busy,restarts,path,values=fixture(monkeypatch,tmp_path);c=app.test_client();busy[0]=True
    data=dict(BAMBU_HOST='192.0.2.51',BAMBU_ACCESS_CODE='',TAPO_PASSWORD='',idle_confirm='yes')
    assert c.post('/settings/connections',data=data).status_code==409 and not path.exists()
    busy[0]=False
    assert c.post('/settings/connections',data=dict(data,BAMBU_HOST='http://bad:8883/path')).status_code==409 and not path.exists()
    assert c.post('/settings/connections',data=data).status_code==200
    saved=read_overrides(path);assert saved['BAMBU_HOST']=='192.0.2.51' and saved['TAPO_PASSWORD']==values['TAPO_PASSWORD'] and saved['BAMBU_ACCESS_CODE']==values['BAMBU_ACCESS_CODE']
    monkeypatch.setenv('BAMBU_ACCESS_CODE_FILE','nonexistent-old-secret-file');load_overrides(path)
    assert os.getenv('BAMBU_HOST')=='192.0.2.51' and not os.getenv('BAMBU_ACCESS_CODE_FILE')


def test_invalid_private_config_and_failed_restart_are_safe(monkeypatch,tmp_path):
    path=tmp_path/'bad.json';path.write_text('{bad')
    with pytest.raises(ValueError,match='ungültig'):read_overrides(path)
    target=tmp_path/'target';target.write_text('secret');link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(OSError):read_overrides(link)
    app,view,busy,restarts,path,values=fixture(monkeypatch,tmp_path)
    # A failed restart preparation leaves previous overrides intact.
    app=Flask('failed');write_overrides(path,{'BAMBU_HOST':'old.example'})
    register_config(app,path,lambda v:None,lambda:(_ for _ in ()).throw(RuntimeError('failure')))
    assert app.test_client().post('/settings/connections',data={'idle_confirm':'yes'}).status_code==409
    assert read_overrides(path)=={'BAMBU_HOST':'old.example'}


def test_missing_legacy_secret_file_does_not_block_settings(monkeypatch,tmp_path):
    app,view,busy,restarts,path,values=fixture(monkeypatch,tmp_path)
    monkeypatch.setenv('TAPO_PASSWORD_FILE',str(tmp_path/'missing-secret'))
    assert view()['saved']['TAPO_PASSWORD'] is False
    assert str(tmp_path) not in str(view())
