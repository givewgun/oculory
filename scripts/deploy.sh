#!/usr/bin/env bash
# Deploy Oculory to the GiveWGun VM. Run from the repo root on your machine.
#   ./scripts/deploy.sh
#
# Requires: ssh + rsync, the private key, and a populated .env in the repo root.
set -euo pipefail

KEY="${OCULORY_KEY:-/c/Users/gunka/OneDrive/Documents/gunvest/givewgun-pvt.key}"
HOST="${OCULORY_HOST:-ubuntu@161.118.201.235}"
REMOTE=/opt/oculory
SSH="ssh -i $KEY -o StrictHostKeyChecking=no"

cd "$(dirname "$0")/.."

[ -f .env ] || { echo "ERROR: .env missing. cp .env.example .env and fill it."; exit 1; }

echo ">> render alertmanager.yml from template (.env)"
set -a; . ./.env; set +a
envsubst < alertmanager/alertmanager.tmpl.yml > alertmanager/alertmanager.yml

echo ">> stage repo to VM (/tmp/oculory-stage)"
rsync -az --delete -e "$SSH" \
  --exclude '.git' --exclude 'data' \
  ./ "$HOST:/tmp/oculory-stage/"

echo ">> install into $REMOTE and bring the stack up"
# shellcheck disable=SC2087
$SSH "$HOST" "bash -s" <<EOF
set -euo pipefail
sudo mkdir -p $REMOTE
sudo rsync -a --delete --exclude '.env' /tmp/oculory-stage/ $REMOTE/
sudo cp /tmp/oculory-stage/.env $REMOTE/.env
sudo chmod 600 $REMOTE/.env
cd $REMOTE
sudo docker compose --env-file .env pull --ignore-build-error || true
sudo docker compose --env-file .env up -d
echo "-- containers --"
sudo docker compose ps
EOF

echo ">> done. Prometheus targets: ssh -i \$KEY -L 9090:localhost:9090 $HOST  then open http://localhost:9090/targets"
