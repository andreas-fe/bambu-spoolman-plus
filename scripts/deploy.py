#!/usr/bin/env python3
"""Upload and deploy only this bridge over SSH. No credentials are bundled."""
import argparse
import io
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='SSH user@host')
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--target', required=True, help='Absolute QNAP project path')
    parser.add_argument('--legacy', help='Existing project path for first migration')
    parser.add_argument('--spoolman-url', required=True)
    parser.add_argument('--spoolman-network', required=True)
    parser.add_argument('--spoolman-browser-url', required=True)
    args = parser.parse_args()
    for path in [args.target, args.legacy or args.target]:
        if not path.startswith('/') or path == '/' or '\n' in path:
            parser.error('Project paths must be absolute directories')
    root = Path(__file__).resolve().parents[1]
    ssh = ['ssh', '-F', '/dev/null', '-i', str(args.key.expanduser()), '-o', 'BatchMode=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', args.host]
    # Check authentication before making remote changes.
    subprocess.run(ssh + ['true'], check=True)
    stage = '/tmp/bambu-spoolman-' + uuid.uuid4().hex
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for relative in ['bridge/bridge.py', 'bridge/Dockerfile', 'bridge/requirements.txt', 'docker-compose.yml', '.env.example', 'scripts/deploy-remote.sh']:
            tar.add(root / relative, arcname=relative)
    subprocess.run(ssh + [f'umask 077; mkdir {shlex.quote(stage)} && tar -xzf - -C {shlex.quote(stage)}'], input=archive.getvalue(), check=True)
    command = ['sh', stage + '/scripts/deploy-remote.sh', args.target, args.legacy or args.target,
               stage, args.spoolman_url, args.spoolman_network, args.spoolman_browser_url]
    subprocess.run(ssh + [' '.join(shlex.quote(part) for part in command)], check=True)


if __name__ == '__main__':
    main()
