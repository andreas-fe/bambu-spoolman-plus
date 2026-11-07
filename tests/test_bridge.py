import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    for key, value in {
        'BAMBU_HOST': '192.0.2.50', 'BAMBU_ACCESS_CODE': 'test-only',
        'BAMBU_SERIAL': 'test-only', 'DB_PATH': str(tmp_path / 'bridge.sqlite3'),
        'MAPPING_FILE': str(tmp_path / 'mapping.json'), 'DRY_RUN': 'true',
        'WEB_USERNAME': '', 'WEB_PASSWORD': '',
    }.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location('tested_bridge', Path(__file__).parents[1] / 'bridge/bridge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Tests never make requests to a real printer or inventory.
    monkeypatch.setattr(module.requests, 'get', Mock(side_effect=AssertionError('Unmocked HTTP GET')))
    monkeypatch.setattr(module.requests, 'put', Mock(side_effect=AssertionError('Unmocked HTTP PUT')))
    module.save_mapping({'0': '10', '1': '20', '2': '30', '3': '40', 'external': '50'})
    return module


def finished_job(b, weights=None, ams=None):
    job = b.store.create_or_update_running_job('test.3mf', 'Test', ams if ams is not None else [2, 0])
    b.store.finish_active_job('', '', None)
    if weights is not None:
        b.store.update_job(job, weights_json=json.dumps(weights))
    return job


def test_multimaterial_mapping_and_cached_json(bridge, monkeypatch):
    b = bridge
    b.DRY_RUN = False
    job = finished_job(b, {1: 2.5, 2: 7})
    send = Mock(return_value=('DONE', 'HTTP 200'))
    monkeypatch.setattr(b, 'send_spoolman_deduction', send)
    b.process_job(job)
    assert [(r['spool_id'], r['grams']) for r in b.store.deductions_for_job(job)] == [(30, 2.5), (10, 7)]
    assert b.store.get_job(job)['state'] == 'DONE'
    b.process_job(job)
    assert send.call_count == 2
    assert b.store.finish_active_job('', '', None) is None


def test_retry_is_scheduled_and_spool_is_frozen(bridge, monkeypatch):
    b = bridge
    b.DRY_RUN = False
    job = finished_job(b, {1: 3})
    send = Mock(side_effect=[('RETRY', 'offline'), ('DONE', 'HTTP 200')])
    monkeypatch.setattr(b, 'send_spoolman_deduction', send)
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'RETRY'
    assert job in b.store.recoverable_jobs()
    b.save_mapping({'2': '99'})
    b.process_job(job)
    assert send.call_args.args[0]['spool_id'] == 30
    assert b.store.get_job(job)['state'] == 'DONE'


@pytest.mark.parametrize('status', ['UNKNOWN', 'REJECTED'])
def test_uncertain_and_rejected_deductions_are_never_replayed(bridge, monkeypatch, status):
    b = bridge
    b.DRY_RUN = False
    job = finished_job(b, {1: 3})
    send = Mock(return_value=(status, 'failure'))
    monkeypatch.setattr(b, 'send_spoolman_deduction', send)
    b.process_job(job)
    b.process_job(job)
    assert send.call_count == 1
    assert b.store.get_job(job)['state'] == status
    assert job not in b.store.recoverable_jobs()


def test_dry_run_is_not_replayed_when_switching_to_live(bridge, monkeypatch):
    b = bridge
    job = finished_job(b, {1: 3})
    b.process_job(job)
    b.DRY_RUN = False
    send = Mock()
    monkeypatch.setattr(b, 'send_spoolman_deduction', send)
    b.process_job(job)
    send.assert_not_called()
    assert job not in b.store.recoverable_jobs()
    assert b.store.get_job(job)['state'] == 'DRY_RUN'


def test_missing_metadata_recovers(bridge, monkeypatch):
    b = bridge
    job = finished_job(b)
    fetch = Mock(side_effect=[{}, {1: 3}])
    monkeypatch.setattr(b, 'extract_weights_via_ftps', fetch)
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'WAITING_METADATA'
    assert job in b.store.recoverable_jobs()
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'DRY_RUN'


def test_unmapped_filament_is_not_guessed(bridge):
    b = bridge
    job = finished_job(b, {1: 3}, ams=[])
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'WAITING_MAPPING'
    assert b.store.deductions_for_job(job)[0]['status'] == 'UNMAPPED'


def test_restart_marks_in_flight_unknown_and_reuses_active_job(bridge):
    b = bridge
    job = b.store.create_or_update_running_job('test.3mf', '', [0])
    b.store.upsert_deduction(job, 'filament:1', 1, 0, 10, 3, 'IN_FLIGHT')
    b.store = b.Store(b.DB_PATH)
    assert b.store.create_or_update_running_job('test.3mf', '', None) == job
    assert b.store.deductions_for_job(job)[0]['status'] == 'UNKNOWN'


def test_claim_is_atomic(bridge):
    b = bridge
    job = b.store.create_manual_job(10, 0, 3)
    row = b.store.get_pending_deductions(job)[0]
    assert b.store.claim_deduction(row['id'])
    assert not b.store.claim_deduction(row['id'])


def message(b, payload):
    b.on_message(None, None, SimpleNamespace(payload=json.dumps({'print': payload}).encode()))


def test_mqtt_delta_preserves_slot_progress_and_records_mapping(bridge):
    b = bridge
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf', 'mc_percent': 42, 'ams': {'tray_now': 2}})
    job = b.store.active_job_id()
    message(b, {'ams_mapping': [2, 0], 'param': 'Metadata/plate_2.gcode'})
    assert b.printer_status['active_slot'] == 2
    assert b.printer_status['progress'] == 42
    assert json.loads(b.store.get_job(job)['ams_mapping_json']) == [2, 0]
    assert b.store.get_job(job)['plate_index'] == 2
    message(b, {'gcode_state': 'FINISH', 'ams': {'tray_now': 255}})
    assert b.job_queue.get_nowait() == job
    message(b, {'gcode_state': 'FINISH'})
    assert b.job_queue.empty()


def test_cancel_does_not_book_full_slicer_weight(bridge):
    b = bridge
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf'})
    job = b.store.active_job_id()
    message(b, {'gcode_state': 'FAILED'})
    assert b.store.get_job(job)['state'] == 'FAILED'
    assert b.job_queue.empty()


@pytest.mark.parametrize('raw,mapping2,expected', [([2], None, 2), ([254], None, 254), ([255], None, None), (None, [{'ams_id': 0, 'slot_id': 3}], 3), (None, [{'ams_id': 255, 'slot_id': 255}], 254), (None, [{'ams_id': 'bad'}], None)])
def test_resolve_mapping(bridge, raw, mapping2, expected):
    assert bridge.resolve_tray_for_filament(1, raw, mapping2) == expected


def test_selects_only_printed_plate(bridge):
    xml = b'<config><plate><metadata key="index" value="1"/><filament id="1" used_g="3"/></plate><plate><metadata key="index" value="2"/><filament id="1" used_g="8"/></plate></config>'
    assert bridge.parse_slice_info_config(xml, 2) == {1: 8}
    with pytest.raises(ValueError):
        bridge.parse_slice_info_config(xml)
    with pytest.raises(ValueError):
        bridge.parse_slice_info_config(xml, 9)


def test_invalid_weights_and_xml_limit(bridge, monkeypatch):
    assert bridge.parse_slice_info_config(b'<config><filament id="1" used_g="NaN"/><filament id="2" used_g="-3"/></config>') == {}
    monkeypatch.setattr(bridge, 'MAX_XML_BYTES', 2)
    with pytest.raises(ValueError):
        bridge.parse_slice_info_config(b'<config/>')


@pytest.mark.parametrize('error,expected', [(requests.ConnectTimeout(), 'RETRY'), (requests.ReadTimeout(), 'UNKNOWN'), (requests.ConnectionError(), 'UNKNOWN')])
def test_api_failure_classification(bridge, monkeypatch, error, expected):
    monkeypatch.setattr(bridge.requests, 'get', Mock(return_value=Mock(status_code=200)))
    put = Mock(side_effect=error)
    monkeypatch.setattr(bridge.requests, 'put', put)
    assert bridge.send_spoolman_deduction({'spool_id': 1, 'grams': 3})[0] == expected
    assert put.call_args.kwargs['json'] == {'use_weight': 3}


def test_offline_preflight_never_sends_a_deduction(bridge, monkeypatch):
    monkeypatch.setattr(bridge.requests, 'get', Mock(side_effect=requests.ConnectionError()))
    put = Mock()
    monkeypatch.setattr(bridge.requests, 'put', put)
    assert bridge.send_spoolman_deduction({'spool_id': 1, 'grams': 3})[0] == 'RETRY'
    put.assert_not_called()


def test_forms_auth_health_and_rendering(bridge, monkeypatch):
    b = bridge
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [])
    client = b.app.test_client()
    assert client.get('/health').status_code == 200
    assert client.get('/').status_code == 200
    assert client.post('/save', data={}).status_code == 403
    with client.session_transaction() as session:
        token = session['csrf_token']
    for grams in ['nan', 'inf', '-1', '1001', 'bad']:
        assert client.post('/manual_deduct', data={'csrf_token': token, 'spool_id': '1', 'grams': grams}).status_code == 400
    assert client.post('/manual_deduct', data={'csrf_token': token, 'spool_id': '1', 'grams': '1.25'}).status_code == 302
    assert client.get('/api/status').json['dry_run'] is True
    b.WEB_USERNAME, b.WEB_PASSWORD = 'tester', 'test-password'
    assert client.get('/api/status').status_code == 401
    assert client.get('/api/status', auth=('tester', 'test-password')).status_code == 200
    assert client.get('/health').status_code == 200


def test_v21_database_migrates_without_losing_jobs(bridge, tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, created_at TEXT, updated_at TEXT, filename TEXT, subtask_name TEXT, state TEXT, ams_mapping_json TEXT, ams_mapping2_json TEXT, weights_json TEXT, error TEXT)')
        con.execute("INSERT INTO jobs(id,state) VALUES ('old','DONE')")
    migrated = bridge.Store(path)
    assert migrated.get_job('old')['state'] == 'DONE'
    assert migrated.get_job('old')['slot_mapping_json'] is None


def test_waiting_dry_run_job_stays_simulated_after_mode_switch(bridge, monkeypatch):
    b = bridge
    job = finished_job(b, ams=[0])
    b.DRY_RUN = False
    send = Mock()
    monkeypatch.setattr(b, 'send_spoolman_deduction', send)
    monkeypatch.setattr(b, 'extract_weights_via_ftps', lambda *args: {1: 4})
    b.process_job(job)
    send.assert_not_called()
    assert b.store.get_job(job)['state'] == 'DRY_RUN'
