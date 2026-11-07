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
    assert b'Zur Bridge-Oberfl' in invalid.data
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
