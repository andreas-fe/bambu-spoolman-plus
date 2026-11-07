# Verarbeitungsflow

Verarbeitung vom Druckstart bis zur bestätigten Buchung oder Fehlerbehandlung.

Dieses Dokument enthält nur diesen Flow. Über die Diagrammansicht auf GitHub lässt er sich unabhängig vergrößern und zoomen.

```mermaid
flowchart TD
    Start["MQTT: Druckstart erkannt"] --> Snapshot["Slot-Zuordnung und AMS-Bestand speichern<br/>Worker lädt Slicer-Daten und Spoolman-Restgewichte"]
    Snapshot --> Track["Fortschritt und verwendete Slots verfolgen<br/>während des Druckens nichts abbuchen"]
    Track --> End{"Druckende"}
    End -->|"FINISH"| Full["Slicer-Grammwerte der gedruckten Platte"]
    End -->|"Abbruch / Fehler / unerwartet IDLE"| Estimate["Slicer-Grammwerte × letzter Fortschritt<br/>als Schätzung kennzeichnen"]
    Full --> Map{"Zuordnung vollständig?"}
    Estimate --> Map
    Map -->|"Nein"| Wait["Auftrag offenhalten<br/>Metadaten erneut laden oder Zuordnung bestätigen"]
    Wait --> Map
    Map -->|"Ja"| Allocate["Beobachtete Slots bevorzugen<br/>gleiche Spulen nach kleinstem positivem Restgewicht<br/>bei Bedarf passende Spoolman-Reserven einbeziehen"]
    Allocate --> Plan["Buchungsplan pro Spule speichern<br/>Aufteilungen und Bestandsdefizite kennzeichnen"]
    Plan --> Mode{"Dry-Run?"}
    Mode -->|"Ja"| Sim["Nur simulieren<br/>historisch nicht nachbuchen"]
    Mode -->|"Nein"| Book["Spoolman: PUT Verbrauch in Gramm"]
    Book --> Result{"Ergebnis"}
    Result -->|"Bestätigt"| Done["Buchung durch Spoolman bestätigt"]
    Result -->|"Sicher noch nicht gebucht"| Retry["Plan unverändert später erneut versuchen"]
    Retry --> Book
    Result -->|"Antwort unklar"| Unknown["UNKNOWN: keine automatische Wiederholung<br/>Bestand prüfen"]
    Result -->|"Abgelehnt"| Rejected["REJECTED: Fehler im Journal anzeigen"]
```

[Zurück zur Übersicht](../../README.md#kommunikation-und-verarbeitung)
