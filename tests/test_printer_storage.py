import io,zipfile
import pytest
from flask import Flask
from bridge.printer_storage import safe_file,parse_listing,register_storage
from bridge.archive_preview import streamed_thumbnail


def test_paths_listing_and_thumbnail():
    for name in ['../a.3mf','a\nDELE b.3mf','a/b.3mf','config.ini','a\\b.gcode']:
        with pytest.raises(ValueError):safe_file('/cache',name)
    with pytest.raises(ValueError):safe_file('/etc','a.3mf')
    rows=parse_listing(['-rw-r--r-- 1 owner group 12 Nov 16 10:00 My file.3mf','drwxrwxrwx 1 a b 0 Nov 16 10:00 dir.3mf','lrwxrwxrwx 1 a b 5 Nov 16 10:00 link.3mf -> private'], '/cache')
    assert len(rows)==1 and rows[0]['path']=='/cache/My file.3mf'
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
    assert streamed_thumbnail(io.BytesIO(data.getvalue()).read)[1]=='image/png'
    with pytest.raises(Exception):streamed_thumbnail(io.BytesIO(b'invalid').read)


class FTP:
    def __init__(self):self.deleted=[];self.closed=False;self.size=12
    def retrlines(self,cmd,callback):callback('-rw-r--r-- 1 a b '+str(self.size)+' Nov 16 10:00 Example.3mf')
    def delete(self,path):self.deleted.append(path)
    def quit(self):self.closed=True
    def close(self):self.closed=True


def context():
    app=Flask(__name__);app.secret_key='test';ftp=FTP();blocked=[None];idle=[True]
    register_storage(app,lambda:ftp,lambda name:blocked[0],lambda:idle[0],lambda s:None)
    c=app.test_client();c.get('/storage')
    token=c.get('/api/storage?folder=/cache').json['files'][0]['token']
    return c,ftp,blocked,idle,token


def test_delete_rechecks_selection_idle_and_blocking():
    c,ftp,blocked,idle,token=context()
    payload=dict(files=token,confirm='delete-selected')
    idle[0]=False;assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    idle[0]=True;blocked[0]='Open job';assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    blocked[0]=None;ftp.size=13;assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    ftp.size=12;assert c.post('/storage/delete',data=payload).status_code==200
    assert ftp.deleted==['/cache/Example.3mf'] and ftp.closed


def test_invalid_confirm_token_and_folder_never_delete():
    c,ftp,blocked,idle,token=context()
    assert c.post('/storage/delete',data=dict(files=token)).status_code==400
    assert c.post('/storage/delete',data=dict(files='bad',confirm='delete-selected')).status_code==409
    assert c.get('/api/storage?folder=/etc').status_code==400
    assert not ftp.deleted


def test_preview_cache_persists_isolates_and_evicts(tmp_path,monkeypatch):
    from bridge.preview_cache import PreviewCache
    import bridge.preview_cache as module
    clock=[1000];monkeypatch.setattr(module.time,'time',lambda:clock[0])
    path=tmp_path/'cache.db';c=PreviewCache(path,'printer',max_bytes=5,max_entries=2,ttl=100)
    e=lambda name:dict(path='/'+name,name=name,size=10,modified='Nov 16 10:00')
    c.put(e('a.3mf'),b'aa','image/png');clock[0]+=1;c.put(e('b.3mf'),b'bb','image/png')
    clock[0]+=1;assert c.get(e('a.3mf'))[0]==b'aa'
    clock[0]+=1;c.put(e('c.3mf'),b'cc','image/png')
    assert not c.contains(e('b.3mf')) and c.contains(e('a.3mf'))
    assert PreviewCache(path,'printer').get(e('a.3mf'))[0]==b'aa'
    assert not PreviewCache(path,'other-printer').contains(e('a.3mf'))
    assert not c.contains(dict(e('a.3mf'),size=11))
    clock[0]+=101;assert not c.get(e('a.3mf'))
    c.clear();assert c.db.execute('SELECT COUNT(*) FROM previews').fetchone()[0]==0


