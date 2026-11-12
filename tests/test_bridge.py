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
        'WEB_USERNAME': '', 'WEB_PASSWORD': '', 'AUTO_MATCH_FILAMENTS': 'false',
    }.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location('tested_bridge', Path(__file__).parents[1] / 'bridge/bridge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Tests never make requests to a real printer or inventory.
    monkeypatch.setattr(module.requests, 'get', Mock(side_effect=AssertionError('Unmocked HTTP GET')))
    monkeypatch.setattr(module.requests, 'put', Mock(side_effect=AssertionError('Unmocked HTTP PUT')))
    module.fetch_spoolman_spools = lambda: [
        {'id': sid, 'remaining_weight': 1000, 'filament': {'material': material, 'color_hex': color}}
        for sid, material, color in [(10,'PLA','104831'),(20,'PLA','FFFFFF'),(30,'PLA','18C241'),(40,'PLA','A4DAE6'),(50,'TPU','FFFFFF')]]
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


def test_cancel_is_queued_for_estimation(bridge):
    b = bridge
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf'})
    job = b.store.active_job_id()
    message(b, {'gcode_state': 'FAILED'})
    assert b.store.get_job(job)['state'] == 'CANCEL_PENDING'
    assert b.store.get_job(job)['cancelled'] == 1
    assert b.job_queue.get_nowait() == job


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


def test_per_job_mapping_form_resolves_only_selected_job(bridge, monkeypatch):
    b = bridge
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [])
    job = finished_job(b, {1: 1.25, 2: 0.21}, ams=[])
    b.process_job(job)
    client = b.app.test_client()
    page = client.get('/')
    assert page.status_code == 200
    assert b'Filament 1' in page.data
    with client.session_transaction() as session:
        token = session['csrf_token']
    response = client.post(f'/jobs/{job}/mapping', data={'csrf_token': token, 'filament_1': '1', 'filament_2': '0'})
    assert response.status_code == 302
    assert json.loads(b.store.get_job(job)['ams_mapping_json']) == [1, 0]
    b.process_job(job)
    rows = b.store.deductions_for_job(job)
    assert [(r['tray_id'], r['grams'], r['status']) for r in rows] == [(1, 1.25, 'DRY_RUN'), (0, 0.21, 'DRY_RUN')]
    next_job = b.store.create_or_update_running_job('other.3mf', '', None)
    assert b.store.get_job(next_job)['ams_mapping_json'] is None
    assert client.post(f'/jobs/{job}/mapping', data={'csrf_token': token, 'filament_1': '0'}).status_code == 409


@pytest.mark.parametrize('values', [{}, {'filament_bad': '1'}, {'filament_0': '1'}, {'filament_1': '255'}, {'filament_257': '1'}])
def test_job_mapping_rejects_invalid_inputs(bridge, monkeypatch, values):
    b = bridge
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [])
    job = finished_job(b, {1: 1})
    client = b.app.test_client()
    client.get('/')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post(f'/jobs/{job}/mapping', data={'csrf_token': token, **values}).status_code == 400


def test_job_mapping_cannot_change_active_worker(bridge, monkeypatch):
    b = bridge
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [])
    job = finished_job(b, {1: 1})
    b.enqueue_job(job)
    client = b.app.test_client()
    client.get('/')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post(f'/jobs/{job}/mapping', data={'csrf_token': token, 'filament_1': '1'}).status_code == 409


def inventory():
    return {
        '0': {'material': 'PLA', 'color': '#104831', 'available': True, 'known': True},
        '1': {'material': 'PLA', 'color': '#FFFFFF', 'available': True, 'known': True},
        '2': {'material': 'PLA', 'color': '#18C241', 'available': True, 'known': True},
        '3': {'material': 'PLA', 'color': '#A4DAE6', 'available': True, 'known': True},
    }


def matched_tray(b, filament, trays):
    job = {'ams_snapshot_json': json.dumps(trays), 'ams_mapping_json': None}
    rows, _, _ = b.plan_consumption(job, {1: 1}, {'1': filament},
        {'0':'10','1':'20','2':'30','3':'40'}, b.stock_snapshot(b.fetch_spoolman_spools()))
    return rows[0]['tray_id'] if rows[0]['status'] == 'PENDING' else None


@pytest.mark.parametrize('color,material,expected', [('#ffffff', 'PLA', 1), ('FFFFFFFF', ' pla ', 1), ('#104831', 'PLA', 0), ('#FFFFFF', 'PETG', None), ('#FFFFFE', 'PLA', None), ('', 'PLA', None), ('invalid', 'PLA', None)])
def test_exact_color_and_material_matching(bridge, color, material, expected):
    bridge.AUTO_MATCH_FILAMENTS = True
    assert matched_tray(bridge, {'color': color, 'material': material}, inventory()) == expected


