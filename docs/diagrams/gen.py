"""Generate the HTML diagrams of docs/ (pixel-placed ones and flow-layout ones).

    python3 docs/diagrams/gen.py docs/diagrams            # writes docs/diagrams/*.html

Boxes and arrows are placed by pixel coordinates (canvas 1140 px wide). Change a position or a label
here, run this script, then rebuild the page (docs/build-html.py), which embeds the generated files.
"""
import sys


CSS = """
<style>
/* Quiet diagrams, in the docs' tokens (docs/build-html.py): white cards on the figure's gray ground,
   gray lines for calls, the accent for policy-allowed egress. Card kinds differ by surface, edge and
   one accent; colours come from the page's theme variables, with light fallbacks when a diagram is
   opened on its own. */
.dg,.df{--d-strong:var(--text-strong,#17171a);--d-text:var(--text,#3b3b41);--d-faint:var(--text-faint,#6e6e76);--d-ghost:var(--text-ghost,#a3a3aa);
  --d-card:var(--raised,#fff);--d-ground:var(--well,#f3f3f2);--d-line:var(--line,rgba(15,15,20,.08));--d-line2:var(--line-strong,rgba(15,15,20,.15));
  --d-accent:var(--accent,#2563eb);--d-accent-soft:var(--accent-soft,#edf2fe);--d-accent-text:var(--accent-text,#1d4fd7);
  --d-danger:var(--danger,#c2321f);--d-danger-soft:var(--danger-soft,#fcefec);
  --d-shadow:var(--shadow-card,0 1px 2px rgba(15,15,20,.05),0 0 0 1px rgba(15,15,20,.08));
  --d-hatch:repeating-linear-gradient(-45deg,var(--hc,rgba(15,15,20,.09)) 0 1px,transparent 1px 7px);
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:var(--d-text);
  -webkit-font-smoothing:antialiased}
.dg *,.df *{box-sizing:border-box}
/* ---- fixed-canvas diagrams (.dg) ---- */
.dg{position:relative;margin:0 auto;font-weight:400}
.dg .sec{position:absolute;border:1px solid var(--d-line2);border-radius:16px;background:transparent}
.dg .sec.dash{border-style:dashed}
.dg .sec>.t{position:absolute;top:10px;left:16px;font-size:12px;font-weight:600;color:var(--d-faint);white-space:nowrap}
.dg .card{position:absolute;background:var(--d-card);border:0;box-shadow:var(--d-shadow);border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:6px 10px;line-height:1.4}
.dg .card b{font-size:14.5px;font-weight:600;color:var(--d-strong)}
.dg .card span{font-size:12px;color:var(--d-faint)}
.dg .card ul{margin:8px 0 0;padding:0;list-style:none;font-size:13px;color:var(--d-text);line-height:1.9}
.dg .card hr{width:80%;border:0;border-top:1px solid var(--d-line);margin:6px 0}
.dg .box{position:absolute;border-radius:12px}
.dg .lbl{position:absolute;font-size:12px;font-weight:500;color:var(--d-faint);background:var(--d-ground);padding:1px 5px;border-radius:4px;white-space:nowrap;line-height:1.4}
.dg .lbl.g{color:var(--d-accent-text);font-weight:600}.dg .lbl.gr{color:var(--d-ghost)}
.dg .lbl.c{transform:translateX(-50%)}
.dg .note{position:absolute;font-size:12.5px;color:var(--d-text);line-height:1.7}
.dg svg{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
/* ---- flow-layout diagrams (.df) ---- */
.df{font-size:13px;margin:0;line-height:1.5;font-weight:400}
.df .c{background:var(--d-card);box-shadow:var(--d-shadow);border-radius:10px;padding:9px 12px;text-align:center}
.df .c b{display:block;font-size:14px;font-weight:600;color:var(--d-strong)}
.df .c span{display:block;font-size:12px;color:var(--d-faint)}
.df .c code,.df .chip{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.df .row{display:flex;align-items:center}
.df .row>.c{flex:1 1 0}
.df .col{display:flex;flex-direction:column;align-items:stretch}
.df .arr{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 4px;min-width:56px;font-size:11.5px;font-weight:500;color:var(--d-faint);text-align:center}
.df .arr i{display:block;width:100%;min-width:40px;height:1.5px;background:var(--d-ghost);position:relative;margin:3px 0}
.df .arr i::after{content:"";position:absolute;right:-2px;top:-4.5px;border-left:8px solid var(--d-ghost);border-top:5px solid transparent;border-bottom:5px solid transparent}
.df .arr.bi i::before{content:"";position:absolute;left:-2px;top:-4.5px;border-right:8px solid var(--d-ghost);border-top:5px solid transparent;border-bottom:5px solid transparent}
.df .down{display:flex;justify-content:center;align-items:center;gap:8px;height:34px;font-size:11.5px;font-weight:500;color:var(--d-faint)}
.df .down i{display:block;width:1.5px;height:26px;background:var(--d-ghost);position:relative}
.df .down i::after{content:"";position:absolute;bottom:-2px;left:-4.5px;border-top:8px solid var(--d-ghost);border-left:5px solid transparent;border-right:5px solid transparent}
.df .down.dash i{background:repeating-linear-gradient(var(--d-ghost) 0 4px,transparent 4px 8px)}
.df .down.g i{width:3px;background:var(--d-accent)}.df .down.g i::after{border-top-color:var(--d-accent)}.df .down.g span{color:var(--d-accent-text);font-weight:600}
.df .frame{position:relative;border:1px solid var(--d-line2);border-radius:16px;background:transparent;padding:36px 14px 14px}
.df .frame.dash{border-style:dashed}
.df .frame>.t{position:absolute;top:11px;left:16px;font-size:12px;font-weight:600;color:var(--d-faint)}
.df .grid{display:grid;gap:10px}
.df .note{font-size:12px;color:var(--d-faint);text-align:center;margin-top:10px}
.df .band{display:grid;grid-template-columns:118px 1fr;box-shadow:var(--d-shadow);border-radius:10px;overflow:hidden;background:var(--d-card)}
.df .band .tag{background:var(--d-ground);border-right:1px solid var(--d-line);display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:600;font-size:13px;color:var(--d-strong);padding:8px}
.df .band .tag small{font-weight:500;font-size:11px;color:var(--d-faint)}
.df .band .body{padding:10px 14px;text-align:left}
.df .band.hl{box-shadow:0 0 0 1.5px var(--d-accent)}.df .band.hl .tag{background:var(--d-accent-soft);color:var(--d-accent-text)}
.df .chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px}
.df .chip{background:var(--d-ground);border-radius:5px;padding:1px 7px;font-weight:500;color:var(--d-strong)}
.df .chip.p{background:transparent;border:1px dashed var(--d-line2);color:var(--d-ghost)}
.df .body ul{margin:0;padding-left:18px;font-size:12.5px;color:var(--d-text)}
.df .steps{display:grid;grid-template-columns:34px 170px 1fr;gap:8px 12px;align-items:center}
.df .num{width:24px;height:24px;border-radius:50%;background:var(--d-accent-soft);color:var(--d-accent-text);font-weight:600;display:flex;align-items:center;justify-content:center;font-size:12px}
.df .steps .name{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;font-weight:500;color:var(--d-strong);background:var(--d-card);box-shadow:var(--d-shadow);border-radius:6px;padding:6px 8px;text-align:center}
.df .steps .desc{font-size:12.5px;color:var(--d-text)}
.df .stage{display:grid;grid-template-columns:170px 1fr 130px;gap:12px;align-items:center;text-align:left}
.df .stage b{font-size:13.5px}
.df .badge{justify-self:end;border-radius:999px;padding:1px 10px;font-size:11px;font-weight:600;white-space:nowrap}
.df .badge.k{background:var(--d-accent-soft);color:var(--d-accent-text)}.df .badge.n{background:var(--d-ground);color:var(--d-faint)}
.df .badge.x{background:transparent;color:var(--d-faint);border:1px dashed var(--d-line2)}
/* ---- card kinds, shared ---- */
.dg .blue,.df .blue{box-shadow:0 0 0 1.5px var(--d-accent),0 4px 14px -6px rgba(37,99,235,.35)}          /* the component this view is about */
.dg .blue b,.df .blue b{color:var(--d-accent-text)}
.dg .green,.df .green{background:var(--d-hatch),var(--d-card)}                                          /* isolation and egress */
.dg .you,.df .you{box-shadow:0 0 0 1.5px var(--d-strong)}                                                 /* a person, or where you are needed */
.dg .dark,.df .dark{background:var(--d-strong);box-shadow:none}                                         /* untrusted agent process */
.dg .dark b,.df .dark b{color:var(--d-card)}.dg .dark span,.df .dark span{color:var(--d-ghost)}
.dg .paused,.df .paused{background:transparent;box-shadow:none;border:1px dashed var(--d-line2)}
.dg .paused b,.dg .paused span,.df .paused b,.df .paused span{color:var(--d-ghost)}
.dg .data,.df .data{background:var(--d-card);box-shadow:0 0 0 1px var(--d-line);border-radius:8px}
.dg .data b,.df .data b{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace;font-size:13px;font-weight:500}
.dg .red,.df .red{background:var(--d-danger-soft);box-shadow:0 0 0 1.5px var(--d-danger)}
</style>
"""

