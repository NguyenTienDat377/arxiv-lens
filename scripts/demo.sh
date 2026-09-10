#!/usr/bin/env bash
# Bring the whole system up locally and print where everything is.
#
#   ./scripts/demo.sh up      start every service, wait for health
#   ./scripts/demo.sh seed    build the graph into Neo4j (only needed once)
#   ./scripts/demo.sh check   run a query end to end and show the result
#   ./scripts/demo.sh down    stop everything
#
# Nothing here needs a cloud account. The stack is Docker Compose with the
# `app` and `observability` profiles from infra/docker-compose.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml" --profile app --profile observability)

wait_for() {
  local name="$1" url="$2" tries=60
  printf "  %-16s" "$name"
  until curl -sf "$url" >/dev/null 2>&1; do
    tries=$((tries - 1))
    [ "$tries" -le 0 ] && { echo "TIMED OUT ($url)"; return 1; }
    sleep 2
  done
  echo "ready"
}

case "${1:-up}" in
  up)
    echo "starting the stack (first run builds images, ~2 min)..."
    "${COMPOSE[@]}" up -d --build
    echo
    echo "waiting for health:"
    wait_for "neo4j"          "http://localhost:7474"
    wait_for "query-service"  "http://localhost:8082/actuator/health"
    wait_for "prometheus"     "http://localhost:9090/-/ready"
    wait_for "grafana"        "http://localhost:3000/api/health"
    echo
    echo "  REST API        http://localhost:8082/api/stats"
    echo "  API docs        http://localhost:8082/swagger-ui.html"
    echo "  Neo4j browser   http://localhost:7474   (neo4j / password)"
    echo "  Grafana         http://localhost:3000   (admin / admin)"
    echo "  Prometheus      http://localhost:9090/targets"
    echo
    echo "  next: ./scripts/demo.sh seed   (once, to populate the graph)"
    echo "        ./scripts/demo.sh check  (free: graph stats)"
    echo "        ./scripts/demo.sh query  (cents: a cited answer from Claude)"
    ;;

  seed)
    echo "building the graph into Neo4j..."
    "${COMPOSE[@]}" run --rm graph-pipeline python -m pipeline.build_graph
    ;;

  check)
    # Free: no LLM call, just the graph traversal path.
    echo "== graph stats (REST -> gRPC -> Neo4j) =="
    curl -s http://localhost:8082/api/stats | python3 -m json.tool
    ;;

  query)
    # Costs a couple of cents: the answer is written by Claude from the facts.
    Q="${2:-What builds on Logic Tensor Networks?}"
    echo "== \"$Q\" answered from the graph, with citations =="
    curl -s -X POST http://localhost:8082/api/query \
      -H 'Content-Type: application/json' \
      -d "{\"question\": \"$Q\"}" \
      | python3 -m json.tool
    ;;

  down)
    "${COMPOSE[@]}" down
    ;;

  *)
    echo "usage: $0 {up|seed|check|query|down}" >&2
    exit 1
    ;;
esac
