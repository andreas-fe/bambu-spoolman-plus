"""Private device overrides. Separate from inventory, exports and API responses."""
import json
import ipaddress
import os
import re
import tempfile
import threading
from pathlib import Path
from flask import request,jsonify

KEYS=('BAMBU_HOST','BAMBU_ACCESS_CODE','BAMBU_SERIAL','BAMBU_MODEL','TAPO_HOST','TAPO_USERNAME','TAPO_PASSWORD')
SECRETS={'BAMBU_ACCESS_CODE','TAPO_PASSWORD'}


def read_overrides(path):
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    except FileNotFoundError:return {}
    with os.fdopen(fd) as stream:
        raw=stream.read(16385)
    try:
        data=json.loads(raw)
        if len(raw)>16384 or not isinstance(data,dict) or set(data)-set(KEYS) or any(not isinstance(v,str) or len(v)>1024 for v in data.values()):raise ValueError()
        return data
    except Exception:raise ValueError('Private Gerätekonfiguration ist ungültig; Serverkonfiguration prüfen.') from None


def load_overrides(path):
    for name,value in read_overrides(path).items():
        os.environ[name]=value
        os.environ.pop(name+'_FILE',None)


def effective_values():
    data={}
    for name in KEYS:
        filename=os.getenv(name+'_FILE')
        try:data[name]=Path(filename).read_text().strip() if filename else os.getenv(name,'').strip()
        except OSError:data[name]=''
    data['BAMBU_MODEL']=data['BAMBU_MODEL'] or 'Bambu Lab A1'
    return data


def write_overrides(path,values):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.device-settings-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),0o600)
            json.dump({k:v for k,v in values.items() if k in KEYS},stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def validate_devices(form,current):
    result=dict(current)
    for name in KEYS[:7]:
        if name not in form:continue
        if name=='TAPO_PASSWORD' and form[name] and form[name]!=form[name].strip():raise ValueError('Passwörter ohne führende oder nachgestellte Leerzeichen eingeben.')
        value=form[name].strip()
        if name in SECRETS and not value:continue
        if len(value)>512 or any(c in value for c in '\r\n\x00'):raise ValueError('Ungültige Eingabe in den Verbindungsdaten.')
        result[name]=value
    for key in ('BAMBU_HOST','TAPO_HOST'):
        value=result[key]
        if key=='TAPO_HOST' and not value:continue
        # Host/IP only: no URL, port, path or embedded credentials.
        try:ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,252}',value) or re.fullmatch(r'\d+\.\d+\.\d+\.\d+',value):raise ValueError('Bitte nur eine IP-Adresse oder einen Hostnamen ohne Port/URL eingeben.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,80}',result['BAMBU_SERIAL']):raise ValueError('Drucker-Seriennummer prüfen.')
    if not re.fullmatch(r'\d{8}',result['BAMBU_ACCESS_CODE']):raise ValueError('Der LAN-Zugangscode muss acht Ziffern enthalten.')
    if result['BAMBU_MODEL'] not in ('Bambu Lab A1','Bambu Lab A1 mini'):raise ValueError('Unterstütztes Druckermodell auswählen.')
    if result['TAPO_HOST'] and not all(result[k] for k in ('TAPO_USERNAME','TAPO_PASSWORD')):raise ValueError('Für Tapo werden Konto und Passwort benötigt.')
    return result


def register_config(app,path,guard,restart):
    lock=threading.Lock()
    def safe_view():
        current=effective_values()
        return dict(values={k:v for k,v in current.items() if k not in SECRETS},saved={k:bool(current[k]) for k in SECRETS},pending=bool(app.config.get('DEVICE_RESTART_PENDING')))
    def save(values):
        if request.form.get('idle_confirm')!='yes':raise ValueError('Änderung nur ohne laufenden Druck bestätigen.')
        with lock:
            if app.config.get('DEVICE_RESTART_PENDING'):raise ValueError('Eine Konfigurationsänderung wartet bereits auf den Neustart.')
            guard(values)
            old=read_overrides(path)
            write_overrides(path,values)
            try:
                app.config['DEVICE_RESTART_PENDING']=True
                restart()
            except Exception:
                app.config['DEVICE_RESTART_PENDING']=False;write_overrides(path,old)
                raise ValueError('Neustart konnte nicht vorbereitet werden. Konfiguration wurde nicht geändert.') from None
        return jsonify(message='Gespeichert. Die Anwendung startet ohne laufenden Druck neu. Die lokalen Verbindungsdaten werden danach verwendet.',restart=True)
    @app.post('/settings/connections')
    def save_devices():
        try:return save(validate_devices(request.form,effective_values()))
        except ValueError as e:return jsonify(error=str(e)),409
    return safe_view


