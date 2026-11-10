from __future__ import annotations

import asyncio
import codecs
import hashlib
import io
import logging
import re
import shlex
import subprocess
import tarfile
from collections.abc import AsyncIterator
from pathlib import Path

from myorch.core.manifest import MANIFEST_FILE, QUESTION_FILE, Manifest, Question, parse_manifest, parse_question
from myorch.core.types import StepKey
from myorch.storage.base import MountSource, job_dir, knowledge_dir, project_snapshot_dir, run_dir, shared_dir, step_dir

log = logging.getLogger(__name__)

SANDBOX_UID = 1000
HELPER_IMAGE = "nvcr.io/nvidia/base/ubuntu:24.04"
GIT_IMAGE = "alpine/git:latest"  # helper with git, for diffs of a run's repository
MEMORY_SEED = "# 本次运行的共享记忆\n\n各步骤开始前先读这里；结束时把对后续步骤有用的信息追加到末尾。\n"


def _session_name(name: str) -> str:
    """A file name to look for in a session dir: a plain name, no path and no pattern."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError(f"not a session file name: {name!r}")
    return name


def _log_name(file: str) -> str:
    """A file name inside a step's logs/ (turn_log), never a path out of it."""
    if not file or "/" in file or file.startswith("."):
        raise ValueError(f"not a log file name: {file!r}")
    return file


