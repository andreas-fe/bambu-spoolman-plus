import os
import json
import ssl
import time
import io
import socket
import zipfile
import ftplib
import xml.etree.ElementTree as ET
import threading
from collections import deque
import requests
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
import paho.mqtt.client as mqtt

PRINTER_IP = os.getenv("BAMBU_HOST", "10.10.10.48")
ACCESS_CODE = os.getenv("BAMBU_ACCESS_CODE", "30485337")
SERIAL = os.getenv("BAMBU_SERIAL", "03919D550409147")
SPOOLMAN_URL = os.getenv("SPOOLMAN_URL", "http://spoolman:8000")
MAPPING_FILE = "/app/data/mapping.json"

os.makedirs("/app/data", exist_ok=True)

log_buffer = deque(maxlen=250)

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(f"[Bridge] {line}", flush=True)
    log_buffer.append(line)

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        try:
            with open(MAPPING_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"0": None, "1": None, "2": None, "3": None, "external": None}

def save_mapping(mapping):
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)

ams_state = {
    0: {"name": "Slot 1", "type": "-", "color": "#888888", "remain": -1},
    1: {"name": "Slot 2", "type": "-", "color": "#888888", "remain": -1},
    2: {"name": "Slot 3", "type": "-", "color": "#888888", "remain": -1},
    3: {"name": "Slot 4", "type": "-", "color": "#888888", "remain": -1},
    "external": {"name": "Externe Spule", "type": "Manuell / TPU", "color": "#888888", "remain": -1}
}

printer_status = {
    "state": "OFFLINE",
    "progress": 0,
    "subtask_name": "",
    "active_slot": "2",          # Aktueller Live-Slot ("0"-"3", "external")
    "active_slot_label": "Slot 3"
}

tracker = {
    "is_printing": False,
    "print_slot": None,          # Eingefrorener Druckslot (Slot der gearbeitet hat)
    "last_state": "INIT",
    "gcode_file": "",
    "slot_weights": {}
}

seq_counter = 1
client_instance = None

def get_slot_label(slot_key):
    if slot_key in ["0", 0]:
        return "Slot 1"
    elif slot_key in ["1", 1]:
        return "Slot 2"
    elif slot_key in ["2", 2]:
        return "Slot 3"
    elif slot_key in ["3", 3]:
        return "Slot 4"
    return "Externe Spule"

class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def connect(self, host='', port=990, timeout=-999):
        if host != '':
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        
        raw_sock = socket.create_connection((self.host, self.port), self.timeout)
        self.af = raw_sock.family
        self.sock = self.context.wrap_socket(raw_sock, server_hostname=self.host)
        self.file = self.sock.makefile('r', encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def makepasv(self):
        host, port = super().makepasv()
        return PRINTER_IP, port

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session
            )
        return conn, size

def extract_weights_via_ftps(filename):
    if not filename:
        return {}
    
    clean_name = os.path.basename(filename.strip().lstrip("/"))
    candidate_paths = [
        clean_name,
        f"/cache/{clean_name}",
        f"/{clean_name}",
        clean_name.replace(".3mf", ".gcode.3mf"),
        f"/cache/{clean_name.replace('.3mf', '.gcode.3mf')}"
    ]

    log(f"Lade '{clean_name}' via FTPS herunter...")
    
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        ftps = ImplicitFTP_TLS(context=ctx)
        ftps.connect(PRINTER_IP, 990, timeout=12)
        ftps.login("bblp", ACCESS_CODE)
        ftps.prot_p()

        bio = None
        for p in candidate_paths:
            try:
                b = io.BytesIO()
                ftps.retrbinary(f"RETR {p}", b.write)
                bio = b
                log(f"Gefunden unter: {p}")
                break
            except Exception:
                continue

        if not bio:
            try:
                cache_files = ftps.nlst("/cache")
                for cf in cache_files:
                    if clean_name.split(".")[0] in cf:
                        b = io.BytesIO()
                        ftps.retrbinary(f"RETR {cf}", b.write)
                        bio = b
                        log(f"Gefunden im Cache: {cf}")
                        break
            except Exception:
                pass

        ftps.quit()

        if not bio:
            log(f"Datei '{clean_name}' nicht auf FTPS gefunden.")
            return {}

        bio.seek(0)
        weights_by_tray = {}
        with zipfile.ZipFile(bio, 'r') as z:
            for item in z.namelist():
                if item.endswith("slice_info.config"):
                    xml_content = z.read(item).decode('utf-8', errors='ignore')
                    root = ET.fromstring(xml_content)
                    
                    for fil in root.findall(".//filament"):
                        raw_id = fil.get("id")
                        used_g = fil.get("used_g")
                        
                        if not used_g:
                            continue
                            
                        g = round(float(used_g), 2)
                        
                        if raw_id and raw_id.isdigit():
                            idx = int(raw_id)
                            if 1 <= idx <= 4:
                                slot_key = str(idx - 1)
                            elif idx in [254, 255]:
                                slot_key = "external"
                            else:
                                slot_key = str(idx)
                        else:
                            slot_key = str(printer_status["active_slot"])

                        weights_by_tray[slot_key] = g

                    if weights_by_tray:
                        log(f"Slicer-Verbräuche ermittelt: {weights_by_tray}")
                        return weights_by_tray

    except Exception as e:
        log(f"FTPS/Parsing-Fehler bei {clean_name}: {e}")
    return {}

