"""Read-only, bounded G-code geometry; preserves contours and interpolates arcs."""
import base64
import json
import math
import re
import struct
import time
import zipfile
import zlib

VERSION=3
MAX_SEGMENTS=400000
MAX_GCODE=192*1024*1024
MAX_MODEL_JSON=32*1024*1024
WORDS=re.compile(r'([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))')


def arc_points(start,end,values,clockwise,plane=17,absolute_center=False):
    """IJK/R arcs with bounded chord error; includes full circles and helices."""
    u,v,w,ku,kv={17:(0,1,2,'I','J'),18:(2,0,1,'K','I'),19:(1,2,0,'J','K')}[plane]
    def sweep(cx,cy):
        a=math.atan2(start[v]-cy,start[u]-cx);b=math.atan2(end[v]-cy,end[u]-cx)
        delta=-((a-b)%(2*math.pi)) if clockwise else (b-a)%(2*math.pi)
        if abs(delta)<1e-9:delta=-2*math.pi if clockwise else 2*math.pi
        return a,delta
    if ku in values or kv in values:
        cx=values.get(ku,start[u]) if absolute_center else start[u]+values.get(ku,0)
        cy=values.get(kv,start[v]) if absolute_center else start[v]+values.get(kv,0)
        radius=math.hypot(start[u]-cx,start[v]-cy)
    elif 'R' in values:
        radius=abs(values['R']);dx=end[u]-start[u];dy=end[v]-start[v];chord=math.hypot(dx,dy)
        if chord<1e-8 or chord>2*radius+.01:raise ValueError('Ungültiger Radiusbogen.')
        height=math.sqrt(max(0,radius*radius-chord*chord/4));mx=(start[u]+end[u])/2;my=(start[v]+end[v])/2
        centers=[(mx-sign*dy/chord*height,my+sign*dx/chord*height) for sign in (1,-1)]
        cx,cy=min(centers,key=lambda c:abs(sweep(*c)[1])) if values['R']>=0 else max(centers,key=lambda c:abs(sweep(*c)[1]))
    else:raise ValueError('Bogenmittelpunkt fehlt.')
    if not 1e-5<radius<10000 or abs(math.hypot(end[u]-cx,end[v]-cy)-radius)>max(.1,radius*.005):raise ValueError('Unplausibler Bogenmittelpunkt.')
    angle,delta=sweep(cx,cy)
    step=min(1.5/radius,2*math.acos(max(-1,min(1,1-.04/radius))))
    steps=max(2,min(1024,math.ceil(abs(delta)/max(step,.001))))
    for i in range(1,steps+1):
        if i==steps:yield tuple(end);continue
        a=angle+delta*i/steps;point=list(start);point[u]=cx+radius*math.cos(a);point[v]=cy+radius*math.sin(a);point[w]=start[w]+(end[w]-start[w])*i/steps
        yield tuple(point)


