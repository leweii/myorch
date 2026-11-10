"""The relay over HTTP, for the web adapter (the console in the browser), mounted at /channel.

    GET  /channel/events?after=<seq>[&run=<id>]   Server-Sent Events: every event after <seq>, as it
                                                  is written (`id:` is the seq; reconnect with
                                                  Last-Event-ID or ?after= to continue without gaps)
    GET  /channel/events.json?after=&run=&limit=  the same events as one JSON list (no streaming)
    GET  /channel/output?run=<id>[&step=<instance>][&after=<seq>]
                                                  Server-Sent Events: the live output of running steps
                                                  (event `output`, data {seq, step, attempt, turn, text})
    GET  /channel/runs?limit=[&label=]            recent runs: run_started data + engine status + labels;
                                                  label (or q): only runs with that label, e.g. MD-18700
                                                  (any case), or whose id contains it; searches every run
    POST /channel/runs                            start {flow, project, inputs, message, labels?}
    POST /channel/runs/<id>/<command>             reply {step, text} · approve {step} · reject {step}
                                                  (header X-Myorch-Via: web | claude-code; claude-code may
                                                  not approve, reject or request changes)
                                                  · changes {step, text} · cancel {}
                                                  · resume {steps?} (a failed or cancelled run)
    GET  /channel/runs/<id>/files?dir=docs        file list in the run's workspace
    GET  /channel/runs/<id>/file?path=            one file
    GET  /channel/runs/<id>/steps/<instance>      a step's manifest, output files and log tail
    GET  /channel/runs/<id>/changes?base=         the change against origin/<base> and review material

Commands from the browser are made by the local user (`via: web`). Local only: the host must be
127.0.0.1 or localhost, and every POST needs the X-Myorch-UI header (no cross-site requests).
"""

from __future__ import annotations

import asyncio
import getpass
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from myorch.channels.relay import Relay

KEEPALIVE = 15.0  # seconds: an SSE comment line keeps proxies and the browser from closing the stream


# Who may send commands through this API, by the X-Myorch-Via header (default web: the console). An
# agent (claude-code: the local Claude Code channel, channels/claude_code.py) may start, reply and
# resume, never approve: the human gate stays with people.
VIAS = ("web", "claude-code")
HUMAN_ONLY = ("approve", "reject", "changes")


def labels_of(body: dict) -> list[str] | None:
    """`labels` of a start request: a list, or one comma-separated string."""
    raw = body.get("labels")
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(x) for x in raw if str(x).strip()] if isinstance(raw, list) else None