def fetch_spoolman_spools():
    try:
        r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool", timeout=4)
        if r.status_code == 200:
            spools = [s for s in r.json() if not s.get("archived", False)]
            for s in spools:
                fil = s.get("filament", {})
                color = fil.get("color_hex")
                if not color or color == "None":
                    color = "888888"
                color = color.replace("#", "")
                s["clean_color"] = f"#{color[:6]}"
                s["material"] = fil.get("material", "-")
            return spools
    except Exception as e:
        log(f"Fehler Spoolman: {e}")
    return []

def deduct_spoolman_weight(spool_id, used_grams):
    try:
        url = f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}/use"
        payload = {"use_weight": round(float(used_grams), 2)}
        r = requests.put(url, json=payload, timeout=5)
        if r.status_code == 200:
            log(f"ERFOLG: {used_grams}g von Spule #{spool_id} abgebucht.")
            return True
        else:
            log(f"Fehler Spoolman ({r.status_code}): {r.text}")
    except Exception as e:
        log(f"API Fehler: {e}")
    return False

def on_connect(client, userdata, flags, rc, *args):
    code = getattr(rc, "value", rc)
    log(f"MQTT verbunden (RC: {code})")
    if code == 0:
        topic = f"device/{SERIAL}/report"
        client.subscribe(topic)
        send_pushall(client)