CONFIG_UI=r'''
<section class="panel settings-width" id="connection-settings"><h2>Verbindungen & Zugangsdaten</h2>
{% if connections.pending %}<p class="notice">Neue Konfiguration gespeichert; Anwendung startet im Leerlauf neu.</p>{% endif %}
<form action="/settings/connections" method="post" onsubmit="saveConnections(event,this)"><input type="hidden" name="csrf_token" value="{{token}}"><div class="form-grid">
<label>Drucker-IP oder Hostname<input name="BAMBU_HOST" value="{{connections['values'].BAMBU_HOST}}" required autocomplete="off"></label><label>Druckermodell<select name="BAMBU_MODEL">{% for model in ['Bambu Lab A1','Bambu Lab A1 mini'] %}<option {% if connections['values'].BAMBU_MODEL==model %}selected{% endif %}>{{model}}</option>{% endfor %}</select></label>
<label>Seriennummer<input name="BAMBU_SERIAL" value="{{connections['values'].BAMBU_SERIAL}}" required autocomplete="off"><small>Eine andere Seriennummer bezeichnet einen anderen Drucker; vorhandene AMS-Bindungen werden nicht auf ihn übertragen.</small></label>
<label>LAN-Zugangscode<input name="BAMBU_ACCESS_CODE" type="password" inputmode="numeric" pattern="[0-9]{8}" autocomplete="new-password" placeholder="{{'Gespeichert – leer lassen zum Beibehalten' if connections.saved.BAMBU_ACCESS_CODE else 'Achtstelliger LAN-Zugangscode'}}"><small>Der Code für die lokale Bambu-Schnittstelle, kein WLAN-Passwort.</small></label>
<label>Tapo-IP oder Hostname<input name="TAPO_HOST" value="{{connections['values'].TAPO_HOST}}" autocomplete="off"><small>Leer lassen deaktiviert die Steckdosenanbindung.</small></label><label>Tapo-Konto<input name="TAPO_USERNAME" value="{{connections['values'].TAPO_USERNAME}}" autocomplete="off"></label>
<label>Tapo-Passwort<input name="TAPO_PASSWORD" type="password" autocomplete="new-password" placeholder="{{'Gespeichert – leer lassen zum Beibehalten' if connections.saved.TAPO_PASSWORD else 'Tapo-Passwort'}}"></label></div>
<label class="setting"><input type="checkbox" name="idle_confirm" value="yes" required><span>Drucker ist im Leerlauf. Verbindungen speichern und Anwendung neu starten.</span></label><button>Verbindungen speichern …</button></form>
<p class="muted small">Passwörter und Zugangscode werden nicht wieder angezeigt. Leere Passwortfelder behalten den gespeicherten Wert. Datenpfade, Ports und Bestandsmodus bleiben in der Serverkonfiguration.</p>
<p id="connections-status" role="status"></p></section>
<script>
async function saveConnections(event,form){event.preventDefault();const button=form.querySelector('button'),status=document.getElementById('connections-status');button.disabled=true;status.textContent='Konfiguration wird geprüft …';try{const r=await fetch(form.action,{method:'POST',body:new FormData(form)}),d=await r.json();if(!r.ok)throw Error(d.error);for(const input of form.querySelectorAll('input[type=password]'))input.value='';status.textContent=d.message;setTimeout(()=>location.reload(),10000)}catch(e){status.textContent=e.message;button.disabled=false}}
</script>
'''
