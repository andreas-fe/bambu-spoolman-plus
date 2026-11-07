#!/bin/sh
set -eu
umask 077
target=$1
source_dir=$2
stage=$3
spoolman_url=$4
spoolman_network=$5
spoolman_browser_url=$6
project=bambu-spoolman
container=bambu-spoolman-bridge
if command -v docker >/dev/null 2>&1; then
    docker_bin=$(command -v docker)
else
    docker_bin=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
fi
docker_cmd() { "$docker_bin" --config "$stage/docker-config" "$@"; }
docker_cmd compose version
docker_cmd network inspect "$spoolman_network" >/dev/null
[ -f "$source_dir/.env" ] || { echo 'Existing .env missing; nothing changed.' >&2; exit 1; }
mkdir -p "$target"
source_real=$(cd "$source_dir" && pwd -P)
target_real=$(cd "$target" && pwd -P)
if [ "$source_real" != "$target_real" ] && [ -e "$target/data" ]; then
    echo 'Target already contains data; refusing to merge databases.' >&2
    exit 1
fi
# Verify dependencies from the new image before stopping the old container.
docker_cmd build -t bambu-spoolman-bridge:local "$stage/bridge"
docker_cmd run --rm --entrypoint python bambu-spoolman-bridge:local -c 'import flask, paho.mqtt.client, requests'
stamp=$(date +%Y%m%d-%H%M%S)
backup="$target/backups/$stamp"
mkdir -p "$backup"
old_image=$(docker_cmd inspect "$container" --format '{{.Image}}')
printf '%s\n' "$old_image" > "$backup/image-id"
docker_cmd inspect "$container" --format '{{.State.Running}}' > "$backup/was-running"
# Refuse migration during a known running/paused print.
docker_cmd exec "$container" python -c '
import json, urllib.request
status=json.load(urllib.request.urlopen("http://127.0.0.1:7913/api/status",timeout=5))
state=status["printer"]["state"]
if state in ("RUNNING","PREPARE","PAUSE","PAUSED"):
    raise SystemExit("Print active; retry deployment after FINISH")
'
docker_cmd stop -t 30 "$container"
rollback() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        echo "Deployment failed. Restoring previous bridge from $backup" >&2
        docker_cmd rm -f "$container" >/dev/null 2>&1 || true
        if [ "$source_real" = "$target_real" ]; then
            if [ -f "$backup/project.tar.gz" ]; then
                tar -xzf "$backup/project.tar.gz" -C "$source_dir" || true
            fi
            docker_cmd tag "$old_image" bambu-spoolman-bridge:local
        fi
        docker_cmd compose -p "$project" -f "$source_dir/docker-compose.yml" --project-directory "$source_dir" up -d --no-build bambu-bridge || true
    fi
    exit "$code"
}
trap rollback EXIT
# Container stopped: SQLite DB and WAL files now form a consistent backup.
tar -czf "$backup/project.tar.gz" -C "$source_dir" .env docker-compose.yml bridge data
if [ "$source_real" != "$target_real" ]; then
    cp -a "$source_dir/data" "$target/data"
fi
cp "$source_dir/.env" "$target/.env.deploy"
# Private config is changed only on the NAS, never included in the upload or Git.
sed '/^SPOOLMAN_URL=/d; /^BRIDGE_DATA_DIR=/d; /^SPOOLMAN_NETWORK=/d; /^SPOOLMAN_BROWSER_URL=/d' "$target/.env.deploy" > "$target/.env"
printf '\nSPOOLMAN_URL=%s\nBRIDGE_DATA_DIR=%s/data\n' "$spoolman_url" "$target_real" >> "$target/.env"
printf 'SPOOLMAN_NETWORK=%s\nSPOOLMAN_BROWSER_URL=%s\n' "$spoolman_network" "$spoolman_browser_url" >> "$target/.env"
chmod 600 "$target/.env"
rm "$target/.env.deploy"
mkdir -p "$target/bridge"
cp "$stage/bridge/bridge.py" "$stage/bridge/Dockerfile" "$stage/bridge/requirements.txt" "$target/bridge/"
cp "$stage/docker-compose.yml" "$stage/.env.example" "$target/"
docker_cmd compose -p "$project" -f "$target/docker-compose.yml" --project-directory "$target" config --quiet
docker_cmd rm "$container"
docker_cmd compose -p "$project" -f "$target/docker-compose.yml" --project-directory "$target" up -d --no-build bambu-bridge
healthy=false
for attempt in $(seq 1 30); do
    if docker_cmd exec "$container" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:7913/health",timeout=3)' >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 2
done
[ "$healthy" = true ] || { echo 'Health check failed' >&2; exit 1; }
docker_cmd exec "$container" python -c '
import os, requests
r=requests.get(os.environ["SPOOLMAN_URL"]+"/api/v1/spool",timeout=10)
r.raise_for_status()
print("Spoolman reachable from container; spool count:",len(r.json()))
'
docker_cmd inspect "$container" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{end}}'
printf 'Deployment successful. Backup: %s\n' "$backup"
# Source directory is deliberately retained for review and rollback.
