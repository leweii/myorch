import pytest

from myorch.ui.app import render_md
from myorch.ui.service import _safe_rel, check_run_id


def test_markdown_never_emits_raw_html_or_script_links():
    out = render_md("<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n[a](javascript:alert(1)) [b](https://ok.example)")
    assert "<script" not in out and "<img" not in out
    assert 'href="#"' in out and 'href="https://ok.example"' in out


@pytest.mark.parametrize("path", ["../x", "docs/../../etc", "", "/", "docs//a.md"])
def test_paths_stay_inside_the_workspace(path):
    with pytest.raises(ValueError):
        _safe_rel(path)


def test_relative_paths_are_kept():
    assert _safe_rel("/docs/review/round-1/summary.md") == "docs/review/round-1/summary.md"


@pytest.mark.parametrize("run_id", ["../runs", "a/b", "", "x y"])
def test_run_ids_are_plain_names(run_id):
    with pytest.raises(ValueError):
        check_run_id(run_id)


def test_valid_run_ids():
    assert check_run_id("development-blog24-issue24") == "development-blog24-issue24"
    assert check_run_id("review.r1.x2") == "review.r1.x2"


def test_images_of_a_run_load_from_its_workspace():
    from myorch.ui.app import run_md

    text = ("![shot](attachments/a.png) ![up](../../../x.png) ![svg](attachments/b.svg) [js](javascript:alert(1))\n\n"
            "- a.png → docs/external/attachments/301-a.png\n- `repo/ui/logo.JPG`\n- docs/notes.md\n")
    out = run_md(text, "ck-development-1", "docs/external/ticket.md")
    raw = "/api/runs/ck-development-1/raw?path="
    assert f'src="{raw}docs/external/attachments/a.png"' in out
    assert f'<a href="{raw}docs/external/attachments/301-a.png" target="_blank" rel="noopener">docs/external/attachments/301-a.png</a>' in out
    assert f'href="{raw}repo/ui/logo.JPG"' in out
    assert 'src="#"' in out and out.count(raw) == 3  # outside the workspace and SVG are not served
    assert 'href="#"' in out and "javascript" not in out.split('href="#"')[0][-20:]
    assert f'"{raw}docs/notes.md"' not in out  # only images are linked


def test_the_console_serves_only_images_of_bounded_size(tmp_path):
    import asyncio

    from myorch.ui.service import MAX_IMAGE_BYTES, Service

    class Relay:
        client = store = None

        async def read_bytes(self, run_id, rel, max_bytes):
            return {"docs/a.png": b"\x89PNG", "docs/big.jpg": b"x" * (max_bytes + 1)}.get(rel)

    svc = Service(Relay(), tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")
    assert asyncio.run(svc.read_image("r1", "docs/a.png")) == (b"\x89PNG", "image/png")
    assert asyncio.run(svc.read_image("r1", "docs/none.png")) is None
    for path in ("docs/big.jpg", "docs/x.svg", "docs/a.md", "../a.png"):
        with pytest.raises(ValueError):
            asyncio.run(svc.read_image("r1", path))
    assert MAX_IMAGE_BYTES >= 5_000_000  # fetch-ticket saves attachments up to 5 MB
