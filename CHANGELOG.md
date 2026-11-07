# Änderungen

## v2.2.0 — 2026-11-07

- Aktuellen v2.1-Stand mit SQLite-Journal und Mehrmaterialunterstützung in das Repository übernommen.
- Separate bestehende Spoolman-Instanz über konfigurierbare URL angebunden; Compose startet keinen zweiten Spoolman.
- MQTT-Abhängigkeit auf Callback-API v2 abgestimmt, Abhängigkeiten festgelegt.
- Persistierte JSON-Filament-IDs normalisiert; Druckplatte eindeutig ausgewählt und Download-/XML-Größen begrenzt.
- Fehlgeschlagene Positionen werden nicht mehr pauschal als erledigt markiert. Sichere Wiederaufnahme, atomare Buchungsfreigabe und Schutz unklarer API-Anfragen vor automatischer Wiederholung.
- Historische Dry-Runs werden beim Moduswechsel nicht nachgebucht.
- MQTT-Deltas erhalten Slot und Fortschritt; AMS-Metadaten werden auch ohne Zustandsmeldung gespeichert.
- Spulenzuordnungen pro Auftrag gespeichert; unbekannte Filamentzuordnung blockiert die automatische Abbuchung.
- Web-Anmeldung und Formularprüfung aktiviert, ungültige manuelle Grammwerte abgefangen.
- Container-Healthcheck, wiederholbares SSH-Deployment mit Datensicherung, Pfadmigration und Rückfall auf den vorherigen Stack ergänzt.
- Hardcodierte Drucker-Zugangsdaten aus dem aktuellen Code entfernt. Historische Git-Commits wurden nicht umgeschrieben.
- Regressionstests für Buchungen, Mehrmaterial, Neustart, Dry-Run, API-Ausfälle und Webformulare ergänzt.
