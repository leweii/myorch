"""Load provider profiles and credentials into the gateway from a file (compose service `providers`).

    /secrets/providers.yaml   (host: deploy/secrets/providers.yaml, see providers.example.yaml)

1. Imports the provider profiles: openshell/providers/*.yaml of this repository, each project's
   <knowledge>/<project>/providers/*.yaml, the GitHub profiles of the projects under
   `github_profiles:` (made by `myorch project github-profile`), and the extra files under
   `profiles:`. A profile that is already in the gateway is updated from the file.
2. Creates each provider under `providers:`, or updates its credentials when it exists. A provider
   whose credentials are all empty is skipped (filled in later); one with only some values fails.

Credential values reach openshell through the environment of the child process only
(`--credential KEY` reads KEY from there): never in argv, never printed. Providers in the gateway
that the file does not list are left alone and reported. Runs on every `docker compose up`; without
the file it does nothing.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import yaml

SECRETS = Path(os.environ.get("MYORCH_PROVIDERS_FILE", "/secrets/providers.yaml"))
REPO_PROFILES = Path("/app/openshell/providers")
KNOWLEDGE = Path(os.environ.get("MYORCH_KNOWLEDGE", "/knowledge"))


def sh(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["openshell", *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def wait_for_gateway(seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while True:
        r = sh("provider", "list")
        if r.returncode == 0:
            return
        if time.monotonic() > deadline:
            sys.exit(f"gateway not reachable: {(r.stderr or r.stdout).strip()[-300:]}")
        time.sleep(2)


def profile_id(path: Path) -> str:
    return str((yaml.safe_load(path.read_text()) or {}).get("id", ""))


def apply_profile(path: Path) -> None:
    r = sh("provider", "profile", "import", "-f", str(path))
    if r.returncode == 0:
        print(f"profile {path.name}: imported")
        return
    pid = profile_id(path)
    if "already exists" not in r.stdout + r.stderr or not pid:
        sys.exit(f"profile {path}: import failed\n{(r.stdout + r.stderr).strip()}")
    # Updating needs the gateway's current resource_version (optimistic lock).
    ex = sh("profile", "export", pid, "-o", "json")
    if ex.returncode != 0:
        sys.exit(f"profile {pid}: export failed: {ex.stderr.strip()}")
    rv = json.loads(ex.stdout).get("resource_version")
    tmp = Path("/tmp") / f"profile-{pid}.yaml"
    tmp.write_text(f"resource_version: {rv}\n" + path.read_text())
    up = sh("provider", "profile", "update", "-f", str(tmp), pid)
    tmp.unlink()
    if up.returncode != 0:
        sys.exit(f"profile {pid}: update failed\n{(up.stdout + up.stderr).strip()}")
    print(f"profile {path.name}: updated")


def existing_providers() -> set[str]:
    r = sh("provider", "list")
    lines = r.stdout.strip().splitlines()[1:]  # header: NAME TYPE ...
    return {line.split()[0] for line in lines if line.strip()}


def apply_provider(name: str, spec: dict, existing: set[str]) -> None:
    creds = {str(k): str(v) for k, v in (spec.get("credentials") or {}).items()}
    config = {str(k): str(v) for k, v in (spec.get("config") or {}).items()}
    empty = [k for k, v in creds.items() if not v.strip() or v.startswith("<")]
    if creds and len(empty) == len(creds):  # not filled in yet: leave it for later
        print(f"provider {name}: skipped, no value yet")
        return
    if empty:
        sys.exit(f"provider {name}: credential {', '.join(empty)} has no value")
    args: list[str] = []
    for k in creds:
        args += ["--credential", k]  # the value comes from the environment below
    for k, v in config.items():
        args += ["--config", f"{k}={v}"]
    if name in existing:
        r = sh("provider", "update", *args, name, env=creds)
        verb = "updated"
    else:
        if not spec.get("type"):
            sys.exit(f"provider {name}: type is missing")
        r = sh("provider", "create", "--name", name, "--type", str(spec["type"]), *args, env=creds)
        verb = "created"
    if r.returncode != 0:
        out = r.stdout + r.stderr
        for v in creds.values():  # in case openshell ever echoes a value
            out = out.replace(v, "***")
        sys.exit(f"provider {name}: {verb[:-1]} failed\n{out.strip()}")
    print(f"provider {name}: {verb}")


def main() -> None:
    if not SECRETS.is_file():
        print(f"{SECRETS} not found: nothing to load (see deploy/providers.example.yaml)")
        return
    mode = SECRETS.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"warning: {SECRETS} is readable by group/others (mode {oct(mode & 0o777)}); chmod 600 it")
    doc = yaml.safe_load(SECRETS.read_text()) or {}

    wait_for_gateway()
    profiles = sorted(REPO_PROFILES.glob("*.yaml")) + sorted(KNOWLEDGE.glob("*/providers/*.yaml"))
    for project in doc.get("github_profiles") or []:
        r = subprocess.run(["myorch", "project", "github-profile", str(project)], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"github profile of {project}: {r.stderr.strip()}")
        out = Path("/tmp") / f"gh-{project}.yaml"
        out.write_text(r.stdout)
        profiles.append(out)
    profiles += [Path(p) for p in doc.get("profiles") or []]
    for p in profiles:
        apply_profile(p)

    wanted = doc.get("providers") or {}
    existing = existing_providers()
    for name, spec in wanted.items():
        apply_provider(str(name), spec or {}, existing)
    extra = sorted(existing - set(map(str, wanted)))
    if extra:
        print(f"not in {SECRETS.name} (left as is): {', '.join(extra)}")


if __name__ == "__main__":
    main()