class DockerVolumeStore:
    """ArtifactStore backed by a named docker volume.

    On macOS the volume lives inside the Docker Desktop VM, so every operation goes
    through a short-lived helper container that mounts the volume at /v.
    """

    def __init__(self, volume: str = "myorch-artifacts", helper_image: str = HELPER_IMAGE) -> None:
        self.volume = volume
        self.helper_image = helper_image

    def _src(self, subpath: str) -> MountSource:
        return MountSource(volume=self.volume, subpath=subpath)

    def workspace_source(self, run_id: str) -> MountSource:
        return self._src(run_dir(run_id))

    def repo_source(self, run_id: str) -> MountSource:
        return self._src(f"{run_dir(run_id)}/repo")

    def out_source(self, run_id: str, instance_id: str) -> MountSource:
        return self._src(f"{step_dir(run_id, instance_id)}/out")

    def logs_source(self, run_id: str, instance_id: str) -> MountSource:
        return self._src(f"{step_dir(run_id, instance_id)}/logs")

    def project_source(self, name: str, version: str = "") -> MountSource:
        base = shlex.quote(f"/v/{project_snapshot_dir(name)}")
        want = shlex.quote(version.removeprefix("sha256:"))
        found = self._sh(
            f"h={want}; if [ -n \"$h\" ] && [ -d {base}/\"$h\" ]; then echo \"$h\"; else"
            f" c=$(sed 's/^sha256://' {base}/.version 2>/dev/null); [ -n \"$c\" ] && [ -d {base}/\"$c\" ] && echo \"$c\"; fi; true"
        ).strip()
        if version and found != version.removeprefix("sha256:"):
            log.warning("project %s: snapshot %s is not stored, mounting %s", name, version, found or "the unversioned one")
        return self._src(f"{project_snapshot_dir(name)}/{found}" if found else project_snapshot_dir(name))

    def shared_source(self, project: str, name: str) -> MountSource:
        return self._src(shared_dir(project, name))

    def ensure_shared(self, project: str, name: str) -> None:
        d = shlex.quote(f"/v/{shared_dir(project, name)}")
        self._sh(f"mkdir -p {d} && chown {SANDBOX_UID}:{SANDBOX_UID} {d}")

    def tools_source(self) -> MountSource:
        return self._src("tools")

    def job_source(self, key: str) -> MountSource:
        return self._src(job_dir(key))

    def prepare_job(self, key: str, files: dict[str, str]) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "in").mkdir()
            (root / "out").mkdir()
            for name, text in files.items():
                (root / "in" / name).write_text(text, encoding="utf-8")
            paths = [p for p in sorted(root.rglob("*")) if p.is_file()] or []
            self._put_tree(paths, root, f"/v/{job_dir(key)}", mode=0o644)
        d = shlex.quote(f"/v/{job_dir(key)}")
        self._sh(f"mkdir -p {d}/in {d}/out && chown -R {SANDBOX_UID}:{SANDBOX_UID} {d}")

    def read_job_result(self, key: str) -> str | None:
        return self._read(f"{job_dir(key)}/out/result.json")

    def init_run(self, run_id: str, flow_yaml: str, project: str | None) -> str | None:
        w = f"/v/{run_dir(run_id)}"
        snap = shlex.quote(f"/v/{project_snapshot_dir(project)}") if project else None
        # The project snapshot must exist (`myorch project sync`); otherwise every sandbox would fail.
        check = f"[ -d {snap} ] || {{ echo 'project not synced: {project}' >&2; exit 3; }}; " if snap else ""
        script = (
            f"{check}mkdir -p {w}/repo {w}/docs {w}/memory {w}/.claude {w}/.myorch/steps {w}/project {w}/shared"
            f" && cat > {w}/.myorch/flow.yaml"
            f" && {{ [ -f {w}/memory/MEMORY.md ] || printf %s {shlex.quote(MEMORY_SEED)} > {w}/memory/MEMORY.md; }}"
        )
        self._sh(script, stdin=flow_yaml)
        # Only the skeleton itself: repo/ may already hold a large checkout on resume.
        self._sh(
            f"chown {SANDBOX_UID}:{SANDBOX_UID} {w} {w}/repo {w}/docs {w}/memory {w}/memory/MEMORY.md"
            f" {w}/.claude {w}/.myorch {w}/.myorch/steps {w}/.myorch/flow.yaml {w}/project {w}/shared"
        )
        if not project:
            return None
        v = self._read(f"{project_snapshot_dir(project)}/.version")
        return v.strip() if v else "unknown"

    def write_run_file(self, run_id: str, rel_path: str, text: str) -> None:
        p = shlex.quote(f"/v/{run_dir(run_id)}/{rel_path}")
        # The directories it creates belong to the sandbox user too: steps write next to the file
        # (eval's prepare writes docs/eval/requirement.md beside docs/eval/case.json).
        parts = rel_path.split("/")[:-1]
        dirs = " ".join(shlex.quote(f"/v/{run_dir(run_id)}/{'/'.join(parts[:i])}") for i in range(1, len(parts) + 1))
        self._sh(f"mkdir -p \"$(dirname {p})\" && cat > {p} && chown {SANDBOX_UID}:{SANDBOX_UID} {p} {dirs}", stdin=text)

    def prepare_step(self, key: StepKey) -> None:
        d = f"/v/{step_dir(key.run_id, key.step_id)}"
        session = f"/v/{run_dir(key.run_id)}/.claude/{key.step_id}"
        # Subpath mounts fail if the directory does not exist, and the sandbox user
        # (uid 1000) must own it to write. A new attempt starts with a clean record (out/);
        # a new turn keeps it so the agent can pick up where it stopped.
        # logs/ and the session dir are never emptied: logs are named per attempt (turn_log) and are
        # the evidence of what was tried before the retry; sessions have an id per attempt, and a
        # retry after a platform interruption continues the interrupted one (runner._session).
        # A later turn drops the old manifest: it said needs_input or failed, and must not be
        # read again if the agent does not write a new one.
        reset = f"rm -rf {d}/out && " if key.turn == 1 else f"rm -f {d}/out/{MANIFEST_FILE} && "
        self._sh(
            f"{reset}rm -f {d}/out/{QUESTION_FILE}"
            f" && mkdir -p {d}/out {d}/logs {session}"
            f" && chown -R {SANDBOX_UID}:{SANDBOX_UID} {d} {session}"
        )

    def session_files(self, run_id: str, instance_id: str, names: list[str]) -> set[str]:
        wanted = {_session_name(n) for n in names}
        if not wanted:
            return set()
        d = shlex.quote(f"/v/{run_dir(run_id)}/.claude/{instance_id}")
        match = " -o ".join(f"-name {shlex.quote(n)}" for n in sorted(wanted))
        out = self._sh(f"[ -d {d} ] || exit 0; find {d} -type f \\( {match} \\)")
        return {line.rsplit("/", 1)[-1] for line in out.splitlines()} & wanted

    def set_aside_session_file(self, run_id: str, instance_id: str, name: str) -> None:
        d = shlex.quote(f"/v/{run_dir(run_id)}/.claude/{instance_id}")
        self._sh(f"[ -d {d} ] || exit 0; t=$(date -u +%Y%m%dT%H%M%SZ);"
                 f" find {d} -mindepth 1 -name {shlex.quote(_session_name(name))} -prune"
                 ' -exec sh -c \'mv "$1" "$1.replaced-$2"\' _ {} "$t" \\;')

    def read_manifest(self, run_id: str, instance_id: str) -> Manifest | None:
        text = self._read(f"{step_dir(run_id, instance_id)}/out/{MANIFEST_FILE}")
        return parse_manifest(text, instance_id)

    def read_question(self, run_id: str, instance_id: str) -> Question | None:
        return parse_question(self._read(f"{step_dir(run_id, instance_id)}/out/{QUESTION_FILE}"))

    def list_outputs(self, run_id: str, instance_id: str) -> list[str]:
        d = f"/v/{step_dir(run_id, instance_id)}/out"
        out = self._sh(f"cd {d} 2>/dev/null && find . -type f | sed 's|^\\./||' || true")
        return [line for line in out.splitlines() if line]

    def read_log_tail(self, run_id: str, instance_id: str, max_bytes: int = 4000, file: str = "") -> str:
        d = shlex.quote(f"/v/{step_dir(run_id, instance_id)}/logs")
        if file:
            pick = f"f={shlex.quote(_log_name(file))}"
        else:  # the newest by (attempt, turn), like turn_log_order: an old turn-<turn>.log is attempt 0
            pick = ("f=$(ls -1 | sed -n -E 's/^turn-([0-9]+)-([0-9]+)[.]log$/\\1 \\2 &/p; s/^turn-([0-9]+)[.]log$/0 \\1 &/p'"
                    " | sort -n -k1,1 -k2,2 | tail -1 | cut -d' ' -f3)")
        return self._sh(f'cd {d} 2>/dev/null || exit 0; {pick}; [ -n "$f" ] && [ -f "$f" ] && tail -c {int(max_bytes)} "$f" || true')

    async def follow_log(self, run_id: str, instance_id: str, file: str, name: str, max_seconds: int,
                         idle: float = 0.5) -> AsyncIterator[str]:
        """The log file `file` as it grows, from its first byte: a read-only helper container runs `tail -F`.

        Yields text as it arrives, and "" after `idle` seconds without any, so the caller can flush.
        The container is removed when the caller stops reading; `timeout` ends it on its own if the
        worker dies first.
        """
        path = shlex.quote(f"/v/{step_dir(run_id, instance_id)}/logs/{_log_name(file)}")
        await self._remove_container(name)
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--init", "--name", name, "--label", "myorch.follow=1", "--network", "none",
            "-v", f"{self.volume}:/v:ro", self.helper_image,
            "timeout", str(int(max_seconds)), "tail", "-s", "0.25", "-n", "+1", "-F", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(65536), idle)
                except TimeoutError:
                    yield ""
                    continue
                if not chunk:
                    return
                yield decoder.decode(chunk)
        finally:
            await asyncio.shield(self._remove_container(name))
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _remove_container(self, name: str) -> None:
        proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()

    def run_records(self) -> list[tuple[str, str, str]]:
        """(run_id, mandate.json, push-and-pr manifest.json or "") for every run, in one helper call."""
        out = self._sh(
            "cd /v/runs 2>/dev/null || exit 0; for d in */; do r=${d%/}; [ -f \"$r/.myorch/mandate.json\" ] || continue;"
            " echo \"@@RUN $r\"; cat \"$r/.myorch/mandate.json\"; echo; echo @@PR;"
            " cat \"$r/.myorch/steps/push-and-pr/out/manifest.json\" 2>/dev/null; echo; done"
        )
        records = []
        for chunk in out.split("@@RUN ")[1:]:
            head, _, rest = chunk.partition("\n")
            mandate, _, pr = rest.partition("@@PR\n")
            records.append((head.strip(), mandate.strip(), pr.strip()))
        return records

    def review_bundle(self, run_id: str, base: str) -> dict[str, str]:
        """What a reviewer needs from one run, in one helper call: the docs, the Mandate, every step's
        manifest, and `git diff origin/<base>...HEAD` of the run's repository (path -> text)."""
        r = shlex.quote(f"/v/{run_dir(run_id)}")
        b = shlex.quote(f"origin/{base}")
        # The repository was written by agents and by the project's own code: its config must not run
        # anything here (diff.external, textconv, fsmonitor, hooks), and the volume is mounted read-only.
        script = (
            "g() { git -c safe.directory='*' -c core.fsmonitor=false -c core.hooksPath=/dev/null -C repo \"$@\"; };"
            f" cd {r} || exit 0; for f in .myorch/mandate.json $(find docs -type f \\( -name '*.md' -o -name '*.json' \\) 2>/dev/null)"
            " $(ls .myorch/steps/*/out/manifest.json 2>/dev/null); do echo \"@@FILE $f\"; cat \"$f\"; echo; done;"
            f" echo '@@FILE git/diff'; g diff --no-ext-diff --no-textconv --stat {b}...HEAD 2>&1; echo;"
            f" g diff --no-ext-diff --no-textconv {b}...HEAD 2>&1;"
            f" echo '@@FILE git/log'; g log --no-ext-diff --format='%h %an %s' {b}..HEAD 2>&1"
        )
        cmd = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", "-v", f"{self.volume}:/v:ro",
               GIT_IMAGE, "-c", script]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # diffs of any file
        if res.returncode != 0:
            raise RuntimeError(f"storage helper failed ({res.returncode}): {res.stderr.strip()}")
        out: dict[str, str] = {}
        for chunk in res.stdout.split("@@FILE ")[1:]:
            name, _, text = chunk.partition("\n")
            out[name.strip()] = text.rstrip("\n")
        return out

    def seed_repo(self, run_id: str, git_dir: Path, repo: str, base: str) -> str:
        """Put a repository into the run's repo/ from a local clone (its git dir on this host), so
        sync-master only fetches what is new instead of cloning everything from GitHub.

        Only the objects of the clone's refs/remotes/origin/<base> come over: no local branches,
        commits, uncommitted changes, config or hooks. origin points at GitHub. repo/ gets no
        working tree; sync-master checks out and resets to origin/<base> after its fetch.
        Returns the short sha of the copied origin/<base>; "" when repo/ already has a repository.
        """
        w = shlex.quote(f"/v/{run_dir(run_id)}")
        ref = f"refs/remotes/origin/{base}"
        script = (
            f"set -e; cd {w}; [ -d repo/.git ] && exit 0; rm -rf repo.seed; mkdir repo.seed; cd repo.seed;"
            f" git -c init.defaultBranch={shlex.quote(base)} init -q;"
            # The clone is the user's: its config and hooks must not run here (it is read-only anyway).
            f" git -c safe.directory='*' -c core.hooksPath=/dev/null -c protocol.file.allow=always"
            f" fetch -q --no-tags /src {shlex.quote(f'+{ref}:{ref}')};"
            f" git remote add origin {shlex.quote(f'https://github.com/{repo}.git')};"
            f" sha=$(git rev-parse --short {shlex.quote(ref)}); cd ..; mkdir -p repo;"
            # sync-master may have started a clone of its own meanwhile: then that one stays.
            " if [ -d repo/.git ]; then rm -rf repo.seed; exit 0; fi; mv repo.seed/.git repo/.git;"
            f" rm -rf repo.seed; chown -R {SANDBOX_UID}:{SANDBOX_UID} repo;"
            f" echo \"- seed: repo/ 从本机克隆复制（origin/{base} @ $sha），sync-master 只拉增量\" >> memory/MEMORY.md;"
            " echo \"$sha\""
        )
        name = f"myorch-seed-{run_id}"
        cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "--entrypoint", "sh",
               "-v", f"{git_dir}:/src:ro", "-v", f"{self.volume}:/v", GIT_IMAGE, "-c", script]
        try:  # stop before the activity's 15 minutes run out, so no copy keeps going after it
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12 * 60)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            self._sh(f"rm -rf {w}/repo.seed")
            raise RuntimeError("seed helper timed out after 12m") from None
        if res.returncode != 0:
            self._sh(f"rm -rf {w}/repo.seed")
            raise RuntimeError(f"seed helper failed ({res.returncode}): {res.stderr.strip()}")
        return res.stdout.strip()

    def read_file(self, rel_path: str) -> str | None:
        """Read any file on the volume (CLI helper)."""
        return self._read(rel_path)

    def read_bytes(self, rel_path: str, max_bytes: int) -> bytes | None:
        """Read a file as bytes (images for the console), at most max_bytes + 1: a longer result
        means the file is larger than max_bytes. None when there is no such file."""
        p = shlex.quote(f"/v/{rel_path}")
        cmd = [
            "docker", "run", "--rm", "--network", "none", "-v", f"{self.volume}:/v", self.helper_image,
            "sh", "-c", f"[ -f {p} ] || exit 3; head -c {int(max_bytes) + 1} {p}",
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 3:
            return None
        if res.returncode != 0:
            raise RuntimeError(f"storage helper failed ({res.returncode}): {res.stderr.decode(errors='replace').strip()}")
        return res.stdout

    def list_files(self, rel_dir: str, max_depth: int = 3) -> list[str]:
        d = shlex.quote(f"/v/{rel_dir}")
        out = self._sh(
            f"cd {d} 2>/dev/null && find . -maxdepth {int(max_depth)} -type f"
            " -not -path './repo/.git/*' -not -path './repo/node_modules/*' -not -path './.claude/*'"
            " | sed 's|^\\./||' | sort || true"
        )
        return [line for line in out.splitlines() if line]

    def export_run(self, run_id: str, dest: Path) -> int:
        """Copy a run's workspace to a local directory (read-only on the volume). Returns the file count.

        Skips what is large or not meant for humans: git objects, node_modules, build output and
        harness sessions.
        """
        src = shlex.quote(f"/v/{run_dir(run_id)}")
        script = (
            f"[ -d {src} ] || {{ echo 'no such run' >&2; exit 3; }}; cd {src} && tar -c"
            " --exclude=./repo/.git --exclude=./repo/node_modules --exclude=./repo/_site"
            " --exclude=./repo/.jekyll-cache --exclude=./.claude ."
        )
        cmd = [
            "docker", "run", "--rm", "--network", "none", "-v", f"{self.volume}:/v:ro",
            self.helper_image, "sh", "-c", script,
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(f"export failed: {res.stderr.decode(errors='replace').strip()}")
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
            members = [m for m in tar.getmembers() if not m.issym() and not m.islnk()]
            tar.extractall(dest, members=members, filter="data")
            n = sum(1 for m in members if m.isfile())
        return n

    # ---- project snapshots -----------------------------------------------------------

    def project_sync(self, name: str, local_dir: Path, version: str, keep: int = 3, days: int = 30) -> None:
        """Versions are kept while a run may still use them: the newest `keep`, and any written in
        the last `days` days (Temporal keeps finished runs 15 days; a resumed run mounts its own)."""
        base = f"/v/{project_snapshot_dir(name)}"
        b, h = shlex.quote(base), version.removeprefix("sha256:")
        # Before versions, projects/<name>/ held the files themselves: move them into their version's
        # directory, so runs that recorded that version keep finding it.
        self._sh(
            f"mkdir -p {b} && if [ -f {b}/project.json ]; then"
            f" old=$(sed 's/^sha256://' {b}/.version 2>/dev/null); [ -n \"$old\" ] || old=legacy;"
            f" mkdir -p {b}/.migrate && find {b} -mindepth 1 -maxdepth 1 ! -name .version ! -name .migrate -exec mv {{}} {b}/.migrate/ \\;"
            f" && mv {b}/.migrate {b}/\"$old\"; fi")
        if self._sh(f"[ -d {b}/{shlex.quote(h)} ] && echo yes || true").strip() != "yes":  # immutable: never rewritten
            tmp = f"{base}/.tmp-{h}"
            files = [f for f in sorted(local_dir.rglob("*")) if f.is_file()]
            self._put_tree(files, local_dir, tmp, mode=0o644)
            t = shlex.quote(tmp)
            self._sh(f"chown -R {SANDBOX_UID}:{SANDBOX_UID} {t} && find {t} -type f -exec chmod 444 {{}} +"
                     f" && mv {t} {b}/{shlex.quote(h)}")
        self._sh(
            f"printf %s {shlex.quote(version)} > {b}/.version && cd {b} && i=0; for v in $(ls -1t); do"
            f" [ -d \"$v\" ] || continue; i=$((i+1)); [ \"$v\" = {shlex.quote(h)} ] && continue; [ $i -le {int(keep)} ] && continue;"
            f" [ -n \"$(find \"$v\" -maxdepth 0 -mtime -{int(days)})\" ] && continue; rm -rf \"$v\"; done; true")

    # ---- knowledge snapshots (myorch/knowledge.py) ----------------------------------

    def knowledge_source(self, name: str, version: str) -> MountSource:
        return self._src(knowledge_dir(name, version))

    def _knowledge_finish(self, name: str, version: str, keep: int) -> None:
        """Write the snapshot's .meta (time, files, bytes) and keep only the newest versions."""
        d, base = shlex.quote(f"/v/{knowledge_dir(name, version)}"), shlex.quote(f"/v/{knowledge_dir(name)}")
        self._sh(
            f"cd {d} && printf '%s %s %s' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" "
            f"\"$(find . -type f ! -name .meta | wc -l | tr -d ' ')\" \"$(du -sb . | cut -f1)\" > .meta"
            f" && chown -R {SANDBOX_UID}:{SANDBOX_UID} {d} && find {d} -type f -exec chmod 444 {{}} +"
            f" && cd {base} && ls -1t | tail -n +{keep + 1} | xargs -r rm -rf")

    def knowledge_sync(self, name: str, version: str, local_dir: Path, keep: int = 5) -> None:
        files = [f for f in sorted(local_dir.rglob("*")) if f.is_file()]
        self._put_tree(files, local_dir, f"/v/{knowledge_dir(name, version)}", mode=0o644)
        self._knowledge_finish(name, version, keep)

    def knowledge_adopt_job(self, key: str, name: str, version: str, keep: int = 5) -> None:
        src, dest = shlex.quote(f"/v/{job_dir(key)}/out/tree"), shlex.quote(f"/v/{knowledge_dir(name, version)}")
        self._sh(f"test -d {src} && mkdir -p {shlex.quote(f'/v/{knowledge_dir(name)}')} && rm -rf {dest} && mv {src} {dest}")
        self._knowledge_finish(name, version, keep)

    def knowledge_versions(self, name: str) -> list[dict]:
        base = shlex.quote(f"/v/{knowledge_dir(name)}")
        out = self._sh(f"if [ -d {base} ]; then cd {base} && for v in $(ls -1t); do "
                       f"printf '%s %s\\n' \"$v\" \"$(cat \"$v/.meta\" 2>/dev/null)\"; done; fi")
        rows = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 4:
                rows.append({"version": f"sha256:{parts[0]}", "synced_at": parts[1], "files": int(parts[2]), "bytes": int(parts[3])})
        return rows

    def project_list(self) -> list[str]:
        out = self._sh("ls -1 /v/projects 2>/dev/null || true")
        return [line for line in out.splitlines() if line]

    def install_tools(self, local_dir: Path) -> None:
        """Copy helper commands into tools/ on the volume (mounted read-only into sandboxes)."""
        files = [f for f in sorted(local_dir.iterdir()) if f.is_file()]
        self._put_tree(files, local_dir, "/v/tools", mode=0o755)

    def ensure_volume(self) -> None:
        """One-time setup: create the approved volume and hand its root to the sandbox user."""
        exists = subprocess.run(
            ["docker", "volume", "inspect", self.volume], capture_output=True
        ).returncode == 0
        if not exists:
            subprocess.run(
                [
                    "docker", "volume", "create",
                    "--label", "openshell.ai/sandbox-attachable=true",
                    "--label", "openshell.ai/sandbox-attachable-workspace=default",
                    self.volume,
                ],
                check=True, capture_output=True,
            )
        self._sh(f"chown {SANDBOX_UID}:{SANDBOX_UID} /v")

    # ---- helpers -------------------------------------------------------------------

    def _put_tree(self, files: list[Path], root: Path, dest: str, mode: int) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for f in files:
                info = tar.gettarinfo(str(f), arcname=str(f.relative_to(root)))
                info.mode, info.uid, info.gid = mode, SANDBOX_UID, SANDBOX_UID
                with f.open("rb") as fh:
                    tar.addfile(info, fh)
        q = shlex.quote(dest)
        cmd = [
            "docker", "run", "--rm", "-i", "--network", "none", "-v", f"{self.volume}:/v",
            self.helper_image, "sh", "-c",
            f"rm -rf {q} && mkdir -p {q} && tar -x --no-same-owner -C {q}"
            f" && chown -R {SANDBOX_UID}:{SANDBOX_UID} {q} && find {q} -type d -exec chmod 755 {{}} +",
        ]
        res = subprocess.run(cmd, input=buf.getvalue(), capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(f"copy to {dest} failed: {res.stderr.decode(errors='replace')}")

    def _read(self, rel_path: str) -> str | None:
        p = shlex.quote(f"/v/{rel_path}")
        out = self._sh(f"if [ -f {p} ]; then cat {p}; else echo __MISSING__; fi")
        return None if out.strip() == "__MISSING__" else out

    def _sh(self, script: str, stdin: str | None = None) -> str:
        cmd = [
            "docker", "run", "--rm", "-i", "--network", "none",
            "-v", f"{self.volume}:/v", self.helper_image, "sh", "-c", script,
        ]
        # errors="replace": file contents may be cut mid-character (tail -c) or not be UTF-8 at all.
        res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, errors="replace")
        if res.returncode != 0:
            raise RuntimeError(f"storage helper failed ({res.returncode}): {res.stderr.strip()}")
        return res.stdout
