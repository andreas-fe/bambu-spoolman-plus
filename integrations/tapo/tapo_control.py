import asyncio
import os
import sys
from tapo import ApiClient
from tapo_settings import load_config, safe_off


async def control(action=None):
    config = load_config()
    client = ApiClient(config['username'], config['password'])
    device = await client.p110(config['ip'])
    info = await device.get_device_info()
    print('Tapo:', 'Eingeschaltet' if info.device_on else 'Ausgeschaltet')
    if action is None:
        action = input('Aktion (on/off/Enter für Status): ').strip().lower()
    if action == 'on' and not info.device_on:
        await device.on()
    elif action == 'off' and info.device_on:
        await safe_off(device)
    elif action not in ('', 'on', 'off'):
        raise ValueError('Ungültige Aktion.')


def main(action=None):
    try:
        asyncio.run(control(action))
        sys.stdout.flush()
        os._exit(0)  # Avoid the native SDK's unsafe interpreter finalizer.
    except Exception:
        print('Tapo-Aktion fehlgeschlagen; Konfiguration und Druckerstatus prüfen.', file=sys.stderr, flush=True)
        os._exit(1)