class D:
    def __init__(s, key, w, h):
        s.k, s.w, s.h, s.items, s.paths = key, w, h, [], []
    def sec(s, x, y, w, h, title, cls="", right=False):
        pos = "right:14px;left:auto" if right else ""
        s.items.append(f'<div class="sec {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px"><div class="t" style="{pos}">{title}</div></div>')
    def box(s, x, y, w, h, style):
        s.items.append(f'<div class="box" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;{style}"></div>')
    def card(s, x, y, w, h, title, *subs, cls="", inner=""):
        body = f"<b>{title}</b>" + "".join(f"<span>{t}</span>" for t in subs) + inner
        s.items.append(f'<div class="card {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">{body}</div>')
    def label(s, x, y, text, cls=""):
        s.items.append(f'<div class="lbl {cls}" style="left:{x}px;top:{y}px">{text}</div>')
    def note(s, x, y, text):
        s.items.append(f'<div class="note" style="left:{x}px;top:{y}px">{text}</div>')
    def arrow(s, pts, kind="call"):
        # one ink; the kind shows in weight and dash (calls thin solid, egress heavy, data and paused dashed)
        color = {"call": "var(--d-faint)", "paused": "var(--d-ghost)", "egress": "var(--d-accent)",
                 "data": "var(--d-ghost)", "deny": "var(--d-danger)"}[kind]
        width = {"egress": 3}.get(kind, 1.5)
        dash = "stroke-dasharray:5 4;" if kind in ("data", "paused", "deny") else ""
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        end = "" if kind == "deny" else f' marker-end="url(#{s.k}-{kind})"'
        s.paths.append(f'<path d="{d}" fill="none" style="stroke:{color};stroke-width:{width};{dash}"{end}/>')
    def render(s):
        markers = "".join(
            f'<marker id="{s.k}-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" orient="auto-start-reverse">'
            f'<path d="M0 0 L10 5 L0 10 z" style="fill:{c}"/></marker>'
            for k, c in {"call": "var(--d-faint)", "paused": "var(--d-ghost)", "egress": "var(--d-accent)", "data": "var(--d-ghost)"}.items())
        svg = f'<svg width="{s.w}" height="{s.h}" viewBox="0 0 {s.w} {s.h}"><defs>{markers}</defs>{"".join(s.paths)}</svg>'
        return (f'<div class="dg" style="width:{s.w}px;height:{s.h}px">' + "".join(s.items[:]) + svg + "".join([]) + "</div>")

def legend(d, x, y, rows):
    for i, (kind, text) in enumerate(rows):
        yy = y + i * 24
        d.arrow([(x, yy + 9), (x + 46, yy + 9)], kind)
        d.label(x + 54, yy, text)

