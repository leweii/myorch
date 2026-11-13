"""Eval: score reviewers on real cases, so a change to their prompt or model is measured.

A case is a change a past run reviewed (its round-1 diff) plus the findings a person confirmed as
real. Cases come from the history (collect): every development / review run whose round-1
verified.json has Important or Nit findings becomes a candidate; the person keeps the findings
that are real problems (console, Flows → Eval set), and only then the case counts.

The change is replayed from the source run's own workspace (the project's eval flow, flows/eval.yaml, mounts it read-only
with observe_runs), so commits that never reached GitHub work too. The reviewers run through
lib/review-loop.yaml unchanged (one round, no fix); the score step matches their verified.json
against the kept findings (same file, line within LINE_SLACK). `myorch eval run` starts one run
per case and variant (depth); `myorch eval report` puts them side by side.

Cases from a postmortem (the project's postmortem flow, flows/postmortem.yaml) have origin "git": the change is a commit on the
project's repository, not a run's round 1, so `prepare` clones it from GitHub instead. Their id is
the postmortem run's id; the postmortem starts their replays itself (engines/temporal/replay.py).

State: myorch-data/evals/cases/<id>.json (status draft / pending / confirmed / rejected; a draft is
a postmortem case whose findings are not written yet, and is not shown for confirming).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from myorch.core.flow import role_of

LINE_SLACK = 10
GIT_IMAGE = "openshell-claude-code:latest"  # any image with git; the volume is mounted read-only
COUNTED = ("Important", "Nit")  # Pre-existing findings are not part of the change


def cases_dir(data_root: Path) -> Path:
    return data_root / "evals" / "cases"


def _json(text: str | None, default):
    try:
        v = json.loads(text) if text else default
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def _git_info(volume: str, run: str, base_ref: str, head: str) -> dict[str, Any]:
    """Merge base, the commits on the run's branch, and the head to replay (read-only, no network)."""
    script = (
        f"g() {{ git -c safe.directory='*' -C /v/runs/{run}/repo \"$@\"; }}; "
        f"h={head or '$(g rev-list --reverse origin/' + base_ref + '..HEAD | head -1)'}; "
        f"[ -n \"$h\" ] || h=$(g rev-parse HEAD); echo \"head=$(g rev-parse $h)\"; "
        f"echo \"base=$(g merge-base origin/{base_ref} $h)\"; g log --format='commit=%h %s' origin/{base_ref}..HEAD"
    )
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/v:ro", "--entrypoint", "sh",
                        GIT_IMAGE, "-c", script], capture_output=True, text=True)
    info: dict[str, Any] = {"commits": []}
    for line in r.stdout.splitlines():
        k, _, v = line.partition("=")
        if k == "commit":
            info["commits"].append(v)
        elif k in ("head", "base") and v:
            info[k] = v
    return info


def _project_of(mandate: dict, kb_root: Path) -> tuple[str | None, str, str]:
    """(project, repo, base branch) of a run: from its mandate; old runs named only the repo."""
    from myorch import project as projects

    name = (mandate.get("project") or {}).get("name")
    req = mandate.get("request") or {}
    if name:
        p = projects.load_project(kb_root, name)
        return name, p.repo, p.base
    repo = req.get("repo", "")
    for n in projects.list_projects(kb_root):
        try:
            p = projects.load_project(kb_root, n)
        except Exception:
            continue
        if p.repo == repo:
            return n, p.repo, req.get("base") or p.base
    return None, repo, req.get("base") or "main"


def collect(store, kb_root: Path, data_root: Path) -> list[dict[str, Any]]:
    """Candidates from the history; cases already decided are kept as they are. Returns the new ones."""
    out = store._sh("cd /v/runs && find . -path '*/docs/review/round-1/verified.json' -type f 2>/dev/null | sort; true")
    d = cases_dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    new = []
    for line in out.split():
        run = line.removeprefix("./").split("/")[0]
        if (d / f"{run}.json").is_file():
            continue
        found = [f for f in _json(store.read_file(f"runs/{run}/docs/review/round-1/verified.json"), [])
                 if isinstance(f, dict) and f.get("severity") in COUNTED and f.get("file")]
        if not found:
            continue
        mandate = _json(store.read_file(f"runs/{run}/.myorch/mandate.json"), {})
        project, repo, base = _project_of(mandate, kb_root)
        if not project:
            continue  # no pack to review it with
        head = (store.read_file(f"runs/{run}/docs/ci/round-1/head.txt") or "").strip()
        flow = (mandate.get("flow") or {}).get("name") or run.rsplit("-", 1)[0]
        if not head and role_of(flow, (mandate.get("flow") or {}).get("role")) == "review":
            m = store.read_manifest(run, "fetch-pr")
            head = (m.outputs.get("head_sha") or "") if m else ""
            base = (m.outputs.get("base_ref") or base) if m else base
        req_file = "docs/requirements.md" if store.read_file(f"runs/{run}/docs/requirements.md") else "docs/pr.md"
        info = _git_info(store.volume, run, base, head)
        if not info.get("head") or not info.get("base"):
            continue
        case = {"id": run, "source_run": run, "flow": flow, "project": project, "repo": repo, "base_ref": base,
                "base_sha": info["base"], "head_sha": info["head"], "commits": info["commits"],
                "head_guessed": not bool(head), "requirement": store.read_file(f"runs/{run}/{req_file}") or "",
                "findings": [{k: f.get(k) for k in ("severity", "class", "title", "file", "line", "locations", "evidence")}
                             for f in found],
                "status": "pending", "keep": [], "collected_at": time.time()}
        (d / f"{run}.json").write_text(json.dumps(case, ensure_ascii=False, indent=1))
        new.append(case)
    return new


