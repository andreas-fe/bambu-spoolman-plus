# Bambu Lab to Spoolman Bridge

Eine leichtgewichtige, containerisierte Schnittstelle zur vollautomatischen und grammgenauen Verbuchung von Filamentverbräuchen von **Bambu Lab 3D-Druckern** (A1-Serie, P1-Serie, X1-Serie) in **Spoolman**.

![Webinterface](screenshot.png)

---

## Highlights & Gelöste Herausforderungen

Viele bestehende Integrationen schätzen Verbräuche lediglich grob oder scheitern an den Eigenheiten der Bambu-Firmware. Dieses Projekt löst folgende Probleme:

1. **Grammgenaue Slicer-Verbräuche via Implizites FTPS (Port 990)**:
   - Statt ungenauer Berechnungen lädt die Bridge beim Druckstart die Modelldatei (\`.3mf\` bzw. \`.gcode.3mf\`) im Hintergrund direkt vom internen Speicher bzw. aus dem Druckercache (\`/cache/\`) herunter.
   - Die Slicer-Konfiguration (\`Metadata/slice_info.config\`) wird im Speicher entpackt und geparst, um den exakten Materialverbrauch (\`used_g\`) pro Filament-ID zu ermitteln.

2. **Schacht-Zuverlässigkeit & Unload-Schutz (State-Locking)**:
   - **Das Problem**: Sobald ein Druck 100 % erreicht, führt der Drucker sofort einen Filamentrückzug (*Retract / Unload*) durch und meldet über MQTT Status \`255\` (Schacht leer/entladen). Standard-Skripte interpretieren dies oft fälschlicherweise als externe Spule oder brechen die Zuordnung ab.
   - **Die Lösung**: Die Bridge lockt den während des Druckens (\`RUNNING\`) tatsächlich fördernden Schacht. Bei Auftreten des Status \`FINISH\` wird das ermittelte Gewicht exakt auf den zuvor aktiven Slot gebucht – unabhängig davon, ob der Extruder zum Zeitpunkt der Fertigmeldung bereits entladen ist.

3. **Robuste Delta-Update-Auswertung via MQTT (Port 8883)**:
   - Bambu-Drucker senden während des Drucks überwiegend partielle MQTT-Delta-Reports, in denen Schacht-Informationen oft fehlen. Die Bridge behält den Schacht-Zustand zuverlässig bei und liest die verschachtelten Status-Bits (\`ams.tray_now\`, \`ams.tray_tar\`) mit automatischer 0-Index-Korrektur aus.

4. **Web-Dashboard & Fallback-Handling**:
   - Visuelle Zuordnung von AMS-Slots 1–4 und externer Spule zu Spoolman-Spulen.
   - Anzeige von Druckerstatus, Druckfortschritt und Live-Logs direkt im Browser.
   - Manuelle Abbuchungsmöglichkeit mit Schnellkorrektur direkt vom aktiven Schacht.

---

## Architektur

\`\`\`text
┌─────────────────────────┐
│   Bambu Lab 3D Drucker  │
│  - MQTT Port 8883 (TLS) │ ──( Telemetrie / tray_now )──┐
│  - FTPS Port 990  (TLS) │ ──( slice_info.config )───┐  │
└─────────────────────────┘                           │  │
                                                      ▼  ▼
                                          ┌────────────────────────┐
                                          │  Bambu-Spoolman Bridge │
                                          │  - Web-UI (Port 7913)  │
                                          └────────────────────────┘
                                                      │
                                           ( PUT /api/v1/spool/use )
                                                      ▼
                                          ┌────────────────────────┐
                                          │        Spoolman        │
                                          │      (Port 7912)       │
                                          └────────────────────────┘
\`\`\`

---

## Schnellstart

### 1. Repository klonen & Konfiguration anlegen
\`\`\`bash
git clone https://github.com/andreas-fe/bambu-spoolman.git
cd bambu-spoolman
cp .env.example .env
\`\`\`

Passe die Werte in \`.env\` an:
\`\`\`env
BAMBU_HOST=192.168.1.50
BAMBU_ACCESS_CODE=12345678
BAMBU_SERIAL=0123456789ABCDE
SPOOLMAN_URL=http://spoolman:8000
\`\`\`

| Variable | Beschreibung |
|---|---|
  | \`BAMBU_HOST\` | Lokale IP-Adresse deines Druckers |
| \`BAMBU_ACCESS_CODE\` | 8-stelliger LAN-Zugangscode (*Display -> Netzwerk*) |
| \`BAMBU_SERIAL\` | Seriennummer des Druckers (*Display -> Geräteinfo*) |
| \`SPOOLMAN_URL\` | Interne Docker-URL zu Spoolman (\`http://spoolman:8000\`) |

### 2. Container starten
\`\`\`bash
docker compose up -d --build
\`\`\`

- **Webinterface (Bridge)**: \`http://<HOST-IP>:7913\`
- **Spoolman**: \`http://<HOST-IP>:7912\`

---

## Lizenz
MIT License
