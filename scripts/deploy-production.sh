#!/usr/bin/env bash

set -Eeuo pipefail

release_id=${1:?usage: deploy-production.sh RELEASE_ID ARCHIVE_PATH ENVIRONMENT_PATH}
archive_path=${2:?usage: deploy-production.sh RELEASE_ID ARCHIVE_PATH ENVIRONMENT_PATH}
incoming_environment=${3:?usage: deploy-production.sh RELEASE_ID ARCHIVE_PATH ENVIRONMENT_PATH}

if [[ $EUID -ne 0 ]]; then
    echo "deployment must run as root" >&2
    exit 1
fi

if [[ ! $release_id =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid release identifier" >&2
    exit 1
fi

for staged_file in "$archive_path" "$incoming_environment"; do
    if [[ ! -f $staged_file ]]; then
        echo "required staged deployment file is missing" >&2
        exit 1
    fi
done

deployment_root=/opt/incident-investigation-agent
release_dir="$deployment_root/releases/$release_id"
environment_dir=/etc/incident-investigation-agent
environment_file="$environment_dir/environment"
compose_project=incident-agent

if [[ -e $release_dir ]]; then
    echo "release directory already exists" >&2
    exit 1
fi

install -d -m 0755 "$deployment_root" "$deployment_root/releases"
install -d -m 0700 "$environment_dir"
install -d -m 0755 "$release_dir"
install -m 0600 "$incoming_environment" "$environment_file"
rm -f "$incoming_environment"

tar --extract --gzip --file "$archive_path" --directory "$release_dir" --no-same-owner
rm -f "$archive_path"

compose=(
    docker compose
    --env-file "$environment_file"
    --project-name "$compose_project"
    --file "$release_dir/docker-compose.yml"
    --file "$release_dir/docker-compose.production.yml"
)

"${compose[@]}" config --quiet
"${compose[@]}" build
"${compose[@]}" up --detach db

database_container=$("${compose[@]}" ps --quiet db)
for _ in $(seq 1 45); do
    database_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$database_container")
    if [[ $database_health == healthy ]]; then
        break
    fi
    sleep 2
done
if [[ ${database_health:-unknown} != healthy ]]; then
    echo "PostgreSQL did not become healthy" >&2
    exit 1
fi

network_name="${compose_project}_default"
proxy_gateway=$(docker network inspect "$network_name" --format '{{(index .IPAM.Config 0).Gateway}}')
if [[ ! $proxy_gateway =~ ^[0-9A-Fa-f:.]+$ ]]; then
    echo "Docker network gateway is invalid" >&2
    exit 1
fi

sed -i "s/^TRUSTED_PROXY_IPS=.*/TRUSTED_PROXY_IPS=$proxy_gateway/" "$environment_file"
chmod 0600 "$environment_file"
"${compose[@]}" config --quiet

"${compose[@]}" run --rm --no-TTY --interactive=false migrate </dev/null
"${compose[@]}" up --detach --no-deps api

for _ in $(seq 1 45); do
    if curl --fail --silent --show-error \
        --header 'Host: api.marvinjb.dev' \
        http://127.0.0.1:8002/health/live >/dev/null \
        && curl --fail --silent --show-error \
        --header 'Host: api.marvinjb.dev' \
        http://127.0.0.1:8002/health/ready >/dev/null; then
        break
    fi
    sleep 2
done

curl --fail --silent --show-error \
    --header 'Host: api.marvinjb.dev' \
    http://127.0.0.1:8002/health/live >/dev/null
curl --fail --silent --show-error \
    --header 'Host: api.marvinjb.dev' \
    http://127.0.0.1:8002/health/ready >/dev/null

ln --symbolic --force --no-dereference "$release_dir" "$deployment_root/current"

api_container=$("${compose[@]}" ps --quiet api)
echo "deployment_status=healthy"
echo "release_id=$release_id"
echo "database_health=$database_health"
echo "trusted_proxy=$proxy_gateway"
docker inspect "$api_container" --format 'api_user={{.Config.User}} api_ports={{json .HostConfig.PortBindings}} api_health={{.State.Health.Status}}'
docker inspect "$database_container" --format 'database_ports={{json .HostConfig.PortBindings}} database_health={{.State.Health.Status}}'
