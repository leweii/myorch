"""channels: the layer between myorch and the platforms people use (docs/notes/2026-11-02-channel-layer.md).

    relay.py     forwards events (events.db -> adapters), commands (adapters -> Temporal) and run content
    http.py      the relay over HTTP and SSE at /channel, for the console in the browser
    serve.py     `myorch serve`: the relay, the console, the Slack adapter and the watchdog in one process
    model.py     channels.yaml: who is who across platforms

The only adapter today is the console (src/myorch/ui/). Platform adapters (GitHub, Slack, ...) are
built on the relay when they are needed; the earlier GitHub adapter was removed on 2026-11-03.
"""
