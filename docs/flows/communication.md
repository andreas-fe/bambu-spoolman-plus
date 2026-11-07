# Kommunikationsflow

Verbindungen zwischen Drucker, Bridge, Spoolman, Datenbank und Browser.

Dieses Dokument enthält nur diesen Flow. Über die Diagrammansicht auf GitHub lässt er sich unabhängig vergrößern und zoomen.

```mermaid
flowchart LR
    Printer["Bambu A1 / A1 mini<br/>AMS Lite"]
    Slicer["Bambu Studio / OrcaSlicer<br/>G-Code in 3MF + Slicer-Metadaten"]
    Bridge["Bridge im Docker-Container<br/>Tracker + Worker + Weboberfläche"]
    Ledger[("Persistentes SQLite-Journal<br/>Aufträge + Buchungspositionen")]
    Spoolman["Bestehender Spoolman<br/>Spulenbestand + REST API"]
    Browser["Browser<br/>Zuordnung + Journal + manuelle Buchung"]
    Slicer -->|"Druckdatei und Startauswahl"| Printer
    Printer -->|"MQTT TLS :8883<br/>Status, Fortschritt, AMS-Slots"| Bridge
    Bridge -->|"MQTT: Statusanforderung pushall"| Printer
    Printer -->|"FTPS :990 + passive Ports<br/>geslicte 3MF-Datei"| Bridge
    Bridge <-->|"GET Spulen / PUT Verbrauch<br/>HTTP REST API"| Spoolman
    Bridge <--> Ledger
    Browser <-->|"HTTP :7913"| Bridge
```

[Zurück zur Übersicht](../../README.md#kommunikation-und-verarbeitung)
