"""On-demand camera and isolated, explicit Tapo actions.

A1/P1 protocol: https://github.com/Doridian/OpenBambuAPI/blob/main/video.md
No camera thread runs while the page is closed. Device failures never enter
inventory transactions or the print job queue.
"""
import asyncio
import math
import json
import subprocess
import sys
import os
from pathlib import Path
import socket
import ssl
import struct
import threading
import time
from flask import Blueprint, Response, jsonify, request, session
import secrets
from .ui import render_ui


def read_secret(name):
    filename = os.getenv(name + '_FILE')
    return Path(filename).read_text().strip() if filename else os.getenv(name, '').strip()


def recv_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError('Kamera hat die Verbindung beendet.')
        chunks.extend(chunk)
    return bytes(chunks)


def camera_frames(host, access_code):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Bambu LAN camera uses the printer's self-signed certificate.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 6000), timeout=8) as raw:
        with context.wrap_socket(raw, server_hostname=host) as stream:
            stream.settimeout(12)
            stream.sendall(struct.pack('<IIII32s32s', 64, 0x3000, 0, 0, b'bblp', access_code.encode('ascii')))
            while True:
                length = struct.unpack('<I', recv_exact(stream, 16)[:4])[0]
                if not 4 <= length <= 5 * 1024 * 1024:
                    raise ValueError('Ungültige Kamera-Bildgröße.')
                frame = recv_exact(stream, length)
                if not frame.startswith(b'\xff\xd8') or not frame.endswith(b'\xff\xd9'):
                    raise ValueError('Ungültiges JPEG-Bild.')
                yield frame


def _measurement(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def power_measurements(energy, power):
    # SDK EnergyUsageResult.current_power is mW; CurrentPowerResult is W.
    milliwatts = _measurement(energy.get('current_power'))
    return dict(power_w=milliwatts / 1000 if milliwatts is not None else _measurement(power.get('current_power')),
                today_energy_wh=_measurement(energy.get('today_energy')),
                month_energy_wh=_measurement(energy.get('month_energy')),
                today_runtime_min=_measurement(energy.get('today_runtime')))


class ShutdownBlocked(ValueError):
    pass


class TapoSwitch:
    async def request(self, action=None, guard=None):
        from tapo import ApiClient
        host = read_secret('TAPO_HOST')
        username, password = read_secret('TAPO_USERNAME'), read_secret('TAPO_PASSWORD')
        if not all((host, username, password)):
            raise ValueError('Tapo-Zugang ist noch nicht konfiguriert.')
        client = ApiClient(username, password)
        device = await client.p110(host)
        status = await device.get_device_info()
        current = bool(status.device_on)
        if action is False and (guard is None or not guard()):
            raise ShutdownBlocked('Abschalten wegen Druckerstatus gesperrt.')
        if action is not None and current != action:
            await (device.on() if action else device.off())
            status = await device.get_device_info()
        result = {'on': bool(status.device_on), 'observed_at': time.time()}
        if action is None:
            energy, power = {}, {}
            try:
                energy = (await asyncio.wait_for(device.get_energy_usage(), timeout=4)).to_dict()
            except Exception:
                pass  # An unsupported meter must not prevent switching.
            if _measurement(energy.get('current_power')) is None:
                try:
                    power = (await asyncio.wait_for(device.get_current_power(), timeout=4)).to_dict()
                except Exception:
                    pass
            result.update(power_measurements(energy, power))
            result['observed_at'] = time.time()
        return result

    def run(self, action=None, guard=None, expected_job_id=None):
        if action is False and (guard is None or not guard()):
            raise ShutdownBlocked('Abschalten wegen Druckerstatus gesperrt.')
        # Rust extensions can abort the interpreter on incompatible device data.
        # Keep their entire lifecycle outside Flask and the inventory worker.
        worker_env=None
        if expected_job_id:
            worker_env=dict(os.environ,AUTO_OFF_JOB_ID=expected_job_id)
        result = subprocess.run([sys.executable, '-m', 'bridge.tapo_worker'],
                                input=json.dumps({'action': action}), capture_output=True,
                                text=True, timeout=25, check=False,env=worker_env)
        if result.returncode == 3:
            raise ShutdownBlocked('Der Drucker hat keinen sicheren Leerlauf bestätigt.')
        if result.returncode != 0:
            raise ValueError('Tapo-Client konnte die Anfrage nicht abschließen.')
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('on'), bool):
            raise ValueError('Ungültige Tapo-Antwort.')
        result = {'on': data['on']}
        for key in ('power_w', 'today_energy_wh', 'month_energy_wh', 'today_runtime_min', 'observed_at'):
            if key in data:
                result[key] = _measurement(data[key])
        return result


