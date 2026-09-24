#!/usr/bin/env bash
# Retries `terraform apply` while OCI answers "Out of host capacity", which is
# how Always Free A1 requests in busy regions usually fail. Any other error
# stops the loop, so a real mistake is never retried.
#
#   ./apply-until-capacity.sh            # retry every 60s
#   INTERVAL=120 ./apply-until-capacity.sh
#
# Variables come from terraform.tfvars as usual.
set -uo pipefail
cd "$(dirname "$0")"

INTERVAL="${INTERVAL:-60}"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

terraform plan -input=false || exit 1
read -r -p "Apply this plan, retrying on capacity errors? [y/N] " answer
[[ "$answer" == [yY] ]] || exit 1

attempt=1
while true; do
  echo "== attempt $attempt, $(date '+%H:%M:%S')"
  # pipefail makes the pipeline fail when terraform does, despite tee.
  if terraform apply -input=false -auto-approve 2>&1 | tee "$LOG"; then
    break
  fi
  if grep -qiE "out of host capacity|TooManyRequests" "$LOG"; then
    echo "   no capacity yet; next try in ${INTERVAL}s (Ctrl-C to stop)"
    sleep "$INTERVAL"
    attempt=$((attempt + 1))
  else
    echo "   failed for a reason other than capacity; not retrying"
    exit 1
  fi
done

terraform output
