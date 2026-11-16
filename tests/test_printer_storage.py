import io,zipfile
import pytest
from flask import Flask
from bridge.printer_storage import safe_file,parse_listing,thumbnail,register_storage


def test_paths_listing_and_thumbnail():
    for name in ['../a.3mf','a\nDELE b.3mf','a/b.3mf','config.ini','a\\b.gcode']:
        with pytest.raises(ValueError):safe_file('/cache',name)
    with pytest.raises(ValueError):safe_file('/etc','a.3mf')
    rows=parse_listing(['-rw-r--r-- 1 owner group 12 Nov 16 10:00 My file.3mf','drwxrwxrwx 1 a b 0 Nov 16 10:00 dir.3mf','lrwxrwxrwx 1 a b 5 Nov 16 10:00 link.3mf -> private'], '/cache')
    assert len(rows)==1 and rows[0]['path']=='/cache/My file.3mf'
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
    assert thumbnail(data.getvalue())[1]=='image/png'
    with pytest.raises(Exception):thumbnail(b'invalid')


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
    ftp.retrbinary=lambda command,write:(calls.append(command),write(data))
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
