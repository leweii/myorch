#!/usr/bin/env bash
# Set up myorch on a new Mac (fresh environment, no history) and start it with docker compose.
#
#   curl -fsSL ... is not needed: clone myorch first, then run this from anywhere:
#   bash ~/orca/projects/myorch/deploy/bootstrap-mac.sh
#
# It is safe to run again: every step checks first and skips what is done. It stops with a message
# where a person has to act (install an app, log in, copy the credential files). It never prints or
# copies credentials; it only checks that deploy/secrets/ has the files.
#
# Steps: tools → directories (myorch, myorch-knowledge, myorch-data) → credential files → images →
# compose up → project packs → smoke test. Details: deploy/README.md「新机器」.
set -euo pipefail

ROOT="${MYORCH_ROOT:-$HOME/orca/projects}"   # the parent of myorch/, myorch-knowledge/, myorch-data/
MYORCH="$ROOT/myorch"
KB="$ROOT/myorch-knowledge"
DATA="$ROOT/myorch-data"
DC=(docker compose -f "$MYORCH/deploy/compose.yaml")

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ok  %s\n' "$*"; }
stop() { printf '\n\033[1mStopped:\033[0m %s\n' "$*" >&2; exit 1; }

say "1. Tools"
command -v git >/dev/null || stop "git is missing: run \`xcode-select --install\`, then run this script again."
command -v docker >/dev/null || stop "Docker Desktop is missing: install it from https://www.docker.com/products/docker-desktop/, start it, then run this script again."
docker info >/dev/null 2>&1 || stop "Docker Desktop is not running: start it (and set it to start at login), then run this script again."
docker compose version >/dev/null 2>&1 || stop "docker compose is missing: update Docker Desktop."
command -v gh >/dev/null || stop "gh is missing: install Homebrew (https://brew.sh), then \`brew install gh\`."
gh auth status >/dev/null 2>&1 || stop "gh is not logged in: run \`gh auth login\` (account leweii; myorch-knowledge is private)."
ok "git, Docker $(docker version --format '{{.Server.Version}}'), gh ($(gh api user -q .login))"
[ "$(uname -m)" = "arm64" ] || echo "   note: this Mac is $(uname -m); myorch has only been run on Apple Silicon (arm64)."

say "2. Directories under $ROOT"
mkdir -p "$ROOT" "$DATA"
[ -d "$MYORCH/.git" ] || gh repo clone leweii/myorch "$MYORCH"
[ -d "$KB/.git" ] || gh repo clone leweii/myorch-knowledge "$KB"
git -C "$MYORCH" pull -q --ff-only || echo "   note: myorch has local changes or diverged; not pulled"
git -C "$KB" pull -q --ff-only || echo "   note: myorch-knowledge has local changes or diverged; not pulled"
ok "myorch $(git -C "$MYORCH" rev-parse --short HEAD), myorch-knowledge $(git -C "$KB" rev-parse --short HEAD), myorch-data"

