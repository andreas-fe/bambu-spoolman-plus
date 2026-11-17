"""Explicit same-directory rename, protecting active files and retaining history."""
import hashlib
import json
from pathlib import PurePosixPath
from flask import request,jsonify


def register_rename(app,storage,decode,cache,idle,blocked,log):
    with cache.lock:
        cache.db.execute('CREATE TABLE IF NOT EXISTS file_aliases(namespace TEXT,old_path TEXT,new_path TEXT,created REAL)');cache.db.commit()
    namespace=lambda:hashlib.sha256(cache.namespace.encode()).hexdigest()
    def names(entry):
        with cache.lock:rows=cache.db.execute('SELECT old_path,new_path FROM file_aliases WHERE namespace=? ORDER BY created DESC LIMIT 1000',(namespace(),)).fetchall()
        paths={entry['path']}
        for _ in range(100):
            previous=set(paths)
            paths.update(old for old,new in rows if new in paths)
            if paths==previous:break
        return sorted({PurePosixPath(p).name for p in paths})
    @app.post('/storage/rename')
    def rename():
        if not storage.lock.acquire(False):return jsonify(error='Dateizugriff läuft bereits. Bitte abwarten.'),409
        try:
            from .printer_storage import safe_file
            import time
            entry=decode(request.form.get('file'));name=request.form.get('name','').strip()
            suffix=next(s for s in ('.gcode.3mf','.3mf','.gcode') if entry['name'].lower().endswith(s))
            if not name.lower().endswith(suffix) or len(name.encode('utf-8'))>240:raise ValueError('Dateiendung beibehalten und einen kürzeren Namen wählen.')
            folder=str(PurePosixPath(entry['path']).parent);target=safe_file(folder,name)
            if target==entry['path']:raise ValueError('Bitte einen neuen Namen eingeben.')
            if not idle() or blocked(entry['name']) or blocked(name):raise ValueError('Datei wird benötigt oder Drucker ist nicht im bestätigten Leerlauf.')
            with storage.connection() as ftp:
                storage.checked(ftp,entry)
                if any(r['name'].casefold()==name.casefold() for r in storage.listing(ftp,folder)):raise ValueError('Dieser Dateiname ist bereits vergeben. Es wird nichts überschrieben.')
                if not idle() or blocked(entry['name']) or blocked(name):raise ValueError('Drucker- oder Auftragsstatus geändert. Umbenennen gestoppt.')
                ftp.rename(entry['path'],target)
                actual=next((r for r in storage.listing(ftp,folder) if r['path']==target),None)
                if not actual or actual['size']!=entry['size']:raise ValueError('Umbenennung nicht vollständig verifiziert. Dateiliste aktualisieren und Ergebnis prüfen.')
            image=cache.get(entry)
            if image:cache.put(actual,*image)
            cache.remove(entry)
            with cache.lock:
                row=cache.db.execute('SELECT data,created FROM storage_metadata WHERE key=?',(cache.key(entry),)).fetchone()
                if row:cache.db.execute('INSERT OR REPLACE INTO storage_metadata VALUES (?,?,?)',(cache.key(actual),row[0],row[1]))
                cache.db.execute('DELETE FROM storage_metadata WHERE key=?',(cache.key(entry),))
                cache.db.execute('INSERT INTO file_aliases VALUES (?,?,?,?)',(namespace(),entry['path'],target,time.time()));cache.db.commit()
            log('Druckerspeicher: Eine Datei ausdrücklich umbenannt; Buchungshistorie unverändert.')
            return jsonify(message='Datei umbenannt. Zugehörige Historie und vorhandene Vorschaudaten bleiben erhalten.',name=name)
        except ValueError as e:return jsonify(error=str(e)),409
        except Exception:return jsonify(error='Umbenennung nicht eindeutig bestätigt. Dateiliste aktualisieren und Ergebnis prüfen; nicht ungeprüft wiederholen.'),503
        finally:storage.lock.release()
    return names
