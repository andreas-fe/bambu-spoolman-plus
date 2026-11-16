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
