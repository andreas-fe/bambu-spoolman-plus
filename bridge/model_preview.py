"""Bounded, read-only prototype of extruded G-code paths; never execute commands."""
import base64
import json
import math
import re
import struct
import time
import zipfile
import zlib

VERSION=2
MAX_SEGMENTS=60000
MAX_GCODE=192*1024*1024
WORDS=re.compile(r'([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))')


def build_model(stream,plate,metadata,cancel=lambda:None):
    selected=next((p for p in metadata['plates'] if p['index']==plate),None)
    if not selected:raise ValueError('Modellplatte nicht in den geprüften Dateidaten vorhanden.')
    palette={f['id']-1:bytes.fromhex(f['color'].lstrip('#')[:6]) for f in selected['filaments'] if re.fullmatch(r'#?[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?',f.get('color',''))}
    tool=next(iter(palette),0);pos=[0.,0.,0.];extruder=0.;relative=False;relative_e=False
    records=[];count=0;stride=1;warnings=set();lower=[float('inf')]*3;upper=[-float('inf')]*3;deadline=time.monotonic()+25
    with zipfile.ZipFile(stream) as z:
        info=z.getinfo(f'Metadata/plate_{plate}.gcode')
        if info.file_size>MAX_GCODE or info.flag_bits&1:raise ValueError('3D-Prototyp unterstützt unverschlüsselten G-Code bis 192 MiB.')
        with z.open(info) as source:
            lines=0
            while True:
                raw=source.readline(4097)
                if not raw:break
                lines+=1
                if lines>3000000:raise ValueError('G-Code überschreitet die Grenzen des 3D-Prototyps.')
                if len(raw)>4096:
                    if b';' not in raw:raise ValueError('G-Code-Befehl überschreitet 4 KiB.')
                    prefix=raw.split(b';',1)[0];tail=raw
                    while tail and not tail.endswith(b'\n'):
                        cancel()
                        if time.monotonic()>deadline:raise ValueError('Zeitlimit bei der 3D-Aufbereitung erreicht.')
                        tail=source.readline(65536)
                    raw=prefix+b'\n'

                if lines%128==0:
                    cancel()
                    if time.monotonic()>deadline:raise ValueError('3D-Aufbereitung überschreitet 25 Sekunden. Vorhandenes Vorschaubild verwenden.')
                line=raw.split(b';',1)[0].decode('ascii','ignore').upper()
                command=re.match(r'\s*(?:N\d+\s*)?([GMT])\s*(\d+)(.*)',line)
                if not command:continue
                family,n,tail=command.groups();n=int(n)
                if family=='T':
                    if 0<=n<64:tool=n
                    continue
                if family=='M':
                    if n==82:relative_e=False
                    elif n==83:relative_e=True
                    continue
                if n==20:raise ValueError('Zoll-G-Code wird vom 3D-Prototyp nicht unterstützt.')
                if n==90:relative=False;continue
                if n==91:relative=True;continue
                if n not in (0,1,2,3,28,92):continue
                values={key:float(value) for key,value in WORDS.findall(tail)}
                if any(not math.isfinite(v) for v in values.values()):raise ValueError('Ungültige G-Code-Koordinaten.')
                if n==28:
                    axes=[i for i,key in enumerate('XYZ') if key in tail] or [0,1,2]
                    for i in axes:pos[i]=0
                    continue
                if n==92:
                    for i,key in enumerate('XYZ'):
                        if key in values:pos[i]=values[key]
                    if 'E' in values:extruder=values['E']
                    continue
                target=[pos[i]+values.get(key,0) if relative else values.get(key,pos[i]) for i,key in enumerate('XYZ')]
                if any(abs(v)>10000 for v in target):raise ValueError('G-Code-Koordinaten außerhalb der Vorschaugrenzen.')
                extrusion=values.get('E',0) if relative_e else values.get('E',extruder)-extruder
                if 'E' in values:extruder=extruder+values['E'] if relative_e else values['E']
                if n in (2,3):warnings.add('Bögen (G2/G3) werden im Prototyp als gerade Verbindungen angenähert.')
                if extrusion>1e-7 and any(abs(target[i]-pos[i])>1e-6 for i in range(3)):
                    count+=1
                    for i in range(3):lower[i]=min(lower[i],pos[i],target[i]);upper[i]=max(upper[i],pos[i],target[i])
                    if count%stride==0:
                        records.append((tuple(pos),tuple(target),palette.get(tool,b'\x94\xa8\xb5')))
                        if len(records)>MAX_SEGMENTS:records=records[::2];stride*=2
                pos=target
    cancel()
    if not records:raise ValueError('Keine darstellbaren Extrusionswege in dieser Modellplatte gefunden.')
    if len(records)<count:warnings.add(f'Für flüssige Bedienung vereinfacht: {len(records):,} von {count:,} Druckwegsegmenten.')
    warnings.add('Druckwegvorschau, kein CAD-Modell: Leerfahrten werden ausgeblendet; Linien zeigen keine reale Düsenbreite. Farben folgen auswertbaren Slicer-T-Befehlen.')
    positions=bytearray();colors=bytearray()
    for a,b,color in records:positions.extend(struct.pack('<6f',*a,*b));colors.extend(color*2)
    return dict(version=VERSION,plate=plate,positions=base64.b64encode(positions).decode(),colors=base64.b64encode(colors).decode(),
                segments=len(records),total_segments=count,bounds=[lower,upper],warnings=sorted(warnings))


def model_key(cache,entry,plate):return cache.key(dict(entry,model_plate=plate,model_version=VERSION))


def cached_model(cache,entry,plate):
    with cache.lock:
        row=cache.db.execute('SELECT data FROM storage_models WHERE key=? AND created>?',(model_key(cache,entry,plate),time.time()-30*86400)).fetchone()
    if not row:return None
    try:
        decoder=zlib.decompressobj();data=decoder.decompress(row[0],4*1024*1024+1)
        return json.loads(data) if decoder.eof and len(data)<=4*1024*1024 else None
    except (ValueError,zlib.error):return None


def save_model(cache,entry,plate,data):
    payload=zlib.compress(json.dumps(data,separators=(',',':')).encode())
    with cache.lock:
        cache.db.execute('DELETE FROM storage_models WHERE created<?',(time.time()-30*86400,))
        cache.db.execute('INSERT OR REPLACE INTO storage_models VALUES (?,?,?,?)',(model_key(cache,entry,plate),cache.path_key(entry),payload,time.time()))
        while True:
            count,size=cache.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM storage_models').fetchone()
            if count<=20 and size<=32*1024*1024:break
            cache.db.execute('DELETE FROM storage_models WHERE key=(SELECT key FROM storage_models ORDER BY created,key LIMIT 1)')
        cache.db.commit()
