# Verarbeitungsflow

```mermaid
flowchart TD
    Start["Druckstart erkennen"] --> Snapshot["Rollen, AMS, Bestand und Optionen festhalten"]
    Snapshot --> Track["3MF-Metadaten laden; Fortschritt und Slots verfolgen"]
    Track --> End{"Druckende"}
    End -->|"Normal"| Full["Slicer-Mengen"]
    End -->|"Abbruch"| Estimate["Mengen mal Fortschritt; Schätzung"]
    Full --> Check{"Metadaten und Zuordnung geklärt?"}
    Estimate --> Check
    Check -->|"Nein"| Wait["Auftrag wartet; nichts teilweise abbuchen"]
    Wait --> Check
    Check -->|"Ja"| Extra["Optional einmalig 2 g je positiver Filamentposition"]
    Extra --> Plan["Rollenaufteilung und Buchungsplan einfrieren"]
    Plan --> Mode{"Simulation?"}
    Mode -->|"Ja"| Dry["DRY_RUN; später nicht nachbuchen"]
    Mode -->|"Nein"| Backend{"Bestandssystem"}
    Backend -->|"Intern"| Atomic["Bestand, Bewegung und Journal atomar/idempotent"]
    Backend -->|"Spoolman"| API["REST-Buchung; unklare Antworten sperren"]
    Atomic --> Done["Bestätigt abgeschlossen"]
    API --> Done
    Done --> Off{"Abschaltung für diesen Auftrag aktiviert?"}
    Off -->|"Ja"| Delay["Normaler Abschluss, Verzögerung, neuer Leerlauf und finale Prüfung"]
    Delay --> Switch["Tapo einmalig ausschalten"]
```

Offene Prüfungen, neuer Druck und Abbruch verhindern die automatische Abschaltung. Das Diagramm fasst den Erfolgsweg zusammen; externe UNKNOWN-/RETRY-Zustände erläutert die [Architektur](../architecture.md). [Zur Übersicht](../../README.md).