def build_model(stream,plate,metadata,cancel=lambda:None):
    selected=next((p for p in metadata['plates'] if p['index']==plate),None)
    if not selected:raise ValueError('Modellplatte nicht in den geprüften Dateidaten vorhanden.')
    palette={f['id']-1:bytes.fromhex(f['color'].lstrip('#')[:6]) for f in selected['filaments'] if re.fullmatch(r'#?[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?',f.get('color',''))}
    try:width=float(selected.get('nozzle') or .4)*1.1
    except ValueError:width=.44
    width=max(.05,min(3,width));height=.2;role=0;custom=False;wiping=False;layer_started=False;plane=17;absolute_center=False
    tool=next(iter(palette),0);pos=(0.,0.,0.);extruder=0.;relative=False;relative_e=False
    records=[];count=0;arcs=0;removed=0;warnings=set();deadline=time.monotonic()+30
    def guard():
        cancel()
        if time.monotonic()>deadline:raise ValueError('3D-Aufbereitung überschreitet 30 Sekunden.')
    def emit(a,b):
        nonlocal count
        if not any(abs(b[i]-a[i])>1e-7 for i in range(3)):return
        if any(not math.isfinite(n) or abs(n)>10000 for n in (*a,*b)):raise ValueError('G-Code-Koordinaten außerhalb der Vorschaugrenzen.')
        count+=1
        if count%1024==0:guard()
        color=palette.get(tool,b'\x94\xa8\xb5')
        # Merge only truly collinear connected segments; never discard alternate paths.
        if records and records[-1][1]==a and records[-1][2:]==(color,width,height,role):
            first=records[-1][0];ab=[a[i]-first[i] for i in range(3)];bc=[b[i]-a[i] for i in range(3)]
            cross=[ab[1]*bc[2]-ab[2]*bc[1],ab[2]*bc[0]-ab[0]*bc[2],ab[0]*bc[1]-ab[1]*bc[0]]
            if sum(x*x for x in cross)<1e-12 and sum(ab[i]*bc[i] for i in range(3))>0:
                records[-1]=(first,b,color,width,height,role);return
        if len(records)>=MAX_SEGMENTS:raise ValueError('Modell überschreitet die Geometriegrenze der 3D-Ansicht; eingebettetes Vorschaubild bleibt verfügbar.')
        records.append((a,b,color,width,height,role))
    with zipfile.ZipFile(stream) as z:
        info=z.getinfo(f'Metadata/plate_{plate}.gcode')
        if info.file_size>MAX_GCODE or info.flag_bits&1:raise ValueError('3D unterstützt unverschlüsselten G-Code bis 192 MiB.')
        with z.open(info) as source:
            lines=0
            while True:
                raw=source.readline(4097)
                if not raw:break
                lines+=1
                if lines>3000000:raise ValueError('G-Code überschreitet drei Millionen Zeilen.')
                if len(raw)>4096:
                    if b';' not in raw:raise ValueError('G-Code-Befehl überschreitet 4 KiB.')
                    prefix=raw.split(b';',1)[0];tail=raw
                    while tail and not tail.endswith(b'\n'):guard();tail=source.readline(65536)
                    raw=prefix+b'\n'
                if lines%128==0:guard()
                text=raw.decode('ascii','ignore').strip().upper()
                if re.match(r';\s*(?:CHANGE_LAYER|LAYER_CHANGE|LAYER\s*:\s*0\b)',text) and not layer_started:
                    removed+=count;records.clear();count=0;layer_started=True
                feature=re.match(r';\s*(?:FEATURE|TYPE)\s*:\s*(.*)',text)
                if feature:
                    name=feature[1];custom=any(v in name for v in ('CUSTOM','PRIME TOWER','PURGE','WIPE TOWER'))
                    role=4 if any(v in name for v in ('SPARSE INFILL','INTERNAL SOLID INFILL','FILL')) and 'GAP' not in name else 1
                    if any(v in name for v in ('TOP','BOTTOM','BRIDGE','IRONING','GAP')):role=2
                    if 'SUPPORT' in name:role=3
                if re.match(r';\s*WIPE(?:_TOWER)?_START\b',text):wiping=True
                if re.match(r';\s*WIPE(?:_TOWER)?_END\b',text):wiping=False
                dim=re.match(r';\s*(LINE_WIDTH|LAYER_HEIGHT)\s*:\s*([\d.]+)',text)
                if dim:
                    try:value=float(dim[2])
                    except ValueError:value=0
                    if dim[1]=='LINE_WIDTH' and .03<=value<=3:width=value
                    elif dim[1]=='LAYER_HEIGHT' and .01<=value<=2:height=value
                line=text.split(';',1)[0]
                command=re.match(r'\s*(?:N\d+\s*)?([GMT])\s*(\d+(?:\.\d+)?)(.*)',line)
                if not command:continue
                family,raw_n,tail=command.groups();n=float(raw_n)
                if family=='T':
                    if n.is_integer() and 0<=n<64:tool=int(n)
                    continue
                if family=='M':
                    if n==82:relative_e=False
                    elif n==83:relative_e=True
                    continue
                if n==20:raise ValueError('Zoll-G-Code wird nicht unterstützt.')
                if n in (17,18,19):plane=int(n);continue
                if n==90.1:absolute_center=True;continue
                if n==91.1:absolute_center=False;continue
                if n==90:relative=False;continue
                if n==91:relative=True;continue
                if n not in (0,1,2,3,28,92):continue
                values={key:float(value) for key,value in WORDS.findall(tail)}
                if any(not math.isfinite(v) for v in values.values()):raise ValueError('Ungültige G-Code-Koordinaten.')
                if n==28:
                    updated=list(pos)
                    for i in ([j for j,key in enumerate('XYZ') if key in tail] or [0,1,2]):updated[i]=0
                    pos=tuple(updated);continue
                if n==92:
                    pos=tuple(values.get(key,pos[i]) for i,key in enumerate('XYZ'))
                    if 'E' in values:extruder=values['E']
                    continue
                target=tuple(pos[i]+values.get(key,0) if relative else values.get(key,pos[i]) for i,key in enumerate('XYZ'))
                if any(abs(v)>10000 for v in target):raise ValueError('G-Code-Koordinaten außerhalb der Vorschaugrenzen.')
                extrusion=values.get('E',0) if relative_e else values.get('E',extruder)-extruder
                if 'E' in values:extruder=extruder+values['E'] if relative_e else values['E']
                if extrusion>1e-7:
                    if custom or wiping:removed+=1
                    elif n in (2,3):
                        try:
                            previous=pos
                            for point in arc_points(pos,target,values,n==2,plane,absolute_center):emit(previous,point);previous=point
                            arcs+=1
                        except ValueError as exc:
                            if 'bogen' not in str(exc).lower():raise
                            warnings.add('Einzelne ungültige Kreisbögen wurden als direkte Verbindungen dargestellt.');emit(pos,target)
                    else:emit(pos,target)
                pos=target
    guard()
    if not records:raise ValueError('Keine darstellbaren Modellbahnen gefunden.')
    lower=[min(min(a[i],b[i]) for a,b,*_ in records) for i in range(3)];upper=[max(max(a[i],b[i]) for a,b,*_ in records) for i in range(3)]
    positions=bytearray();colors=bytearray();dimensions=bytearray();roles=bytearray()
    for a,b,color,w,h,r in records:positions.extend(struct.pack('<6f',*a,*b));colors.extend(color*2);dimensions.extend(struct.pack('<2f',w,h));roles.append(r)
    warnings.add('Darstellung aus Druckbahnen; Breite und Schichthöhe werden aus Slicerangaben angenähert. Reinigung und Startbahnen sind ausgeblendet, sofern markiert.')
    return dict(version=VERSION,plate=plate,positions=base64.b64encode(positions).decode(),colors=base64.b64encode(colors).decode(),dimensions=base64.b64encode(dimensions).decode(),roles=base64.b64encode(roles).decode(),
                segments=len(records),total_segments=count,bounds=[lower,upper],warnings=sorted(warnings),arc_commands=arcs,removed_segments=removed)

