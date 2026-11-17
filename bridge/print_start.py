"""Build a reviewed local project_file command; no guessed filament mapping."""
import hashlib
import json
import re
from urllib.parse import quote
from .materials import materials_match


def prepare_start(entry,metadata,choice,context):
    if not isinstance(choice,dict) or set(choice)!={'plate','mapping','options','allow_mismatch'}:raise ValueError('Ungültige Startauswahl.')
    if type(choice['plate']) is not int:raise ValueError('Druckplatte auswählen.')
    plate=next((p for p in metadata['plates'] if p['index']==choice['plate']),None)
    if plate and plate.get('outside'):raise ValueError('Druckdatei meldet Objekte außerhalb des Druckbereichs.')
    if not plate or not plate['filaments']:raise ValueError('Druckplatte enthält keine eindeutigen Filamentdaten.')
    if metadata['model'].casefold()!=context['model'].casefold():raise ValueError('Druckermodell der Datei passt nicht.')
    try:
        if float(plate['nozzle'])!=float(context['nozzle']):raise ValueError('Düsengröße passt nicht.')
    except (TypeError,ValueError):raise ValueError('Düsengröße fehlt oder passt nicht.')
    options=choice['options']
    if not isinstance(options,dict) or set(options)!={'bed_leveling','flow_cali','vibration_cali','timelapse'} or any(type(v) is not bool for v in options.values()):raise ValueError('Ungültige Druckoptionen.')
    mapping=choice['mapping'];ids={str(f['id']) for f in plate['filaments']}
    if not isinstance(mapping,dict) or set(mapping)!=ids or type(choice['allow_mismatch']) is not bool:raise ValueError('Alle Filamente zuordnen.')
    external=any(v=='external' for v in mapping.values())
    if external and (len(mapping)!=1 or set(mapping.values())!={'external'}):raise ValueError('Externe Rolle unterstützt hier nur ein Filament ohne AMS-Mischung.')
    count=metadata.get('filament_count') or max(f['id'] for f in plate['filaments'])
    if not 1<=count<=16:raise ValueError('Projekt enthält zu viele Filamentpositionen.')
    ams=[-1]*count;summary=[];fingerprints=[]
    for filament in plate['filaments']:
        slot=mapping[str(filament['id'])]
        tray=next((t for t in context['slots'] if t['slot']==slot),None)
        if not tray or not tray['available'] or not tray.get('spool_id'):raise ValueError('Schacht benötigt aktuelle Druckerangaben und eine Bestandsrolle.')
        stock_matches=materials_match(tray['material'],tray.get('spool_material',tray['material'])) and tray['color'].lstrip('#').upper()==tray.get('spool_color',tray['color']).lstrip('#').upper()
        matches=stock_matches and materials_match(filament['material'],tray['material']) and filament['color'].lstrip('#').upper()==tray['color'].lstrip('#').upper()
        if not matches and not choice['allow_mismatch']:raise ValueError('Abweichung von Material/Farbe ausdrücklich bestätigen.')
        if not external:ams[filament['id']-1]=int(slot)
        fingerprints.append(tray)
        summary.append(f"Filament {filament['id']}: {filament['material']} {filament['color']} → {tray['label']} · {tray['spool_name']}"+(' · ABWEICHUNG' if not matches else ''))
        if 'pressure_profile' in tray:
            summary.append('Drucksteuerung: '+tray['pressure_profile']+' · gemeldeter K-Faktor '+(f"{tray['pressure_k']:.4f}" if tray.get('pressure_k') is not None else 'unbekannt'))
    command=dict(command='project_file',param=f"Metadata/plate_{plate['index']}.gcode",project_id='0',profile_id='0',task_id='0',subtask_id='0',subtask_name=entry['name'],url='ftp:///'+quote(entry['path'].lstrip('/'),safe='/'),md5='',bed_type='auto',layer_inspect=False,use_ams=not external,ams_mapping=ams if not external else [],**options)
    fingerprint=hashlib.sha256(json.dumps([entry,command,fingerprints,context['model'],context['nozzle']],sort_keys=True).encode()).hexdigest()
    return dict(payload=command,fingerprint=fingerprint,summary=[entry['name'],f"Druckplatte {plate['index']} · {metadata['model']} · Düse {plate['nozzle']} mm",*summary,*[dict(bed_leveling='Druckbett nivellieren',flow_cali='Dynamische Flusskontrolle',vibration_cali='Vibrationskompensation',timelapse='Zeitraffer')[k]+': '+('Ein' if v else 'Aus') for k,v in options.items()]])


def pressure_status(tray,printer):
    import math
    try:index=int(tray.get('cali_idx'))
    except (ValueError,TypeError):index=None
    try:
        k=float(tray.get('k'))
        if not math.isfinite(k) or not 0<=k<=1:k=None
    except (ValueError,TypeError):k=None
    label='Noch nicht gemeldet'
    if index==-1:label='Standard · kein gespeichertes Profil'
    elif index is not None:
        matches=[p for p in printer.get('calibration_profiles',[]) if p.get('cali_idx')==index and p.get('nozzle_diameter')==str(printer.get('nozzle_diameter') or '') and p.get('filament_id')==tray.get('tray_info_idx')]
        label=matches[0]['name'] if len(matches)==1 else 'Gespeichertes Schachtprofil'
    return dict(pressure_profile=label,pressure_index=index,pressure_k=k)
