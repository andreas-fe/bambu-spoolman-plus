# Betriebsstand

## 2026-11-07 — v2.4.0

Nach ausdrücklicher Freigabe wurde der QNAP-Betrieb auf `DRY_RUN=false` umgestellt. Automatische Zuordnung sowie echte Abbuchungen für neue Druckaufträge und manuelle Buchungen sind aktiv. Schätzungen für Druckabbrüche und gewichtsbasierte Spulenaufteilungen werden entsprechend der freigegebenen Verarbeitungslogik ebenfalls gebucht und im Journal gekennzeichnet.

Prüfung nach dem Neustart: Anwendung gesund, Spoolman verbunden, automatische Zuordnung aktiv. Das bestehende Journal wurde mit einer vor der Umschaltung gesicherten Ausgangslage verglichen und blieb unverändert. Historische Dry-Runs wurden nicht nachgebucht.

Die private Konfiguration wurde vor der Umschaltung auf dem NAS gesichert. Zugangsdaten, konkrete Netzadressen, Datenbanken und Sicherungen werden nicht in Git gespeichert. `.env.example` behält den sicheren Startwert `DRY_RUN=true` für neue Installationen.
