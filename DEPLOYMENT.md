# Deployment und Prüfstand

Stand: 16. November 2026. Das Repository enthält den geprüften Plus-Quellstand einschließlich aller neuen Python-Module. Persönliche Infrastruktur, Gerätezugänge, Bestandszahlen, private Sicherungspfade und individuelle Imagekennungen werden nicht veröffentlicht.

## Voraussetzungen und Bereitstellung

- Docker Compose v2; persistente Daten und private `.env` beziehungsweise geschützte Secret-Dateien.
- Für AMS-Material-/Farbübertragung am kompatiblen Drucker **LAN-only und Entwicklermodus**. Modus nach Druckende am Gerät aktivieren; gültigen LAN-Code privat konfigurieren.
- Vollständiges Verzeichnis `bridge/` ins Image übernehmen. Das historische `deploy_qnap.py` nicht unverändert verwenden: Modulumfang und Annahmen über laufendes Spoolman passen nicht zum heutigen Plus-Betrieb.
- Vor jeder Migration oder größeren Änderung vollständige private Sicherungen und Testimport auf separater Kopie. Bestehenden Bestand und Betriebsmodus erhalten; interner Betrieb setzt offline geprüften Umschaltmarker voraus.
- Neue Installation startet mit Spoolman und Simulation. Internes Inventar nicht durch bloßes Setzen einer Variable oder eine leere Datenbank ersetzen.

Nach Deployment `/health`, tatsächlichen Backend-/Simulationsmodus, Inventarseite, Journal, Rollenzuordnung, Kamera/Licht und Steckdosenstatus prüfen. Geräte-Schreibtests bewusst am ruhenden Gerät ausführen; keine automatischen Testbefehle gegen Produktionsgeräte.

## Automatisierte Prüfung

**158 Tests lokal und im NAS-Container bestanden.** Abgedeckt sind unter anderem Migration/Import, JSON-Roundtrip, Referenzen, Nebenläufigkeit, Abbruch, Wiederaufnahme, idempotente Buchung, Korrekturen, Entfernen archivierter Rollen, Gerätefehler, AMS-Vorschau und Einmalversuch, aktive/angeforderte Schachtsperre, Flusszuschlag und Abschaltregeln. Browserprüfungen bestätigen Rollenwahl, Fallback, Bestätigung/Abbruch, Einzelschachtaktionen und Desktop-/Mobilansicht.

Temporäre Testdaten auf NAS möglichst im Arbeitsspeicher (`--tmpfs /tmp:rw,size=256m`) halten. Das vermeidet langsame SQLite-Schreibtests im Container-Layer. Niemals produktive Datenverzeichnisse in einen Testlauf mounten.

## Reale Prüfungen am Referenzsystem

| Funktion | Nachweis und Grenze |
|---|---|
| Licht | Ein/Aus am Drucker bestätigt; Erfolg benötigt neue Gerätezustandsmeldung. |
| Manuelle Tapo-Steuerung | Ein/Aus bestätigt; Licht darf beim Abschalten an sein. |
| AMS-Farbübertragung | Echter Farbwechsel an Slot 3 im Leerlauf nach LAN-/Entwicklermodus bestätigt; Zustand und Bestandsbindung blieben stabil. Ursprüngliche Farbe/Bindung danach wiederhergestellt. |
| AMS-Änderung während Druck | Schachtschutz und Freigabe anderer Schächte software-/browsergeprüft; tatsächlicher Farbwechsel während Druck nach Modusumstellung noch nicht physisch bestätigt. |
| Automatische Abschaltung | Regelwerk getestet, Option standardmäßig aus; vollständiger neuer automatischer Ausschaltzyklus noch nicht hardwarebestätigt. |
| Rundung und Flusszuschlag | Laufende Gewichtsanzeige und verfügbare deaktivierte Option geprüft; Pauschale softwaregetestet, keine gemessene Kalibrierungsmenge. |

Die Nachweise gelten für das Referenzsystem und ersetzen keine Prüfung anderer Firmware-/Modellkombinationen. Health bestätigt Anwendungsbetrieb, nicht die Erreichbarkeit jedes Geräts. Screenshot-Beispieldaten sind kein Hardwaretestnachweis.

## Weiterführend

- [Bedienungsanleitung mit Voraussetzungen und Screenshots](docs/user-guide.md)
- [Backup, Testimport, Migration und Rollback](docs/plus-operations.md)
- [Architektur und Grenzen](docs/architecture.md)
- [Changelog](CHANGELOG.md)
