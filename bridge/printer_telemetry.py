"""Tolerant MQTT telemetry, independent of inventory and external devices."""
import time
import threading
import math
from .pressure_advance import parse_profiles


class AMSCommandRejected(ValueError):
    pass


class PrinterTelemetry:
    def __init__(self):
        self._state_changed = threading.Condition()
        self._state_sequence = 0
        self._light_sequence = 0
        self._light_mode = None
        self._cal_sequences = {i: 0 for i in range(4)}
        self._profile_reply = None
        self._ams_sequences = {i: 0 for i in range(4)}
        self._ams_revisions = {i: 0 for i in range(4)}
        self._ams_signatures = {i: None for i in range(4)}
        self._ams_commands = {}
        self._ams_command_times = {}
        self.ams = {i: dict(type='-', material='', color='#888888', remain=-1, available=False, known=False) for i in range(4)}
        self.status = dict(state='OFFLINE', progress=0, subtask_name='', active_slot=None,
                           active_slot_label='Kein Slot erkannt', layer=0, stage=None,
                           remaining_minutes=None, remaining_seen_at=0)
        self.status.update(target_slot=None,target_slot_known=False)

    def update(self, payload, color_normalizer):
        if payload.get('command') in ('ams_filament_setting','extrusion_cali_sel') and payload.get('sequence_id') is not None:
            code=payload.get('err_code',0)
            try:code=int(str(code),0) if isinstance(code,str) else int(code)
            except (ValueError,TypeError):code=-1
            failed=code!=0 or str(payload.get('result','')).lower() in ('fail','failed','error')
            error=('Drucker lehnt AMS-Änderung ab (Fehler '+str(code)+').') if failed else None
            if code==84033543:
                error+=' Die Firmware verlangt eine Befehlsautorisierung. LAN-/Entwicklermodus oder zugelassene Bambu-Anbindung prüfen.'
            self.status['ams_write_error']=error
            with self._state_changed:
                self._ams_commands[str(payload['sequence_id'])]=error
                self._ams_command_times[str(payload['sequence_id'])]=time.monotonic()
                while len(self._ams_commands)>32:
                    old=next(iter(self._ams_commands));self._ams_commands.pop(old);self._ams_command_times.pop(old,None)
                self._state_changed.notify_all()
        if 'nozzle_diameter' in payload:
            self.status['nozzle_diameter'] = str(payload['nozzle_diameter'])
        if payload.get('command') == 'extrusion_cali_get':
            with self._state_changed:
                if isinstance(payload.get('filaments'),list) and str(payload.get('result','success')).lower()=='success':
                    self.status.update(calibration_profiles=parse_profiles(payload),calibration_profiles_seen_at=time.time())
                    self._profile_reply = str(payload.get('sequence_id'))
                    self._state_changed.notify_all()
        if payload.get('command')=='project_file' and payload.get('sequence_id') is not None:
            self.status['project_file_reply']=dict(sequence_id=str(payload['sequence_id']),accepted=str(payload.get('result','')).lower() in ('success','ok'),failed=str(payload.get('result','')).lower() in ('fail','failed','error') or str(payload.get('err_code',0)) not in ('0','None',''),seen_at=time.time())
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
                if any(k in tray for k in ('cali_idx','k','n')):
                    with self._state_changed:
                        for key in ('cali_idx','k','n'):
                            if key in tray: current[key] = tray[key]
                        if 'cali_idx' in tray:
                            self._cal_sequences[tid] += 1
                            current['calibration_seen_at'] = time.time()
                        self._state_changed.notify_all()
                for key in ('tray_info_idx', 'nozzle_temp_min', 'nozzle_temp_max'):
                    if key in tray:
                        current[key] = tray[key]
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
                if 'tray_color' in tray or 'tray_type' in tray:
                    current['seen_at'] = time.time()
                    with self._state_changed:
                        self._ams_sequences[tid] += 1
                        self._state_changed.notify_all()
        if 'tray_exist_bits' in ams:
            try:
                bits = int(str(ams['tray_exist_bits']), 16)
                for tid in self.ams:
                    if not bits & (1 << tid):
                        self.ams[tid].update(available=False, known=True)
            except (TypeError, ValueError):
                pass
        with self._state_changed:
            for slot,tray in self.ams.items():
                signature=tuple(tray.get(k) for k in ('known','available','material','color','tray_info_idx'))
                if signature!=self._ams_signatures[slot]:
                    self._ams_signatures[slot]=signature;self._ams_revisions[slot]+=1
                    self._state_changed.notify_all()
        raw_tray = ams.get('tray_now', payload.get('tray_now'))
        if 'tray_tar' in ams:
            try:
                target=int(ams['tray_tar'])
                self.status.update(target_slot=target if target in (0,1,2,3,254) else None,
                                   target_slot_known=target in (0,1,2,3,254,255))
            except (TypeError,ValueError):
                self.status.update(target_slot=None,target_slot_known=False)
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
                if state in ('RUNNING','PREPARE') and self.status['state'] not in ('RUNNING','PREPARE','PAUSE'):
                    self.status.update(remaining_minutes=None, remaining_seen_at=0)
                self.status.update(state=state, state_seen_at=time.time())
                self._state_sequence += 1
                self._state_changed.notify_all()
        if 'mc_remaining_time' in payload:
            try:
                raw = payload['mc_remaining_time']
                minutes = float(raw)
                if isinstance(raw, bool) or not math.isfinite(minutes) or not 0 <= minutes <= 525600:
                    raise ValueError('Invalid remaining time')
                self.status.update(remaining_minutes=math.ceil(minutes), remaining_seen_at=time.time())
            except (ValueError,TypeError,OverflowError):
                self.status.update(remaining_minutes=None, remaining_seen_at=0)
        if state == 'FINISH':
            self.status.update(remaining_minutes=0, remaining_seen_at=time.time())
        elif state in ('IDLE','OFFLINE','FAILED','CANCEL','CANCELLED','CANCELED','STOPPED','ERROR'):
            self.status.update(remaining_minutes=None, remaining_seen_at=0)
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

    def ams_sequence(self, slot):
        with self._state_changed:
            return self._ams_sequences[slot]

    def wait_for_ams(self, slot, after_sequence, material, color, timeout=10):
        with self._state_changed:
            return self._state_changed.wait_for(lambda:
                self._ams_sequences[slot] > after_sequence
                and self.ams[slot].get('material', '').upper() == material.upper()
                and self.ams[slot].get('color', '').upper() == color.upper(), timeout)

    def wait_for_ams_write(self, slot, after_sequence, command_sequence, material, color, timeout=25, settle=3, min_duration=8):
        command_sequence=str(command_sequence)
        with self._state_changed:
            deadline=time.monotonic()+timeout;stable_since=None;last_signature=None
            while True:
                now=time.monotonic()
                if command_sequence in self._ams_commands and self._ams_commands[command_sequence]:
                    raise AMSCommandRejected(self._ams_commands[command_sequence])
                tray=self.ams[slot]
                signature=(self._ams_revisions[slot],)+tuple(tray.get(k) for k in ('known','available','material','color','tray_info_idx'))
                matches=tray.get('known') and tray.get('available') and self._ams_sequences[slot]>after_sequence and str(tray.get('material','')).upper()==material.upper() and str(tray.get('color','')).upper()==color.upper()
                if not matches:stable_since=None
                elif stable_since is None or signature!=last_signature:stable_since=now
                last_signature=signature
                if command_sequence in self._ams_commands and stable_since is not None and now-stable_since>=settle and now-self._ams_command_times[command_sequence]>=min_duration:
                    return True
                if now>=deadline:return False
                self._state_changed.wait(min(.25,deadline-now))

    def wait_for_profiles(self, sequence, timeout=6):
        with self._state_changed:
            return self._state_changed.wait_for(lambda: self._profile_reply == str(sequence), timeout)

    def calibration_sequence(self, slot):
        with self._state_changed:
            return self._cal_sequences[slot]

    def wait_for_calibration(self, slot, after, sequence, index, k=None, timeout=8):
        deadline=time.monotonic()+timeout
        with self._state_changed:
            while True:
                if sequence in self._ams_commands and self._ams_commands[sequence]:
                    raise AMSCommandRejected(self._ams_commands[sequence])
                tray=self.ams[slot]
                try:
                    match=int(tray.get('cali_idx',-999))==index and (k is None or abs(float(tray.get('k',float('nan')))-k)<0.00005)
                except (TypeError,ValueError):match=False
                if sequence in self._ams_commands and self._cal_sequences[slot]>after and match:return True
                left=deadline-time.monotonic()
                if left<=0:return False
                self._state_changed.wait(min(left,.25))

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
