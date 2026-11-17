"""Disposable, bounded thumbnail cache, independent of inventory bookkeeping."""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path


class PreviewCache:
    def __init__(self,path=None,namespace='',max_bytes=64*1024*1024,max_entries=200,ttl=30*86400):
        self.namespace=namespace;self.max_bytes=max_bytes;self.max_entries=max_entries;self.ttl=ttl;self.lock=threading.Lock()
        if path:Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path) if path else ':memory:',check_same_thread=False)
        if path:Path(path).chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')  # Disposable images, never inventory data.
        self.db.execute('CREATE TABLE IF NOT EXISTS previews(key TEXT PRIMARY KEY,path_key TEXT,mime TEXT,data BLOB,created REAL,accessed REAL)');self.db.commit()
    def key(self,entry):return hashlib.sha256(json.dumps([self.namespace,entry],sort_keys=True).encode()).hexdigest()
    def path_key(self,entry):return hashlib.sha256((self.namespace+'|'+entry['path']).encode()).hexdigest()
    def contains(self,entry):
        with self.lock:return bool(self.db.execute('SELECT 1 FROM previews WHERE key=? AND created>?',(self.key(entry),time.time()-self.ttl)).fetchone())
    def get(self,entry):
        with self.lock:
            row=self.db.execute('SELECT data,mime FROM previews WHERE key=? AND created>?',(self.key(entry),time.time()-self.ttl)).fetchone()
            if row:self.db.execute('UPDATE previews SET accessed=? WHERE key=?',(time.time(),self.key(entry)));self.db.commit()
            return (bytes(row[0]),row[1]) if row else None
    def put(self,entry,data,mime):
        if len(data)>self.max_bytes:return
        with self.lock:
            now=time.time();self.db.execute('DELETE FROM previews WHERE created<? OR (path_key=? AND key!=?)',(now-self.ttl,self.path_key(entry),self.key(entry)))
            self.db.execute('INSERT OR REPLACE INTO previews VALUES (?,?,?,?,?,?)',(self.key(entry),self.path_key(entry),mime,data,now,now))
            while True:
                count,size=self.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM previews').fetchone()
                if count<=self.max_entries and size<=self.max_bytes:break
                self.db.execute('DELETE FROM previews WHERE key=(SELECT key FROM previews ORDER BY accessed,key LIMIT 1)')
            self.db.commit()
    def remove(self,entry):
        with self.lock:
            self.db.execute('DELETE FROM previews WHERE path_key=?',(self.path_key(entry),))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_models'").fetchone():self.db.execute('DELETE FROM storage_models WHERE path_key=?',(self.path_key(entry),))
            self.db.commit()
    def clear(self):
        with self.lock:
            self.db.execute('DELETE FROM previews')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_models'").fetchone():self.db.execute('DELETE FROM storage_models')
            self.db.commit();self.db.execute('VACUUM')
