"""Flow inputs: what each one is (its spec) and the rules every entry point applies before a run
starts. The CLI, the relay (console, channels) and the console form all read the same spec, so a
rule lives in the flow file, not in code that knows input names.

An input is "string" or a spec:

    type      string | text | select | ticket | run | message | hidden
    label     the form label;  hint: the placeholder
    more      the console folds it under More options (rarely used inputs); a required one stays in sight
    required  the run does not start without it
    default   goes into the flow's `defaults`
    options   select: values, or {value, label}
    prefix    ticket: "MD-"; MD-18700 and 18700 are both stored as 18700
    role      run: the role of the runs to choose from
    empty     run: the text of the "none" choice
    option    run: how a choice reads; " · "-separated parts, a part whose fields are all
              empty is left out. Fields: {run_id} {status} {pr} {inputs.<name>}
    carry     run: inputs copied from the chosen run when not given (e.g. [ticket])
    continue  run: the run continues the chosen one; {label, note, hint} for the console's
              "continue" button, the note shown above the form and the message placeholder
    from_message  message: "text" (default, the whole first message) or "first_line"

A flow may also say `require_one_of: [[commit, bad]]`: at least one of each group.

Labels: every run carries the Jira tickets it is about, so a run started from the CLI with a run id
or a list of tickets can still be found by its ticket (`labels`, below).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# What each role is called in the console, what it does, and its place in the task list. A flow
# file may override label and hint (`label:`, `hint:` at its top level).
ROLE_INFO: dict[str, dict[str, str]] = {
    "development": {"label": "Development", "hint": "From requirement to PR: requirement analysis, technical design, implementation, review by several reviewers, risk approval. The branch is pushed and the PR opened only after approval."},
    "review": {"label": "Review PR", "hint": "Review an existing PR. The verdict is shown only in the console."},
    "postmortem": {"label": "Postmortem", "hint": "Postmortem: a problem already got into the code. Find the change that introduced it and the layer that should have caught it, propose knowledge changes, and generate an eval case. Does not change project code."},
    "sweep": {"label": "Find bugs", "hint": "Find bugs in existing code, a batch of files at a time; produces only a daily report."},
    "learn": {"label": "Learn", "hint": "Distil knowledge from past runs: improve guide/, record decisions people made, add deterministic checks."},
    "eval": {"label": "Eval", "hint": "Replay an eval case and score the reviewers."},
    "onboard": {"label": "Onboard project", "hint": "Onboard a new project: analyse the repository and propose changes to the project pack."},
}
DEFAULT_ROLE = "development"  # the task the console selects first


def role_view(role: str | None, doc: dict[str, Any]) -> dict[str, Any]:
    """How the console shows a flow: label, hint, order in the task list, default task or not."""
    info = ROLE_INFO.get(role or "", {})
    order = list(ROLE_INFO).index(role) if role in ROLE_INFO else len(ROLE_INFO)
    return {"label": doc.get("label") or info.get("label") or str(doc.get("flow") or ""),
            "hint": doc.get("hint") or info.get("hint") or "", "order": order, "default": role == DEFAULT_ROLE}


def spec(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {"type": "string"}


def specs(declared: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(k): spec(v) for k, v in (declared or {}).items()}


def normalize(declared: dict[str, Any], inputs: dict[str, str]) -> None:
    """Values as the flow wants them: a ticket input with a prefix keeps only the number
    (MD-18700 and 18700 both become 18700; the branch template adds MD-)."""
    for name, s in specs(declared).items():
        if s.get("type") == "ticket" and s.get("prefix") and inputs.get(name):
            v = inputs[name].strip()
            if v.upper().startswith(str(s["prefix"]).upper()):
                v = v[len(s["prefix"]):]
            inputs[name] = v


def message_input(declared: dict[str, Any]) -> str | None:
    return next((n for n, s in specs(declared).items() if s.get("type") == "message"), None)


def fill_from_message(declared: dict[str, Any], inputs: dict[str, str], text: str) -> None:
    """The first message fills the flow's message input, unless the caller gave a value."""
    name, text = message_input(declared), (text or "").strip()
    if not name or not text or inputs.get(name):
        return
    if spec(declared[name]).get("from_message") == "first_line":
        first = text.splitlines()[0].strip()
        inputs[name] = first if len(first) <= 120 else first[:119] + "…"
    else:
        inputs[name] = text