# ---------------- overview ----------------
o = D("ov", 1140, 905)
o.sec(20, 20, 440, 110, "People")
o.card(40, 52, 115, 58, "You", "browser")
o.card(170, 52, 115, 58, "Claude Code", "local MCP adapter")
o.card(300, 52, 120, 58, "You", "terminal")
o.sec(720, 20, 400, 110, "Third parties · sandbox egress")
o.card(735, 52, 120, 58, "GitHub", "read PRs · push branches")
o.card(865, 52, 120, 58, "Jira", "issues")
o.card(995, 52, 115, 58, "Claude API", "Anthropic")
o.sec(720, 190, 190, 125, "Third party · direct")
o.card(735, 228, 160, 64, "Slack", "DMs · questions · approvals")
o.sec(20, 190, 690, 125, "Entry")
o.card(40, 228, 200, 64, "Console (myorch serve)", "127.0.0.1:8800 · channel layer", "Slack adapter · Watchdog", cls="blue")
o.card(260, 228, 200, 64, "myorch CLI", "uv run myorch &lt;command&gt;", "reads events via the console")
o.card(40, 385, 260, 72, "Temporal dev server", "gRPC :7233 · UI :8233")
o.card(440, 385, 260, 72, "myorch worker", "runs steps · writes the event log")
o.card(440, 540, 260, 92, "OpenShell", "gateway + one sandbox per step", "work credentials live only here", cls="green")
o.arrow([(97, 110), (97, 226)]); o.label(105, 150, "HTTP")
o.arrow([(227, 110), (227, 226)]); o.label(235, 150, "HTTP")
o.arrow([(360, 110), (360, 226)]); o.label(368, 150, "CLI")
o.arrow([(170, 315), (170, 383)]); o.label(180, 340, "gRPC: start · reply · approve · status")
o.arrow([(440, 421), (302, 421)]); o.label(371, 398, "gRPC: tasks", "c")
o.arrow([(570, 457), (570, 538)]); o.label(580, 488, "openshell CLI: create/delete sandbox")
o.arrow([(700, 586), (925, 586), (925, 132)], "egress"); o.label(935, 330, "policy-allowed egress<br>supervisor injects<br>real credentials", "g")
o.arrow([(225, 292), (225, 330), (815, 330), (815, 294)]); o.label(470, 336, "Slack adapter: Socket Mode, outbound")
o.sec(20, 680, 1100, 175, "Local data · who reads and writes: see the table below")
chips = [("myorch-data/temporal.db", "Temporal reads and writes"), ("myorch-data/events.db", "worker writes · console reads (host CLI via console)"),
         ("myorch-data/ui/", "console reads and writes"),
         ("myorch-knowledge/", "entry points read · project-apply and console write"), ("volume myorch-artifacts", "sandboxes and helper containers read and write")]