def test_preview_cache_avoids_transfer_and_works_without_idle():
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
    data=archive.getvalue();ftp=FTP();ftp.size=len(data);calls=[]
    class Stream(io.BytesIO):
        def recv(self,size):return self.read(size)
    ftp.voidcmd=lambda command:None
    ftp.transfercmd=lambda command:(calls.append(command),Stream(data))[1]
    app=Flask(__name__);app.secret_key='cache-test';idle=[True]
    register_storage(app,lambda:ftp,lambda n:None,lambda:idle[0],lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/cache').json['files'][0]
    assert entry['preview_url'] is None
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==200
    idle[0]=False
    r=client.post('/storage/preview',data={'file':entry['token']});assert r.status_code==200 and r.headers['X-Preview-Cache']=='hit'
    assert len(calls)==1
    cached=client.get('/api/storage?folder=/cache').json['files'][0]
    assert client.get(cached['preview_url']).status_code==200
    ftp.size+=1
    assert client.get('/api/storage?folder=/cache').json['files'][0]['preview_url'] is None
    assert client.post('/storage/preview-cache/clear',data={'confirm':'wrong'}).status_code==400
    assert client.get(cached['preview_url']).status_code==200
    assert client.post('/storage/preview-cache/clear',data={'confirm':'clear-previews'}).status_code==200
    assert client.get(cached['preview_url']).status_code==404
    assert not ftp.deleted


def test_large_archive_preview_stops_before_gcode_and_rechecks_idle():
    from tests.test_archive_preview import archive
    data=archive(stream=True);ftp=FTP();ftp.size=60*1024*1024
    reads=[];streams=[]
    class Stream(io.BytesIO):
        def recv(self,size):
            data=self.read(size);reads.append(len(data));return data
    def transfer(command):
        stream=Stream(data);streams.append(stream);return stream
    ftp.voidcmd=lambda command:None;ftp.transfercmd=transfer
    app=Flask(__name__);app.secret_key='large-test';idle=[False]
    register_storage(app,lambda:ftp,lambda n:None,lambda:idle[0],lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/cache').json['files'][0]
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==409 and not reads
    idle[0]=True;ftp.size+=1
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==400 and not reads
    ftp.size-=1
    r=client.post('/storage/preview',data={'file':entry['token']})
    assert r.status_code==200 and r.mimetype=='image/png'
    assert sum(reads)<=65536 and all(s.closed for s in streams) and ftp.closed and not ftp.deleted


def test_storage_metadata_real_plate_indices_and_bounds():
    from bridge.storage_metadata import inspect_archive
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('Metadata/plate_3.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><metadata key="prediction" value="120"/><filament id="2" type="PLA" color="#FFFFFF" used_g="9.61"/></plate></config>')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1"}')
    result=inspect_archive(data)
    assert result['plates'][0]['index']==3 and result['plates'][0]['filaments'][0]['id']==2
    assert result['plates'][0]['seconds']==120
    bad=io.BytesIO()
    with zipfile.ZipFile(bad,'w') as z:z.writestr('Metadata/slice_info.config','<!DOCTYPE a><config/>')
    with pytest.raises(ValueError):inspect_archive(bad)


def test_background_details_and_single_use_review_guard_file_change():
    import json,time
    from bridge.print_start import prepare_start
    from tests.test_print_start import inputs
    _,metadata,choice,ctx=inputs()
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('Metadata/plate_3.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><metadata key="nozzle_diameters" value="0.4"/><filament id="2" type="PLA" color="#FFFFFF" used_g="2"/></plate></config>')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1","filament_type":["PLA","PLA"]}')
    data=data.getvalue();ftp=FTP();ftp.size=len(data);transfers=[];starts=[]
    def transfer(command,write,blocksize=8192):transfers.append(command);write(data)
    ftp.retrbinary=transfer
    handlers=dict(context=lambda:ctx,history=lambda n:[dict(state='DONE')],prepare=lambda e,m,c:prepare_start(e,m,c,ctx),start=lambda r:(starts.append(r),dict(confirmed=True))[1])
    app=Flask(__name__);app.secret_key='actions-test'
    register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None,handlers=handlers)
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0]
    r=c.post('/storage/details',data=dict(file=entry['token']));assert r.status_code==202
    for _ in range(100):
        d=c.get('/api/storage/details',query_string=dict(ticket=r.json['ticket'])).json
        if d['state']!='loading':break
        time.sleep(.01)
    assert d['state']=='done' and d['metadata']['plates'][0]['index']==3
    assert c.post('/storage/details',data=dict(file=entry['token'])).json['state']=='done' and len(transfers)==1
    assert c.get('/api/storage/history',query_string=dict(file=entry['token'])).json['entries'][0]['state']=='DONE'
    def review():
        return c.post('/storage/start-review',data=dict(file=entry['token'],choice=json.dumps(choice))).json['token']
    token=review()
    assert c.post('/storage/start',data=dict(review=token)).status_code==400 and not starts
    ftp.size+=1
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).status_code==409 and not starts
    ftp.size-=1;token=review()
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).json['confirmed']
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).status_code==409 and len(starts)==1