def send_pushall(client):
    global seq_counter
    seq_counter += 1
    try:
        cmd = json.dumps({"pushing": {"command": "pushall", "sequence_id": str(seq_counter)}})
        client.publish(f"device/{SERIAL}/request", cmd)
    except Exception as e:
        log(f"Fehler bei pushall: {e}")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode('utf-8'))
        p = data.get("print", {})
        if not p:
            return

        ams_obj = p.get("ams", {})

        # 1. Aktiven Slot präzise auswerten
        # 255 = Unloaded / Leerlauf -> DARF NICHT als external gewertet werden!
        # 254 = Externe Spule
        # 0-3 = AMS Slots 1-4
        detected_slot = None
        if isinstance(ams_obj, dict):
            for k in ["tray_now", "tray_tar", "ams_curr_tray"]:
                v = ams_obj.get(k)
                if v is not None and str(v).isdigit():
                    v_int = int(v)
                    if v_int < 4:
                        detected_slot = str(v_int)
                        break
                    elif v_int == 254:
                        detected_slot = "external"
                        break
                    # v_int == 255 ignorieren (Unloaded)

        if detected_slot is None:
            for k in ["tray_now", "ams_curr_tray", "tray_tar"]:
                if k in p and p[k] is not None and str(p[k]).isdigit():
                    v_int = int(p[k])
                    if v_int < 4:
                        detected_slot = str(v_int)
                        break
                    elif v_int == 254:
                        detected_slot = "external"
                        break

        # Wenn ein valider Schacht aktiv ist, aktualisieren
        if detected_slot is not None:
            printer_status["active_slot"] = detected_slot
            printer_status["active_slot_label"] = get_slot_label(detected_slot)
            # Solange gedruckt wird, tracken wir den arbeitenden Slot
            if tracker["is_printing"]:
                tracker["print_slot"] = detected_slot

        # 2. AMS Trays
        ams_list = ams_obj.get("ams", []) if isinstance(ams_obj, dict) else []
        if ams_list:
            for unit in ams_list:
                for tray in unit.get("tray", []):
                    tid = int(tray.get("id", 0))
                    if tid in ams_state:
                        ams_state[tid]["type"] = tray.get("tray_sub_brands") or tray.get("tray_type") or "Generic"
                        color_hex = tray.get("tray_color", "FFFFFFFF")[:6]
                        ams_state[tid]["color"] = f"#{color_hex}"
                        ams_state[tid]["remain"] = tray.get("remain", -1)

        # 3. Externe Spule
        vt_tray = p.get("vt_tray", {})
        if vt_tray:
            ams_state["external"]["type"] = vt_tray.get("tray_sub_brands") or vt_tray.get("tray_type") or "Extern"
            color_hex = vt_tray.get("tray_color", "FFFFFFFF")[:6]
            ams_state["external"]["color"] = f"#{color_hex}"

        gcode_state = p.get("gcode_state")
        percent = p.get("mc_percent", 0)
        subtask = p.get("subtask_name", "")
        gcode_file = p.get("gcode_file", "")

        if gcode_file:
            tracker["gcode_file"] = gcode_file

        if gcode_state:
            printer_status["state"] = gcode_state
            printer_status["progress"] = percent
            if subtask:
                printer_status["subtask_name"] = subtask

            if gcode_state != tracker["last_state"]:
                log(f"Status: {tracker['last_state']} -> {gcode_state} ({percent}%) [Aktiv: {printer_status['active_slot_label']}]")
                tracker["last_state"] = gcode_state

            # Druck Start
            if gcode_state == "RUNNING" and not tracker["is_printing"]:
                tracker["is_printing"] = True
                tracker["print_slot"] = printer_status["active_slot"]
                log(f"START erkannt. Datei: '{tracker['gcode_file']}', Aktiver Schacht: {printer_status['active_slot_label']}")
                
                def async_weight_fetch():
                    time.sleep(3)
                    w_dict = extract_weights_via_ftps(tracker["gcode_file"])
                    if w_dict:
                        tracker["slot_weights"] = w_dict
                threading.Thread(target=async_weight_fetch, daemon=True).start()

            # Druck Ende
            elif gcode_state == "FINISH" and tracker["is_printing"]:
                tracker["is_printing"] = False
                
                # Der Slot, der tatsächlich gedruckt hat (gesichert gegen Unload-Reset)
                final_slot = tracker.get("print_slot") or printer_status["active_slot"]
                final_label = get_slot_label(final_slot)
                
                log(f"FINISH erkannt. Zugewiesener Druck-Slot: {final_label} (ID: {final_slot})")
                
                weights = tracker.get("slot_weights", {})
                if not weights:
                    weights = extract_weights_via_ftps(tracker["gcode_file"])

                mapping = load_mapping()

                if weights:
                    if len(weights) == 1:
                        single_weight = list(weights.values())[0]
                        spool_id = mapping.get(str(final_slot))
                        if spool_id:
                            log(f"Buche Verbrauch ({single_weight}g) auf {final_label} (Spule #{spool_id}) ab.")
                            deduct_spoolman_weight(spool_id, single_weight)
                        else:
                            log(f"{final_label} hat keine Spulenzuweisung in Spoolman (ID {final_slot}).")
                    else:
                        for slot_key, w_val in weights.items():
                            s_label = get_slot_label(slot_key)
                            spool_id = mapping.get(str(slot_key))
                            if spool_id and w_val > 0:
                                log(f"Buche {w_val}g für {s_label} von Spule #{spool_id} ab.")
                                deduct_spoolman_weight(spool_id, w_val)
                            else:
                                log(f"{s_label} ({w_val}g) ist keiner Spule zugeordnet.")
                    tracker["slot_weights"] = {}
                else:
                    spool_id = mapping.get(str(final_slot))
                    log(f"Keine Slicer-Gewichte verfügbar für {final_label} (Spule #{spool_id}).")

    except Exception as e:
        log(f"Fehler in on_message: {e}")

