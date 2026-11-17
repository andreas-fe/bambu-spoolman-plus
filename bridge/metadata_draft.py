"""Verified early ZIP entries prepare the UI; only final slice_info enables start."""
import base64
import json
import queue
import re
import threading
from .archive_preview import streamed_entries
from .storage_metadata import plate_settings,describe_plate


class DraftCollector:
    def __init__(self,publish):
        self.queue=queue.Queue(maxsize=4);self.finished=threading.Event();self.publish=publish;self.cancelled=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def feed(self,data):
        while not self.finished.is_set():
            try:self.queue.put(data,timeout=.1);return
            except queue.Full:continue
    def abort(self):
        self.cancelled.set();self.finished.set()
        try:
            while True:self.queue.get_nowait()
        except queue.Empty:pass
        try:self.queue.put_nowait(b'')
        except queue.Full:pass
    def close(self):self.feed(b'');self.thread.join(timeout=1)
    def receive(self,size):
        if self.cancelled.is_set():return b''
        data=self.queue.get(timeout=15)
        if len(data)>size:self.pending=data[size:];return data[:size]
        return data
    def run(self):
        values={};self.pending=b''
        def receive(size):
            if self.pending:
                data,self.pending=self.pending[:size],self.pending[size:];return data
            return self.receive(size)
        def wanted(name):return name in ('Metadata/project_settings.config','Metadata/model_settings.config') or bool(re.fullmatch(r'Metadata/plate_\d+(?:_small)?\.(?:json|png)',name))
        try:
            for name,data in streamed_entries(receive,select=wanted,stop_before=lambda n:bool(re.fullmatch(r'Metadata/plate_\d+\.gcode',n))):
                if data is not None:values[name]=data;continue
                index=int(re.search(r'plate_(\d+)',name).group(1))
                project=json.loads(values.get('Metadata/project_settings.config',b'{}'))
                info=json.loads(values.get(f'Metadata/plate_{index}.json',b'{}'))
                ids=info.get('filament_ids',[]);types=project.get('filament_type',[]);colors=project.get('filament_colour',[])
                if not project.get('printer_model') or not ids or len(ids)>16:return
                if any(type(i) is not int or i<0 or i>=len(types) or i>=len(colors) for i in ids):return
                preview=values.get(f'Metadata/plate_{index}_small.png') or values.get(f'Metadata/plate_{index}.png')
                image='data:image/png;base64,'+base64.b64encode(preview).decode() if preview and preview.startswith(b'\x89PNG\r\n\x1a\n') and len(preview)<=256*1024 else None
                nozzles=project.get('nozzle_diameter') or []
                self.publish(dict(model=project['printer_model'],filament_count=len(types),draft=True,plates=[dict(**describe_plate(index,plate_settings(values.get('Metadata/model_settings.config')),project),index=index,seconds=None,grams=None,nozzle=str(nozzles[0]) if nozzles else '',preview=image,filaments=[dict(id=i+1,material=types[i],color=colors[i],grams=None) for i in ids],objects=[str(o.get('name','')) for o in info.get('bbox_objects',[]) if isinstance(o,dict)],warnings=[])]))
        except Exception:pass  # Full validated inspection remains the fallback.
        finally:self.finished.set()
