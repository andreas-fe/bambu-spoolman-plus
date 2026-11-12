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
