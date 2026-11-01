# Bambu Lab to Spoolman Bridge

Eine leichtgewichtige, containerisierte Schnittstelle zur automatischen Erfassung des Filamentverbrauchs von **Bambu Lab 3D-Druckern** (A1-Serie, P1-Serie, X1-Serie) in **Spoolman**.

---

## Highlights & Gelöste Herausforderungen

1. **Grammgenaue Slicer-Verbräuche via Implizites FTPS (Port 990)**:
   - Lädt beim Druckstart die Modelldatei (\`.3mf\` bzw. \`.gcode.3mf\`) rein im Arbeitsspeicher (\`io.BytesIO\`) herunter – kein Datenmüll auf der Festplatte.
   - Parst \`Metadata/slice_info.config\` für exakte \`used_g\`-Werte pro Schacht.

2. **Schacht-Zuverlässigkeit & Unload-Schutz (State-Locking)**:
   - Verhindert Fehlbuchungen durch den automatischen Filamentrückzug (*Unload* / Code \`255\`) bei Druckende (100 %).
   - Die Bridge lockt den während des Druckens aktiven Schacht und bucht auch nach dem Entladen punktgenau ab.

3. **Robuste Delta-Update-Auswertung via MQTT (Port 8883)**:
   - Liest verschachtelte Status-Bits (\`ams.tray_now\`, \`ams.tray_tar\`) mit automatischer 0-Index-Korrektur aus.

4. **Ressourcenschonend & Wartungsfrei**:
   - Rolling Ring-Buffer für Web-Logs (\`maxlen=250\`) – kein Speicherüberlauf.
   - Vollständig lokal ohne Cloud-Zwang.

---

## Architektur

```mermaid
flowchart TD
    subgraph Printer [Bambu Lab 3D Drucker]
        direction TB
        MQTT[MQTT Broker :8883<br>Telemetrie & Schacht-Status]
        FTPS[FTPS Server :990<br>.3mf / slice_info.config]
    end

    subgraph Bridge [Bambu-Spoolman Bridge :7913]
        direction TB
        Engine[State Tracker & Slicer Parser]
        UI[Web-UI & Manuelle Buchung]
    end

    subgraph Storage [Spoolman :7912]
        API[REST API<br>/api/v1/spool/{id}/use]
    end

    MQTT -->|tray_now, gcode_state| Engine
    FTPS -->|In-Memory Download| Engine
    Engine -->|Automatische Abbuchung| API
    UI -->|Manuelle Korrektur| API
```

---

## Schnellstart

### 1. Repository klonen & Konfiguration anlegen
```bash
git clone [https://github.com/andreas-fe/bambu-spoolman.git](https://github.com/andreas-fe/bambu-spoolman.git)
cd bambu-spoolman
cp .env.example .env
```

Passe die Werte in `.env` an:
```env
BAMBU_HOST=192.168.1.50
BAMBU_ACCESS_CODE=12345678
BAMBU_SERIAL=0123456789ABCDE
SPOOLMAN_URL=http://spoolman:8000
```

### 2. Starten
```bash
docker compose up -d --build
```

- **Webinterface (Bridge)**: `http://<HOST-IP>:7913`
- **Spoolman**: `http://<HOST-IP>:7912`

---

## Lizenz
MIT License
