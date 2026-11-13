"""Reviewer eval (myorch/evals.py): matching findings, scoring, the comparison, the person's verdict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myorch import evals

WANT = [{"severity": "Important", "title": "Word count unit shown as words", "file": "_layouts/home.html", "line": 40},
        {"severity": "Nit", "title": "Missing tests", "file": "_includes/x.html", "line": 5,
         "locations": [{"file": "_includes/x.html", "line": 5}, {"file": "_includes/y.html", "line": 90}]}]


def test_findings_match_by_file_and_nearby_line():
    assert evals.matches({"file": "_layouts/home.html", "line": 46}, WANT[0])
    assert not evals.matches({"file": "_layouts/home.html", "line": 80}, WANT[0])
    assert not evals.matches({"file": "_layouts/post.html", "line": 40}, WANT[0])
    assert evals.matches({"locations": [{"file": "_includes/y.html", "line": 95}]}, WANT[1])  # any location
    assert evals.matches({"file": "_layouts/home.html"}, WANT[0])  # no line: the file is enough


def test_score_counts_caught_missed_and_extra():
    found = [{"severity": "Important", "file": "_layouts/home.html", "line": 41, "title": "Wrong unit"},
             {"severity": "Important", "file": "a.js", "line": 3, "title": "Another problem"},
             {"severity": "Pre-existing", "file": "_includes/x.html", "line": 5}]  # not counted
    s = evals.score(found, WANT)
    assert (s["expected"], s["caught"], s["missed"], s["extra"], s["extra_important"]) == (2, 1, 1, 1, 1)
    assert s["missed_titles"] == ["Missing tests"]


def test_report_compares_variants():
    rows = [{"variant": "quick", "outputs": {"expected": 2, "caught": 1, "missed": 1, "extra": 0}},
            {"variant": "quick", "outputs": {"expected": 1, "caught": 0, "missed": 1, "extra": 2}},
            {"variant": "standard", "outputs": {"expected": 3, "caught": 3, "missed": 0, "extra": 1}}]
    q, s = evals.report(rows)
    assert (q["variant"], q["cases"], q["recall"], q["extra"]) == ("quick", 2, 0.33, 2)
    assert (s["variant"], s["recall"]) == ("standard", 1.0)


def test_the_person_keeps_real_findings_or_rejects_the_case(tmp_path: Path):
    d = evals.cases_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "development-1.json").write_text(json.dumps({"id": "development-1", "findings": WANT, "status": "pending", "keep": []}))
    c = evals.decide(tmp_path, "development-1", [1, 1, 7], "jakobhe")  # duplicates and out-of-range dropped
    assert (c["status"], c["keep"], c["decided_by"]) == ("confirmed", [1], "jakobhe")
    assert evals.expected(c) == [WANT[1]]
    assert evals.decide(tmp_path, "development-1", [], "jakobhe")["status"] == "rejected"
    assert [x["id"] for x in evals.list_cases(tmp_path, "rejected")] == ["development-1"]
    with pytest.raises(ValueError):
        evals.decide(tmp_path, "nope", [0], "jakobhe")
