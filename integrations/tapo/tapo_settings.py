"""Shared legacy configuration; secrets remain outside the source tree."""
import json
import os
from pathlib import Path


def load_config():
    path = Path(os.getenv('TAPO_CONFIG_FILE', Path(__file__).with_name('tapo_config.json')))
    config = json.loads(path.read_text()) if path.exists() else {}
    for key, env in [('ip', 'TAPO_HOST'), ('username', 'TAPO_USERNAME'), ('password', 'TAPO_PASSWORD')]:
        if os.getenv(env):
            config[key] = os.environ[env]
    if not all(config.get(key) for key in ('ip', 'username', 'password')):
        raise ValueError('Tapo-Konfiguration fehlt. TAPO_CONFIG_FILE oder TAPO_HOST/USERNAME/PASSWORD setzen.')
    return config


async def safe_off(device):
    """Legacy shutdown uses the Plus application's fresh printer status."""
    import asyncio
    import base64
    import time
    import urllib.request
    config = load_config()
    url = os.getenv('BAMBU_PLUS_URL', config.get('bridge_url', '')).rstrip('/')
    if not url:
        raise ValueError('Abschalten gesperrt: BAMBU_PLUS_URL oder bridge_url fehlt.')
    def status():
        req = urllib.request.Request(url + '/api/status')
        username, password = os.getenv('WEB_USERNAME', ''), os.getenv('WEB_PASSWORD', '')
        if username:
            req.add_header('Authorization', 'Basic ' + base64.b64encode((username+':'+password).encode()).decode())
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)['printer']
    printer = await asyncio.to_thread(status)
    if printer.get('state') not in ('IDLE', 'FINISH') or not 0 <= time.time()-printer.get('state_seen_at', 0) <= 30:
        raise ValueError('Abschalten gesperrt: Drucker aktiv oder Status unbekannt/veraltet.')
    await device.off()
