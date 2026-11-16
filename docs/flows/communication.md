# Kommunikationsflow

```mermaid
flowchart LR
    Slicer["Bambu Studio / OrcaSlicer"] -->|"Projekt und Startzuordnung"| Printer["A1 / A1 mini + AMS Lite"]
    Printer -->|"MQTT: Status und Befehlsantworten"| Bridge["Bambu-Spoolman-Plus"]
    Bridge -->|"MQTT: Status, Licht, geprüfte AMS-Angaben"| Printer
    Printer -->|"FTPS: geslicte 3MF"| Bridge
    Browser["Browser"] <-->|"HTTP: Ansichten und bestätigte Aktionen"| Bridge
    Bridge <--> DB[("SQLite: Journal, Einstellungen und internes Inventar")]
    Bridge <-->|"Nur externes Backend: REST"| Spoolman["Vorhandenes Spoolman"]
    Bridge --> Worker["Isolierter Tapo-Prozess"]
    Worker <--> Tapo["P110: Schalten und Energie"]
    Catalog["SpoolmanDB"] -->|"Optionale öffentliche Produktvorlagen"| Bridge
```

Interner und externer Bestandsweg sind alternative Betriebsarten; es gibt keinen automatischen Rückfall. AMS-Schreiben erfordert LAN-only und Entwicklermodus. [Architektur](../architecture.md) · [Zur Übersicht](../../README.md).