def test_duplicate_color_uses_stock_and_empty_trays_are_excluded(bridge):
    bridge.AUTO_MATCH_FILAMENTS = True
    trays = inventory()
    trays['2']['color'] = '#FFFFFF'
    assert matched_tray(bridge, {'color': '#FFFFFF', 'material': 'PLA'}, trays) == 1
    trays['2']['available'] = False
    assert matched_tray(bridge, {'color': '#FFFFFF', 'material': 'PLA'}, trays) == 1
    trays['1']['available'] = False
    assert matched_tray(bridge, {'color': '#FFFFFF', 'material': 'PLA'}, trays) is None


def auto_job(b, monkeypatch, ambiguous=False):
    b.AUTO_MATCH_FILAMENTS = True
    b.ams_state = {int(k): dict(v) for k, v in inventory().items()}
    if ambiguous: b.ams_state[2]['color'] = '#FFFFFF'
    job = finished_job(b, ams=[])
    def download(name, plate, metadata):
        metadata.update({'1': {'color': '#FFFFFF', 'material': 'PLA'}, '2': {'color': '#104831', 'material': 'PLA'}})
        return {1: 1.25, 2: 0.21}
    monkeypatch.setattr(b, 'extract_weights_via_ftps', download)
    return job


def test_automatic_mapping_uses_start_snapshot_and_audits_source(bridge, monkeypatch):
    b = bridge
    job = auto_job(b, monkeypatch)
    # Subsequent inventory/spool changes cannot redirect this print.
    b.ams_state[0]['color'] = '#FFFFFF'
    b.save_mapping({'0': '90', '1': '91'})
    b.process_job(job)
    rows = b.store.deductions_for_job(job)
    assert [(r['filament_id'], r['tray_id'], r['spool_id'], r['grams']) for r in rows] == [(1, 1, 20, 1.25), (2, 0, 10, 0.21)]
    assert b.store.get_job(job)['mapping_source'] == 'AUTO_COLOR_MATERIAL'
    assert all('automatisch' in r['detail'] for r in rows)
    assert all(r['mapping_source'] == 'AUTO_COLOR_MATERIAL' for r in b.store.list_deductions())


def test_duplicate_color_uses_remaining_weight_automatically(bridge, monkeypatch):
    b = bridge
    job = auto_job(b, monkeypatch, ambiguous=True)
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'DRY_RUN'
    assert b.store.get_job(job)['estimated'] == 1
    assert [r['tray_id'] for r in b.store.deductions_for_job(job)] == [1, 0]


def test_explicit_mapping_takes_priority_over_color(bridge, monkeypatch):
    b = bridge
    job = auto_job(b, monkeypatch)
    b.store.update_job(job, ams_mapping_json='[0, 1]', mapping_source='MQTT')
    b.process_job(job)
    assert [r['tray_id'] for r in b.store.deductions_for_job(job)] == [0, 1]
    assert b.store.get_job(job)['mapping_source'] == 'MQTT'


def test_slice_metadata_contains_colors_only_from_selected_plate(bridge):
    metadata = {}
    xml = b'<config><plate><metadata key="index" value="1"/><filament id="1" used_g="1.25" color="#FFFFFF" type="PLA"/></plate><plate><metadata key="index" value="2"/><filament id="1" used_g="9" color="#000000" type="PETG"/></plate></config>'
    assert bridge.parse_slice_info_config(xml, 1, metadata) == {1: 1.25}
    assert metadata == {'1': {'color': '#FFFFFF', 'material': 'PLA'}}