def model_key(cache,entry,plate):return cache.key(dict(entry,model_plate=plate,model_version=VERSION))


def cached_model(cache,entry,plate):
    with cache.lock:
        row=cache.db.execute('SELECT data FROM storage_models WHERE key=? AND created>?',(model_key(cache,entry,plate),time.time()-30*86400)).fetchone()
    if not row:return None
    try:
        decoder=zlib.decompressobj();data=decoder.decompress(row[0],MAX_MODEL_JSON+1)
        return json.loads(data) if decoder.eof and len(data)<=MAX_MODEL_JSON else None
    except (ValueError,zlib.error):return None


def save_model(cache,entry,plate,data):
    raw=json.dumps(data,separators=(',',':')).encode()
    if len(raw)>MAX_MODEL_JSON:raise ValueError('3D-Daten überschreiten das Vorschau-Limit.')
    payload=zlib.compress(raw)
    with cache.lock:
        cache.db.execute('DELETE FROM storage_models WHERE created<?',(time.time()-30*86400,))
        cache.db.execute('INSERT OR REPLACE INTO storage_models VALUES (?,?,?,?)',(model_key(cache,entry,plate),cache.path_key(entry),payload,time.time()))
        while True:
            count,size=cache.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM storage_models').fetchone()
            if count<=20 and size<=32*1024*1024:break
            cache.db.execute('DELETE FROM storage_models WHERE key=(SELECT key FROM storage_models ORDER BY created,key LIMIT 1)')
        cache.db.commit()
