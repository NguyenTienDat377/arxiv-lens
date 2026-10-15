#!/usr/bin/env bash
# Deploy the newest commit on main that CI has built images for.
#
#   ./scripts/deploy.sh      run on the demo host by arxiv-lens-deploy.timer,
#                            every 2 minutes and once at boot
#
# ci.yml only builds images for a commit whose tests passed, so "ghcr has an
# image for this SHA" is the gate. A deploy that fails its health checks is
# rolled back and its SHA remembered, so the timer does not retry it every tick.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml" --profile app)
# Named, because a bare `up --wait` also starts the one-shot certbot service and
# then reports its normal exit as a failure.
SERVICES=(graph-pipeline query-service nginx)
REGISTRY=ghcr.io/nguyentiendat377/arxiv-lens
FAILED=/var/tmp/arxiv-lens-failed-sha

exec 9>/var/tmp/arxiv-lens-deploy.lock
flock -n 9 || exit 0

cd "$ROOT"
git fetch --quiet origin main
target=$(git rev-parse origin/main | cut -c1-7)
running=$(docker inspect -f '{{.Config.Image}}' query-service 2>/dev/null | sed 's/.*://' || true)

[ "$target" = "$running" ] && exit 0
[ "$target" = "$(cat "$FAILED" 2>/dev/null || true)" ] && exit 0
for svc in graph-pipeline query-service; do
  if ! docker manifest inspect "$REGISTRY-$svc:$target" >/dev/null 2>&1; then
    echo "no $svc image for $target yet (CI still running, or red)"
    exit 0
  fi
done

previous=$(git rev-parse HEAD)

rollback() {
  echo "$target failed ($1); rolling back to ${running:-the previous build}"
  echo "$target" > "$FAILED"
  git checkout --quiet --detach "$previous"
  if [[ $running =~ ^[0-9a-f]{7}$ ]]; then
    TAG=$running "${COMPOSE[@]}" up -d --no-build --wait "${SERVICES[@]}"
  else
    # The first deploy replaces a stack that was built on this host, so there
    # is no registry image to go back to.
    TAG=local "${COMPOSE[@]}" up -d --build --wait "${SERVICES[@]}"
  fi
  exit 1
}

echo "deploying $target (running: ${running:-nothing})"
git checkout --quiet --detach "$target"
export TAG=$target

"${COMPOSE[@]}" run --rm --no-deps -T nginx nginx -t || rollback "nginx config test"
"${COMPOSE[@]}" pull --quiet graph-pipeline query-service
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 300 "${SERVICES[@]}" || rollback "health checks"

# nginx is only recreated when its compose definition changes; a reload picks
# up a changed default.conf either way.
docker exec nginx nginx -s reload
docker image prune -af --filter "until=168h" >/dev/null
rm -f "$FAILED"
echo "deployed $target"