def test_ams_delta_does_not_erase_matching_attributes(bridge):
    b = bridge
    message(b, {'ams': {'ams': [{'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'FFFFFFFF'}]}]}})
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf'})
    job = b.store.active_job_id()
    message(b, {'ams': {'ams': [{'tray': [{'id': '1', 'remain': 90}]}]}})
    assert b.ams_state[1]['color'] == '#FFFFFF'
    assert b.ams_state[1]['material'] == 'PLA'
    assert b.ams_state[1]['available'] is True
    assert json.loads(b.store.get_job(job)['ams_snapshot_json'])['1']['color'] == '#FFFFFF'


def test_empty_slot_bitmask_blocks_stale_color_match(bridge):
    b = bridge
    message(b, {'ams': {'ams': [{'tray': [{'id': '1', 'tray_type': 'PLA', 'tray_color': 'FFFFFFFF'}]}], 'tray_exist_bits': '0'}})
    assert b.ams_state[1]['available'] is False


def test_session_and_csrf_survive_application_restart(bridge, monkeypatch):
    b = bridge
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [])
    client = b.app.test_client()
    client.get('/')
    with client.session_transaction() as session:
        token = session['csrf_token']
    cookie = client.get_cookie('session')
    # Reload the application with the same persistent DB, as a container restart would.
    spec = importlib.util.spec_from_file_location('restarted_bridge', Path(__file__).parents[1] / 'bridge/bridge.py')
    restarted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restarted)
    assert restarted.app.secret_key == b.app.secret_key
    second_client = restarted.app.test_client()
    second_client.set_cookie('session', cookie.value)
    response = second_client.post('/manual_deduct', data={'csrf_token': token, 'spool_id': '1', 'grams': '0.21'})
    assert response.status_code == 302
    assert len(restarted.store.list_deductions()) == 1
    invalid = second_client.post('/manual_deduct', data={'csrf_token': 'stale', 'spool_id': '1', 'grams': '0.21'})
    assert invalid.status_code == 403
    assert b'Seite neu' in invalid.data
    assert len(restarted.store.list_deductions()) == 1


def test_user_confirmed_mapping_survives_later_mqtt_updates(bridge):
    b = bridge
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf', 'ams_mapping': [0, 1]})
    job = b.store.active_job_id()
    assert b.store.set_job_mapping(job, [1, 0])
    for state in ['RUNNING', None, 'FINISH']:
        payload = {'ams_mapping': [0, 1]}
        if state: payload['gcode_state'] = state
        message(b, payload)
        assert json.loads(b.store.get_job(job)['ams_mapping_json']) == [1, 0]
        assert b.store.get_job(job)['mapping_source'] == 'USER'


def allocation_job(b, weights, stock, observed=None, mapping=None):
    b.AUTO_MATCH_FILAMENTS = True
    b.ams_state = {
        0: {'color': '#FFFFFF', 'material': 'PLA', 'available': True, 'known': True},
        1: {'color': '#FFFFFF', 'material': 'PLA', 'available': True, 'known': True},
        2: {'available': False}, 3: {'available': False},
    }
    job = finished_job(b, weights, ams=mapping if mapping is not None else [])
    b.store.update_job(job,
        filaments_json=json.dumps({str(i): {'color': '#FFFFFF', 'material': 'PLA'} for i in weights}),
        spools_snapshot_json=json.dumps({str(sid): {'remaining_weight': grams, 'color': '#FFFFFF', 'material': 'PLA'} for sid, grams in stock.items()}),
        observed_slots_json=json.dumps(observed or []))
    return job


def test_refill_splits_100_grams_into_30_and_70(bridge):
    b = bridge
    job = allocation_job(b, {1: 100}, {10: 30, 20: 100})
    b.process_job(job)
    rows = b.store.deductions_for_job(job)
    assert [(r['spool_id'], r['tray_id'], r['grams']) for r in rows] == [(10, 0, 30), (20, 1, 70)]
    assert sum(r['grams'] for r in rows) == 100
    assert b.store.get_job(job)['estimated'] == 1
    assert all('SCHÄTZUNG' in r['detail'] for r in rows)
    b.process_job(job)
    assert len(b.store.deductions_for_job(job)) == 2


def test_observed_slot_has_priority_over_smallest_spool(bridge):
    job = allocation_job(bridge, {1: 50}, {10: 30, 20: 100}, observed=[1])
    bridge.process_job(job)
    assert [(r['spool_id'], r['grams']) for r in bridge.store.deductions_for_job(job)] == [(20, 50)]


def test_fallback_to_matching_stock_reserve_outside_ams(bridge):
    b = bridge
    job = allocation_job(b, {1: 100}, {10: 30, 99: 100})
    # Only the first slot is assigned; the other reserve exists only in Spoolman.
    b.store.update_job(job, slot_mapping_json=json.dumps({'0': '10'}))
    b.process_job(job)
    rows = b.store.deductions_for_job(job)
    assert [(r['spool_id'],r['tray_id'],r['grams']) for r in rows] == [(10,0,30),(99,None,70)]
    assert 'physische Verwendung nicht bestätigt' in b.store.get_job(job)['estimate_reason']


def test_stock_shortfall_is_charged_to_last_matching_spool(bridge):
    b = bridge
    job = allocation_job(b, {1: 100}, {10: 30, 20: 20})
    b.process_job(job)
    # Smallest positive stock (20 g) first, then 80 g against the remaining spool.
    assert [(r['spool_id'],r['grams']) for r in b.store.deductions_for_job(job)] == [(20,20),(10,80)]
    assert 'übersteigt' in b.store.get_job(job)['estimate_reason']


def test_shared_stock_capacity_is_not_reused_for_each_filament(bridge):
    b = bridge
    job = allocation_job(b, {1: 60, 2: 40}, {10: 30, 20: 100})
    b.process_job(job)
    totals = {}
    for row in b.store.deductions_for_job(job): totals[row['spool_id']] = totals.get(row['spool_id'],0)+row['grams']
    assert totals == {10: 30, 20: 70}


def test_missing_weights_on_duplicate_spools_hold_job(bridge):
    job = allocation_job(bridge, {1: 100}, {10: None, 20: None})
    bridge.process_job(job)
    assert bridge.store.get_job(job)['state'] == 'WAITING_MAPPING'
    assert bridge.store.deductions_for_job(job)[0]['status'] == 'UNMAPPED'


def test_abort_estimate_uses_last_progress_even_if_failure_resets_it(bridge):
    b = bridge
    message(b, {'gcode_state': 'RUNNING', 'gcode_file': 'test.3mf', 'mc_percent': 40, 'ams_mapping': [0]})
    job = b.store.active_job_id()
    b.store.update_job(job, weights_json='{"1":100}')
    message(b, {'gcode_state': 'FAILED', 'mc_percent': 0})
    assert b.store.get_job(job)['progress'] == 40
    b.process_job(job)
    rows = b.store.deductions_for_job(job)
    assert len(rows) == 1 and rows[0]['grams'] == 40
    assert '40%' in rows[0]['detail'] and 'SCHÄTZUNG' in rows[0]['detail']


def test_abort_at_zero_does_not_deduct_full_slicer_amount(bridge):
    b = bridge
    job = finished_job(b, {1:100}, ams=[0])
    b.store.update_job(job, cancelled=1, progress=0, state='CANCEL_PENDING')
    b.process_job(job)
    assert b.store.deductions_for_job(job) == []
    assert b.store.get_job(job)['state'] == 'CANCELLED_ZERO'


def test_start_worker_freezes_stock_and_never_books_during_print(bridge, monkeypatch):
    b = bridge
    message(b, {'gcode_state': 'RUNNING','gcode_file':'test.3mf','mc_percent':10,'ams_mapping':[0]})
    job = b.store.active_job_id()
    monkeypatch.setattr(b, 'extract_weights_via_ftps', lambda *args: {1: 100})
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'RUNNING'
    assert b.store.deductions_for_job(job) == []
    original = b.store.get_job(job)['spools_snapshot_json']
    monkeypatch.setattr(b, 'fetch_spoolman_spools', lambda: [{'id':10,'remaining_weight':1,'filament':{}}])
    message(b, {'gcode_state':'FINISH','mc_percent':100})
    b.process_job(job)
    assert b.store.get_job(job)['spools_snapshot_json'] == original
    assert b.store.deductions_for_job(job)[0]['grams'] == 100


def test_retry_does_not_replan_split_or_duplicate_successful_part(bridge, monkeypatch):
    b = bridge
    b.DRY_RUN = False
    job = allocation_job(b, {1:100}, {10:30,20:100})
    send = Mock(side_effect=[('DONE','OK'),('RETRY','offline'),('DONE','OK')])
    monkeypatch.setattr(b,'send_spoolman_deduction',send)
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'RETRY'
    b.store.update_job(job, spools_snapshot_json=json.dumps({'10':{'remaining_weight':500,'color':'#FFFFFF','material':'PLA'}}))
    b.process_job(job)
    assert send.call_count == 3
    assert [(c.args[0]['spool_id'],c.args[0]['grams']) for c in send.call_args_list] == [(10,30),(20,70),(20,70)]
    assert b.store.get_job(job)['state'] == 'DONE'


def test_successful_api_confirmation_is_readable(bridge, monkeypatch):
    monkeypatch.setattr(bridge.requests,'get',Mock(return_value=Mock(status_code=200)))
    monkeypatch.setattr(bridge.requests,'put',Mock(return_value=Mock(status_code=200)))
    assert bridge.send_spoolman_deduction({'spool_id':1,'grams':0.21}) == ('DONE','Buchung durch Spoolman bestätigt')


def test_historical_confirmation_is_readable_without_changing_audit_data(bridge):
    job=bridge.store.create_manual_job(1,0,0.21)
    row=bridge.store.deductions_for_job(job)[0]
    bridge.store.set_deduction_status(row['id'],'DONE','HTTP 200; Zuordnung vom Drucker')
    assert bridge.store.list_deductions()[0]['detail']=='Buchung durch Spoolman bestätigt; Zuordnung vom Drucker'
    assert bridge.store.deductions_for_job(job)[0]['detail']=='HTTP 200; Zuordnung vom Drucker'


def test_changed_slicer_color_explains_unmapped_and_keeps_known_slot(bridge, monkeypatch):
    b=bridge
    b.AUTO_MATCH_FILAMENTS=True
    b.ams_state={int(k):dict(v) for k,v in inventory().items()}
    job=finished_job(b,{1:0.42,2:1.29},ams=[])
    b.store.update_job(job,filaments_json=json.dumps({'1':{'color':'#FFFFFF','material':'PLA'},'2':{'color':'#80FF80','material':'PLA'}}))
    send=Mock()
    monkeypatch.setattr(b,'send_spoolman_deduction',send)
    b.process_job(job)
    rows=b.store.deductions_for_job(job)
    assert [(r['status'],r['tray_id'],r['grams']) for r in rows]==[('PENDING',1,0.42),('UNMAPPED',None,1.29)]
    assert '#80FF80' in rows[1]['detail'] and 'PLA' in rows[1]['detail']
    assert '#80FF80' in b.store.get_job(job)['error']
    send.assert_not_called()
    form=next(j for j in b.job_mapping_forms() if j['id']==job)
    assert form['filaments'][0]['tray']==1
    assert form['filaments'][1]['color']=='80FF80'
    assert form['assignment_summary']=='1 von 2 Filamenten zugeordnet'
    assert '#104831' in form['slot_labels'][0]
    monkeypatch.setattr(b,'fetch_spoolman_spools',lambda:[])
    page=b.app.test_client().get('/')
    assert page.status_code==200 and b'#80FF80' in page.data


def test_matching_color_with_missing_spool_mapping_explains_actual_problem(bridge):
    b=bridge;b.AUTO_MATCH_FILAMENTS=True
    b.ams_state={int(k):dict(v) for k,v in inventory().items()}
    b.save_mapping({'0':None,'1':'20'})
    job=finished_job(b,{1:1},ams=[])
    b.store.update_job(job,filaments_json=json.dumps({'1':{'color':'#104831','material':'PLA'}}))
    b.process_job(job)
    assert 'keine Spoolman-Spulenzuordnung' in b.store.deductions_for_job(job)[0]['detail']


def enable_internal(b):
    from bridge.inventory_db import connection
    from bridge.spoolman_import import apply_snapshot
    b.INVENTORY_BACKEND = 'internal'
    b.DRY_RUN = False
    apply_snapshot(b.DB_PATH, {'vendors': [], 'filaments': [{'id': 1, 'name': 'PLA'}],
                              'spools': [{'id': 50, 'filament': {'id': 1}, 'remaining_weight': 100}]})
    with connection(b.DB_PATH) as db:
        boundary = db.execute('SELECT COALESCE(MAX(rowid),0) FROM jobs').fetchone()[0]
        db.execute("INSERT INTO settings(key,value_json,updated_at) VALUES ('cutover_job_rowid',?,'t')", (json.dumps(boundary),))
    return 1


def test_internal_worker_commits_once_without_http(bridge):
    from bridge.inventory_db import connection
    b = bridge
    sid = enable_internal(b)
    job = b.store.create_manual_job(sid, None, 5)
    b.process_job(job)
    b.process_job(job)
    assert b.store.get_job(job)['state'] == 'DONE'
    with connection(b.DB_PATH) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 95
        assert db.execute('SELECT COUNT(*) FROM consumption_entries').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM inventory_job_plans').fetchone()[0] == 1


def test_internal_worker_rolls_back_journal_and_balance_on_failure(bridge):
    from bridge.inventory_db import connection
    b = bridge
    sid = enable_internal(b)
    job = b.store.create_manual_job(sid, None, 5)
    row = b.store.deductions_for_job(job)[0]
    with connection(b.DB_PATH) as db:
        db.execute("CREATE TRIGGER fail_done BEFORE UPDATE OF status ON deductions WHEN NEW.status='DONE' BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        b.inventory_service.apply_ledger_deduction(row['id'])
    with connection(b.DB_PATH) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 100
        assert db.execute('SELECT COUNT(*) FROM consumption_entries').fetchone()[0] == 0
        assert db.execute('SELECT status FROM deductions').fetchone()[0] == 'PENDING'
        db.execute('DROP TRIGGER fail_done')
    assert b.inventory_service.apply_ledger_deduction(row['id'])['applied']


def test_internal_concurrent_workers_and_historical_barrier(bridge):
    from concurrent.futures import ThreadPoolExecutor
    from bridge.inventory_service import InventoryError
    from bridge.inventory_db import connection
    b = bridge
    old = b.store.create_manual_job(1, None, 5)
    sid = enable_internal(b)
    with pytest.raises(InventoryError):
        b.inventory_service.apply_ledger_deduction(b.store.deductions_for_job(old)[0]['id'])
    job = b.store.create_manual_job(sid, None, 5)
    row = b.store.deductions_for_job(job)[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: b.inventory_service.apply_ledger_deduction(row['id']), range(8)))
    assert sum(r['applied'] for r in results) == 1
    with connection(b.DB_PATH) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 95


def test_inventory_routes_share_auth_and_csrf_protection(bridge):
    b = bridge
    client = b.app.test_client()
    assert client.get('/inventory').status_code == 200
    assert client.get('/api/inventory/spools').json == []
    assert client.post('/inventory/vendors/save', data={'name': 'x'}).status_code == 403
    b.WEB_USERNAME, b.WEB_PASSWORD = 'test', 'secret'
    assert client.get('/inventory/export').status_code == 401


def test_native_json_roundtrip_including_journal(bridge, tmp_path):
    from bridge.inventory_db import apply_migrations
    from bridge.inventory_exchange import export_inventory, restore_inventory
    b = bridge
    sid = enable_internal(b)
    job = b.store.create_manual_job(sid, None, 4)
    b.process_job(job)
    exported = export_inventory(b.DB_PATH)
    assert all(r['key'] != 'flask_secret_key' for r in exported['tables']['runtime'])
    target = tmp_path / 'restored.sqlite3'
    b.Store(target)
    apply_migrations(target)
    restore_inventory(target, exported)
    assert export_inventory(target) == exported
    with pytest.raises(ValueError):
        restore_inventory(target, exported)


def test_tapo_off_is_blocked_without_fresh_idle_status(bridge):
    import time
    from bridge.devices import safe_to_power_off
    b = bridge
    client = b.app.test_client()
    client.get('/')
    with client.session_transaction() as s:
        token = s['csrf_token']
    assert client.post('/devices/tapo', data={'csrf_token': token, 'action': 'off'}).status_code == 409
    assert not safe_to_power_off({'state': 'RUNNING', 'state_seen_at': time.time()})
    assert not safe_to_power_off({'state': 'IDLE', 'state_seen_at': time.time()-60})
    assert safe_to_power_off({'state': 'IDLE', 'state_seen_at': time.time()})


def test_camera_frame_reader_handles_fragmented_data():
    from bridge.devices import recv_exact
    sock = Mock()
    sock.recv.side_effect = [b'ab', b'c', b'def']
    assert recv_exact(sock, 6) == b'abcdef'
    sock.recv.side_effect = [b'']
    with pytest.raises(ConnectionError):
        recv_exact(sock, 1)


def test_settings_are_persisted_and_csrf_protected(bridge):
    from bridge.inventory_db import connection
    client = bridge.app.test_client()
    assert client.get('/settings').status_code == 200
    with client.session_transaction() as s:
        token = s['csrf_token']
    assert client.post('/settings', data={'csrf_token': token, 'auto_match': 'on'}).status_code == 302
    assert bridge.DRY_RUN is False
    assert bridge.AUTO_MATCH_FILAMENTS is True
    with connection(bridge.DB_PATH) as db:
        assert json.loads(db.execute("SELECT value_json FROM settings WHERE key='dry_run'").fetchone()[0]) is False


def test_native_tapo_crash_cannot_kill_bridge(bridge, monkeypatch):
    from bridge.devices import TapoSwitch
    from bridge import devices
    monkeypatch.setattr(devices.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=-11, stdout='', stderr='native failure')))
    with pytest.raises(ValueError):
        TapoSwitch().run()
    assert bridge.app.test_client().get('/health').status_code == 200
    monkeypatch.setattr(devices.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0, stdout='{"on":false}')))
    assert TapoSwitch().run() == {'on': False}


