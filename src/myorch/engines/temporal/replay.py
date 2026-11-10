"""Start a reviewer-eval replay (the project's eval flow, flows/eval.yaml) on a case (myorch/evals.py). Used by `myorch eval
run` and, through the worker's launch callback, by the postmortem's host action."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio.exceptions import WorkflowAlreadyStartedError

from myorch import evals
from myorch.engines.temporal import client as tc
from myorch.flowfile import find_flow, read_flow_file


async def start_replay(client, config: Path, kb_root: Path, case: dict[str, Any], depth: str, by: str,
                       source: str, run_id: str | None = None) -> str:
    """Start the project's eval flow on a case (with the project's newest knowledge). A given run_id makes it
    idempotent: an already started run is returned as it is."""
    from myorch.cli import _load_project

    path = find_flow(config / "flows", "eval", case["project"])  # the eval flow for the project (eval.yaml)
    inputs = {"case": case["id"], "depth": depth}
    pub = _load_project(kb_root, case["project"], {}, path)
    try:
        return await tc.start_run(client, read_flow_file(path, inputs, pub), inputs, by, [], pub, run_id=run_id,
                                  source=source, knowledge=[], labels=[str(case["ticket"])] if case.get("ticket") else [],
                                  files={"docs/eval/case.json": json.dumps(evals.visible(case), ensure_ascii=False)})
    except WorkflowAlreadyStartedError:
        assert run_id
        return run_id
