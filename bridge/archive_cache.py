"""Short-lived local reuse of fully verified downloads; never inventory data."""
import tempfile
import threading
import time
from pathlib import Path


class ArchiveCache:
    def __init__(self,max_bytes=128*1024*1024,max_entries=2,ttl=300):
        self.max_bytes=max_bytes;self.max_entries=max_entries;self.ttl=ttl
        self.directory=tempfile.TemporaryDirectory(prefix='plus-archives-')
        self.lock=threading.Lock();self.entries={};self.timer=None;self.generation=0

    def _remove(self,key):
        item=self.entries.pop(key)
        Path(item['path']).unlink(missing_ok=True)

    def _prune(self):
        now=time.monotonic()
        for key,item in list(self.entries.items()):
            if item['expires']<=now:self._remove(key)
        while len(self.entries)>self.max_entries or sum(i['size'] for i in self.entries.values())>self.max_bytes:
            self._remove(min(self.entries,key=lambda k:self.entries[k]['used']))

    def _schedule(self):
        if self.timer:self.timer.cancel()
        self.timer=None
        if self.entries:
            self.timer=threading.Timer(max(.01,min(i['expires'] for i in self.entries.values())-time.monotonic()),self.expire)
            self.timer.daemon=True;self.timer.start()

    def expire(self):
        with self.lock:self._prune();self._schedule()

    def get(self,key):
        with self.lock:
            self._prune();item=self.entries.get(key)
            if not item:return None
            try:stream=open(item['path'],'rb')
            except OSError:self._remove(key);return None
            item['used']=time.monotonic()
            return stream

    def put(self,key,source,cancel=lambda:None,generation=None):
        source.seek(0,2);size=source.tell();source.seek(0)
        if not 0<size<=self.max_bytes:return
        path=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory.name,delete=False) as target:
                path=target.name
                while True:
                    cancel();block=source.read(1024*1024)
                    if not block:break
                    target.write(block)
                cancel()
            with self.lock:
                if generation is not None and generation!=self.generation:return
                if key in self.entries:self._remove(key)
                now=time.monotonic();self.entries[key]=dict(path=path,size=size,expires=now+self.ttl,used=now)
                path=None;self._prune();self._schedule()
        finally:
            if path:Path(path).unlink(missing_ok=True)
            source.seek(0)

    def clear(self):
        with self.lock:
            self.generation+=1
            for key in list(self.entries):self._remove(key)
            self._schedule()