def safe_to_power_off(printer):
    return printer.get('state') in ('IDLE', 'FINISH') and 0 <= time.time() - printer.get('state_seen_at', 0) <= 30


def register_devices(app, host, access_code, printer, light_control=None, refresh_printer=None, shutdown_job_guard=None):
    bp = Blueprint('devices', __name__)
    camera_lock, switch_lock = threading.Lock(), threading.Lock()
    switch = TapoSwitch()
    status_cache = {'data': None, 'at': 0.0}

    @bp.get('/camera')
    def camera_page():
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        return render_ui(CAMERA_PAGE, active='camera', title='Kamera', token=session['csrf_token'])

    @bp.get('/devices')
    def page():
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        return render_ui(POWER_PAGE, active='devices', title='Steckdose & Energie', token=session['csrf_token'], configured=bool(os.getenv('TAPO_HOST') or os.getenv('TAPO_HOST_FILE')))

    def confirmed_idle():
        try:
            return bool(refresh_printer and refresh_printer()) and safe_to_power_off(printer)
        except Exception:
            return False

    @bp.get('/api/printer/shutdown-status')
    def shutdown_status():
        # The isolated switch process rechecks immediately before off().
        safe = confirmed_idle()
        job_id=request.args.get('job_id')
        if job_id:
            safe=safe and bool(shutdown_job_guard and shutdown_job_guard(job_id))
        return jsonify(safe=safe, state=printer.get('state'), state_seen_at=printer.get('state_seen_at'))

    @bp.post('/devices/light')
    def light():
        action = request.form.get('action')
        if action not in ('on', 'off'):
            return jsonify(error='Ungültige Lichtaktion.'), 400
        if light_control is None:
            return jsonify(error='Lichtsteuerung nicht verfügbar.'), 503
        try:
            light_control(action)
        except TimeoutError:
            return jsonify(error='Keine Bestätigung der Lichtstellung vom Drucker. LAN-Steuerung und Entwicklermodus prüfen.'), 504
        except Exception:
            return jsonify(error='Drucker nicht erreichbar. Lichtsteuerung fehlgeschlagen.'), 503
        return jsonify(message='Lichtstellung vom Drucker bestätigt: ' + ('Ein' if action == 'on' else 'Aus'))

    @bp.get('/devices/camera')
    def camera():
        if not camera_lock.acquire(blocking=False):
            return Response('Kamera wird bereits angezeigt.', 409)
        frames = camera_frames(host, access_code)
        try:
            first = next(frames)
        except Exception:
            frames.close()
            camera_lock.release()
            return Response('Kamera nicht erreichbar. LAN-Livebild am Drucker prüfen.', 503)
        closed = threading.Event()
        def close_camera():
            if not closed.is_set():
                closed.set()
                frames.close()
                camera_lock.release()
        def stream():
            try:
                frame = first
                while True:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
                    frame = next(frames)
            except (OSError, ValueError, StopIteration):
                return
            finally:
                close_camera()
        response = Response(stream(), mimetype='multipart/x-mixed-replace; boundary=frame', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        response.call_on_close(close_camera)
        return response

    @bp.route('/devices/tapo', methods=['GET', 'POST'])
    def tapo():
        action = None
        if request.method == 'POST':
            if request.form.get('action') not in ('on', 'off'):
                return jsonify(error='Ungültige Schaltaktion.'), 400
            action = request.form['action'] == 'on'
        cached = status_cache['data']
        if action is None and cached is not None and time.monotonic() - status_cache['at'] < 3:
            return jsonify(cached)
        if not switch_lock.acquire(timeout=10 if action is not None else 5):
            return jsonify(error='Eine Geräteabfrage läuft noch. Bitte kurz erneut versuchen.'), 409
        try:
            if action is False and not confirmed_idle():
                return jsonify(error='Abschalten gesperrt: Kein frischer Leerlauf bestätigt. Der Drucker ist aktiv, offline oder antwortet nicht.'), 409
            cached = status_cache['data']
            if action is None and cached is not None and time.monotonic() - status_cache['at'] < 3:
                return jsonify(cached)
            result = switch.run(action, lambda: safe_to_power_off(printer))
            if action is None:
                status_cache.update(data=result, at=time.monotonic())
            else:
                status_cache.update(data=None, at=0.0)
            return jsonify(result)
        except ShutdownBlocked:
            return jsonify(error='Abschalten gesperrt: Der Drucker ist inzwischen aktiv oder hat seinen Leerlauf nicht bestätigt.'), 409
        except Exception:
            return jsonify(error='Tapo nicht erreichbar. Zugang und Netzwerk prüfen.'), 503
        finally:
            switch_lock.release()
    app.register_blueprint(bp)
    return lambda:camera_lock.locked() or switch_lock.locked()


CAMERA_PAGE = r'''
<div class="page-heading"><div><div class="eyebrow">Dein Drucker</div><h1>Kamera</h1><p>Livebild und Druckerlicht an einem Platz.</p></div><span class="badge" id="camera-badge">Geschlossen</span></div>
<section class="panel"><div class="camera-toolbar"><div class="control-group"><button onclick="startCamera()">Livebild öffnen</button><button class="secondary" onclick="stopCamera()">Livebild schließen</button></div><div class="control-group" data-control="light"><button class="secondary" onclick="light('on')">Licht einschalten</button><button class="quiet" onclick="light('off')">Licht ausschalten</button></div></div><p id="light-status" class="device-status" role="status"></p><p id="camera-status" class="device-status" role="status">Die Kamera verbindet sich erst beim Öffnen. Beim Verlassen wird die Verbindung beendet.</p><img id="camera" class="camera-image" hidden alt="Livebild des Druckers"></section>
<script>
const img=document.getElementById('camera'),cameraStatus=document.getElementById('camera-status'),cameraBadge=document.getElementById('camera-badge');
function startCamera(){if(!img.hidden)return;cameraStatus.textContent='Kamera wird verbunden …';cameraBadge.textContent='Livebild';img.hidden=false;img.src='/devices/camera?t='+Date.now()}
function stopCamera(){img.hidden=true;img.removeAttribute('src');cameraStatus.textContent='Livebild geschlossen.';cameraBadge.textContent='Geschlossen'}
img.onerror=()=>{if(!img.hidden){img.hidden=true;img.removeAttribute('src');cameraStatus.textContent='Kamera nicht erreichbar. Prüfe Drucker und LAN-Livebild und öffne die Ansicht erneut.';cameraBadge.textContent='Nicht erreichbar'}};
img.onload=()=>{if(!img.hidden)cameraStatus.textContent='Livebild verbunden.'};
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera()});window.addEventListener('pagehide',stopCamera);
async function light(action){const buttons=[...document.querySelectorAll('[data-control="light"] button')],status=document.getElementById('light-status');if(buttons.some(b=>b.disabled))return;buttons.forEach(b=>b.disabled=true);status.textContent='Lichtbefehl wird gesendet …';try{const r=await fetch('/devices/light',{method:'POST',body:new URLSearchParams({action,csrf_token:{{token|tojson}}})});const data=await r.json();status.textContent=data.error||data.message}catch(error){status.textContent='Lichtsteuerung nicht erreichbar.'}finally{buttons.forEach(b=>b.disabled=false)}}
</script>
'''

POWER_PAGE = r'''
<div class="page-heading"><div><div class="eyebrow">Tapo P110</div><h1>Steckdose & Energie</h1><p>Schalten, Leistung ablesen und Energieverbrauch verfolgen.</p></div><span class="badge" id="plug-state">Noch nicht abgefragt</span></div>
{% if configured %}<section class="panel"><div class="section-heading"><h2>Steckdose bedienen</h2><div class="control-group"><button id="plug-on" onclick="tapo('on')">Einschalten</button><button class="danger" id="plug-off" onclick="tapo('off')">Ausschalten</button><button class="secondary" id="plug-refresh" onclick="readPower(true)">Status aktualisieren</button></div></div><p id="tapo-status" class="device-status" role="status">Status wird abgefragt …</p><p class="field-help">Vor dem Abschalten bestätigt der Drucker seinen aktuellen Leerlauf. Das Druckerlicht darf eingeschaltet bleiben.</p></section>
<section class="panel"><div class="section-heading"><div><h2>Leistung & Verbrauch</h2><p>Messwerte direkt von deiner Steckdose.</p></div><span class="muted small" id="meter-updated">Noch kein Messwert</span></div><div class="meter-grid"><div class="meter-card primary"><span>Aktuelle Leistung</span><strong id="power-w">—</strong><small>Watt</small></div><div class="meter-card"><span>Verbrauch heute</span><strong id="energy-today">—</strong><small>kWh</small></div><div class="meter-card"><span>Verbrauch im Monat</span><strong id="energy-month">—</strong><small>kWh</small></div><div class="meter-card"><span>Laufzeit heute</span><strong id="runtime-today">—</strong><small>Stunden / Minuten</small></div></div><div class="live-controls"><label class="live-toggle"><input id="live-meter" type="checkbox" onchange="configureLive()"> Live-Status aktivieren</label><label>Intervall<select id="meter-interval" onchange="configureLive()"><option value="5">Alle 5 Sekunden</option><option value="10" selected>Alle 10 Sekunden</option><option value="30">Alle 30 Sekunden</option></select></label><span class="muted small" id="live-hint">Einzelabfrage · Live-Status ist aus</span></div><p class="field-help">Live-Abfragen pausieren automatisch, wenn dieser Reiter nicht sichtbar ist. Fehlende Messwerte bleiben als „—“ gekennzeichnet.</p></section>
{% else %}<section class="panel"><h2>Steckdose einrichten</h2><p>Die Tapo-Steckdose ist noch nicht eingerichtet.</p></section>{% endif %}
<script>
const configured={{configured|tojson}};let pollTimer=null,pollController=null,pollBusy=false,actionBusy=false,lastOn=null,actionError=false,refreshPending=false;
const number=(value,digits=2)=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('de-DE',{maximumFractionDigits:digits}).format(value):'—';
function setButtons(){if(!configured)return;document.getElementById('plug-on').disabled=actionBusy||lastOn===true;document.getElementById('plug-off').disabled=actionBusy||lastOn===false;document.getElementById('plug-refresh').disabled=actionBusy||pollBusy}
function displayStatus(data){if(typeof data.on==='boolean'){lastOn=data.on;const badge=document.getElementById('plug-state');badge.textContent=data.on?'Eingeschaltet':'Ausgeschaltet';badge.classList.toggle('good',data.on)}if('power_w' in data){document.getElementById('power-w').textContent=number(data.power_w);document.getElementById('energy-today').textContent=number(data.today_energy_wh==null?null:data.today_energy_wh/1000,3);document.getElementById('energy-month').textContent=number(data.month_energy_wh==null?null:data.month_energy_wh/1000,3);document.getElementById('runtime-today').textContent=data.today_runtime_min==null?'—':Math.floor(data.today_runtime_min/60)+' h '+Math.floor(data.today_runtime_min%60)+' min';document.getElementById('meter-updated').textContent=data.observed_at?'Stand: '+new Date(data.observed_at*1000).toLocaleTimeString('de-DE'):'Zeitpunkt unbekannt'}setButtons()}
function stopPoll(){clearTimeout(pollTimer);pollTimer=null;if(pollController)pollController.abort()}
function schedulePoll(){clearTimeout(pollTimer);if(configured&&!document.hidden&&!actionBusy&&document.getElementById('live-meter').checked)pollTimer=setTimeout(readPower,Number(document.getElementById('meter-interval').value)*1000)}
function configureLive(){stopPoll();if(!configured)return;const live=document.getElementById('live-meter').checked;document.getElementById('live-hint').textContent=live?'Live-Status aktiv · pausiert bei ausgeblendeter Seite':'Einzelabfrage · Live-Status ist aus';if(live&&!document.hidden)readPower()}
async function readPower(manual=false){if(manual)actionError=false;if(!configured||pollBusy||actionBusy||document.hidden)return;clearTimeout(pollTimer);pollBusy=true;pollController=new AbortController();setButtons();try{const response=await fetch('/devices/tapo',{cache:'no-store',signal:pollController.signal});const data=await response.json();if(!response.ok)throw Error(data.error||'Abfrage fehlgeschlagen.');displayStatus(data);if(!actionError)document.getElementById('tapo-status').textContent=data.on?'Steckdose eingeschaltet.':'Steckdose ausgeschaltet.'}catch(error){if(error.name!=='AbortError')document.getElementById('tapo-status').textContent=error.message+' Letzte Messwerte werden nicht als neue Werte ausgegeben.'}finally{pollBusy=false;pollController=null;setButtons();if(refreshPending&&!actionBusy){refreshPending=false;readPower()}else schedulePoll()}}
async function tapo(action){if(actionBusy||!configured)return;stopPoll();actionBusy=true;actionError=false;setButtons();const status=document.getElementById('tapo-status');status.textContent=action==='off'?'Leerlauf wird am Drucker geprüft …':'Steckdose wird eingeschaltet …';try{const response=await fetch('/devices/tapo',{method:'POST',body:new URLSearchParams({action,csrf_token:{{token|tojson}}})});const data=await response.json();if(!response.ok)throw Error(data.error||'Schalten fehlgeschlagen.');displayStatus(data);status.textContent=data.on?'Steckdose eingeschaltet.':'Steckdose ausgeschaltet.'}catch(error){actionError=true;status.textContent=error.message}finally{actionBusy=false;setButtons();if(pollBusy)refreshPending=true;else readPower()}}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPoll();else if(configured&&document.getElementById('live-meter').checked)readPower()});window.addEventListener('pagehide',stopPoll);if(configured)readPower();
</script>
'''
