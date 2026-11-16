"""Explicit AMS metadata command; reuse a live printer profile, never guess one.

Protocol: https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md#printams_filament_setting
This changes spool metadata only, not a sliced print's settings or heaters.
"""
import re
import time
from .materials import material_family


def slot_write_reason(printer, slot, connected, now=None):
    now = time.time() if now is None else now
    if not connected or not 0 <= now-printer.get('state_seen_at',0) <= 30:
        return 'Aktueller Druckerstatus fehlt. Bitte Status aktualisieren.'
    state=printer.get('state')
    if state in ('IDLE','FINISH'):
        return None
    if state in ('RUNNING','PAUSE'):
        active=printer.get('active_slot')
        if active not in (0,1,2,3,254) or not printer.get('target_slot_known'):
            return 'Aktiver oder angeforderter Schacht ist nicht sicher bekannt. Bitte auf eine neue Druckerrückmeldung warten.'
        if slot==active:
            return 'Dieser Schacht wird gerade gedruckt. Bitte einen unbenutzten Schacht wählen.'
        if slot==printer.get('target_slot'):
            return 'Dieser Schacht ist bereits für einen Filamentwechsel angefordert.'
        return None
    return 'Drucker bereitet einen Wechsel vor oder sein Zustand ist ungeklärt. Bitte kurz warten.'


def filament_payload(slot, spool, trays):
    if slot not in range(4):
        raise ValueError('Ungültiger AMS-Schacht.')
    material = material_family(spool.get('material'))
    color = str(spool.get('clean_color') or '').lstrip('#').upper()
    if not material or not re.fullmatch('[A-F0-9]{6}', color):
        raise ValueError('Filament benötigt Material und einen gültigen RGB-Farbcode.')
    candidates = [trays[slot]] + [tray for key,tray in trays.items() if key != slot]
    for tray in candidates:
        if not tray.get('known') or not tray.get('available') or material_family(tray.get('material')) != material or time.time()-tray.get('seen_at',0)>120:
            continue
        try:
            low,high = int(tray['nozzle_temp_min']),int(tray['nozzle_temp_max'])
            profile = str(tray['tray_info_idx'])
        except (KeyError,ValueError,TypeError):
            continue
        if not profile or not 100 <= low <= high <= 350:
            continue
        return dict(command='ams_filament_setting', ams_id=0, slot_id=slot, tray_id=slot,
                    tray_info_idx=profile, tray_type=material, tray_color=color+'FF',
                    nozzle_temp_min=low,nozzle_temp_max=high)
    raise ValueError('Für diesen Materialtyp fehlt ein aktuelles Druckerprofil. Den Typ einmal in Bambu Studio einstellen und hier aktualisieren. Es werden keine Profilkennungen oder Temperaturen geraten.')
