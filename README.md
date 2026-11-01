# Bambu Lab to Spoolman Bridge

Eine leichtgewichtige Schnittstelle zur automatischen Erfassung des Filamentverbrauchs von **Bambu Lab 3D-Druckern** (A1-Serie, P1-Serie, X1-Serie) in **Spoolman**.

## Features

- **Echtzeit-Status via MQTT (Port 8883)**: Überwacht Druckzustände (\`RUNNING\`, \`FINISH\`) und den aktiven Schacht (\`AMS Slot 1–4\` bzw. \`Externe Spule\`).
- **Präzise Verbrauchsanalyse via FTPS (Port 990)**: Lädt beim Druckstart die Modelldatei (\`.3mf\` / \`.gcode.3mf\`) und extrahiert die grammgenauen Slicer-Metadaten (\`used_g\`) aus \`Metadata/slice_info.config\`.
- **Automatische Verbuchung**: Zieht das verbrauchte Material bei Druckabschluss automatisch von der zugewiesenen Spule in Spoolman ab.
- **Unload-Schutz**: Verhindert Fehlbuchungen durch den automatischen Filamentrückzug (*Unload* / Code \`255\`) nach Druckende.
- **Weboberfläche (Port 7913)**: Übersicht über Druckerstatus, Schachtzuordnungen zu Spoolman-Spulen und manuelle Korrekturbuchungen.

---

## Schnellstart

### 1. Repository klonen & Konfiguration anlegen
\`\`\`bash
git clone https://github.com/<DEIN-NUTZERNAME>/bambu-spoolman.git
cd bambu-spoolman
cp .env.example .env
\`\`\`

Passe die Werte in \`.env\` an:
- \`BAMBU_HOST\`: Lokale IP-Adresse des Druckers
- \`BAMBU_ACCESS_CODE\`: 8-stelliger LAN-Zugangscode (Display -> Netzwerk)
- \`BAMBU_SERIAL\`: Seriennummer des Druckers (Display -> Geräteinfo)
- \`SPOOLMAN_URL\`: \`http://spoolman:8000\`

### 2. Starten
\`\`\`bash
docker compose up -d --build
\`\`\`

- Webinterface: \`http://<HOST-IP>:7913\`
- Spoolman: \`http://<HOST-IP>:7912\`

---

## Lizenz
MIT License