def mqtt_background_loop():
    global client_instance
    log(f"Starte MQTT-Thread für {PRINTER_IP}:8883...")
    while True:
        try:
            try:
                client_instance = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="bambu_spoolman_bridge")
            except Exception:
                client_instance = mqtt.Client(client_id="bambu_spoolman_bridge")

            client_instance.username_pw_set("bblp", ACCESS_CODE)
            client_instance.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            client_instance.tls_insecure_set(True)
            client_instance.on_connect = on_connect
            client_instance.on_message = on_message

            client_instance.connect(PRINTER_IP, 8883, 120)
            client_instance.loop_start()
            
            while True:
                time.sleep(45)
                if not client_instance.is_connected():
                    log("MQTT Verbindung verloren...")
                    break
                send_pushall(client_instance)
                log(f"Heartbeat gesendet. Status: {printer_status['state']} (Aktiv: {printer_status['active_slot_label']})")
                
        except Exception as e:
            log(f"MQTT Schleifenfehler: {e}")
            printer_status["state"] = "OFFLINE"
            
        try:
            if client_instance:
                client_instance.loop_stop()
                client_instance.disconnect()
        except:
            pass
            
        time.sleep(15)

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Bambu - Spoolman Bridge</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1a1a1a; color: #eee; padding: 25px; }
    .card { background: #2a2a2a; border-radius: 8px; padding: 20px; max-width: 860px; margin: 0 auto; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
    h1 { margin-top: 0; color: #4ade80; font-size: 22px; display: inline-block; }
    .header-actions { float: right; margin-top: 5px; }
    .btn-secondary { background: #444; border: 1px solid #666; color: #eee; padding: 6px 12px; border-radius: 4px; cursor: pointer; text-decoration: none; font-size: 13px; }
    .btn-secondary:hover { background: #555; }
    .status-bar { display: flex; justify-content: space-between; align-items: center; background: #333; padding: 10px 15px; border-radius: 6px; margin: 15px 0 20px 0; }
    .slot-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid #444; }
    .slot-row.external { background: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; }
    .slot-badge { width: 18px; height: 18px; border-radius: 50%; display: inline-block; vertical-align: middle; margin-right: 8px; border: 1px solid #fff; }
    select { background: #3a3a3a; color: #fff; padding: 7px; border-radius: 4px; border: 1px solid #555; width: 460px; font-family: monospace; font-size: 13px; }
    button.submit-btn { background: #22c55e; border: none; color: #fff; padding: 10px 20px; font-weight: bold; border-radius: 4px; cursor: pointer; margin-top: 15px; float: right; }
    button.submit-btn:hover { background: #16a34a; }
    .manual-box { background: #222; border: 1px solid #444; padding: 12px 15px; border-radius: 6px; margin-top: 25px; display: flex; align-items: center; justify-content: space-between; }
    .manual-box input { background: #333; border: 1px solid #555; color: #fff; padding: 6px 10px; border-radius: 4px; width: 80px; }
    .log-box { background: #111; color: #10b981; font-family: monospace; font-size: 12px; padding: 12px; border-radius: 6px; height: 260px; overflow-y: auto; margin-top: 20px; border: 1px solid #333; }
    .log-title { font-size: 13px; font-weight: bold; color: #aaa; margin-top: 25px; margin-bottom: 6px; }
  </style>
</head>
<body>
<div class="card">
  <div>
    <h1>Bambu Lab &rarr; Spoolman Bridge</h1>
    <div class="header-actions">
      <button class="btn-secondary" onclick="fetchLiveUpdates()">&#x21bb; Aktualisieren</button>
      <a href="{{ spoolman_url }}" target="_blank" class="btn-secondary" style="margin-left: 6px;">Spoolman &nearr;</a>
    </div>
  </div>

  <div class="status-bar">
    <div>
      <strong>Status:</strong> 
      <span id="st-state" style="color: {% if printer_status['state'] in ['RUNNING', 'ONLINE', 'FINISH', 'IDLE'] %}#4ade80{% else %}#f87171{% endif %}; font-weight: bold;">
        {{ printer_status['state'] }}
      </span> 
      (<span id="st-progress">{{ printer_status['progress'] }}</span>%) 
      <span id="st-subtask" style="color: #aaa; margin-left: 10px;">{{ printer_status['subtask_name'] }}</span>
    </div>
    <div style="font-size: 13px; color: #bbb;">
      Aktiv: <strong id="st-active" style="color: #60a5fa;">{{ printer_status['active_slot_label'] }}</strong>
    </div>
  </div>

  <form method="POST" action="/save">
    {% for slot_id in range(4) %}
      <div class="slot-row">
        <div>
          <span class="slot-badge" style="background-color: {{ ams[slot_id]['color'] }};"></span>
          <strong>AMS Slot {{ slot_id + 1 }}</strong> 
          <small style="color: #aaa;">({{ ams[slot_id]['type'] }}, ~{{ ams[slot_id]['remain'] }}%)</small>
        </div>
        <div>
          <select name="slot_{{ slot_id }}">
            <option value="">-- Nicht zugewiesen --</option>
            {% for s in spools %}
              <option value="{{ s.id }}" {% if mapping.get(slot_id|string) == s.id|string %}selected{% endif %}>
                #{{ s.id }} | {{ s.filament.name }} ({{ s.material }}) [{{ s.clean_color }}] ({{ (s.remaining_weight)|round|int if s.remaining_weight is not none else '?' }}g)
              </option>
            {% endfor %}
          </select>
        </div>
      </div>
    {% endfor %}

    <div class="slot-row external">
      <div>
        <span class="slot-badge" style="background-color: {{ ams['external']['color'] }};"></span>
        <strong>Externe Spule (z. B. TPU)</strong> 
        <small style="color: #aaa;">(Spulenhalter / vt_tray)</small>
      </div>
      <div>
        <select name="slot_external">
          <option value="">-- Nicht zugewiesen --</option>
          {% for s in spools %}
            <option value="{{ s.id }}" {% if mapping.get('external') == s.id|string %}selected{% endif %}>
              #{{ s.id }} | {{ s.filament.name }} ({{ s.material }}) [{{ s.clean_color }}] ({{ (s.remaining_weight)|round|int if s.remaining_weight is not none else '?' }}g)
            </option>
          {% endfor %}
        </select>
      </div>
    </div>

    <button type="submit" class="submit-btn">Zuordnung speichern</button>
  </form>

  <div style="clear: both;"></div>

  <form method="POST" action="/manual_deduct" class="manual-box">
    <div style="font-size: 13px;">
      <strong>Manuell von aktiver Spule ({{ printer_status['active_slot_label'] }}) abbuchen:</strong>
    </div>
    <div>
      <input type="number" step="0.1" name="grams" placeholder="Gramm" required> g
      <button type="submit" class="btn-secondary" style="background: #2563eb; border: none; margin-left: 8px;">Jetzt abziehen</button>
    </div>
  </form>

  <div class="log-title">Live Container-Logs:</div>
  <div class="log-box" id="log-box">
    {% for l in logs %}
      <div>{{ l }}</div>
    {% endfor %}
  </div>
</div>

<script>
  let userIsScrolling = false;
  const logBox = document.getElementById('log-box');

  logBox.addEventListener('scroll', () => {
    userIsScrolling = (logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight) > 40;
  });

  function fetchLiveUpdates() {
    fetch('/api/status')
      .then(response => response.json())
      .then(data => {
        document.getElementById('st-state').innerText = data.printer.state;
        document.getElementById('st-progress').innerText = data.printer.progress;
        document.getElementById('st-subtask').innerText = data.printer.subtask_name;
        document.getElementById('st-active').innerText = data.printer.active_slot_label;

        logBox.innerHTML = data.logs.map(l => '<div>' + l + '</div>').join('');
        if (!userIsScrolling) {
          logBox.scrollTop = logBox.scrollHeight;
        }
      })
      .catch(err => console.error("Update-Fehler:", err));
  }

  setInterval(fetchLiveUpdates, 5000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    mapping = load_mapping()
    spools = fetch_spoolman_spools()
    return render_template_string(
        HTML_TEMPLATE,
        printer_status=printer_status,
        spoolman_url=f"http://{request.host.split(':')[0]}:7912",
        ams=ams_state,
        spools=spools,
        mapping=mapping,
        logs=list(log_buffer)
    )

@app.route("/api/status")
def status_api():
    return jsonify({
        "printer": printer_status,
        "logs": list(log_buffer)
    })

@app.route("/save", methods=["POST"])
def save():
    mapping = {
        "0": request.form.get("slot_0") or None,
        "1": request.form.get("slot_1") or None,
        "2": request.form.get("slot_2") or None,
        "3": request.form.get("slot_3") or None,
        "external": request.form.get("slot_external") or None,
    }
    save_mapping(mapping)
    log("Zuordnungen gespeichert.")
    return redirect(url_for("index"))

@app.route("/manual_deduct", methods=["POST"])
def manual_deduct():
    grams = request.form.get("grams")
    if grams:
        mapping = load_mapping()
        active_slot = str(printer_status["active_slot"])
        spool_id = mapping.get(active_slot)
        if spool_id:
            deduct_spoolman_weight(spool_id, float(grams))
        else:
            log(f"Keine Spule für {printer_status['active_slot_label']} zugeordnet.")
    return redirect(url_for("index"))

if __name__ == "__main__":
    t = threading.Thread(target=mqtt_background_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=7913)