def test_internal_process_death_rolls_back_and_recovers(bridge):
    import subprocess
    import sys
    from bridge.inventory_db import connection
    b = bridge
    sid = enable_internal(b)
    job = b.store.create_manual_job(sid, None, 5)
    deduction = b.store.deductions_for_job(job)[0]['id']
    with connection(b.DB_PATH) as db:
        db.execute("CREATE TRIGGER crash_before_commit AFTER UPDATE OF current_weight_g ON spools BEGIN SELECT crash_process(); END")
    child = r'''
import os,sys
from contextlib import contextmanager
from bridge import inventory_service
from bridge.inventory_db import connection
@contextmanager
def crash_connection(path):
    with connection(path) as db:
        db.create_function('crash_process',0,lambda:os._exit(23))
        yield db
inventory_service.connection=crash_connection
inventory_service.InventoryService(sys.argv[1]).apply_ledger_deduction(int(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-c', child, str(b.DB_PATH), str(deduction)], timeout=20)
    assert result.returncode == 23
    with connection(b.DB_PATH) as db:
        assert db.execute('SELECT current_weight_g FROM spools').fetchone()[0] == 100
        assert db.execute('SELECT COUNT(*) FROM consumption_entries').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0] == 0
        assert db.execute('SELECT status FROM deductions WHERE id=?', (deduction,)).fetchone()[0] == 'PENDING'
        db.execute('DROP TRIGGER crash_before_commit')
    assert b.inventory_service.apply_ledger_deduction(deduction)['applied']


def test_native_restore_preserves_cutover_with_gaps_in_job_rowids(bridge, tmp_path):
    from bridge.inventory_db import apply_migrations, connection
    from bridge.inventory_exchange import export_inventory, restore_inventory
    from bridge.inventory_service import InventoryService
    b = bridge
    old = b.store.create_manual_job(1, None, 1)
    with connection(b.DB_PATH) as db:
        db.execute('UPDATE jobs SET rowid=41 WHERE id=?', (old,))
    enable_internal(b)
    path = tmp_path / 'restored-gap.sqlite3'
    restored = b.Store(path)
    apply_migrations(path)
    restore_inventory(path, export_inventory(b.DB_PATH))
    job = restored.create_manual_job(1, None, 1)
    deduction = restored.deductions_for_job(job)[0]['id']
    assert InventoryService(path).apply_ledger_deduction(deduction)['applied']


def test_camera_light_requires_csrf_and_connected_mqtt(bridge, monkeypatch):
    from types import SimpleNamespace
    b = bridge
    client = b.app.test_client()
    assert client.post('/devices/light', data={'action': 'on'}).status_code == 403
    assert client.get('/devices').status_code == 200
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/devices/light', data={'csrf_token': token, 'action': 'invalid'}).status_code == 400
    monkeypatch.setattr(b, 'mqtt_client', None)
    assert client.post('/devices/light', data={'csrf_token': token, 'action': 'on'}).status_code == 503
    mqtt = Mock()
    mqtt.is_connected.return_value = True
    mqtt.publish.return_value = SimpleNamespace(rc=0)
    monkeypatch.setattr(b, 'mqtt_client', mqtt)
    def report_light(topic, payload, **kwargs):
        command = json.loads(payload).get('system')
        if command:
            b.telemetry.update({'lights_report': [{'node': 'chamber_light', 'mode': command['led_mode']}]}, b.normalized_color)
        return SimpleNamespace(rc=0)
    mqtt.publish.side_effect = report_light
    for action in ('on', 'off'):
        assert client.post('/devices/light', data={'csrf_token': token, 'action': action}).status_code == 200
        call = next(c for c in reversed(mqtt.publish.call_args_list) if 'system' in json.loads(c.args[1]))
        topic, payload = call.args
        assert call.kwargs['qos'] == 0
        assert topic == f'device/{b.SERIAL}/request'
        command = json.loads(payload)['system']
        assert 0 < int(command['sequence_id']) < 2147483647
        assert command['led_node'] == 'chamber_light'
        assert command['led_mode'] == action


def test_inventory_cards_and_named_selects_render(bridge):
    b = bridge
    enable_internal(b)
    from flask import Flask
    from bridge.inventory_api import register_inventory
    app = Flask(__name__)
    app.secret_key = 'test'
    register_inventory(app, b.DB_PATH, True)
    response = app.test_client().get('/inventory')
    assert response.status_code == 200
    assert b'spool-cards' in response.data
    assert b'material-filter' in response.data
    assert b'<select name="filament_id">' in response.data


def test_power_off_requests_new_idle_state_independently_of_light(bridge, monkeypatch):
    import time
    from bridge import devices
    client = bridge.app.test_client()
    client.get('/devices')
    with client.session_transaction() as session:
        token = session['csrf_token']
    bridge.printer_status.update(state='IDLE', state_seen_at=time.time()-90, lights_report=[{'node':'chamber_light','mode':'on'}])
    sent = Mock(return_value={'on': False})
    monkeypatch.setattr(devices.TapoSwitch, 'run', sent)
    fake_mqtt=Mock();fake_mqtt.is_connected.return_value=True
    monkeypatch.setattr(bridge,'mqtt_client',fake_mqtt)
    def fresh(_):
        bridge.telemetry.update({'gcode_state':'IDLE','lights_report':[{'node':'chamber_light','mode':'on'}]}, bridge.normalized_color)
    monkeypatch.setattr(bridge,'send_pushall',fresh)
    result=client.post('/devices/tapo',data={'csrf_token':token,'action':'off'})
    assert result.status_code==200
    assert result.json['on'] is False
    assert sent.call_args.args[0] is False
    bridge.printer_status['state_seen_at'] = time.time()-90
    monkeypatch.setattr(bridge,'send_pushall',lambda _:bridge.telemetry.update({'gcode_state':'RUNNING'},bridge.normalized_color))
    assert client.post('/devices/tapo',data={'csrf_token':token,'action':'off'}).status_code==409
    assert sent.call_count==1


def test_power_units_and_missing_measurements_remain_explicit():
    from bridge.devices import power_measurements
    assert power_measurements({'current_power':12500,'today_energy':450,'month_energy':1200,'today_runtime':70},{'current_power':12}) == dict(power_w=12.5,today_energy_wh=450.0,month_energy_wh=1200.0,today_runtime_min=70.0)
    assert power_measurements({}, {'current_power':13})['power_w']==13
    values=power_measurements({'current_power':float('nan'),'today_energy':-1}, {})
    assert values['power_w'] is None and values['today_energy_wh'] is None


def test_tapo_final_guard_still_blocks_a_newly_active_printer(monkeypatch):
    import asyncio,sys
    from unittest.mock import AsyncMock
    from bridge.devices import TapoSwitch, ShutdownBlocked
    from bridge import devices
    device=SimpleNamespace(get_device_info=AsyncMock(return_value=SimpleNamespace(device_on=True)),off=AsyncMock())
    client=SimpleNamespace(p110=AsyncMock(return_value=device))
    monkeypatch.setitem(sys.modules,'tapo',SimpleNamespace(ApiClient=lambda *_:client))
    monkeypatch.setattr(devices,'read_secret',lambda _: 'test-only')
    with pytest.raises(ShutdownBlocked):
        asyncio.run(TapoSwitch().request(False,lambda:False))
    device.off.assert_not_awaited()


def test_light_publish_without_printer_confirmation_is_not_success(bridge, monkeypatch):
    client = bridge.app.test_client()
    client.get('/camera')
    with client.session_transaction() as session:
        token = session['csrf_token']
    mqtt = Mock()
    mqtt.is_connected.return_value = True
    mqtt.publish.return_value = SimpleNamespace(rc=0)
    monkeypatch.setattr(bridge, 'mqtt_client', mqtt)
    monkeypatch.setattr(bridge.telemetry, 'wait_for_light', lambda *a, **k: False)
    response = client.post('/devices/light', data={'csrf_token': token, 'action': 'on'})
    assert response.status_code == 504
    assert 'error' in response.json


def test_shutdown_accepts_fresh_explicit_state_without_demanding_pushall(bridge, monkeypatch):
    import time
    mqtt = Mock()
    mqtt.is_connected.return_value = True
    monkeypatch.setattr(bridge, 'mqtt_client', mqtt)
    bridge.telemetry.update({'gcode_state': 'IDLE'}, bridge.normalized_color)
    monkeypatch.setattr(bridge, 'send_pushall', Mock(side_effect=AssertionError('Unnecessary request')))
    assert bridge.refresh_printer_status() is True
    from bridge.devices import safe_to_power_off
    assert safe_to_power_off(bridge.printer_status)
    bridge.telemetry.update({'gcode_state': 'RUNNING'}, bridge.normalized_color)
    assert not safe_to_power_off(bridge.printer_status)
    mqtt.is_connected.return_value = False
    assert bridge.refresh_printer_status() is False


def test_pushall_uses_bounded_sequence_and_checks_publish_failure(bridge):
    mqtt = Mock()
    mqtt.publish.return_value = SimpleNamespace(rc=0)
    bridge.seq_counter = 2147483646
    bridge.send_pushall(mqtt)
    command = json.loads(mqtt.publish.call_args.args[1])['pushing']
    assert command['sequence_id'] == '1'
    assert mqtt.publish.call_args.kwargs['qos'] == 0
    mqtt.publish.return_value = SimpleNamespace(rc=4)
    with pytest.raises(ConnectionError):
        bridge.send_pushall(mqtt)