say "3. Credential files (deploy/secrets/, copied from the old machine by you)"
SECRETS="$MYORCH/deploy/secrets"
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"
[ -f "$SECRETS/providers.yaml" ] || stop "$SECRETS/providers.yaml is missing. Copy it from the old machine
   (AirDrop or \`scp oldmac:~/orca/projects/myorch/deploy/secrets/providers.yaml $SECRETS/\`), never through chat or e-mail.
   Without the old machine: cp $MYORCH/deploy/providers.example.yaml $SECRETS/providers.yaml and fill it in."
chmod 600 "$SECRETS"/* 2>/dev/null || true
ok "providers.yaml"
if [ -f "$SECRETS/slack.env" ]; then
  ok "slack.env (Slack starts with the console: stop Slack on the old machine first, see deploy/README.md)"
else
  echo "   no slack.env: the console runs without Slack"
fi

say "4. Images (the first build downloads a few GB and takes a while)"
build() {  # build <image> <command...>: only when the image is missing (FORCE=1 rebuilds)
  local image="$1"; shift
  if [ -z "${FORCE:-}" ] && docker image inspect "$image" >/dev/null 2>&1; then ok "$image (present)"; return; fi
  echo "   building $image"; "$@"; ok "$image"
}
build openshell-claude-code:latest docker build -q -t openshell-claude-code:latest "$MYORCH/images/claude-code"
build myorch-dev-core-ng:latest sh "$KB/_templates/core-ng/image/build.sh"
build myorch-dev-mdm:latest sh "$KB/mdm/image/build.sh"
build myorch-dev-myorch:latest sh "$KB/myorch/image/build.sh" "$MYORCH"
if [ -z "${FORCE:-}" ] && docker image inspect myorch-dev-blog:latest >/dev/null 2>&1; then
  ok "myorch-dev-blog:latest (present)"
else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  git clone -q --depth 1 https://github.com/leweii/leweii.github.io.git "$tmp/blog"
  echo "   building myorch-dev-blog, myorch-e2e-blog"; sh "$KB/blog/image/build.sh" "$tmp/blog"; ok "myorch-dev-blog, myorch-e2e-blog"
fi

say "5. Services"
# Who the console and the CLI act as: a person in channels.yaml identities (initiator, approver). Kept in
# deploy/.env (git-ignored, read by compose), so a later `docker compose up` from any shell keeps it.
ENVF="$MYORCH/deploy/.env"
current=""; [ -f "$ENVF" ] && current="$(sed -n 's/^MYORCH_USER=//p' "$ENVF" | tail -1)"
MYORCH_USER="${MYORCH_USER:-${current:-$(id -un)}}"
# channels.yaml is git-ignored (it names real accounts): a new clone starts from channels.example.yaml
# with you in it; add your Slack member id later (deploy/README.md, Slack).
if [ ! -f "$MYORCH/channels.yaml" ]; then
  sed "s/^  you: {github: your-github-login, slack: U0XXXXXXXXX}/  $MYORCH_USER: {}/" "$MYORCH/channels.example.yaml" > "$MYORCH/channels.yaml"
  ok "channels.yaml created from channels.example.yaml, with $MYORCH_USER"
fi
if ! grep -q "^  $MYORCH_USER:" "$MYORCH/channels.yaml"; then
  stop "'$MYORCH_USER' is not a person in channels.yaml identities ($(sed -n 's/^  \([a-z][a-z0-9_-]*\):.*/\1/p' "$MYORCH/channels.yaml" | tr '\n' ' ')).
   Run again with the person's name, e.g. MYORCH_USER=jakobhe bash $0"
fi
{ [ -f "$ENVF" ] && grep -v '^MYORCH_USER=' "$ENVF" || true; } > "$ENVF.tmp"
echo "MYORCH_USER=$MYORCH_USER" >> "$ENVF.tmp" && mv "$ENVF.tmp" "$ENVF"
export MYORCH_USER
ok "console and CLI act as $MYORCH_USER (deploy/.env)"
"${DC[@]}" up -d --build
for i in $(seq 1 30); do
  [ "$("${DC[@]}" ps --format '{{.Service}} {{.State}}' | grep -c ' running')" -ge 4 ] && break; sleep 2
done
"${DC[@]}" ps --format '   {{.Service}}: {{.State}} {{.Status}}'
"${DC[@]}" ps --format '{{.Service}} {{.State}}' | grep -q '^worker running' \
  || stop "the worker is not running. Look at \`${DC[*]} logs providers\` (a wrong credential file stops it)."
for i in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:8800/ && break; sleep 2; done
curl -fs -o /dev/null http://127.0.0.1:8800/ || stop "the console does not answer on http://127.0.0.1:8800/: \`${DC[*]} logs ui\`."
ok "console http://127.0.0.1:8800/"

say "6. Project packs"
for p in $(cd "$KB" && ls -d */ | tr -d / | grep -v '^_'); do
  "${DC[@]}" exec -T worker myorch project sync "$p" | sed 's/^/   /'
done

say "7. Smoke test (two script steps, no model, about 20 seconds)"
"${DC[@]}" exec -T worker myorch run deploy/smoke.yaml --wait --as "$MYORCH_USER" | sed 's/^/   /'

say "Done"
cat <<EOF
   Still to do by hand (deploy/README.md「新机器」):
   - Energy: prevent automatic sleep; Docker Desktop: start at login.
   - Remote Login (System Settings → General → Sharing) if you use this Mac from another one:
       ssh -N -L 8800:127.0.0.1:8800 $(id -un)@$(scutil --get LocalHostName 2>/dev/null || hostname).local
   - Slack: only one machine may run it. Stop it on the old machine before this one starts it.
EOF
