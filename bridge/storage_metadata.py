"""Bounded, read-only inspection of sliced Bambu archives."""
import json
import base64
import math
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_ARCHIVE=128*1024*1024
MAX_CONFIG=2*1024*1024


def finite(value,default=None):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else default
    except (ValueError,TypeError):return default


# Bambu Studio PrintConfig.cpp BedType values; these are file settings, not sensors.
BED_LABELS={
    'Cool Plate':'Cool Plate',
    'Engineering Plate':'Engineering Plate',
    'High Temp Plate':'Smooth PEI Plate / High Temp Plate',
    'Textured PEI Plate':'Textured PEI Plate',
    'Supertack Plate':'Bambu Cool Plate SuperTack',
}
METADATA_VERSION=2


def plate_settings(data):
    """Optional model_settings.config metadata, keyed by one-based plater_id."""
    if not data or len(data)>MAX_CONFIG:return {}
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():return {}
    try:root=ET.fromstring(data)
    except ET.ParseError:return {}
    result={};seen=set()
    for plate in root.findall('./plate'):
        meta={n.get('key'):n.get('value') for n in plate.findall('metadata')}
        try:index=int(meta.get('plater_id','0'))
        except ValueError:continue
        if not 1<=index<=64:continue
        # Ambiguous optional display metadata must not be attached to a plate.
        if index in seen:result[index]={};continue
        seen.add(index);result[index]=meta
    return result


def describe_plate(index,settings,project):
    meta=settings.get(index,{})
    bed=meta.get('bed_type')
    if not bed or bed=='Default Plate':bed=project.get('curr_bed_type')
    bed=bed.strip()[:160] if isinstance(bed,str) else ''
    if bed=='Default Plate':bed=''
    name=meta.get('plater_name','')
    name=name.strip()[:200] if isinstance(name,str) else ''
    return dict(name=name,bed_type=bed,bed_label=BED_LABELS.get(bed,bed))


def inspect_archive(stream):
    with zipfile.ZipFile(stream) as z:
        entries=z.infolist()
        if len(entries)>10000:raise ValueError('Zu viele Einträge im Druckarchiv.')
        names=[i.filename for i in entries]
        if len(names)!=len(set(names)):raise ValueError('Doppelte Archivpfade; Druckdatei nicht eindeutig.')
        def read(name):
            info=z.getinfo(name)
            if info.file_size>MAX_CONFIG or info.flag_bits&1:raise ValueError('Konfigurationsdatei zu groß oder verschlüsselt.')
            return z.read(info)
        if 'Metadata/slice_info.config' not in names:raise ValueError('Keine auswertbaren Druckplatten-Metadaten. Bitte geslicte 3MF verwenden.')
        xml=read('Metadata/slice_info.config')
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('Nicht unterstützte XML-Deklaration.')
        root=ET.fromstring(xml)
        project=json.loads(read('Metadata/project_settings.config')) if 'Metadata/project_settings.config' in names else {}
        if not isinstance(project,dict):raise ValueError('Ungültige Projekteinstellungen.')
        settings=plate_settings(read('Metadata/model_settings.config')) if 'Metadata/model_settings.config' in names and z.getinfo('Metadata/model_settings.config').file_size<=MAX_CONFIG else {}
        plates=[];image_bytes=0
        for plate in root.findall('.//plate'):
            meta={n.get('key'):n.get('value') for n in plate.findall('metadata')}
            try:index=int(meta.get('index','0'))
            except ValueError:continue
            if not 1<=index<=64 or f'Metadata/plate_{index}.gcode' not in names:continue
            filaments=[]
            for f in plate.findall('filament'):
                try:fid=int(f.get('id','0'))
                except ValueError:raise ValueError('Ungültige Filamentnummer.')
                if not 1<=fid<=64:raise ValueError('Ungültige Filamentnummer.')
                if finite(f.get('used_g'),0)<=0 and finite(f.get('used_m'),0)<=0:continue
                filaments.append(dict(id=fid,material=f.get('type',''),color=f.get('color',''),grams=finite(f.get('used_g')),meters=finite(f.get('used_m'))))
            if len({f['id'] for f in filaments})!=len(filaments):raise ValueError('Doppelte Filamentnummer auf einer Druckplatte.')
            preview=None
            for image_path in (f'Metadata/plate_{index}.png',f'Metadata/plate_{index}_small.png'):
                if image_path not in names:continue
                info=z.getinfo(image_path)
                if info.file_size>256*1024 or image_bytes+info.file_size>2*1024*1024:continue
                image=read(image_path)
                if image.startswith(b'\x89PNG\r\n\x1a\n'):
                    preview='data:image/png;base64,'+base64.b64encode(image).decode();image_bytes+=len(image);break
            plates.append(dict(**describe_plate(index,settings,project),outside=meta.get('outside')=='true',preview=preview,index=index,seconds=finite(meta.get('prediction')),grams=finite(meta.get('weight')),nozzle=meta.get('nozzle_diameters',''),model_id=meta.get('printer_model_id',''),filaments=filaments,objects=[o.get('name','') for o in plate.findall('object')],warnings=[w.get('msg','') for w in plate.findall('warning')]))
        if not plates or len({p['index'] for p in plates})!=len(plates):raise ValueError('Keine eindeutigen geslicten Druckplatten gefunden.')
        plates.sort(key=lambda plate:names.index(f'Metadata/plate_{plate["index"]}.gcode'))
        return dict(metadata_version=METADATA_VERSION,filament_count=len(project.get('filament_type',[])) if isinstance(project.get('filament_type'),list) else 0,model=str(project.get('printer_model','')),profile=str(project.get('printer_settings_id','')),plates=plates)
