# Änderungen

## v2.4.0 — 2026-11-07

- Gleichartige AMS-Spulen werden nach beobachteter Nutzung und kleinstem positivem Restgewicht zugeordnet; rechnerischer Verbrauch kann auf mehrere Spulen verteilt werden.
- Passende Reservespulen aus Spoolman werden bei Bedarf einbezogen. Übersteigt der Verbrauch auch deren Bestand, wird die Differenz der letzten passenden Spule belastet und gekennzeichnet.
- Abgebrochene Drucke werden anhand des letzten bekannten Fortschritts geschätzt und automatisch abgerechnet. Schätzungen und ihre Berechnungsbasis sind im Journal sichtbar.
- Hintergrund-Worker sichert Slicer-Daten und Bestandsgewichte bereits zum Druckstart, ohne während des Druckens zu buchen.
- Buchungspläne bleiben nach dem ersten API-Versuch unverändert; erfolgreiche Teilbuchungen und historische Dry-Runs werden nicht wiederholt.
- Regressionstests für 30/70-Aufteilung, Reservespulen, Bestandsmangel, Abbruch, Fortschrittsreset und Wiederholungen ergänzt.

## v2.3.0 — 2026-11-07

- Automatische Slicer→AMS-Zuordnung bei exakt gleicher RGB-Farbe und Materialtyp ergänzt, wenn kein ausdrücklich bestätigtes oder vom Drucker geliefertes Mapping vorliegt.
- AMS-Bestand zum Druckstart und Filament-Metadaten pro Auftrag gespeichert; partielle MQTT-Meldungen überschreiben bekannte Attribute nicht mehr.
- Mehrdeutige oder unvollständige Aufträge warten vollständig auf Bestätigung, ohne vorab Teilpositionen zu buchen.
- Herkunft der Zuordnung im Journal dokumentiert; externe Spulen bleiben explizit zuzuordnen.
- Sitzungsschlüssel dauerhaft in SQLite gespeichert, damit Formulare Container-Updates überstehen. Ungültige Formulare zeigen eine verständliche Fehlerseite und buchen nichts.
- Aktualisieren lädt auch die auftragsbezogenen Zuordnungsformulare neu.
- Regressionstests für automatische Zuordnung, Mehrdeutigkeit, Inventar-Snapshots und Formularsitzungen ergänzt.

## v2.2.1 — 2026-11-07

- Auftragsbezogene Slicer→AMS-Zuordnung in der Oberfläche ergänzt, falls die Firmware kein Mapping liefert.
- Bereits simulierte, gebuchte oder gerade verarbeitete Positionen können nicht umgeleitet werden.
- Bestätigte Zuordnungen werden nicht auf nachfolgende Drucke übertragen.
- Weitere Regressionstests für die Zuordnungsformulare und parallele Verarbeitung ergänzt.

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