VISIBLE = ("origin", "source_run", "flow", "repo", "base_ref", "base_sha", "head_sha", "requirement")


def visible(case: dict[str, Any]) -> dict[str, Any]:
    """What the replay may see: the change, never the findings (docs/eval/case.json)."""
    return {k: case.get(k) for k in VISIBLE if k in case}


def save_case(data_root: Path, case: dict[str, Any]) -> Path:
    from myorch.core.types import check_id

    d = cases_dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{check_id(case['id'], 'case id')}.json"
    p.write_text(json.dumps(case, ensure_ascii=False, indent=1))
    return p


def load_case(data_root: Path, case_id: str) -> dict[str, Any]:
    from myorch.core.types import check_id

    p = cases_dir(data_root) / f"{check_id(case_id, 'case id')}.json"
    return _json(p.read_text() if p.is_file() else None, {})


def list_cases(data_root: Path, status: str | None = None) -> list[dict[str, Any]]:
    d = cases_dir(data_root)
    rows = [_json(p.read_text(), {}) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
    return [c for c in rows if c and (status is None or c.get("status") == status)]


def decide(data_root: Path, case_id: str, keep: list[int], by: str) -> dict[str, Any]:
    """The person's verdict: the findings that are real (keep), or none (the case is rejected)."""
    case = load_case(data_root, case_id)
    if not case or case.get("status") == "draft":
        raise ValueError(f"No such case: {case_id}")
    keep = sorted({int(i) for i in keep if 0 <= int(i) < len(case["findings"])})
    case.update(status="confirmed" if keep else "rejected", keep=keep, decided_by=by, decided_at=time.time())
    save_case(data_root, case)
    return case


def expected(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [case["findings"][i] for i in case.get("keep") or []]


def _locations(f: dict) -> list[tuple[str, int | None]]:
    locs = [(l.get("file"), l.get("line")) for l in (f.get("locations") or []) if isinstance(l, dict) and l.get("file")]
    return locs or [(f.get("file"), f.get("line"))]


def matches(found: dict, want: dict) -> bool:
    for fa, la in _locations(found):
        for fb, lb in _locations(want):
            if fa and fa == fb and (la is None or lb is None or abs(int(la) - int(lb)) <= LINE_SLACK):
                return True
    return False


def score(found: list[dict], want: list[dict]) -> dict[str, Any]:
    """caught / missed among the kept findings; other Important or Nit findings are extra (they may be
    false positives, or real problems nobody marked)."""
    found = [f for f in found if isinstance(f, dict) and f.get("severity") in COUNTED]
    caught = [w for w in want if any(matches(f, w) for f in found)]
    extra = [f for f in found if not any(matches(f, w) for w in want)]
    return {"expected": len(want), "caught": len(caught), "missed": len(want) - len(caught),
            "extra": len(extra), "extra_important": sum(1 for f in extra if f.get("severity") == "Important"),
            "caught_titles": [w.get("title") for w in caught],
            "missed_titles": [w.get("title") for w in want if w not in caught]}


def report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per variant: cases, expected, caught, missed, recall, extra. rows: {variant, case, outputs}."""
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        v = by.setdefault(r["variant"], {"variant": r["variant"], "cases": 0, "expected": 0, "caught": 0, "missed": 0,
                                         "extra": 0, "extra_important": 0})
        o = r["outputs"]
        v["cases"] += 1
        for k in ("expected", "caught", "missed", "extra", "extra_important"):
            v[k] += int(o.get(k) or 0)
    for v in by.values():
        v["recall"] = round(v["caught"] / v["expected"], 2) if v["expected"] else None
    return sorted(by.values(), key=lambda v: v["variant"])