for i, (t, sub) in enumerate(chips):
    o.card(40 + (i % 3) * 360, 714 + (i // 3) * 68, 340, 58, t, sub, cls="data")
o.arrow([(30, 885), (76, 885)]); o.label(84, 876, "call")
o.arrow([(150, 885), (196, 885)], "egress"); o.label(204, 876, "policy-allowed egress")

# ---------------- openshell ----------------
s = D("os", 1140, 590)
s.card(880, 14, 230, 50, "Third parties", "GitHub · Jira · Claude API, etc.")
s.sec(20, 90, 380, 420, "OpenShell gateway (control plane)", right=True)
s.card(45, 130, 330, 76, "API server", "auth · sandbox lifecycle · issues JWTs", cls="blue")
s.card(45, 250, 330, 76, "Persistent state", "sandboxes · policies · providers (real credentials)")
s.card(45, 380, 330, 92, "compute driver: docker", "creates two containers and the socket between them", "builds the boundary, judges no requests", cls="green")
s.sec(440, 90, 680, 380, "sandbox (one per step instance)", "dash")
s.card(465, 135, 270, 300, "supervisor container", "trusted side · bridge network", cls="blue",
       inner="<hr><ul><li>policy decisions</li><li>placeholders → real credentials</li><li>DNS and proxy · TLS re-signing</li><li>keeps the gateway session (JWT)</li></ul>")
s.box(780, 135, 320, 300, "border:1.5px dashed var(--d-danger);background:var(--d-danger-soft)")
s.label(795, 144, "<b>workload container</b> · untrusted · network none")
s.card(805, 178, 270, 76, "harness process", "claude -p or bash · untrusted", cls="dark")
s.note(812, 264, "· Landlock limits the file system<br>· env vars hold only credential placeholders<br>· seccomp blocks TCP and DNS<br>· the run's workspace is under /sandbox/")
s.card(800, 372, 280, 52, "No egress at all", "only a Unix socket to the supervisor", cls="green")
s.arrow([(90, 20), (90, 128)]); s.label(100, 36, "openshell CLI: create, delete sandboxes (myorch worker)<br>import providers (compose's providers service)")
s.arrow([(210, 206), (210, 248)], "data"); s.label(218, 218, "read/write")
s.arrow([(45, 168), (30, 168), (30, 426), (43, 426)])
s.arrow([(375, 426), (438, 426)]); s.label(407, 402, "creates", "c")
s.arrow([(465, 175), (377, 175)]); s.label(421, 136, "gets policy<br>+ credentials", "c")
s.arrow([(805, 216), (737, 216)], "egress"); s.label(771, 222, "Unix<br>socket", "c g")
s.arrow([(700, 135), (700, 39), (878, 39)], "egress"); s.label(712, 44, "policy-allowed egress<br>placeholders swapped<br>for real credentials", "g")
s.arrow([(40, 545), (86, 545)]); s.label(94, 536, "call")
s.arrow([(40, 571), (86, 571)], "egress"); s.label(94, 562, "path of egress requests")
s.arrow([(200, 545), (246, 545)], "data"); s.label(254, 536, "data read/write")



# ---------------- docker compose (deploy/compose.yaml) ----------------
k = D("dc", 1140, 1130)
k.sec(20, 20, 540, 110, "People")
k.card(60, 52, 190, 58, "You", "browser", cls="you")
k.card(290, 52, 240, 58, "You", "terminal · docker compose exec", cls="you")
k.sec(600, 20, 520, 110, "Files on the host")
k.card(615, 52, 240, 58, "deploy/secrets/", "providers.yaml → providers<br>slack.env → ui · not in git or images", cls="data")
k.card(870, 52, 235, 58, "Data and knowledge", "myorch-data/ · myorch-knowledge/<br>channels.yaml · ~/orca/kb (bind mount)", cls="data")
k.sec(20, 170, 1100, 360, "docker compose · network default · services reach each other by service name")
k.card(40, 220, 230, 66, "ui", "console · channel layer · Slack · Watchdog", cls="blue")
k.sec(340, 205, 770, 115, "One-shot services · exit when done", "dash")
k.card(360, 245, 200, 60, "init", "myorch init: volume, tools<br>runs on every up")
k.card(580, 245, 220, 60, "providers", "reads credential files → gateway")
k.card(820, 245, 260, 60, "gateway-init", "creates the sandbox signing key (Ed25519) if missing")
k.card(40, 400, 230, 66, "temporal", "gRPC :7233 · UI :8233")
k.card(360, 400, 230, 66, "worker", "myorch worker · runs steps", cls="blue")
k.card(820, 400, 260, 80, "gateway", "official OpenShell image · :17671", "credentials · policy · sandbox records", cls="green")
k.sec(20, 600, 1100, 330, "Host Docker · via /var/run/docker.sock mounted into containers")
k.card(40, 640, 230, 66, "Docker daemon", "/var/run/docker.sock")
k.card(40, 750, 230, 60, "helper containers", "--network none · read/write volume")
k.card(40, 850, 230, 54, "volume myorch-artifacts", "workspaces of all runs", cls="data")
k.box(330, 630, 520, 170, "border:1px dashed var(--d-line2)")
k.label(345, 638, "<b>sandbox</b> · two containers per step instance")
k.card(350, 670, 200, 100, "supervisor container", "bridge network", "policy · swaps in real credentials", cls="blue")
k.card(620, 670, 210, 100, "workload container", "agent or script · network none", cls="dark")
k.card(880, 640, 200, 56, "volume gateway-state", "signing key · gateway database", cls="data")
k.card(330, 975, 520, 54, "Third parties", "GitHub · api.anthropic.com, etc. · policy-allowed hosts")
# people
k.arrow([(155, 110), (155, 218)]); k.label(163, 140, "HTTP 127.0.0.1:8800")
k.arrow([(310, 110), (310, 415), (358, 415)]); k.label(318, 140, "myorch commands<br>openshell commands")
k.arrow([(690, 110), (690, 243)], "data"); k.label(698, 140, "read-only mount /secrets")
# inside compose
k.arrow([(155, 286), (155, 398)]); k.label(163, 330, "gRPC temporal:7233<br>start · reply · approve")
k.arrow([(360, 433), (272, 433)]); k.label(316, 440, "gRPC: tasks", "c")
k.arrow([(690, 305), (690, 360), (860, 360), (860, 398)]); k.label(698, 330, "openshell CLI: profiles, providers")
k.arrow([(590, 433), (818, 433)]); k.label(704, 410, "openshell CLI: create/delete sandbox", "c")
k.label(704, 440, "http://gateway:17671", "c gr")
k.arrow([(1080, 275), (1108, 275), (1108, 668), (1082, 668)], "data"); k.label(1050, 572, "key")
# to the host's Docker
k.arrow([(380, 466), (380, 570), (300, 570), (300, 673), (272, 673)]); k.label(166, 545, "docker CLI: helper containers<br>(ui, init too)")
k.arrow([(155, 706), (155, 748)]); k.arrow([(155, 810), (155, 848)], "data")
k.arrow([(840, 480), (840, 628)]); k.label(848, 540, "docker API: creates<br>two containers")
k.arrow([(980, 480), (980, 638)], "data"); k.label(988, 556, "read/write")
k.arrow([(340, 800), (340, 877), (272, 877)], "data"); k.label(350, 850, "mounts a subpath")
# the call back and egress
k.arrow([(520, 668), (520, 560), (780, 560), (780, 470), (818, 470)]); k.label(530, 520, "calls host.docker.internal:17671<br>via the port published on the host", "g")
k.arrow([(620, 720), (552, 720)], "egress"); k.label(586, 726, "Unix<br>socket", "c g")
k.arrow([(450, 770), (450, 973)], "egress"); k.label(460, 900, "policy-allowed egress · real credentials swapped in", "g")
legend(k, 40, 1046 - 24 * 0, [("call", "call")])
k.arrow([(160, 1055), (206, 1055)], "egress"); k.label(214, 1046, "egress request")
k.arrow([(320, 1055), (366, 1055)], "data"); k.label(374, 1046, "data read/write")
k.note(40, 1080, "Connections not drawn: ui connects out to Slack (Socket Mode, outbound, no open port);<br>the local Claude Code session connects to ui at 127.0.0.1:8800; <code>uv run myorch</code> on the host connects to temporal at 127.0.0.1:7233 and reads events via ui.")

# ---------------- flow-layout diagrams (no fixed canvas; they fit any width) ----------------
def c(title, *subs, cls=""):
    return f'<div class="c {cls}"><b>{title}</b>' + "".join(f"<span>{x}</span>" for x in subs) + "</div>"
def arr(label="", cls=""):
    return f'<div class="arr {cls}">{label}<i></i></div>'
def down(label="", cls=""):
    return f'<div class="down {cls}"><i></i><span>{label}</span></div>'
def frame(title, inner, cls=""):
    return f'<div class="frame {cls}"><div class="t">{title}</div>{inner}</div>'
def grid(cols, items, style=""):
    return f'<div class="grid" style="grid-template-columns:{cols};{style}">' + "".join(items) + "</div>"
def row(*items, style=""):
    return f'<div class="row" style="{style}">' + "".join(items) + "</div>"
def col(*items, style=""):
    return f'<div class="col" style="{style}">' + "".join(items) + "</div>"
def df(inner):
    return f'<div class="df">{inner}</div>'

def numbered(rows):
    return '<div class="steps">' + "".join(
        f'<div class="num">{n}</div><div class="name">{a}</div><div class="desc">{d}</div>' for n, a, d in rows) + "</div>"

FLOWS = {}

FLOWS["compose-startup.html"] = df(row(
    col(row(c("gateway-init", "creates the key if missing, then exits"), arr("on success"), c("gateway", "long-running", cls="green"), arr("once started"),
            c("providers", "imports credentials, then exits")),
        '<div style="height:10px"></div>',
        row(c("init", "myorch init, runs on every up, then exits"), '<div style="width:14px"></div>', c("temporal", "long-running")),
        style="flex:3 1 0"),
    arr("when all are met"),
    col(c("worker", "needs init, providers succeeded<br>temporal, gateway started", cls="blue"), '<div style="height:8px"></div>',
        c("ui", "needs init succeeded, temporal started"), style="flex:1 1 0"))
    + '<div class="note">compose orders the services by depends_on. If providers fails, worker does not start. The console does not wait for gateway or providers.</div>')

def band(tag, sub, chips, body, hl=False):
    ch = "".join(f'<span class="chip {"p" if "paused" in x else ""}">{x}</span>' for x in chips)
    return (f'<div class="band {"hl" if hl else ""}"><div class="tag">{tag}<small>{sub}</small></div>'
            f'<div class="body"><div class="chips">{ch}</div>{body}</div></div>')

def stage(title, sub, desc, badge, kind, cls=""):
    return (f'<div class="c stage {cls}"><div><b>{title}</b><span>{sub}</span></div>'
            f'<div style="font-size:12.5px;color:var(--d-text)">{desc}</div><span class="badge {kind}">{badge}</span></div>')

FLOWS["deployment.html"] = df(frame("macOS · single machine · docker compose (deploy/compose.yaml)", grid("repeat(3,1fr)", [
    c("temporal", "container · 127.0.0.1:7233 / 8233<br>data in myorch-data/temporal.db"),
    c("gateway", "official OpenShell image · 127.0.0.1:17671<br>credentials, policies, sandbox records", cls="green"),
    c("worker", "container · runs all steps<br>starts sandboxes and helper containers via docker.sock"),
    c("ui (myorch serve)", "container · 127.0.0.1:8800<br>console + channel layer + Watchdog + Slack adapter", cls="blue"),
    c("Docker Desktop", "a pair of sandbox containers per step<br>helper containers · volume myorch-artifacts"),
    c("One-shot services", "gateway-init · init · providers<br>run and exit on every up"),
  ]))
  + down("Events are pushed to Claude Code. It connects to ui's HTTP API to read events and call tools (the session starts it)", "dash")
  + c("Claude Code channel", "channels/claude_code.py · pushes notifications, provides tools · cannot approve", cls="you")
  + '<div class="note">The Slack adapter connects out to Slack (Socket Mode), so the machine opens no ports. Single machine only. Team use (server-side event store, login, per-project permissions) is not built yet.</div>')

# ---------------- layers, components, one step, credentials, console (03-architecture) ----------------
FLOWS["layers.html"] = df(
    band("Entry points", "local processes", ["cli.py", "ui/", "channels/", "watchdog.py", "review_page.py"], "Read flows and project packs, send commands through the Temporal client, read events and workspaces.")
    + down("call")
    + band("L4 flow engine", "replaceable", ["engines/temporal/"], "The only place that imports temporalio. FlowWorkflow calls RunState.plan() over and over; activities only forward. ScheduledLaunch starts scheduled runs.")
    + down("call")
    + band("L3 core library", "engine-independent", ["runner/", "→", "storage/", "→", "core/"],
           "<ul><li>core: schema, scheduling, expressions, prompts, manifests, events; by convention no IO</li><li>storage: the volume's directories and files, the event log</li><li>runner: step → profile + harness → openshell calls; host actions</li>"
           "<li>Domain modules beside them (project, knowledge, evals, takeaways, trail, runs, localrepo, flowfile) depend only on core and are outside the layer contract</li></ul>", hl=True)
    + down("openshell CLI · docker CLI")
    + band("L2 OpenShell", "isolation and credentials", ["gateway", "supervisor"], "<ul><li>gateway: credentials, policies, sandbox lifecycle</li><li>supervisor: trusted container outside the sandbox; enforces policy, injects credentials</li></ul>")
    + down()
    + band("L1 Docker", "compute and storage", ["two containers per sandbox", "helper containers", "volume myorch-artifacts"], "Sandbox containers, helper containers that read and write the volume, the workspaces of all runs.")
    + '<div class="note"><code>uv run lint-imports</code> checks two rules: engines → runner → storage → core may only depend downward; core, storage and runner may not import temporalio.</div>')

FLOWS["components.html"] = df(
    row(c("Console · Slack · Claude Code", "channels/relay.py"), arr("commands"),
        c("Temporal client", "engines/temporal/client.py<br>start · signal · cancel · resume"), arr("gRPC"),
        c("Temporal", "durable state · retries · waiting for signals · Schedules"), arr("drives", "bi"),
        c("FlowWorkflow", "engines/temporal/workflow.py<br>RunState (core/run.py) decides what runs next", cls="blue"))
    + down("calls activities (engines/temporal/activities.py, only converts arguments)")
    + frame("activities · all in the worker", grid("repeat(4,1fr)", [
        col(c("init_run · seed_repo · write_run_file<br>prepare_step · collect_manifest"), down(), c("DockerVolumeStore", "storage/docker_volume.py"), down("docker run"), c("helper containers", "--network none"), down("read/write", "dash"), c("volume", "myorch-artifacts", cls="data")),
        col(c("run_step · cleanup_step", cls="blue"), down(), c("StepRunner", "runner/runner.py<br>profile → image, policy,<br>provider, mounts · harness → command line"), down("openshell CLI"), c("OpenShell gateway", "creates, inspects, deletes sandboxes", cls="green"), down(), c("sandbox", "supervisor container + workload container")),
        col(c("run_action"), down(), c("ActionRunner", "runner/actions.py · no sandbox"), down(), c("5 host actions", "project-apply · postmortem-replay<br>run-trail · takeaway-redact · takeaways-learned"), down("write", "dash"), c("myorch-knowledge/ · myorch-data/ · run workspaces", "manifest written to out/", cls="data")),
        col(c("publish_events"), down(), c("EventLog", "storage/events.py"), down("write", "dash"), c("myorch-data/events.db", "event table · live output table (written by StepRunner)", cls="data")),
      ]))
    + '<div class="note">cleanup_step is called only when run_step raises; normally run_step deletes its sandbox itself. The worker also runs ScheduledLaunch for scheduled starts.</div>')

FLOWS["step-execution.html"] = df(
    c("plan() → [develop]", "RunState picks an instance that can run", cls="blue") + down()
    + frame("FlowWorkflow calls the activities in order", numbered([
        ("1", "prepare_step", "Creates the directories on the volume, chown 1000. A new attempt (turn 1) clears out/; later turns delete only the old manifest and question. The session directory and logs/ are kept."),
        ("2", "run_step", "StepRunner: first deletes any leftover sandbox with the same name. profile → image, policy, provider; builds the mounts, assembles the prompt, <code>openshell sandbox create</code>, named <code>&lt;flow&gt;-&lt;step&gt;-&lt;hash&gt;</code>. Polls until it ends, with regular heartbeats. Afterwards saves the supervisor log, then deletes the sandbox."),
        ("3", "collect_manifest", "Reads manifest.json and question.json, and writes a fallback when one is missing. The workflow then turns results into questions according to escalate_when."),
      ]))
    + down()
    + grid("1fr 1fr 1fr", [
        c("Done", "Back to plan() for the next instances that can run. One sandbox per instance, never reused"),
        c("needs_input → waiting_input", "After you reply, turn+1: a new sandbox resumes the same session", cls="you"),
        c("Failed", "Retries with a new attempt up to limits.retries. After that, on_failed decides", cls="red"),
      ])
    + '<div class="note">A step with <code>action:</code> calls a single run_action (at most 2 tries). If run_step raises (worker crash, heartbeat timeout), cleanup_step makes sure the sandbox is deleted. Whenever the state has new events, the background publish_events writes them to events.db.</div>')

FLOWS["credential-flow.html"] = df(
    stage("deploy/secrets/providers.yaml", "file on the host · not in git", "The compose providers service (deploy/provision.py) reads it and passes each value to the gateway as <code>--credential KEY</code> through the subprocess's environment. No value appears on a command line.", "real tokens", "n", "you")
    + down("on every docker compose up")
    + stage("OpenShell gateway", "control plane", "Stores the real tokens.", "has real tokens", "k", "green")
    + down("when a sandbox is created")
    + stage("myorch worker", "also the console and CLI", "Passes only provider names, e.g. claude, github-mdm.", "names only", "n")
    + down()
    + stage("workload container", "agent process", "Env vars hold only placeholders. The container has no network. seccomp intercepts requests and hands them to the supervisor over a Unix socket.", "placeholders only", "x", "red")
    + down("Unix socket", "g")
    + stage("supervisor container", "trusted side", "Checks that policy allows this host and path → replaces placeholders with real tokens → opens the connection and forwards.", "has real tokens", "k", "blue")
    + down("policy-allowed egress", "g")
    + c("Request target", "policy-allowed hosts"))

FLOWS["console.html"] = df(
    c("browser", "http://127.0.0.1:8800/", cls="you")
    + down("HTTP (pages, /api, images /api/runs/&lt;id&gt;/raw) · SSE (/channel/events, /channel/output)")
    + c("myorch serve", "ui/app.py: pages and /api · channels/http.py: /channel · Host must be local · POST needs X-Myorch-UI", cls="blue")
    + down()
    + c("ui/service.py", "The console's read and write logic. ui/runview.py builds run state from events. No agent in the console")
    + down("call")
    + c("channel relay layer", "channels/relay.py: events · commands · content", cls="green")
    + down()
    + grid("1fr 1fr 1fr", [c("events.db", "events + live output", cls="data"),
                          c("Temporal client", "start · reply · approve · cancel · resume"),
                          c("Run workspace", "files · diffs · logs (helper container)", cls="data")])
    + '<div class="note">Watchdog and the Slack adapter run in the same process. The CLI on the host also reads events through here (/channel/events.json). For old runs without detailed events, the console falls back to a Temporal query for now.</div>')

FLOWS["run-continuation.html"] = df(
    c("An attempt failed", "attempt N of a step")
    + down()
    + grid("1fr 1fr", [
        c("Retries left (limits.retries)", "Automatic retry: new attempt, new sandbox. After a platform interruption (timeout, lost activity, sandbox failure) it resumes the original session and first has the agent look at the repository's current state. No retry once the run is cancelled"),
        col(c("Retries used up: on_failed decides"), '<div style="height:8px"></div>', grid("1fr 1fr 1fr", [
            c("fail (default)", "The step fails, later steps are blocked, the run fails", cls="red"),
            c("skip", "Treated as skipped; the run goes on"),
            c("ask", "Asks you: 1 start over · write a hint to continue the original session · 2 skip · 3 stop", cls="you")])),
      ])
    + down("the run has ended")
    + grid("1fr 1fr", [
        c("Failed or cancelled → resume", "<code>myorch resume</code> or the console button. Same run_id; a new Temporal execution carries the original RunState. Failed, cancelled and blocked steps are reset; succeeded steps and approvals are kept, rejections stay rejected. Only for runs Temporal still holds (ended within 24 hours)", cls="blue"),
        c("Ended → continue (a run input such as from_run)", "New run_id and workspace, same project, same role. carry inputs and tags come along; what people said is written to docs/previous/conversation.md. development continues on the same branch"),
      ]))

FLOWS["channel-layer.html"] = df(row(
    col(frame("worker", c("FlowWorkflow", "publish_events → event table") + down() + c("StepRunner", "follows the agent's log (helper container tail -F) → live output table")),
        down("write", "dash"), c("myorch-data/events.db", "SQLite WAL · event table · live output table", cls="data"), style="flex:1 1 0"),
    arr("read"),
    col(frame("myorch serve (ui container)",
        c("channels/relay.py", "events · commands · content", cls="blue")
        + '<div style="height:8px"></div>'
        + grid("1fr 1fr", [c("Watchdog", "reads events, sends reminders"), c("Slack adapter", "in-process follow · Socket Mode, outbound")])
        + '<div style="height:8px"></div>'
        + c("channels/http.py · /channel", "SSE /events /output · JSON /events.json /runs · POST commands")),
        style="flex:1.3 1 0"),
    arr("HTTP · SSE", "bi"),
    col(c("Browser console", "SSE + /api", cls="you"), '<div style="height:8px"></div>',
        c("Claude Code channel", "separate process started by the session · approve, reject, request changes return 403"), '<div style="height:8px"></div>',
        c("Host CLI", "GET /channel/events.json, never opens the file directly"), '<div style="height:8px"></div>',
        c("Slack DM threads", "progress · questions · approval buttons"), style="flex:1 1 0"))
    + down("Commands: relay identifies the person via channels.yaml → engines/temporal/client.py → start, signal, cancel, resume", "dash")
    + c("FlowWorkflow (Temporal)", "Content (files, diffs, logs) goes separately: relay → DockerVolumeStore's helper container → runs/&lt;id&gt;/")
    + '<div class="note">Events are sent only at the start, when a person is needed and at the end, plus detail events for drawing run state. Live output has its own table.</div>')

# ---------------- directories and storage (01-overview) ----------------
FLOWS["storage-relations.html"] = df(
    grid("1fr 1fr 1fr", [
        c("① myorch/", "flows/ · profiles/ · tools/"),
        c("② myorch-knowledge/", "&lt;project&gt;/project.yaml · prompts/ · guide/ · checks.sh · sources/ · _templates/"),
        c("Knowledge source content", "local directory (under ~/orca/kb) or git repository"),
      ])
    + grid("1fr 1fr 1fr", [down("expanded at start · myorch init installs tools/"), down("project sync (versioned snapshots)"), down("synced automatically when a run starts")])
    + frame("④ volume myorch-artifacts · all a sandbox can see", grid("1fr 1fr 1fr", [
        c("runs/&lt;run&gt;/", "workspace: code, docs/, .myorch/flow.yaml, each step's artifacts and logs<br>mounted read-write at /sandbox/workspace", cls="blue"),
        c("projects/&lt;project&gt;/&lt;version&gt;/", "project pack snapshot<br>mounted read-only at workspace/project", cls="data"),
        c("knowledge/&lt;name&gt;/&lt;version&gt;/", "knowledge source snapshot<br>mounted read-only at workspace/knowledge/&lt;name&gt;", cls="data"),
        c("shared/&lt;project&gt;/&lt;name&gt;/", "caches across runs (build cache, sweep cursor)<br>mounted read-write only for steps that ask", cls="data"),
        c("tools/", "helper commands<br>mounted read-only at /sandbox/bin", cls="data"),
        c("jobs/&lt;key&gt;/", "one-off jobs (knowledge source sync etc.)", cls="data"),
      ]))
    + grid("1fr 1fr 1fr", [down("write back: project-apply commits → new snapshot", "dash"), down("worker writes events and live output", "dash"), down("push the branch after approval, no PR", "g")])
    + grid("1fr 1fr 1fr", [
        c("② myorch-knowledge/", "proposals adopted by learn · onboard · postmortem, knowledge sources registered in the console; all git commits"),
        c("③ myorch-data/", "temporal.db (ended runs kept 24 hours) · events.db · evals/ · learn/ · ui/ · slack/<br>the console reads the run list and conversations here"),
        c("The project's GitHub repository", "pushes only this run's branch"),
      ])
    + '<div class="note">At run start, the mandate pins the versions of the project pack and knowledge sources. ①②③ are on the host; sandboxes cannot see them.</div>')

FLOWS["run-lifecycle.html"] = df(
    c("Start: console “+ New run” · Slack /myorch · Claude Code tools · <code>myorch run</code> · <code>myorch schedule</code>",
      "Client: read the flow → pick parameters by presets → trim by project (if:) → expand fragments → fill in project values → check → sync knowledge sources · label the issue → submit the fully expanded YAML", cls="blue")
    + down("start_run")
    + c("Temporal starts FlowWorkflow (run_id = workflow ID, e.g. development-38b24c5b)",
        "init_run: creates runs/&lt;run_id&gt;/ on the volume, writes the flow snapshot and the mandate. If a local clone is registered, first copies the repository into repo/")
    + down()
    + frame("Loop: RunState.plan() finds the instances that can run now and handles each", grid("1fr 1fr 1fr", [
        c("Run an instance", "create directories → start sandbox → run harness → read manifest → delete sandbox"),
        c("Host action", "the worker runs it directly, no sandbox"),
        c("Approval needed → waiting_approval", "Approve, reject, or request changes (back to the loop before). Reminders and timeouts can be set", cls="you"),
        c("Agent asks → waiting_input", "The sandbox is deleted. After you reply, a new sandbox resumes the same session", cls="you"),
        c("Loop runs out of rounds, until still not met", "Reflects first, then asks you: one more round, accept, or stop", cls="you"),
        c("Step fails with on_failed: ask", "Asks you: start over, continue the session with a hint, skip, or stop", cls="you"),
      ]) + '<div class="note">On every state change the worker writes new events to myorch-data/events.db. Outlined boxes are where you are needed.</div>')
    + down()
    + c("Ended", "succeeded · failed · rejected · cancelled. A failed or cancelled run can be resumed with the same run_id (myorch resume, or the console's “Re-run failed steps”)"))

# ---------------- ARCHITECTURE.md ----------------
FLOWS["framework.html"] = df(
    frame("Entry", grid("repeat(4,1fr)", [
        c("Console", "myorch serve · ui/ + channels/"), c("myorch CLI", "cli.py"),
        c("Slack adapter", "in the serve process"), c("Claude Code channel", "connects to the console over HTTP", cls="paused")]))
    + down("Temporal client (engines/temporal/client.py): start_run · signal · query · cancel · resume")
    + frame("L4 flow engine (replaceable)", row(
        c("Temporal Server (dev)", "durable state · retries · timers · Schedules"), arr("drives", "bi"),
        c("engines/temporal · the only importer of temporalio", "FlowWorkflow: loops over RunState.plan()<br>activities: init_run · seed_repo · write_run_file · prepare_step · run_step · collect_manifest · run_action · cleanup_step · publish_events (only forward)<br>TemporalRunContext · ScheduledLaunch", cls="blue")))
    + down("StepRequest (StepKey: run_id, step_id, attempt, turn) + RunContext (heartbeat, cancellation)")
    + frame("L3 core library (engine-independent)", grid("1fr 1fr 1fr", [
        c("runner", "StepRunner.run() · profiles: image × policy × provider roles · harnesses/: claude_code, script · openshell.py: mount arguments, openshell CLI (each call has a time limit) · actions.py: 5 host actions"),
        c("storage", "DockerVolumeStore: init_run · prepare_step · reads manifests · project pack and knowledge source snapshots<br>EventLog (events.db)"),
        c("core", "flow · fragment · expr · run · prompt · manifest · events · inputs · types<br>pure logic, no IO"),
      ]) + '<div class="note">runner → storage → core, depending only downward</div>')
    + grid("1fr 1fr 1fr", [down("openshell sandbox create (policy · provider · mounts)"), down("docker run helper container", "dash"), down("project-apply: write, git commit", "dash")])
    + grid("2fr 1fr", [
        frame("L2 OpenShell 0.1.6", row(
            c("gateway", "provider records (the only place with real credentials) · policies · sandbox lifecycle · mounts only approved volumes", cls="green"), arr("sends config<br>and credentials"),
            frame("sandbox &lt;flow&gt;-&lt;step&gt;-&lt;hash&gt;",
                  c("workload container", "untrusted · network none<br>Landlock · seccomp · claude -p or bash", cls="dark")
                  + down("Unix socket (the only way out)", "g")
                  + c("supervisor container", "trusted · bridge network · policy decisions<br>injects credentials", cls="blue"), "dash"))
            + down("policy allows · real credentials swapped in → model API · GitHub · Jira", "g")),
        frame("L1 compute + storage (local Docker)",
              c("volume myorch-artifacts", "runs/&lt;run_id&gt;/ → /sandbox/workspace · .myorch/flow.yaml · .myorch/steps/<wbr>&lt;instance&gt;/out/manifest.json · projects/ · knowledge/ · tools/", cls="data")
              + '<div style="height:8px"></div>'
              + c("Host files", "myorch-data/events.db · myorch-knowledge/", cls="data")),
      ]))

FLOWS["dependencies.html"] = df(row(
    col(c("engines/temporal", "", cls="blue"), down("depends only downward, may skip layers"), c("runner"), down(), c("storage"), down(), c("core", "pure logic"), style="flex:1 1 0"),
    col(c("temporalio", "only engines/temporal may import it"), '<div style="height:10px"></div>',
        c("core, storage, runner importing temporalio", "forbidden by import-linter", cls="red"), style="flex:1 1 0"),
    col(frame("Outside the layer contract",
        c("Entry", "cli.py · ui/ · channels/<br>send commands (via client.py), read events and workspaces")
        + '<div style="height:8px"></div>'
        + c("Host helper modules", "project · knowledge · flowfile · evals · takeaways · runs · trail · watchdog · localrepo · review_page<br>runner and actions use project, evals, takeaways, knowledge directly")
        + '<div style="height:8px"></div>'
        + c("Deferred imports", "schedules.py and replay.py in engines/temporal import channels.relay, cli, flowfile inside functions; the contract does not check these", cls="paused")),
        style="flex:1.4 1 0"), style="gap:20px;align-items:flex-start")
    + '<div class="note"><code>uv run lint-imports</code> checks two contracts: layers (engines → runner → storage → core); temporalio only in engines.temporal.</div>')

FLOWS["multi-turn.html"] = df(grid("2fr 1fr", [
    col(c("Turn 1: new sandbox ①", "<code>claude -p &lt;full prompt&gt; --session-id &lt;sid&gt;</code>", cls="blue"),
        down(), c("agent calls /sandbox/bin/myorch-ask", "writes out/.myorch/question.json, ends the turn"),
        down("sandbox deleted"),
        c("workflow: waiting_input", "holds no sandbox · state lives in Temporal · the question event goes to the console, Slack, Claude Code · waits for a reply (limits.input_timeout, default 24 hours)", cls="you"),
        down("reply signal"),
        c("You reply", "console · Slack · myorch reply", cls="you"),
        down(),
        c("Turn 2: new sandbox ②", "<code>claude -p &lt;reply prompt&gt; --resume &lt;sid&gt;</code>", cls="blue"),
        down(), c("Done", "manifest: ok")),
    col(frame("Kept across turns (volume)", c("out/", "only the old manifest and question are deleted", cls="data") + '<div style="height:8px"></div>'
              + c(".claude/&lt;instance&gt;/", "session directory", cls="data") + '<div style="height:8px"></div>'
              + c("repo/ · docs/ · memory/", "workspace", cls="data"))),
  ])
  + '<div class="note">Session ID = uuid5(run_id/step_id/attempt): one session per attempt; a retry after a failure gets a new attempt and a new session; a retry after a platform interruption resumes the original session.</div>')

# ---------------- flow files, loops, project packs (08-flow-syntax, 07-projects) ----------------

FLOWS["flow-expansion.html"] = df(
    c("flows/&lt;flow&gt;.yaml", "The console, CLI, Slack and scheduled starts all use the same expansion (core/fragment.py expand_flow · flowfile.read_flow_file)", cls="data")
    + down()
    + frame("Expanded on the client at start; on an error the run does not start", numbered([
        ("1", "presets", "The input named by <code>presets.by</code> picks a parameter set; without it, the values in defaults apply. Replaces only <code>{{preset.x}}</code> in <code>steps</code>."),
        ("2", "trim by project", "<code>if:</code> on the flow's own steps and on <code>use:</code> is evaluated against <code>project</code>; if false, the step is removed and the steps that depend on it inherit its <code>needs</code>. An input whose <code>if:</code> is false becomes hidden and keeps its default. <code>{{project.x}}</code> in <code>with:</code> is filled in first and passed to the fragment with its type."),
        ("3", "use: fragments", "Reads <code>flows/lib/*.yaml</code>, checks <code>with</code> against <code>params</code>, replaces <code>{{params.x}}</code>, drops unwanted steps by the fragment's <code>if:</code> (params only) and trims <code>needs</code>. Fragment IDs come from the <code>id</code> of the <code>use</code> entry."),
        ("4", "project values", "<code>{{project.x}}</code> is read from the project pack's <code>project.yaml</code> (template overlaid); <code>{{project.prompts.&lt;slot&gt;}}</code> is the text of <code>prompts/&lt;slot&gt;.md</code>. Both are read fresh at start. A flow that uses project values or <code>if:</code> without <code>--project</code> is an error."),
        ("5", "check", "Flow model validation: IDs, needs graph, expressions, field ranges."),
      ]))
    + down("submit the fully expanded YAML")
    + c("Temporal starts FlowWorkflow", "Variables rendered at run time wait until each step runs: {{inputs}} · {{steps}} · {{run}} · {{loop}} · {{replica}} · {{approvals}}", cls="blue")
    + '<div class="note">To see the result first: <code>uv run myorch expand flows/&lt;flow&gt;.yaml --project &lt;project&gt;</code>. The console\'s Flows page shows the same result.</div>')

FLOWS["loop-round.html"] = df(
    c("The loop's needs are all met", "when false → the whole loop is skipped")
    + down("round 1")
    + frame("Round N (core/run.py _advance_loop)", numbered([
        ("1", "run the body", "Runs by needs; instance IDs are <code>x.rN</code>, parallel replicas <code>x.rN.xK</code>."),
        ("2", "all done?", "A step failed, was rejected or is blocked → the loop fails."),
        ("3", "until", "Evaluated on this round's outputs. True → the loop succeeds and the run moves on."),
        ("4", "rounds left?", "N &lt; max_rounds → next round. The next round sees the previous round's manifests (e.g. fix reads verify's verdict)."),
      ]))
    + down("Out of rounds and until still false: on_exhausted decides")
    + grid("1fr 1fr 1.4fr", [
        c("accept", "Accept the current result; the loop succeeds"),
        c("fail", "The loop fails, the run fails", cls="red"),
        c("ask (default)", "reflect runs first and gives advice, then asks you: 1 one more round · 2 accept · 3 stop. No reply in 24 hours counts as failure", cls="you"),
      ])
    + '<div class="note">An approval step\'s <code>changes</code> can reopen the loop: after you request changes, the loop runs up to <code>rounds</code> more rounds, and the steps after it are reset and run again.</div>')

FLOWS["pack-lifecycle.html"] = df(
    row(c("① Edit the project pack", "myorch-knowledge/&lt;project&gt;/<br>edited by hand, git commit", cls="data"), arr("project sync"),
        c("② Build a snapshot", "Overlays the template and the project, adds project.json, risk-rules.json. Leaves out project.yaml, image/, providers/, sources"), arr(),
        c("③ Snapshot in the volume", "projects/&lt;project&gt;/&lt;content hash&gt;/, never modified<br>.version points to the latest. Keeps the latest 3 and those under 30 days old", cls="data"), arr("start"),
        c("④ Run", "the mandate records the version used<br>mounted read-only at /sandbox/workspace/project/ in every step", cls="blue"))
    + down("proposals from learn · onboard · postmortem, project pack proposals from development's review loop")
    + frame("Write back", row(c("Agent makes proposals", "each with evidence"), arr(), c("You pick numbers", "choose step", cls="you"), arr(),
        c("project-apply", "host action: checks the scope of changes<br>writes back to myorch-knowledge, git commit", cls="green"), arr(),
        c("Rebuild the snapshot", "later steps of this run use the new version<br>so do future runs")))
    + '<div class="note"><code>{{project.x}}</code> is read fresh from project.yaml and prompts/ at start, not from the snapshot. The project.json that scripts read is in the snapshot and changes only after <code>project sync</code>.</div>')


out = sys.argv[1]
pages = {"services-overview.html": o.render(), "openshell-internals.html": s.render(),
         "compose-services.html": k.render(), **FLOWS}
HEAD = '<meta charset="utf-8">\n<!-- Generated by docs/diagrams/gen.py. Do not edit by hand. Embedded into docs/index.html by docs/build-html.py. -->\n'
for name, body in pages.items():
    open(f"{out}/{name}", "w").write(HEAD + CSS + body + "\n")
