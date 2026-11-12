"""Tolerant MQTT telemetry, independent of inventory and external devices."""
import time
import threading


class PrinterTelemetry:
    def __init__(self):
        self._state_changed = threading.Condition()
        self._state_sequence = 0
        self._light_sequence = 0
        self._light_mode = None
        self.ams = {i: dict(type='-', material='', color='#888888', remain=-1, available=False, known=False) for i in range(4)}
        self.status = dict(state='OFFLINE', progress=0, subtask_name='', active_slot=None,
                           active_slot_label='Kein Slot erkannt', layer=0, stage=None)

    def update(self, payload, color_normalizer):
        lights = payload.get('lights_report')
        for light in lights if isinstance(lights, list) else []:
            if isinstance(light, dict) and light.get('node') == 'chamber_light' and light.get('mode') in ('on', 'off'):
                with self._state_changed:
                    self._light_mode = light['mode']
                    self._light_sequence += 1
                    self._state_changed.notify_all()
        ams = payload.get('ams')
        ams = ams if isinstance(ams, dict) else {}
        units = ams.get('ams')
        for unit in units if isinstance(units, list) else []:
            if not isinstance(unit, dict):
                continue
            trays = unit.get('tray')
            for tray in trays if isinstance(trays, list) else []:
                if not isinstance(tray, dict):
                    continue
                try:
                    tid = int(tray.get('id', -1))
                except (TypeError, ValueError):
                    continue
                if tid not in self.ams:
                    continue
                current = self.ams[tid]
                if 'tray_type' in tray:
                    material = str(tray['tray_type'] or '')
                    current.update(known=True, material=material, available=bool(material),
                                   type=tray.get('tray_sub_brands') or material or '-')
                if 'tray_color' in tray:
                    current['color'] = '#' + (color_normalizer(tray['tray_color']) or '888888')
                if 'remain' in tray:
                    current['remain'] = tray['remain']
                if set(tray) == {'id'}:
                    current.update(available=False, known=True)
        if 'tray_exist_bits' in ams:
            try:
                bits = int(str(ams['tray_exist_bits']), 16)
                for tid in self.ams:
                    if not bits & (1 << tid):
                        self.ams[tid].update(available=False, known=True)
            except (TypeError, ValueError):
                pass
        raw_tray = ams.get('tray_now', payload.get('tray_now'))
        try:
            tray = int(raw_tray) if raw_tray is not None else None
        except (TypeError, ValueError):
            tray = 255
        if tray in range(4):
            self.status.update(active_slot=tray, active_slot_label=f'AMS Slot {tray+1}')
        elif tray == 254:
            self.status.update(active_slot=254, active_slot_label='Externe Spule')
        elif tray == 255:
            self.status.update(active_slot=None, active_slot_label='Kein Slot erkannt')
        state = str(payload.get('gcode_state') or '').upper()
        filename = str(payload.get('gcode_file') or '')
        subtask = str(payload.get('subtask_name') or '')
        if state:
            with self._state_changed:
                self.status.update(state=state, state_seen_at=time.time())
                self._state_sequence += 1
                self._state_changed.notify_all()
        for source, target in [('mc_percent', 'progress'), ('layer_num', 'layer'), ('stg_cur', 'stage')]:
            if source in payload:
                try:
                    value = int(float(payload[source] or 0))
                    self.status[target] = max(0, min(100, value)) if target == 'progress' else value
                except (TypeError, ValueError, OverflowError):
                    pass
        if subtask:
            self.status['subtask_name'] = subtask
        mapping = payload.get('ams_mapping') or ams.get('ams_mapping')
        mapping2 = payload.get('ams_mapping2') or ams.get('ams_mapping2')
        return state, filename, subtask, mapping, mapping2

    @property
    def state_sequence(self):
        with self._state_changed:
            return self._state_sequence

    def wait_for_state(self, after_sequence, timeout=6):
        """Light/AMS-only deltas must never validate an old idle state."""
        with self._state_changed:
            return self._state_changed.wait_for(lambda: self._state_sequence > after_sequence, timeout)

    @property
    def light_sequence(self):
        with self._state_changed:
            return self._light_sequence

    def wait_for_light(self, after_sequence, mode, timeout=6):
        with self._state_changed:
            return self._state_changed.wait_for(
                lambda: self._light_sequence > after_sequence and self._light_mode == mode, timeout)
