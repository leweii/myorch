"""The console's web server: the page and its JSON API.

There is no agent in the console: it is a workbench where each run opens as a tab holding its
conversation, and what the user writes goes verbatim to the run (the first message is the request;
later ones are replies to the step that asked). Approvals are buttons.

Local only (127.0.0.1). Defences, since run content is untrusted and the page can approve:
- Host must be localhost/127.0.0.1 (DNS rebinding).
- Every POST needs the X-Myorch-UI header, which a cross-site form or fetch cannot send without a
  CORS preflight we never answer (CSRF).
- Markdown is rendered without raw HTML and with only http(s)/relative links; the page's CSP allows
  scripts from /static only, so injected markup cannot run code or press a button.
"""

from __future__ import annotations

import asyncio
import getpass

import json
import posixpath
import re
from html import escape, unescape
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import markdown
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from myorch.channels.http import labels_of
from myorch.ui.flowgraph import FlowDefs
from myorch.ui.service import Service, image_type

STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")


def _blank_before_lists(text: str) -> str:
    """Agents write a list right under a line of text; Markdown needs a blank line before it."""
    out: list[str] = []
    for line in (text or "").splitlines():
        if LIST_ITEM.match(line) and out and out[-1].strip() and not LIST_ITEM.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


# A link or image address without a scheme, not absolute and not in-page: a path relative to the file.
RELATIVE_URL = re.compile(r'(href|src)="(?![a-zA-Z][a-zA-Z0-9+.-]*:|#|/)([^"]*)"')
# A workspace path to an image written as plain text (ticket.md lists its attachments that way). Not
# inside an attribute value: those start right after a quote, = or /.
IMAGE_PATH = re.compile(r'(?<![\w/"\'=.-])((?:docs|repo)/[\w./-]+?\.(?:png|jpe?g|gif|webp))(?![\w/-])', re.I)


def raw_url(rid: str, path: str) -> str:
    return f"/api/runs/{quote(rid, safe='')}/raw?path={quote(path, safe='/')}"


def render_md(text: str, image: Callable[[str, bool], str | None] | None = None) -> str:
    """Markdown to HTML without raw HTML. `image(path, relative)` turns a path to an image into the
    address the page loads it from (None: leave it); relative paths are relative to the file."""
    text = _blank_before_lists(text)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    md.preprocessors.deregister("html_block")  # raw HTML is shown as text, never as markup
    md.inlinePatterns.deregister("html")
    out = md.convert(text or "")
    if image:
        def relative(m: re.Match) -> str:
            url = image(unescape(m.group(2)), True)
            return f'{m.group(1)}="{escape(url)}"' if url else m.group(0)

        def plain(m: re.Match) -> str:
            url = image(m.group(1), False)
            return f'<a href="{escape(url)}" target="_blank" rel="noopener">{m.group(1)}</a>' if url else m.group(0)

        out = RELATIVE_URL.sub(relative, out)
        out = re.sub(r"(>)([^<]+)", lambda m: m.group(1) + IMAGE_PATH.sub(plain, m.group(2)), out)  # text only
    # Only http(s), relative and in-page links; nothing like javascript: or data:.
    return re.sub(r'(href|src)="(?!https?://|#|/|\.)[^"]*"', r'\1="#"', out)


def run_md(text: str, rid: str, path: str) -> str:
    """A Markdown file of a run: its images (relative to the file, or workspace paths in the text)
    load from the run's workspace."""
    base = posixpath.dirname(path)

    def image(p: str, relative: bool) -> str | None:
        p = p.split("#", 1)[0].split("?", 1)[0]
        full = posixpath.normpath(posixpath.join(base, p) if relative else p)
        if full.startswith(("..", "/")) or not image_type(full):
            return None
        return raw_url(rid, full)

    return render_md(text, image)