def create_channel_app(relay: Relay, port: int, user: str | None = None):
    account = user or getpass.getuser()

    def via_of(req: Request) -> str:
        v = req.headers.get("x-myorch-via", "web")
        if v not in VIAS:
            raise ValueError(f"unknown X-Myorch-Via {v!r}")
        return v

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    async def body_of(req: Request) -> dict:
        try:
            b = await req.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def after_of(req: Request) -> int:
        raw = req.headers.get("last-event-id") or req.query_params.get("after") or "0"
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    def sse(source, kind_of):
        """Server-Sent Events from an async iterator of models with a `seq`, with keepalive comments."""
        async def gen():
            it = source.__aiter__()
            nxt = asyncio.ensure_future(it.__anext__())
            try:
                while True:
                    done, _ = await asyncio.wait({nxt}, timeout=KEEPALIVE)
                    if not done:
                        yield ": keepalive\n\n"
                        continue
                    e = nxt.result()
                    nxt = asyncio.ensure_future(it.__anext__())
                    payload = json.dumps(e.model_dump(), ensure_ascii=False)
                    yield f"id: {e.seq}\nevent: {kind_of(e)}\ndata: {payload}\n\n"
            finally:
                nxt.cancel()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"cache-control": "no-cache", "x-accel-buffering": "no"})

    async def events_stream(req: Request) -> Response:
        after, run = after_of(req), req.query_params.get("run") or None
        try:
            relay.events(after, run, limit=1)  # check the run id before the stream starts
        except ValueError as e:
            return err(str(e))
        return sse(relay.follow(after, run), lambda e: e.kind)

    async def output_stream(req: Request) -> Response:
        q = req.query_params
        run, step = q.get("run") or "", q.get("step") or None
        try:
            relay.output(run, step, limit=1)  # check the run id before the stream starts
        except ValueError as e:
            return err(str(e))
        return sse(relay.follow_output(run, step, after_of(req)), lambda _c: "output")

    async def events_json(req: Request) -> Response:
        try:
            limit = min(2000, int(req.query_params.get("limit", "500")))
            evs = await asyncio.to_thread(relay.events, after_of(req), req.query_params.get("run") or None, limit)
        except ValueError as e:
            return err(str(e))
        return JSONResponse([e.model_dump() for e in evs])

    async def runs(req: Request) -> Response:
        if req.method == "POST":
            b = await body_of(req)
            try:
                via = via_of(req)
                run_id = await relay.start(str(b.get("flow") or ""), (str(b.get("project") or "") or None),
                                           dict(b.get("inputs") or {}), str(b.get("message") or ""),
                                           via=via, account=account, source="console" if via == "web" else via,
                                           labels=labels_of(b))
            except (ValueError, FileNotFoundError) as e:
                return err(str(e))
            return JSONResponse({"run_id": run_id})
        q = req.query_params
        return JSONResponse(await relay.runs(limit=min(500, int(q.get("limit", "60"))), label=q.get("label") or q.get("q")))

    async def command(req: Request) -> Response:
        rid, cmd = req.path_params["rid"], req.path_params["cmd"]
        b = await body_of(req)
        step, text = str(b.get("step") or ""), str(b.get("text") or "")
        try:
            via = via_of(req)
        except ValueError as e:
            return err(str(e))
        if via != "web" and cmd in HUMAN_ONLY:
            return err(f"{cmd} 只能由人在控制台或 Slack 里做，{via} 不行", 403)
        who = {"via": via, "account": account}
        try:
            if cmd == "reply":
                await relay.reply(rid, step, text, **who)
            elif cmd == "approve":
                await relay.approve(rid, step, **who)
            elif cmd == "reject":
                await relay.reject(rid, step, **who)
            elif cmd == "changes":
                await relay.request_changes(rid, step, text, **who)
            elif cmd == "cancel":
                await relay.cancel(rid, **who)
            elif cmd == "resume":
                steps = [str(s) for s in b.get("steps") or []]
                return JSONResponse({"ok": True, "reset": await relay.resume(rid, steps or None, **who)})
            else:
                return err(f"unknown command {cmd!r}", 404)
        except (ValueError, RuntimeError) as e:
            return err(str(e))
        return JSONResponse({"ok": True})

    async def files(req: Request) -> Response:
        try:
            return JSONResponse(await relay.list_files(req.path_params["rid"], req.query_params.get("dir", "docs")))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    async def file(req: Request) -> Response:
        path = req.query_params.get("path", "")
        try:
            text = await relay.read_file(req.path_params["rid"], path)
        except (ValueError, RuntimeError) as e:
            return err(str(e))
        if text is None:
            return err(f"没有这个文件：{path}。", 404)
        return JSONResponse({"path": path, "text": text})

    async def step(req: Request) -> Response:
        try:
            return JSONResponse(await relay.step_files(req.path_params["rid"], req.path_params["iid"]))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    async def changes(req: Request) -> Response:
        base = req.query_params.get("base", "")
        if not base:
            return err("base is required (the project's base branch)")
        try:
            return JSONResponse(await relay.changes(req.path_params["rid"], base))
        except (ValueError, RuntimeError) as e:
            return err(str(e))

    app = Starlette(routes=[
        Route("/events", events_stream),
        Route("/events.json", events_json),
        Route("/output", output_stream),
        Route("/runs", runs, methods=["GET", "POST"]),
        Route("/runs/{rid}/files", files),
        Route("/runs/{rid}/file", file),
        Route("/runs/{rid}/steps/{iid}", step),
        Route("/runs/{rid}/changes", changes),
        Route("/runs/{rid}/{cmd}", command, methods=["POST"]),
    ])
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if headers.get("host", "") not in allowed_hosts:
                return await PlainTextResponse("bad host", 403)(scope, receive, send)
            if scope.get("method", "GET") not in ("GET", "HEAD") and headers.get("x-myorch-ui") != "1":
                return await PlainTextResponse("missing X-Myorch-UI header", 403)(scope, receive, send)
        return await app(scope, receive, send)

    return guarded
