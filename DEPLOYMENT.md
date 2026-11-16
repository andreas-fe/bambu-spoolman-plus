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

**192 Tests lokal und im NAS-Container bestanden.** Abgedeckt sind unter anderem Migration/Import, JSON-Roundtrip, Referenzen, Nebenläufigkeit, Abbruch, Wiederaufnahme, idempotente Buchung, Korrekturen, Entfernen archivierter Rollen, Gerätefehler, AMS-Vorschau und Einmalversuch, aktive/angeforderte Schachtsperre, Flusszuschlag und Abschaltregeln. Browserprüfungen bestätigen Rollenwahl, Fallback, Bestätigung/Abbruch, Einzelschachtaktionen und Desktop-/Mobilansicht.

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

### Prüfung der dynamischen Drucksteuerung

Die Profilliste wurde auf dem realen A1 gelesen. Während eines laufenden Drucks wurde auf einem eindeutig inaktiven Schacht dessen bereits bestehendes K-Profil erneut ausgewählt. Neue Befehlsbestätigung und passende Schachtwerte wurden bestätigt; Material, Farbe, K-Wert, Bestände und aktuelle Bindungen blieben unverändert. Das prüft den Schreib-/Bestätigungspfad, nicht den Wechsel auf ein anderes Profil während des Druckens. Neue Kalibrierungen und Bewegungen wurden nicht ausgelöst. Die Auswahl eines anderen Profils und das Lösen auf Standard sind softwaregeprüft; deren tatsächlicher Wechsel bleibt separat am ruhenden Drucker zu prüfen.

## Weiterführend

- [Bedienungsanleitung mit Voraussetzungen und Screenshots](docs/user-guide.md)
- [Backup, Testimport, Migration und Rollback](docs/plus-operations.md)
- [Architektur und Grenzen](docs/architecture.md)
- [Changelog](CHANGELOG.md)

## Druckerspeicher

FTPS-Dateiliste, Pfadgrenzen, Auswahlprüfung, Bestätigung und Löschsperren sind automatisiert geprüft. Vorschau nutzt gespeicherte Bilder aus begrenzten 3MF-Archiven. Die Löschbefehle sind mit einem simulierten Server getestet; produktive Dateien wurden für die Prüfung nicht gelöscht. Eine echte Löschprüfung ist separat mit einer ausdrücklich ausgewählten entbehrlichen Datei vorzunehmen.

Am Referenz-A1 wurden Hauptverzeichnis und Druckcache erfolgreich gelesen; ein kleines eingebettetes PNG-Vorschaubild aus einer vorhandenen 3MF wurde verifiziert. `/model` ist dort nicht verfügbar. Es wurden keine echten Dateien gelöscht oder hochgeladen.

Der entbehrliche Vorschaubildcache ist eine separate private SQLite-Datei im persistenten Datenverzeichnis. Er ist nicht Bestandteil des Inventar-JSON-Exports. Cachetests prüfen Persistenz, Druckertrennung, Größen-/Anzahlgrenzen, Verdrängung, Ablauf, veränderte Dateiangaben und Wiederverwendung ohne erneuten Transfer.

Am Referenz-A1 wurden Vorschau, unmittelbar folgender Cachetreffer und die Inline-Bildroute mit identischen Bilddaten geprüft. Der zweite Vorschauaufruf bestätigte `X-Preview-Cache: hit`. Dabei wurden keine Druckerdateien verändert oder gelöscht.

Die Vorschau liest ZIP-Einträge sequenziell und beendet den FTPS-Transfer nach dem eingebetteten Bild. ZIP-Datenbeschreibungen (bei zunächst unbekannten Größen), Prüfsummen und ZIP64 werden geprüft. Grenzen: 8 MiB übertragener Archivanfang, 2 MiB Bild, 64 MiB insgesamt entpackte Daten und 30 Sekunden Lesezeit; keine vollständigen Massendownloads. Größere 3MF-Dateien bleiben möglich, wenn das Bild früh genug liegt. Der Referenz-A1 unterstützt keine REST-Teilabrufe; der sequenzielle Weg benötigt sie nicht.

Drei zuvor wegen Zeitüberschreitung fehlgeschlagene reale 3MF-Vorschauen wurden vor dem Deployment mit dem neuen Leser erfolgreich geprüft. Nur 16–45 KB wurden dafür gelesen, statt der vollständigen Archive mit etwa 6,8–21,5 MB. Dateinamen und Modellbilder bleiben privat. Der Designwechsel wurde in allen sechs Bereichen im Browser geprüft, einschließlich Speicherung, Rückwechsel und Mobilansicht. Ein produktiver Druckstart aus der Dateiliste ist noch nicht implementiert.

Nach Aktivierung des NAS-Containers wurden alle drei betroffenen Vorschauen über die produktiven App-Routen erfolgreich geladen. Zweiter Aufruf und Inline-Bild lieferten jeweils identische Cachebilder. Alle sechs Seiten antworteten erfolgreich; Health war gesund. Filamentprodukte, Rollenbestände, AMS-Bindungen und Journal wurden gegen den vorher gesicherten Export verglichen und blieben unverändert. Die vollständige Sicherung wurde per SHA-256 geprüft und der Inventarexport vor Aktivierung in eine getrennte leere Datenbank verlustfrei zurückimportiert.