def test_rename_preserves_cache_and_history_and_refuses_overwrite():
    import json,time
    source=io.BytesIO()
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
        z.writestr('Metadata/plate_1.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="1"/><filament id="1" type="PLA" used_g="1"/></plate></config>')
    data=source.getvalue()
    class Files:
        def __init__(self):self.files={'Original.gcode.3mf':data,'Exists.gcode.3mf':data};self.renamed=[];self.transfers=0
        def retrlines(self,command,callback):
            for name,content in self.files.items():callback('-rw-r--r-- 1 a b '+str(len(content))+' Nov 16 10:00 '+name)
        def retrbinary(self,command,write,blocksize=8192):self.transfers+=1;write(self.files[command[5:].split('/')[-1]])
        def rename(self,a,b):self.files[b.split('/')[-1]]=self.files.pop(a.split('/')[-1]);self.renamed.append((a,b))
        def quit(self):pass
        def close(self):pass
    ftp=Files();idle=[True];blocked=[False];app=Flask(__name__);app.secret_key='rename-test'
    handlers=dict(history=lambda name:[dict(id='old-job',created_at='2026-01-01',state='DONE')] if name=='Original.gcode.3mf' else [],context=lambda:{},prepare=lambda *a:{},start=lambda *a:{})
    register_storage(app,lambda:ftp,lambda name:'Protected' if blocked[0] else None,lambda:idle[0],lambda s:None,handlers=handlers)
    c=app.test_client();entry=next(x for x in c.get('/api/storage?folder=/cache').json['files'] if x['name']=='Original.gcode.3mf')
    ticket=c.post('/storage/details',data=dict(file=entry['token'])).json['ticket']
    for _ in range(100):
        response=c.get('/api/storage/details',query_string=dict(ticket=ticket)).json
        if response['state']!='loading':break
        time.sleep(.01)
    assert response['state']=='done'
    def rename(name):return c.post('/storage/rename',data=dict(file=entry['token'],name=name))
    assert rename('Exists.gcode.3mf').status_code==409 and not ftp.renamed
    for name in ('../bad.gcode.3mf','bad.gcode','bad\nname.gcode.3mf'):assert rename(name).status_code==409
    idle[0]=False;assert rename('New.gcode.3mf').status_code==409
    idle[0]=True;blocked[0]=True;assert rename('New.gcode.3mf').status_code==409
    blocked[0]=False;assert rename('New.gcode.3mf').status_code==200
    new=next(x for x in c.get('/api/storage?folder=/cache').json['files'] if x['name']=='New.gcode.3mf')
    assert c.post('/storage/details',data=dict(file=new['token'])).json['state']=='done' and ftp.transfers==1
    assert c.get('/api/storage/history',query_string=dict(file=new['token'])).json['entries'][0]['id']=='old-job'
    assert len(ftp.renamed)==1


def test_draft_does_not_enable_server_start_until_full_inspection():
    import json,time,threading
    source=io.BytesIO()
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('Metadata/plate_3.json','{"filament_ids":[0]}')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1","filament_type":["PLA"],"filament_colour":["#FFFFFF"],"nozzle_diameter":["0.4"]}')
        z.writestr('Metadata/plate_3.gcode','G1 X0\n'*20000)
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><filament id="1" type="PLA" used_g="1"/></plate></config>')
    content=source.getvalue();release=threading.Event();ftp=FTP();ftp.size=len(content);calls=[]
    def transfer(command,write,blocksize=8192):write(content[:65536]);release.wait(3);write(content[65536:])
    ftp.retrbinary=transfer
    app=Flask(__name__);app.secret_key='draft-guard'
    register_storage(app,lambda:ftp,lambda name:None,lambda:True,lambda s:None,handlers=dict(history=lambda n:[],context=lambda:{},prepare=lambda *a:(calls.append(True),dict(summary=['OK']))[1],start=lambda *a:{}))
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0];ticket=c.post('/storage/details',data=dict(file=entry['token'])).json['ticket']
    try:
        for _ in range(100):
            d=c.get('/api/storage/details',query_string=dict(ticket=ticket)).json
            if d.get('draft_metadata'):break
            time.sleep(.01)
        assert d['state']=='loading' and d['draft_metadata']['draft']
        assert c.post('/storage/start-review',data=dict(file=entry['token'],choice='{}')).status_code==409 and not calls
    finally:release.set()
