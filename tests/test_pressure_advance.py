import time
import pytest
from bridge.pressure_advance import calibration_choice,parse_profiles
from bridge.printer_telemetry import PrinterTelemetry,AMSCommandRejected


def test_profile_selection_uses_real_context_and_standard_has_no_invented_k():
    payload=dict(tray_info_idx='GFL99',tray_id=0,slot_id=0)
    printer=dict(nozzle_diameter='0.4',calibration_profiles_seen_at=time.time(),calibration_profiles=[dict(cali_idx=753,filament_id='GFL99',name='Testprofil',k_value=.029,nozzle_diameter='0.4')])
    assert calibration_choice('753',printer,payload)['payload']['cali_idx']==753
    standard=calibration_choice('-1',printer,payload)
    assert standard['payload']['cali_idx']==-1 and 'k' not in standard['payload']
    with pytest.raises(ValueError):calibration_choice('753',dict(printer,calibration_profiles_seen_at=0),payload)
    with pytest.raises(ValueError):calibration_choice('753',printer,dict(payload,tray_info_idx='GFG99'))
    with pytest.raises(ValueError):calibration_choice('-1',{},payload)
    with pytest.raises(ValueError):calibration_choice('999',printer,payload)
    with pytest.raises(ValueError):calibration_choice('753',dict(printer,nozzle_diameter='0.6'),payload)


def test_profile_parser_rejects_invalid_rows_and_preserves_names():
    rows=parse_profiles(dict(nozzle_diameter='0.4',filaments=[dict(cali_idx=1,k_value='0.025',filament_id='GFL99',name='<Profil>'),dict(cali_idx=2,k_value='nan',filament_id='GFL99'),dict(cali_idx=-1,k_value='.02',filament_id='GFL99')]))
    assert len(rows)==1 and rows[0]['name']=='<Profil>' and rows[0]['k_value']==.025


def test_calibration_needs_ack_and_fresh_matching_tray_and_k():
    t=PrinterTelemetry();norm=lambda x:x
    p=lambda d:t.update(d,norm)
    report=lambda idx,k:p(dict(ams=dict(ams=[dict(id='0',tray=[dict(id='0',cali_idx=idx,k=k)])])))
    report(753,.029);seq=t.calibration_sequence(0)
    p(dict(command='extrusion_cali_sel',sequence_id='12',result='success'))
    assert not t.wait_for_calibration(0,seq,'12',753,.029,timeout=0)
    report(753,.025)
    assert not t.wait_for_calibration(0,seq,'12',753,.029,timeout=0)
    report(753,.029)
    assert t.wait_for_calibration(0,seq,'12',753,.029,timeout=0)
    p(dict(command='extrusion_cali_sel',sequence_id='13',result='failed',err_code=1))
    with pytest.raises(AMSCommandRejected):t.wait_for_calibration(0,seq,'13',753,.029,timeout=0)
    # Delta reports retain already observed profile fields.
    p(dict(ams=dict(ams=[dict(id='0',tray=[dict(id='0',remain=90)])])))
    assert t.ams[0]['cali_idx']==753 and t.ams[0]['k']==.029


def test_profile_list_is_correlated_and_not_replaced_by_failed_reply():
    t=PrinterTelemetry()
    t.update(dict(command='extrusion_cali_get',sequence_id='1',nozzle_diameter='0.4',result='success',filaments=[]),lambda x:x)
    assert t.wait_for_profiles('1',timeout=0)
    t.update(dict(command='extrusion_cali_get',sequence_id='2',result='failed',filaments=[]),lambda x:x)
    assert not t.wait_for_profiles('2',timeout=0)