def run_inputs(declared: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The inputs that name an earlier run (type: run)."""
    return {n: s for n, s in specs(declared).items() if s.get("type") == "run"}


def check(declared: dict[str, Any], inputs: dict[str, str], require_one_of: list[list[str]] | None = None) -> None:
    """The flow's own rules: required inputs and require_one_of groups (after fill_from_message).
    Raises ValueError."""
    s = specs(declared)
    label = lambda n: s.get(n, {}).get("label") or n  # noqa: E731
    missing = [label(n) for n, x in s.items() if x.get("required") and not (inputs.get(n) or "").strip()]
    if missing:
        raise ValueError(f"Fill in first: {', '.join(missing)}")
    for group in require_one_of or []:
        if not any((inputs.get(n) or "").strip() for n in group):
            raise ValueError(f"Fill in at least one of: {' or '.join(label(n) for n in group)}")


# ---- labels ------------------------------------------------------------------------------------
# A label is a short tag a run can be found by, mostly a Jira key (MD-18700). Computed when the run
# starts (stored in the Mandate and run_started) and again when runs are listed, so runs started
# before labels existed are found by the same rules (myorch/runs.py).

JIRA_KEY = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9]+-[0-9]+(?![A-Za-z0-9_])")
NOT_TICKETS = frozenset({"UTF", "SHA", "ISO", "RFC", "HTTP", "TLS", "SSL", "UTC", "GMT", "CVE"})  # UTF-8, SHA-256 …
MAX_LABELS, MAX_LABEL_CHARS = 20, 64
FREE_TEXT = ("message", "text", "hidden", "select")  # whole sentences or fixed choices: not scanned for keys


def jira_keys(text: str) -> list[str]:
    """Jira keys written in a value ("MD-1,MD-2", a branch name MD-18700-fix …), upper case only so
    run ids (ck-development-1f29e33a) and words do not count."""
    return [k for k in JIRA_KEY.findall(text or "") if k.split("-", 1)[0] not in NOT_TICKETS]


def clean_labels(labels: Iterable[Any]) -> list[str]:
    """Stripped, Jira keys in upper case, no empties or repeats, in their first order; capped."""
    out: list[str] = []
    for raw in labels:
        v = " ".join(str(raw or "").split())[:MAX_LABEL_CHARS]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+-[0-9]+", v):
            v = v.upper()
        if v and v.lower() not in {x.lower() for x in out}:
            out.append(v)
    return out[:MAX_LABELS]


def input_labels(declared: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """The labels a run's inputs give: a ticket input as a key (with its prefix: 18700 -> MD-18700),
    and the Jira keys in plain string inputs (tickets: "MD-1,MD-2"). Inputs the flow does not declare
    count as plain strings. Run ids in inputs are followed elsewhere (myorch/runs.py)."""
    s, out = specs(declared), []
    for name, value in (inputs or {}).items():
        v, kind = str(value or "").strip(), s.get(name, {}).get("type", "string")
        if not v or kind in FREE_TEXT:
            continue
        if kind == "ticket":
            keys = jira_keys(v.upper())
            prefix = str(s[name].get("prefix") or "")
            out += keys or [v if v.upper().startswith(prefix.upper()) else prefix + v]
        else:
            out += jira_keys(v)
    return out


def start_labels(declared: dict[str, Any], inputs: dict[str, Any], explicit: Iterable[Any] = ()) -> list[str]:
    """The labels a run starts with: the caller's own, then those its inputs give."""
    return clean_labels([*explicit, *input_labels(declared, inputs)])


def label_matches(query: str, run_id: str, labels: Iterable[str]) -> bool:
    """A search: a label equal to the query (any case; a bare number finds MD-<number>), or the run id
    containing it (1f29e33a finds ck-development-1f29e33a). An empty query matches everything."""
    q = (query or "").strip().lower()
    if not q:
        return True
    ls = [str(x).lower() for x in labels]
    return q in ls or (q.isdigit() and any(x.endswith(f"-{q}") for x in ls)) or q in run_id.lower()
