from bridge.printer_telemetry import PrinterTelemetry


def test_bad_ams_fields_do_not_block_finish_status():
    telemetry = PrinterTelemetry()
    state, _, _, _, _ = telemetry.update({'gcode_state': 'FINISH', 'mc_percent': 'bad',
                                          'ams': {'ams': [None, {'tray': [None, {'id': 'bad'}]}]}}, lambda c: c)
    assert state == 'FINISH'
    assert telemetry.status['state'] == 'FINISH'
    assert telemetry.status['state_seen_at'] > 0


def test_delta_preserves_state_freshness_and_previous_ams():
    telemetry = PrinterTelemetry()
    telemetry.update({'gcode_state': 'IDLE', 'ams': {'ams': [{'tray': [{'id': 0, 'tray_type': 'PLA'}]}]}}, lambda c: c)
    seen = telemetry.status['state_seen_at']
    telemetry.update({'ams': {'tray_now': '0'}}, lambda c: c)
    assert telemetry.status['state_seen_at'] == seen
    assert telemetry.ams[0]['material'] == 'PLA'
    assert telemetry.status['active_slot'] == 0


def test_light_delta_cannot_confirm_stale_idle_but_full_report_can():
    import threading
    telemetry = PrinterTelemetry()
    telemetry.update({'gcode_state': 'IDLE'}, lambda c: c)
    sequence = telemetry.state_sequence
    telemetry.update({'lights_report': [{'node': 'chamber_light', 'mode': 'on'}]}, lambda c: c)
    assert not telemetry.wait_for_state(sequence, timeout=0)
    timer = threading.Timer(0.02, lambda: telemetry.update({'gcode_state': 'IDLE'}, lambda c: c))
    timer.start()
    try:
        assert telemetry.wait_for_state(sequence, timeout=1)
    finally:
        timer.join()


def test_light_confirmation_requires_new_matching_report():
    telemetry = PrinterTelemetry()
    normalize = lambda value: value
    telemetry.update({'lights_report': [{'node': 'chamber_light', 'mode': 'on'}]}, normalize)
    sequence = telemetry.light_sequence
    assert not telemetry.wait_for_light(sequence, 'on', timeout=0)
    telemetry.update({'gcode_state': 'IDLE'}, normalize)
    assert not telemetry.wait_for_light(sequence, 'on', timeout=0)
    telemetry.update({'lights_report': [{'node': 'chamber_light', 'mode': 'off'}]}, normalize)
    assert not telemetry.wait_for_light(sequence, 'on', timeout=0)
    assert telemetry.wait_for_light(sequence, 'off', timeout=0)


def test_ams_write_confirmation_requires_a_new_matching_report():
    from bridge.printer_telemetry import PrinterTelemetry
    t=PrinterTelemetry()
    normalize=lambda value:str(value).lstrip('#')[:6]
    t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'1','tray_type':'PLA','tray_color':'FFFFFF00','tray_info_idx':'known','nozzle_temp_min':'190','nozzle_temp_max':'240'}]}]}},normalize)
    sequence=t.ams_sequence(1)
    assert t.ams[1]['tray_info_idx']=='known'
    assert not t.wait_for_ams(1,sequence,'PLA','#FFFFFF',timeout=0)
    t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'1','tray_color':'FFFFFF00'}]}]}},normalize)
    assert t.wait_for_ams(1,sequence,'PLA','#FFFFFF',timeout=0)
    assert not t.wait_for_ams(1,sequence,'PETG','#FFFFFF',timeout=0)


def test_remaining_time_handles_deltas_missing_values_and_completion():
    from bridge.printer_telemetry import PrinterTelemetry
    t=PrinterTelemetry();normalize=lambda x:x
    t.update({'gcode_state':'RUNNING','mc_remaining_time':'125'},normalize)
    assert t.status['remaining_minutes']==125
    t.update({'mc_percent':25},normalize)
    assert t.status['remaining_minutes']==125
    t.update({'gcode_state':'PAUSE'},normalize)
    assert t.status['remaining_minutes']==125
    for value in [-1,None,'bad','nan',True]:
        t.update({'mc_remaining_time':value},normalize)
        assert t.status['remaining_minutes'] is None
    t.update({'gcode_state':'FINISH','mc_remaining_time':5},normalize)
    assert t.status['remaining_minutes']==0
    t.update({'gcode_state':'RUNNING'},normalize)
    assert t.status['remaining_minutes'] is None
    t.update({'mc_remaining_time':10,'gcode_state':'FAILED'},normalize)
    assert t.status['remaining_minutes'] is None


def test_rejected_ams_command_cannot_be_confirmed_by_unchanged_matching_status():
    import pytest
    from bridge.printer_telemetry import PrinterTelemetry,AMSCommandRejected
    t=PrinterTelemetry();normalize=lambda x:str(x)[:6]
    before=t.ams_sequence(2)
    t.update({'command':'ams_filament_setting','sequence_id':'42','err_code':84033543},normalize)
    t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'2','tray_type':'PLA','tray_color':'FFFFFF00'}]}]}},normalize)
    with pytest.raises(AMSCommandRejected,match='84033543'):
        t.wait_for_ams_write(2,before,'42','PLA','#FFFFFF',timeout=0,settle=0,min_duration=0)
    assert not t.wait_for_ams_write(2,before,'43','PLA','#FFFFFF',timeout=0,settle=0,min_duration=0)
    t.update({'command':'ams_filament_setting','sequence_id':'43','err_code':0},normalize)
    assert t.wait_for_ams_write(2,before,'43','PLA','#FFFFFF',timeout=0,settle=0,min_duration=0)


def test_ams_write_waits_out_transient_empty_tray():
    import threading,time
    from bridge.printer_telemetry import PrinterTelemetry
    t=PrinterTelemetry();normalize=lambda x:str(x)[:6]
    before=t.ams_sequence(2)
    t.update({'command':'ams_filament_setting','sequence_id':'5','err_code':0},normalize)
    def send():
        t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'2','tray_type':'PLA','tray_color':'FFFFFF00'}]}]}},normalize)
        time.sleep(.04)
        t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'2'}]}]}},normalize)
        time.sleep(.04)
        t.update({'ams':{'ams':[{'id':'0','tray':[{'id':'2','tray_type':'PLA','tray_color':'FFFFFF00'}]}]}},normalize)
    worker=threading.Thread(target=send);worker.start();start=time.monotonic()
    assert t.wait_for_ams_write(2,before,'5','PLA','#FFFFFF',timeout=1,settle=.08,min_duration=.12)
    assert time.monotonic()-start>=.12
    worker.join()