def create_app(*, client, store, config: Path, kb_root: Path, data_root: Path, port: int,
               relay=None, watchdog=None) -> Starlette:
    """The console: the web adapter of the channel layer. `myorch serve` passes its relay and its
    watchdog; the console reads and writes runs only through the relay."""
    user = getpass.getuser()
    if relay is None:  # standalone (tests): a relay of its own
        from myorch.channels.relay import Relay
        from myorch.storage.events import EventLog

        relay = Relay(client, store, EventLog(data_root / "events.db"), config, kb_root)
    svc = Service(relay, config, kb_root, user, log_dir=data_root / "ui" / "runs")

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    async def body_of(req: Request) -> dict:
        try:
            b = await req.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    async def index(_: Request) -> Response:
        # Versioned asset URLs: after an update the browser loads the new scripts, not its cached copy.
        html = (STATIC / "index.html").read_text()
        for name in re.findall(r'/static/([\w./-]+\.(?:js|css))"', html):
            v = int((STATIC / name).stat().st_mtime)
            html = html.replace(f'/static/{name}"', f'/static/{name}?v={v}"')
        return Response(html, media_type="text/html")

    async def meta(_: Request) -> Response:
        try:
            seq = await asyncio.to_thread(relay.events_log.last_seq)
        except Exception:  # no event log yet
            seq = 0
        return JSONResponse({"user": user, "data": str(data_root), "seq": seq})

    async def runs(req: Request) -> Response:
        if req.method == "POST":  # start: the first message is the request, saved verbatim
            b = await body_of(req)
            flow, project = str(b.get("flow") or ""), (str(b.get("project") or "") or None)
            inputs = {str(k): str(v) for k, v in (b.get("inputs") or {}).items()}
            try:
                kn = b.get("knowledge")  # names to mount; absent: the project's defaults
                run_id = await svc.start(flow, project, inputs, str(b.get("message") or ""),
                                         [str(x) for x in kn] if isinstance(kn, list) else None, labels_of(b))
            except (ValueError, FileNotFoundError) as e:
                return err(str(e))
            return JSONResponse({"run_id": run_id})
        q = req.query_params
        data = await svc.runs(limit=int(q.get("limit", 60)), project=q.get("project") or None,
                              label=q.get("label") or q.get("q") or None)
        if watchdog is not None:  # steps that are alive but not getting anywhere (myorch/watchdog.py)
            live = {r["run_id"]: r for r in data["runs"] if r["status"] == "RUNNING"}
            data["inbox"] += [{"kind": "watchdog", "run_id": a["run_id"], "step": a["instance"], "alert": a["id"],
                               "text": f"{a['label']}：{a['reason']}", "project": live[a["run_id"]].get("project"),
                               "flow": live[a["run_id"]].get("flow")} for a in watchdog.alerts() if a["run_id"] in live]
        return JSONResponse(data)

    async def run_detail(req: Request) -> Response:
        rid = req.path_params["rid"]
        try:
            d = await svc.run_detail(rid)
        except ValueError as e:
            return err(str(e))
        if not d:
            why = svc.state_errors.pop(rid, "")
            return err(f"读不到这个运行的状态：{why}" if why else "运行不存在，或者还没开始。", 404)
        from myorch.core.flow import role_of

        d["role"] = role_of(d.get("flow") or "", d.get("role"))  # what the flow does: the page decides by role
        d["labels"] = await svc.run_labels(rid)
        d["conversation"] = await svc.conversation(rid, d)
        if watchdog is not None and d["status"] == "running":
            items = d["conversation"]["items"]
            for a in watchdog.alerts(rid):
                items.append({"kind": "watchdog", "step": a["instance"], "alert": a["id"], "label": a["label"],
                              "reason": a["reason"], "evidence": a["evidence"], "at": a["at"]})
        d["can_request_changes"] = svc.can_request_changes
        return JSONResponse(d)

    async def act(req: Request) -> Response:
        """reply / approve / reject / cancel, done by the user on the page."""
        rid, action = req.path_params["rid"], req.path_params["action"]
        b = await body_of(req)
        step, text = str(b.get("step") or ""), str(b.get("text") or "")
        try:
            if action == "reply":
                if not text.strip():
                    return err("回复不能为空。")
                await svc.reply(rid, step, text)
            elif action == "approve":
                await svc.approve(rid, step, text)
            elif action == "reject":
                await svc.reject(rid, step, text)
            elif action == "cancel":
                await svc.cancel(rid)
            elif action == "resume":  # a failed or cancelled run: its failed steps run again
                steps = [str(x) for x in b.get("steps") or []]
                return JSONResponse({"ok": True, "reset": await svc.resume(rid, steps or None)})
            elif action == "changes":
                if not text.strip():
                    return err("请写修改意见。")
                await svc.request_changes(rid, step, text)
            elif action == "dismiss":  # a watchdog alert, seen by the user
                if watchdog is None or not watchdog.dismiss(text, user):
                    return err("没有这条提醒，或者已经处理过。", 404)
            else:
                return err("未知操作。", 404)
        except ValueError as e:  # the step is not waiting for this, bad ids
            return err(str(e), 409)
        return JSONResponse({"ok": True})

    async def knowledge(req: Request) -> Response:
        try:
            if req.query_params.get("all"):
                return JSONResponse(await svc.knowledge_all())
            return JSONResponse(await svc.knowledge_catalog(req.query_params.get("project") or None))
        except (ValueError, RuntimeError) as e:  # a broken registry file, Docker down
            return err(str(e))

    async def knowledge_edit(req: Request) -> Response:
        """POST /api/knowledge/{op}: check | register | remove, body {name, project, data, replace}."""
        b = await body_of(req)
        try:
            return JSONResponse(await svc.knowledge_edit(req.path_params["op"], str(b.get("name") or ""),
                                                         b.get("project") or None, b.get("data") or {}, bool(b.get("replace"))))
        except (ValueError, RuntimeError) as e:  # KnowledgeError is a ValueError
            return err(str(e))

    async def step_knowledge(req: Request) -> Response:
        try:
            return JSONResponse(await svc.step_knowledge_use(req.path_params["rid"], req.path_params["iid"]))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    async def eval_cases(req: Request) -> Response:
        """Reviewer-eval cases (myorch/evals.py): candidates and their findings; POST keeps the real ones."""
        from myorch import evals

        if req.method == "POST":
            b = await body_of(req)
            try:
                return JSONResponse(evals.decide(data_root, req.path_params["cid"], list(b.get("keep") or []), user))
            except ValueError as e:
                return err(str(e))
        def with_replays():  # a postmortem case: did each replay catch the expected findings?
            out = []
            for c in evals.list_cases(data_root):
                if c.get("status") == "draft":
                    continue
                want = evals.expected(c) if c.get("status") == "confirmed" else c.get("findings") or []
                c["replay_scores"] = {}
                for phase, rid in (c.get("replays") or {}).items():
                    text = store.read_file(f"runs/{rid}/docs/review/round-1/verified.json")
                    found = json.loads(text) if text else None
                    c["replay_scores"][phase] = {"run": rid, **(evals.score(found, want) if isinstance(found, list) else {"pending": True})}
                out.append(c)
            return out
        return JSONResponse(await asyncio.to_thread(with_replays))

    async def step_files(req: Request) -> Response:
        try:
            return JSONResponse(await svc.step_files(req.path_params["rid"], req.path_params["iid"]))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    async def list_files(req: Request) -> Response:
        try:
            return JSONResponse(await svc.list_files(req.path_params["rid"], req.query_params.get("dir", "docs")))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    async def read_file(req: Request) -> Response:
        path, rid = req.query_params.get("path", ""), req.path_params["rid"]
        if image_type(path):  # the page loads the bytes from /raw; a binary file read as text is noise
            return JSONResponse({"path": path, "text": "", "html": None, "image": raw_url(rid, path)})
        try:
            text = await svc.read_file(rid, path)
        except (ValueError, RuntimeError) as e:  # RuntimeError: the storage helper (Docker) failed
            return err(str(e))
        if text is None:
            return err(f"没有这个文件：{path}。", 404)
        return JSONResponse({"path": path, "text": text, "html": run_md(text, rid, path) if path.endswith(".md") else None})

    async def read_raw(req: Request) -> Response:
        path = req.query_params.get("path", "")
        try:
            got = await svc.read_image(req.path_params["rid"], path)
        except (ValueError, RuntimeError) as e:
            return err(str(e))
        if got is None:
            return err(f"没有这个文件：{path}。", 404)
        data, kind = got
        # An image only, never a document: no script, and a sandbox even when opened on its own.
        return Response(data, media_type=kind, headers={"content-security-policy": "sandbox", "cache-control": "no-store"})

    async def changes(req: Request) -> Response:
        try:
            d = await svc.changes(req.path_params["rid"])
        except (ValueError, RuntimeError) as e:
            return err(str(e))
        d["summary_html"] = render_md(d["summary"]) if d["summary"] else None
        d["risk_html"] = render_md(d["risk"]) if d["risk"] else None
        return JSONResponse(d)

    async def flows(_: Request) -> Response:
        return JSONResponse(svc.flows())

    async def projects(_: Request) -> Response:
        return JSONResponse(svc.projects())

    flowdefs = FlowDefs(config, kb_root)  # flow definitions, read live from flows/ (ui/flowgraph.py)

    async def flowdefs_overview(_: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(flowdefs.overview))

    async def flowdefs_version(_: Request) -> Response:
        return JSONResponse({"version": await asyncio.to_thread(flowdefs.version)})

    async def flowdefs_graph(req: Request) -> Response:
        q = req.query_params
        inputs = {k[2:]: v for k, v in q.items() if k.startswith("i.")}
        return JSONResponse(await asyncio.to_thread(flowdefs.graph, req.path_params["flow"], q.get("project"), inputs))

    routes = [
        Route("/", index),
        Route("/api/meta", meta),
        Route("/api/runs", runs, methods=["GET", "POST"]),
        Route("/api/runs/{rid}", run_detail),
        Route("/api/runs/{rid}/do/{action:str}", act, methods=["POST"]),
        Route("/api/runs/{rid}/steps/{iid}", step_files),
        Route("/api/runs/{rid}/steps/{iid}/knowledge", step_knowledge),
        Route("/api/knowledge", knowledge),
        Route("/api/knowledge/{op:str}", knowledge_edit, methods=["POST"]),
        Route("/api/evals", eval_cases),
        Route("/api/evals/{cid}", eval_cases, methods=["POST"]),
        Route("/api/runs/{rid}/files", list_files),
        Route("/api/runs/{rid}/file", read_file),
        Route("/api/runs/{rid}/raw", read_raw),
        Route("/api/runs/{rid}/changes", changes),
        Route("/api/flows", flows),
        Route("/api/projects", projects),
        Route("/api/flowdefs", flowdefs_overview),
        Route("/api/flowdefs/version", flowdefs_version),
        Route("/api/flowdefs/{flow}", flowdefs_graph),
        Mount("/static", app=StaticFiles(directory=STATIC)),
    ]
    app = Starlette(routes=routes)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Guard:
        def __init__(self, inner) -> None:
            self.inner = inner

        async def __call__(self, scope, receive, send_):
            if scope["type"] != "http":
                return await self.inner(scope, receive, send_)
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            path, method = scope.get("path", ""), scope.get("method", "GET")
            if headers.get("host", "") not in allowed_hosts:
                return await PlainTextResponse("bad host", 403)(scope, receive, send_)
            if method not in ("GET", "HEAD") and headers.get("x-myorch-ui") != "1":
                return await PlainTextResponse("missing X-Myorch-UI header", 403)(scope, receive, send_)

            async def send_with_headers(message):
                if message["type"] == "http.response.start":
                    h = list(message.get("headers", []))
                    h += [(b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
                          (b"referrer-policy", b"no-referrer")]
                    message = {**message, "headers": h}
                await send_(message)

            return await self.inner(scope, receive, send_with_headers)

    return Guard(app)  # type: ignore[return-value]
