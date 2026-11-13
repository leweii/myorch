#!/usr/bin/env bash
# Deploy origin/main of this repository to the local compose stack (worker and console), safely.
#
#   deploy/update.sh           fetch, fast-forward main, wait for a quiet moment, rebuild, smoke, tag
#   deploy/update.sh --check   only say what would be deployed and what it would wait for
#
# Used by the development-eval orchestrator after it merges a whitelisted change (myorch-workspace
# PLAN.md §3), and by people. Rules:
# - Fast-forward only: main must be an ancestor of origin/main.
# - A restart kills the activity of a running step, so it waits until no step of any run is running
#   (WAIT_MAX seconds, default 6h). Runs waiting for an answer or an approval may stay, unless the
#   change touches the workflow code (src/myorch/engines/, src/myorch/core/run.py): Temporal replays
#   an unfinished run against the new code, so then it waits until no run is unfinished at all.
# - After the restart the smoke flow (deploy/smoke.yaml) must pass. Otherwise the previously deployed
#   commit is built and started again, and the failed commit is remembered (deploy/.failed) so the next
#   update does not deploy it again; a newer commit on origin/main clears that.
# - The images are built from a clean checkout of the commit (a temporary git worktree), never from this
#   work tree: someone's uncommitted or staged changes here must not end up in the deployed image.
# - Every successful deploy is a local tag deploy-<UTC time>; `git tag -l 'deploy-*'` is the history.
set -euo pipefail
cd "$(dirname "$0")/.."
URL=${MYORCH_URL:-http://127.0.0.1:8800}
WAIT_MAX=${WAIT_MAX:-21600}
COMPOSE=(docker compose -f deploy/compose.yaml)
check=false; [ "${1:-}" = "--check" ] && check=true
say() { printf '[update %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

[ "$(git branch --show-current)" = main ] || { say "not on the main branch, stopping"; exit 1; }
git fetch --quiet origin main
target=$(git rev-parse origin/main)
prev=$(git describe --tags --match 'deploy-*' --abbrev=0 2>/dev/null || true)
prev=$( [ -n "$prev" ] && git rev-parse "$prev^{commit}" || git rev-parse HEAD )   # no tag yet: what is checked out runs
if [ "$prev" = "$target" ]; then say "no commits to deploy ($(git rev-parse --short "$target"))"; exit 0; fi
if [ -f deploy/.failed ] && [ "$(cat deploy/.failed)" = "$target" ]; then
  say "$(git rev-parse --short "$target") failed the smoke test last time and was rolled back. Waiting for a newer commit on origin/main before deploying"; exit 1
fi
git merge-base --is-ancestor HEAD "$target" || { say "main is not an ancestor of origin/main, cannot fast-forward, stopping"; exit 1; }

changed=$(git diff --name-only "$prev" "$target")
strict=false; grep -qE '^src/myorch/(engines/|core/run\.py)' <<<"$changed" && strict=true
sandbox_image=false; grep -q '^images/claude-code/' <<<"$changed" && sandbox_image=true
say "deploying $(git rev-parse --short "$prev")..$(git rev-parse --short "$target"): $(git rev-list --count "$prev..$target") commits, $(wc -l <<<"$changed" | tr -d ' ') files"
git log --format='  %h %s' "$prev..$target"
$strict && say "workflow code changed: waiting until no run is unfinished" || say "waiting until no step is running (runs waiting for an answer or an approval may stay)"
$check && exit 0

busy() {  # how many runs are in the way; prints them
  python3 - "$URL" "$strict" <<'PY'
import json, sys, urllib.request
url, strict = sys.argv[1], sys.argv[2] == "true"
get = lambda p: json.load(urllib.request.urlopen(url + p, timeout=30))
def running_steps(node):
    if isinstance(node, dict):
        if "attempts" in node and node.get("status") == "running":
            yield node.get("id")
        for v in node.values():
            yield from running_steps(v)
    elif isinstance(node, list):
        for v in node:
            yield from running_steps(v)
n = 0
for r in get("/api/runs?limit=200")["runs"]:
    if str(r.get("status")).upper() != "RUNNING":
        continue
    if strict:
        n += 1; print(f"  {r['run_id']}: unfinished"); continue
    steps = sorted(set(running_steps(get(f"/api/runs/{r['run_id']}").get("tree"))))
    if steps:
        n += 1; print(f"  {r['run_id']}: {', '.join(steps)} running")
sys.exit(min(n, 100))
PY
}
waited=0
until busy; do
  [ "$waited" -ge "$WAIT_MAX" ] && { say "waited ${WAIT_MAX}s and runs are still in the way; not deployed"; exit 2; }
  sleep 60; waited=$((waited + 60))
done

deploy_at() {  # build from a clean checkout of $1, then restart worker and console with that image
  local tmp
  tmp=$(mktemp -d /tmp/myorch-build-XXXXXX)
  git worktree add -q --detach "$tmp" "$1"
  if $sandbox_image; then docker build -q -t openshell-claude-code:latest "$tmp/images/claude-code" > /dev/null; fi
  docker build -q -t myorch:local --build-arg MYORCH_COMMIT="$(git rev-parse "$1")" -f "$tmp/deploy/Dockerfile" "$tmp" > /dev/null
  git worktree remove --force "$tmp"
  "${COMPOSE[@]}" up -d --no-build worker ui
}

smoke() {
  for _ in 1 2 3 4 5 6; do  # the worker needs a moment to connect to Temporal
    "${COMPOSE[@]}" exec -T worker myorch run deploy/smoke.yaml --wait && return 0
    sleep 10
  done
  return 1
}
# Keep the local main in step when the work tree allows it; the build does not depend on it.
git merge --quiet --ff-only "$target" 2>/dev/null || say "could not fast-forward the local main (conflicting changes in the work tree); the images are still built from a clean $(git rev-parse --short "$target")"
say "building the images from a clean checkout of $(git rev-parse --short "$target"), restarting worker and console"
deploy_at "$target"
if smoke; then
  tag="deploy-$(date -u +%Y%m%dT%H%M%SZ)"
  git tag "$tag" "$target"; rm -f deploy/.failed
  say "deployed: $tag = $(git rev-parse --short "$target")"
  exit 0
fi
say "smoke test failed, rolling back to $(git rev-parse --short "$prev")"
echo "$target" > deploy/.failed
deploy_at "$prev"
if smoke; then
  say "rolled back to $(git rev-parse --short "$prev"), which is what runs now. The failed commit $(git rev-parse --short "$target") needs a person to look at it"
  exit 3
fi
say "the smoke test failed after the rollback too: a person must handle it (the running images are from $(git rev-parse --short "$prev"))"
exit 4
