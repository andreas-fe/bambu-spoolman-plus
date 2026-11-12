# Tapo-P110-Übergangsclients

`P110_ON.py` schaltet ein, `P110_OFF.py` schaltet aus und `P110_Switch.py` fragt die Aktion ab. Alle lesen dieselbe externe `tapo_config.json` (`username`, `password`, `ip`) beziehungsweise `TAPO_CONFIG_FILE`. Alternativ: `TAPO_HOST`, `TAPO_USERNAME`, `TAPO_PASSWORD`. Keine Zugangsdaten in Python-Dateien speichern. Die bestehende Tk-GUI bleibt verwendbar; ihr Konfigurationspfad ist jetzt unabhängig vom Arbeitsverzeichnis.

Abschalten benötigt `BAMBU_PLUS_URL` oder `bridge_url` in der privaten Konfiguration sowie gegebenenfalls `WEB_USERNAME`/`WEB_PASSWORD`. Der Plus-Druckerstatus muss frisch sein und IDLE/FINISH melden. Unbekannter Zustand verhindert die Aktion. Für den täglichen Betrieb steht `/devices` in der Plus-Weboberfläche zur Verfügung.

Abhängigkeiten: `tapo==0.12.2`; GUI zusätzlich Pillow. Geschützte private Konfiguration und gegebenenfalls vorhandene Windows-Verknüpfungen bleiben außerhalb des Repositories.
