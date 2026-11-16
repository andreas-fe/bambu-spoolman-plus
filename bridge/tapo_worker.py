"""Short-lived native Tapo process; stdout is a minimal JSON result only."""
import asyncio
import base64
import json
import os
import sys
import urllib.request
from .devices import TapoSwitch, ShutdownBlocked


def printer_is_safe():
    # Recheck the live parent state after get_device_info, just before off().
    port = int(os.getenv('WEB_PORT', '7913'))
    from urllib.parse import urlencode
    query='?'+urlencode({'job_id':os.environ['AUTO_OFF_JOB_ID']}) if os.getenv('AUTO_OFF_JOB_ID') else ''
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/printer/shutdown-status'+query)
    username, password = os.getenv('WEB_USERNAME', ''), os.getenv('WEB_PASSWORD', '')
    if username:
        request.add_header('Authorization', 'Basic ' + base64.b64encode((username+':'+password).encode()).decode())
    with urllib.request.urlopen(request, timeout=9) as response:
        return json.load(response).get('safe') is True


async def execute(action):
    return await asyncio.wait_for(TapoSwitch().request(action, printer_is_safe), timeout=20)


def main():
    try:
        action = json.load(sys.stdin)['action']
        if action is not None and not isinstance(action, bool):
            raise ValueError('Invalid action')
        result = asyncio.run(execute(action))
        print(json.dumps(result), flush=True)
        # The SDK can fail in PyGILState_Release during interpreter shutdown.
        # All network operations are awaited; let the OS reclaim this isolated
        # process instead of running the extension's unsafe finalizers.
        os._exit(0)
    except ShutdownBlocked:
        print(json.dumps({'error':'shutdown_blocked'}), flush=True)
        os._exit(3)
    except Exception:
        # Device errors may contain private data; do not forward raw exceptions.
        print('Tapo-Anfrage fehlgeschlagen.', file=sys.stderr, flush=True)
        os._exit(1)


if __name__ == '__main__':
    main()
