"""Select an existing printer PA profile; never invent K values or profile IDs.

Wire format: BambuStudio DeviceManager.cpp, commnad_select_pa_calibration.
"""
import math
import time


def calibration_choice(value, printer, payload):
    nozzle = str(printer.get('nozzle_diameter') or '')
    if nozzle not in ('0.2', '0.4', '0.6', '0.8'):
        raise ValueError('Aktueller Düsendurchmesser fehlt. Druckerstatus aktualisieren.')
    try:
        index = int(value)
    except (ValueError, TypeError):
        raise ValueError('Bitte eine Drucksteuerung auswählen.')
    filament = payload['tray_info_idx']
    name = 'Standard · kein gespeichertes Profil'
    k = None
    if index != -1:
        if not 0 <= time.time()-printer.get('calibration_profiles_seen_at',0) <= 120:
            raise ValueError('Profilliste veraltet. Drucksteuerungen neu laden.')
        matches = [p for p in printer.get('calibration_profiles',[]) if p['cali_idx']==index and p['nozzle_diameter']==nozzle]
        if len(matches)!=1:
            raise ValueError('Drucksteuerungsprofil nicht mehr eindeutig verfügbar. Liste neu laden.')
        profile = matches[0]
        # Require the exact preset context. Family/color similarity alone is unsafe.
        if profile['filament_id'] != filament:
            raise ValueError('Profil gehört zu einer anderen Drucker-Filamentvorlage. Passendes Profil wählen oder Standard verwenden.')
        name, k = profile['name'], profile['k_value']
    command = dict(command='extrusion_cali_sel',ams_id=0,slot_id=payload['slot_id'],tray_id=payload['tray_id'],
                   cali_idx=index,filament_id=filament,nozzle_diameter=nozzle)
    return dict(payload=command,name=name,k_value=k)


def parse_profiles(payload):
    nozzle = str(payload.get('nozzle_diameter') or '')
    result=[]
    for row in payload.get('filaments',[]):
        try:
            index=int(row['cali_idx']); k=float(row['k_value']); fid=str(row['filament_id'])
            diameter=str(row.get('nozzle_diameter') or nozzle)
            if index<0 or not math.isfinite(k) or not 0<=k<=1 or not fid or diameter not in ('0.2','0.4','0.6','0.8'):continue
            result.append(dict(cali_idx=index,k_value=k,name=str(row.get('name') or 'Unbenanntes Profil'),filament_id=fid,nozzle_diameter=diameter))
        except (KeyError,ValueError,TypeError):continue
    return result
