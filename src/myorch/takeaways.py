"""Which takeaways learn has already learned from, per project. Local only: myorch-data.

development commits each run's task information and takeaway (projects with
flows.development.takeaway) to the project's repository, in
myorch/<YYYY-MM-DD>-<run_id>/ on the run's branch, so they arrive on the base branch with the merged
PR. learn reads them from there (its collect step) and skips the run ids recorded here:

    myorch-data/learn/<project>/takeaways.json   {"learned": {"<run_id>": "<learn run id>", ...}}

Starting a learn run writes the learned run ids into its workspace (docs/learn/takeaways-learned.json);
its takeaways-learned host action adds the ones it took in. Nothing is written to the company
repository or to myorch-knowledge.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from myorch.core.types import ID_PATTERN, check_id

LEARNED_FILE = "docs/learn/takeaways-learned.json"  # in a learn run: run ids to skip
PICKED_FILE = "docs/learn/takeaways.json"  # in a learn run: what collect took in ({"picked": [{"run": ...}]})


# A run id, as learn's collect matches it in myorch/<date>-<run_id>/ (the same pattern there).
RUN_ID = ID_PATTERN.pattern.removeprefix("^").removesuffix("$")


def ledger(data_root: Path, project: str) -> Path:
    return data_root / "learn" / check_id(project, "project name") / "takeaways.json"


def learned(data_root: Path | None, project: str | None) -> dict[str, str]:
    """run id -> the learn run that took its takeaway in."""
    if data_root is None or not project:
        return {}
    try:
        data = json.loads(ledger(data_root, project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    got = data.get("learned") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


@contextmanager
def _locked(path: Path):
    """One writer at a time (two learn runs of a project may end together): read, merge, write under it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def record(data_root: Path, project: str, run_ids: list[str], by: str) -> list[str]:
    """Add run ids learned by the learn run `by`. Returns the ones that were new (idempotent). Ids that
    are not run ids are skipped, never raised on: one odd directory must not stop every later learn."""
    ids = [r for r in dict.fromkeys(str(r) for r in run_ids) if ID_PATTERN.match(r)]
    path = ledger(data_root, project)
    with _locked(path):
        have = learned(data_root, project)  # read again under the lock: another learn may have written
        new = [r for r in ids if r not in have]
        if not new:
            return []
        have.update({r: by for r in new})
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".takeaways-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"learned": have}, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)  # never a half-written ledger
    return new


def learn_files(data_root: Path | None, project: str | None) -> dict[str, str]:
    """For a learn run's workspace: the run ids whose takeaways are already learned (a list)."""
    if not project:
        return {}
    return {LEARNED_FILE: json.dumps(sorted(learned(data_root, project)))}
