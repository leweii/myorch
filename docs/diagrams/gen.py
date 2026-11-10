"""Generate the HTML diagrams of docs/ (pixel-placed ones and flow-layout ones).

    python3 docs/diagrams/gen.py docs/diagrams            # writes docs/diagrams/*.html and docs/diagrams/en/*.html
    python3 docs/diagrams/gen.py docs/diagrams --missing  # prints the Chinese texts that en.py does not translate yet

Boxes and arrows are placed by pixel coordinates (canvas 1140 px wide). Change a position here, run
this script, then rebuild the page (docs/build-html.py), which embeds the generated files.

The labels here are Chinese. The English diagrams (docs/diagrams/en/, used by the English docs) are the
same drawings with each Chinese text replaced from docs/diagrams/en.py. A text is what lies between two
block tags (div, b, span, li, ...), inline tags (code, br, i) included. Change a label here, then add
its translation to en.py; the script stops and lists any text without one.
"""
import html, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from en import EN  # noqa: E402

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
o.sec(20, 20, 440, 110, "人")
o.card(40, 52, 115, 58, "你", "浏览器")
o.card(170, 52, 115, 58, "Claude Code", "本机 MCP 适配器")
o.card(300, 52, 120, 58, "你", "终端")
o.sec(720, 20, 400, 110, "第三方平台 · sandbox 出站")
o.card(735, 52, 120, 58, "GitHub", "读 PR · 推分支")
o.card(865, 52, 120, 58, "Jira", "工单")
o.card(995, 52, 115, 58, "Claude API", "Anthropic")
o.sec(720, 190, 190, 125, "第三方 · 直连")
o.card(735, 228, 160, 64, "Slack", "私信 · 提问 · 审批")
o.sec(20, 190, 690, 125, "入口")
o.card(40, 228, 200, 64, "控制台（myorch serve）", "127.0.0.1:8800 · channel 层", "Slack 适配器 · Watchdog", cls="blue")
o.card(260, 228, 200, 64, "myorch CLI", "uv run myorch &lt;命令&gt;", "读事件日志经控制台")
o.card(40, 385, 260, 72, "Temporal dev server", "gRPC :7233 · 界面 :8233")
o.card(440, 385, 260, 72, "myorch worker", "执行步骤 · 写事件日志")
o.card(440, 540, 260, 92, "OpenShell", "gateway + 每个步骤一个 sandbox", "工作凭证只在这里", cls="green")
o.arrow([(97, 110), (97, 226)]); o.label(105, 150, "HTTP")
o.arrow([(227, 110), (227, 226)]); o.label(235, 150, "HTTP")
o.arrow([(360, 110), (360, 226)]); o.label(368, 150, "命令行")
o.arrow([(170, 315), (170, 383)]); o.label(180, 340, "gRPC：发起 · 回复 · 审批 · 查状态")
o.arrow([(440, 421), (302, 421)]); o.label(371, 398, "gRPC：取任务", "c")
o.arrow([(570, 457), (570, 538)]); o.label(580, 488, "openshell CLI：建、删 sandbox")
o.arrow([(700, 586), (925, 586), (925, 132)], "egress"); o.label(935, 330, "policy 放行的出站<br>supervisor 注入真实凭证", "g")
o.arrow([(225, 292), (225, 330), (815, 330), (815, 294)]); o.label(470, 336, "Slack 适配器：Socket Mode 出站")
o.sec(20, 680, 1100, 175, "本机数据 · 谁读谁写见下面的表")
chips = [("myorch-data/temporal.db", "Temporal 读写"), ("myorch-data/events.db", "worker 写 · 控制台读，宿主机命令经控制台读"),
         ("myorch-data/ui/", "控制台读写"),
         ("myorch-knowledge/", "入口读 · project-apply 和控制台写"), ("volume myorch-artifacts", "sandbox 和辅助容器读写")]
for i, (t, sub) in enumerate(chips):
    o.card(40 + (i % 3) * 360, 714 + (i // 3) * 68, 340, 58, t, sub, cls="data")
o.arrow([(30, 885), (76, 885)]); o.label(84, 876, "调用")
o.arrow([(150, 885), (196, 885)], "egress"); o.label(204, 876, "policy 放行的出站")

# ---------------- openshell ----------------
s = D("os", 1140, 590)
s.card(880, 14, 230, 50, "第三方平台", "GitHub · Jira · Claude API 等")
s.sec(20, 90, 380, 420, "OpenShell gateway（控制面）", right=True)
s.card(45, 130, 330, 76, "API server", "认证 · sandbox 生命周期 · 签发 JWT", cls="blue")
s.card(45, 250, 330, 76, "持久状态", "sandbox · policy · provider（真实凭证）")
s.card(45, 380, 330, 92, "compute driver：docker", "建两个容器和它们之间的 socket", "只建边界，不判定请求", cls="green")
s.sec(440, 90, 680, 380, "sandbox（每个步骤实例一个）", "dash")
s.card(465, 135, 270, 300, "supervisor 容器", "可信一侧 · bridge 网络", cls="blue",
       inner="<hr><ul><li>policy 判定</li><li>占位符换成真实凭证</li><li>DNS 和代理 · TLS 重签</li><li>保持 gateway 会话（JWT）</li></ul>")
s.box(780, 135, 320, 300, "border:1.5px dashed var(--d-danger);background:var(--d-danger-soft)")
s.label(795, 144, "<b>workload 容器</b> · 不可信一侧 · 网络 none")
s.card(805, 178, 270, 76, "harness 进程", "claude -p 或 bash · 不可信", cls="dark")
s.note(812, 264, "· Landlock 限制文件系统<br>· 环境变量里只有凭证占位符<br>· seccomp 拦截 TCP 和 DNS<br>· /sandbox/ 下挂着 run 的工作区")
s.card(800, 372, 280, 52, "禁止所有出站", "只能经 Unix socket 到 supervisor", cls="green")
s.arrow([(90, 20), (90, 128)]); s.label(100, 36, "openshell CLI：建、删 sandbox（myorch worker）<br>导入 provider（compose 的 providers 服务）")
s.arrow([(210, 206), (210, 248)], "data"); s.label(218, 218, "读写")
s.arrow([(45, 168), (30, 168), (30, 426), (43, 426)])
s.arrow([(375, 426), (438, 426)]); s.label(407, 402, "建容器", "c")
s.arrow([(465, 175), (377, 175)]); s.label(421, 136, "回连：取<br>policy 和凭证", "c")
s.arrow([(805, 216), (737, 216)], "egress"); s.label(771, 222, "Unix<br>socket", "c g")
s.arrow([(700, 135), (700, 39), (878, 39)], "egress"); s.label(712, 44, "policy 放行的出站<br>占位符换成真实凭证", "g")
s.arrow([(40, 545), (86, 545)]); s.label(94, 536, "调用")
s.arrow([(40, 571), (86, 571)], "egress"); s.label(94, 562, "出站请求的路径")
s.arrow([(200, 545), (246, 545)], "data"); s.label(254, 536, "读写数据")



# ---------------- docker compose (deploy/compose.yaml) ----------------
k = D("dc", 1140, 1130)
k.sec(20, 20, 540, 110, "人")
k.card(60, 52, 190, 58, "你", "浏览器", cls="you")
k.card(290, 52, 240, 58, "你", "终端 · docker compose exec", cls="you")
k.sec(600, 20, 520, 110, "宿主机上的文件")
k.card(615, 52, 240, 58, "deploy/secrets/", "providers.yaml → providers<br>slack.env → ui · 不进 git、不进镜像", cls="data")
k.card(870, 52, 235, 58, "数据目录 · 知识库", "myorch-data/ · myorch-knowledge/<br>channels.yaml · ~/orca/kb（bind mount）", cls="data")
k.sec(20, 170, 1100, 360, "docker compose · 网络 default · 服务之间用服务名互相访问")
k.card(40, 220, 230, 66, "ui", "控制台 · channel 层 · Slack · Watchdog", cls="blue")
k.sec(340, 205, 770, 115, "一次性服务 · 跑完退出", "dash")
k.card(360, 245, 200, 60, "init", "myorch init：volume、tools<br>每次 up 都跑")
k.card(580, 245, 220, 60, "providers", "读凭证文件 → 写进 gateway")
k.card(820, 245, 260, 60, "gateway-init", "没有密钥时生成 sandbox 签名密钥（Ed25519）")
k.card(40, 400, 230, 66, "temporal", "gRPC :7233 · 界面 :8233")
k.card(360, 400, 230, 66, "worker", "myorch worker · 执行步骤", cls="blue")
k.card(820, 400, 260, 80, "gateway", "OpenShell 官方镜像 · :17671", "凭证 · policy · sandbox 记录", cls="green")
k.sec(20, 600, 1100, 330, "宿主机 Docker · 经挂进容器的 /var/run/docker.sock")
k.card(40, 640, 230, 66, "Docker daemon", "/var/run/docker.sock")
k.card(40, 750, 230, 60, "辅助容器", "--network none · 读写 volume")
k.card(40, 850, 230, 54, "volume myorch-artifacts", "所有 run 的工作区", cls="data")
k.box(330, 630, 520, 170, "border:1px dashed var(--d-line2)")
k.label(345, 638, "<b>sandbox</b> · 每个步骤实例一对容器")
k.card(350, 670, 200, 100, "supervisor 容器", "bridge 网络", "policy · 换入真实凭证", cls="blue")
k.card(620, 670, 210, 100, "workload 容器", "agent 或脚本 · 网络 none", cls="dark")
k.card(880, 640, 200, 56, "volume gateway-state", "签名密钥 · gateway 数据库", cls="data")
k.card(330, 975, 520, 54, "第三方平台", "GitHub · api.anthropic.com 等，policy 放行的地址")
# people
k.arrow([(155, 110), (155, 218)]); k.label(163, 140, "HTTP 127.0.0.1:8800")
k.arrow([(310, 110), (310, 415), (358, 415)]); k.label(318, 140, "myorch 命令<br>openshell 命令")
k.arrow([(690, 110), (690, 243)], "data"); k.label(698, 140, "只读挂载 /secrets")
# inside compose
k.arrow([(155, 286), (155, 398)]); k.label(163, 330, "gRPC temporal:7233<br>发起 · 回复 · 审批")
k.arrow([(360, 433), (272, 433)]); k.label(316, 440, "gRPC：取任务", "c")
k.arrow([(690, 305), (690, 360), (860, 360), (860, 398)]); k.label(698, 330, "openshell CLI：profile、provider")
k.arrow([(590, 433), (818, 433)]); k.label(704, 410, "openshell CLI：建、删 sandbox", "c")
k.label(704, 440, "http://gateway:17671", "c gr")
k.arrow([(1080, 275), (1108, 275), (1108, 668), (1082, 668)], "data"); k.label(1050, 572, "写密钥")
# to the host's Docker
k.arrow([(380, 466), (380, 570), (300, 570), (300, 673), (272, 673)]); k.label(166, 545, "docker CLI：起辅助容器<br>（ui、init 也这样）")
k.arrow([(155, 706), (155, 748)]); k.arrow([(155, 810), (155, 848)], "data")
k.arrow([(840, 480), (840, 628)]); k.label(848, 540, "docker API：建两个容器")
k.arrow([(980, 480), (980, 638)], "data"); k.label(988, 556, "读写")
k.arrow([(340, 800), (340, 877), (272, 877)], "data"); k.label(350, 850, "挂载 subpath")
# the call back and egress
k.arrow([(520, 668), (520, 560), (780, 560), (780, 470), (818, 470)]); k.label(530, 520, "回连 host.docker.internal:17671<br>经宿主机上发布的端口", "g")
k.arrow([(620, 720), (552, 720)], "egress"); k.label(586, 726, "Unix<br>socket", "c g")
k.arrow([(450, 770), (450, 973)], "egress"); k.label(460, 900, "policy 放行的出站 · 换入真实凭证", "g")
legend(k, 40, 1046 - 24 * 0, [("call", "调用")])
k.arrow([(160, 1055), (206, 1055)], "egress"); k.label(214, 1046, "出站请求")
k.arrow([(320, 1055), (366, 1055)], "data"); k.label(374, 1046, "读写数据")
k.note(40, 1080, "图里没画的连接：ui 主动连 Slack（Socket Mode，出站，不开端口）；<br>本机 Claude Code 会话连 ui 的 127.0.0.1:8800；宿主机上的 <code>uv run myorch</code> 连 temporal 的 127.0.0.1:7233，读事件经 ui。")

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
    col(row(c("gateway-init", "没有密钥时生成，跑完退出"), arr("成功后"), c("gateway", "常驻", cls="green"), arr("启动后"),
            c("providers", "导入凭证，跑完退出")),
        '<div style="height:10px"></div>',
        row(c("init", "myorch init，每次 up 都跑，跑完退出"), '<div style="width:14px"></div>', c("temporal", "常驻")),
        style="flex:3 1 0"),
    arr("都满足后"),
    col(c("worker", "要 init、providers 成功<br>temporal、gateway 已启动", cls="blue"), '<div style="height:8px"></div>',
        c("ui", "要 init 成功、temporal 已启动"), style="flex:1 1 0"))
    + '<div class="note">compose 按 depends_on 排序。providers 失败时 worker 不启动。控制台不等 gateway 和 providers。</div>')

def band(tag, sub, chips, body, hl=False):
    ch = "".join(f'<span class="chip {"p" if "暂停" in x else ""}">{x}</span>' for x in chips)
    return (f'<div class="band {"hl" if hl else ""}"><div class="tag">{tag}<small>{sub}</small></div>'
            f'<div class="body"><div class="chips">{ch}</div>{body}</div></div>')

def stage(title, sub, desc, badge, kind, cls=""):
    return (f'<div class="c stage {cls}"><div><b>{title}</b><span>{sub}</span></div>'
            f'<div style="font-size:12.5px;color:var(--d-text)">{desc}</div><span class="badge {kind}">{badge}</span></div>')

FLOWS["deployment.html"] = df(frame("macOS · 单机 · docker compose（deploy/compose.yaml）", grid("repeat(3,1fr)", [
    c("temporal", "容器 · 127.0.0.1:7233 / 8233<br>数据在 myorch-data/temporal.db"),
    c("gateway", "OpenShell 官方镜像 · 127.0.0.1:17671<br>凭证、policy、sandbox 记录", cls="green"),
    c("worker", "容器 · 执行所有步骤<br>经 docker.sock 起 sandbox 和辅助容器"),
    c("ui（myorch serve）", "容器 · 127.0.0.1:8800<br>控制台 + channel 层 + Watchdog + Slack 适配器", cls="blue"),
    c("Docker Desktop", "每个步骤一对 sandbox 容器<br>辅助容器 · volume myorch-artifacts"),
    c("一次性服务", "gateway-init · init · providers<br>每次 up 跑完退出"),
  ]))
  + down("事件推给 Claude Code。它主动连 ui 的 HTTP 接口读事件、调用工具（会话启动它）", "dash")
  + c("Claude Code channel", "channels/claude_code.py · 推送通知、提供工具 · 不能审批", cls="you")
  + '<div class="note">Slack 适配器主动连 Slack（Socket Mode），机器不用开放端口。只支持单机。团队化（服务端事件存储、登录、按项目的权限）还没做。</div>')

# ---------------- layers, components, one step, credentials, console (03-architecture) ----------------
FLOWS["layers.html"] = df(
    band("入口", "本机进程", ["cli.py", "ui/", "channels/", "watchdog.py", "review_page.py"], "读流程和项目包，经 Temporal client 发命令，读事件和工作区。")
    + down("调用")
    + band("L4 流程引擎", "可替换", ["engines/temporal/"], "唯一 import temporalio 的地方。FlowWorkflow 反复调用 RunState.plan()，activity 只做转发。ScheduledLaunch 负责定时发起。")
    + down("调用")
    + band("L3 核心库", "与引擎无关", ["runner/", "→", "storage/", "→", "core/"],
           "<ul><li>core：schema、调度、表达式、prompt、manifest、事件，约定不做 IO</li><li>storage：volume 的目录和文件，事件日志</li><li>runner：步骤 → profile + harness → openshell 调用；宿主机动作</li>"
           "<li>旁边的领域模块（project、knowledge、evals、takeaways、trail、runs、localrepo、flowfile）只依赖 core，不在分层契约里</li></ul>", hl=True)
    + down("openshell CLI · docker CLI")
    + band("L2 OpenShell", "隔离和凭证", ["gateway", "supervisor"], "<ul><li>gateway：凭证、policy、sandbox 生命周期</li><li>supervisor：sandbox 外的可信容器，落实 policy，注入凭证</li></ul>")
    + down()
    + band("L1 Docker", "计算和存储", ["每个 sandbox 两个容器", "辅助容器", "volume myorch-artifacts"], "sandbox 容器、读写 volume 的辅助容器、所有 run 的工作区。")
    + '<div class="note"><code>uv run lint-imports</code> 检查两条：engines → runner → storage → core 只能向下依赖；core、storage、runner 不能 import temporalio。</div>')

FLOWS["components.html"] = df(
    row(c("控制台 · Slack · Claude Code", "channels/relay.py"), arr("命令"),
        c("Temporal client", "engines/temporal/client.py<br>start · signal · cancel · resume"), arr("gRPC"),
        c("Temporal", "持久状态 · 重试 · 等待信号 · Schedule"), arr("驱动", "bi"),
        c("FlowWorkflow", "engines/temporal/workflow.py<br>RunState（core/run.py）决定下一步跑什么", cls="blue"))
    + down("调用 activities（engines/temporal/activities.py，只做参数转换）")
    + frame("activities · 都在 worker 里", grid("repeat(4,1fr)", [
        col(c("init_run · seed_repo · write_run_file<br>prepare_step · collect_manifest"), down(), c("DockerVolumeStore", "storage/docker_volume.py"), down("docker run"), c("辅助容器", "--network none"), down("读写", "dash"), c("volume", "myorch-artifacts", cls="data")),
        col(c("run_step · cleanup_step", cls="blue"), down(), c("StepRunner", "runner/runner.py<br>profile → 镜像、policy、<br>provider、挂载 · harness → 命令行"), down("openshell CLI"), c("OpenShell gateway", "建、查、删 sandbox", cls="green"), down(), c("sandbox", "supervisor 容器 + workload 容器")),
        col(c("run_action"), down(), c("ActionRunner", "runner/actions.py · 不起 sandbox"), down(), c("5 个宿主机动作", "project-apply · postmortem-replay<br>run-trail · takeaway-redact · takeaways-learned"), down("写", "dash"), c("myorch-knowledge/ · myorch-data/ · 运行的工作区", "manifest 写进 out/", cls="data")),
        col(c("publish_events"), down(), c("EventLog", "storage/events.py"), down("写", "dash"), c("myorch-data/events.db", "事件表 · 实时输出表（StepRunner 写）", cls="data")),
      ]))
    + '<div class="note">cleanup_step 只在 run_step 出异常时调用；正常时 run_step 自己删 sandbox。worker 里还有定时发起用的 ScheduledLaunch。</div>')

FLOWS["step-execution.html"] = df(
    c("plan() → [develop]", "RunState 选出一个能跑的实例", cls="blue") + down()
    + frame("FlowWorkflow 依次调用 activity", numbered([
        ("1", "prepare_step", "在 volume 建目录，chown 1000。新 attempt（第 1 轮）清空 out/；后面的轮只删旧的 manifest 和 question。会话目录和 logs/ 都保留。"),
        ("2", "run_step", "StepRunner：先删同名的残留 sandbox。profile → 镜像、policy、provider，拼挂载，组装 prompt，<code>openshell sandbox create</code>，名字是 <code>&lt;流程&gt;-&lt;步骤&gt;-&lt;哈希&gt;</code>。轮询直到结束，定时心跳。结束后先存 supervisor 日志，再删 sandbox。"),
        ("3", "collect_manifest", "读 manifest.json 和 question.json，缺了就兜底生成。workflow 再按 escalate_when 把结果转成提问。"),
      ]))
    + down()
    + grid("1fr 1fr 1fr", [
        c("完成", "回到 plan()，找下一批能跑的实例。一个实例一个 sandbox，不复用"),
        c("needs_input → waiting_input", "你回复后 turn+1，起新 sandbox 续接同一个会话", cls="you"),
        c("失败", "按 limits.retries 换 attempt 重试。用完后看 on_failed", cls="red"),
      ])
    + '<div class="note">带 <code>action:</code> 的步骤只调用一个 run_action（最多 2 次）。run_step 出异常（worker 崩溃、心跳超时）时调用 cleanup_step，确保 sandbox 被删。每次状态有新事件，后台的 publish_events 把它写进 events.db。</div>')

FLOWS["credential-flow.html"] = df(
    stage("deploy/secrets/providers.yaml", "宿主机上的文件 · 不进 git", "compose 的 providers 服务（deploy/provision.py）读它，以 <code>--credential KEY</code> 经子进程的环境变量传给 gateway。不在命令行里出现值。", "真实 token", "n", "you")
    + down("每次 docker compose up")
    + stage("OpenShell gateway", "控制面", "保存真实 token。", "有真实 token", "k", "green")
    + down("sandbox 创建时")
    + stage("myorch worker", "也包括控制台、CLI", "只传 provider 的名字，例如 claude、github-mdm。", "只有名字", "n")
    + down()
    + stage("workload 容器", "agent 进程", "环境变量里只有占位符。容器没有网络。请求被 seccomp 拦截，经 Unix socket 交给 supervisor。", "只有占位符", "x", "red")
    + down("Unix socket", "g")
    + stage("supervisor 容器", "可信一侧", "检查 policy 是否放行这个地址和路径 → 把占位符换成真实 token → 建立连接并转发。", "有真实 token", "k", "blue")
    + down("policy 放行的出站", "g")
    + c("请求的目标", "policy 放行的地址"))

FLOWS["console.html"] = df(
    c("浏览器", "http://127.0.0.1:8800/", cls="you")
    + down("HTTP（页面、/api、图片 /api/runs/&lt;id&gt;/raw）· SSE（/channel/events、/channel/output）")
    + c("myorch serve", "ui/app.py：页面和 /api · channels/http.py：/channel · Host 必须是本机 · POST 要带 X-Myorch-UI", cls="blue")
    + down()
    + c("ui/service.py", "控制台的读写逻辑。ui/runview.py 用事件拼出运行状态。控制台里没有 agent")
    + down("调用")
    + c("channel 转发层", "channels/relay.py：事件 · 命令 · 内容", cls="green")
    + down()
    + grid("1fr 1fr 1fr", [c("events.db", "事件 + 实时输出", cls="data"),
                          c("Temporal client", "发起 · 回复 · 审批 · 取消 · 重新执行"),
                          c("run 的工作区", "文件 · diff · 日志（辅助容器）", cls="data")])
    + '<div class="note">同一个进程里还有 Watchdog 和 Slack 适配器。宿主机上的 CLI 也经这里读事件（/channel/events.json）。控制台对没有详细事件的旧运行，暂时回退到 Temporal query。</div>')

FLOWS["run-continuation.html"] = df(
    c("一次尝试失败了", "步骤的 attempt N")
    + down()
    + grid("1fr 1fr", [
        c("还有重试次数（limits.retries）", "自动重试：新 attempt、新 sandbox。平台中断的（超时、activity 丢失、sandbox 失败）续接原会话，先让 agent 看仓库现状。运行已取消时不重试"),
        col(c("重试用完：看 on_failed"), '<div style="height:8px"></div>', grid("1fr 1fr 1fr", [
            c("fail（默认）", "步骤失败，后面的被挡住，运行失败", cls="red"),
            c("skip", "当作跳过，运行接着走"),
            c("ask", "问你：1 重新开始 · 写提示接着原会话 · 2 跳过 · 3 终止", cls="you")])),
      ])
    + down("运行结束了")
    + grid("1fr 1fr", [
        c("失败或取消 → 重新执行", "<code>myorch resume</code> 或控制台按钮。同一个 run_id，新的 Temporal 执行带着原来的 RunState。失败、取消、被挡住的步骤重置；成功的步骤和审批保留，驳回的仍是驳回。只能是 Temporal 还留着的运行（结束 24 小时内）", cls="blue"),
        c("已结束 → 接着做（from_run 这类 run 输入）", "新的 run_id 和工作区，同一个项目、同一个角色。carry 的输入和标签带过来，人说过的话写进 docs/previous/conversation.md。development 在原分支上接着改"),
      ]))

FLOWS["channel-layer.html"] = df(row(
    col(frame("worker", c("FlowWorkflow", "publish_events → 事件表") + down() + c("StepRunner", "跟着 agent 的日志（辅助容器 tail -F）→ 实时输出表")),
        down("写", "dash"), c("myorch-data/events.db", "SQLite WAL · 事件表 · 实时输出表", cls="data"), style="flex:1 1 0"),
    arr("读"),
    col(frame("myorch serve（ui 容器）",
        c("channels/relay.py", "事件 · 命令 · 内容", cls="blue")
        + '<div style="height:8px"></div>'
        + grid("1fr 1fr", [c("Watchdog", "读事件，发提醒"), c("Slack 适配器", "进程内 follow · Socket Mode 出站")])
        + '<div style="height:8px"></div>'
        + c("channels/http.py · /channel", "SSE /events /output · JSON /events.json /runs · POST 命令")),
        style="flex:1.3 1 0"),
    arr("HTTP · SSE", "bi"),
    col(c("浏览器控制台", "SSE + /api", cls="you"), '<div style="height:8px"></div>',
        c("Claude Code channel", "会话启动的单独进程 · 批准、驳回、提修改意见返回 403"), '<div style="height:8px"></div>',
        c("宿主机 CLI", "GET /channel/events.json，从不直接打开文件"), '<div style="height:8px"></div>',
        c("Slack 私信线程", "进度 · 提问 · 审批按钮"), style="flex:1 1 0"))
    + down("命令：relay 按 channels.yaml 认人 → engines/temporal/client.py → 发起、signal、取消、重新执行", "dash")
    + c("FlowWorkflow（Temporal）", "内容（文件、diff、日志）另走 relay → DockerVolumeStore 的辅助容器 → runs/&lt;id&gt;/")
    + '<div class="note">事件只在开始、需要人、结束时发，加上画运行状态用的细节事件。实时输出单独一张表。</div>')

# ---------------- directories and storage (01-overview) ----------------
FLOWS["storage-relations.html"] = df(
    grid("1fr 1fr 1fr", [
        c("① myorch/", "flows/ · profiles/ · tools/"),
        c("② myorch-knowledge/", "&lt;项目&gt;/project.yaml · prompts/ · guide/ · checks.sh · sources/ · _templates/"),
        c("知识源的内容", "本机目录（~/orca/kb 下）或 git 仓库"),
      ])
    + grid("1fr 1fr 1fr", [down("发起时展开 · myorch init 装 tools/"), down("project sync（分版本快照）"), down("发起运行时自动同步")])
    + frame("④ volume myorch-artifacts · sandbox 只看得到这里", grid("1fr 1fr 1fr", [
        c("runs/&lt;run&gt;/", "工作区：代码、docs/、.myorch/flow.yaml、每步的产物和日志<br>读写挂到 /sandbox/workspace", cls="blue"),
        c("projects/&lt;项目&gt;/&lt;版本&gt;/", "项目包快照<br>只读挂到 workspace/project", cls="data"),
        c("knowledge/&lt;名字&gt;/&lt;版本&gt;/", "知识源快照<br>只读挂到 workspace/knowledge/&lt;名字&gt;", cls="data"),
        c("shared/&lt;项目&gt;/&lt;名字&gt;/", "跨运行的缓存（构建缓存、sweep 游标）<br>步骤要了才读写挂载", cls="data"),
        c("tools/", "辅助命令<br>只读挂到 /sandbox/bin", cls="data"),
        c("jobs/&lt;key&gt;/", "一次性任务（知识源同步等）", cls="data"),
      ]))
    + grid("1fr 1fr 1fr", [down("写回：project-apply 提交 → 新快照", "dash"), down("worker 写事件和实时输出", "dash"), down("审批后推分支，不开 PR", "g")])
    + grid("1fr 1fr 1fr", [
        c("② myorch-knowledge/", "learn · onboard · postmortem 采纳的建议，控制台登记知识源，都是 git 提交"),
        c("③ myorch-data/", "temporal.db（已结束保留 24 小时）· events.db · evals/ · learn/ · ui/ · slack/<br>控制台从这里读运行列表和对话"),
        c("项目的 GitHub 仓库", "只推这个运行的分支"),
      ])
    + '<div class="note">运行开始时，mandate 固定项目包和知识源的版本。①②③ 在宿主机上，sandbox 看不到。</div>')

FLOWS["run-lifecycle.html"] = df(
    c("发起：控制台「＋ 新运行」· Slack /myorch · Claude Code 工具 · <code>myorch run</code> · <code>myorch schedule</code>",
      "客户端：读 flow → 按 presets 选参数 → 按项目裁剪（if:）→ 展开片段 → 填入项目值 → 检查 → 同步知识源 · 工单加标签 → 提交展开后的完整 YAML", cls="blue")
    + down("start_run")
    + c("Temporal 启动 FlowWorkflow（run_id = workflow ID，形如 development-38b24c5b）",
        "init_run：在 volume 建 runs/&lt;run_id&gt;/，写 flow 快照和 mandate。登记了本机克隆时先把仓库复制进 repo/")
    + down()
    + frame("循环：RunState.plan() 找出现在能跑的实例，逐个处理", grid("1fr 1fr 1fr", [
        c("执行一个实例", "建目录 → 起 sandbox → 跑 harness → 读 manifest → 删 sandbox"),
        c("宿主机动作", "worker 直接执行，不起 sandbox"),
        c("需要审批 → waiting_approval", "批准、驳回，或提修改意见（回到前面的循环）。可以设提醒和超时", cls="you"),
        c("agent 提问 → waiting_input", "sandbox 已删除。你回复后起新 sandbox，续接同一个会话", cls="you"),
        c("循环跑满仍未满足 until", "先反思，再问你：再跑一轮、接受或终止", cls="you"),
        c("步骤失败且 on_failed: ask", "问你：重新开始、给提示接着会话、跳过或终止", cls="you"),
      ]) + '<div class="note">每次状态变化，worker 把新事件写进 myorch-data/events.db。双线框是需要你的地方。</div>')
    + down()
    + c("结束", "succeeded · failed · rejected · cancelled。失败或取消的运行可以用同一个 run_id 重新执行（myorch resume，或控制台「重新执行失败的步骤」）"))

# ---------------- ARCHITECTURE.md ----------------
FLOWS["framework.html"] = df(
    frame("入口", grid("repeat(4,1fr)", [
        c("控制台", "myorch serve · ui/ + channels/"), c("myorch CLI", "cli.py"),
        c("Slack 适配器", "在 serve 进程里"), c("Claude Code channel", "经 HTTP 连控制台", cls="paused")]))
    + down("Temporal client（engines/temporal/client.py）：start_run · signal · query · cancel · resume")
    + frame("L4 流程引擎（可替换）", row(
        c("Temporal Server（dev）", "持久状态 · 重试 · 定时器 · Schedule"), arr("驱动", "bi"),
        c("engines/temporal · 唯一 import temporalio", "FlowWorkflow：循环 RunState.plan()<br>activities：init_run · seed_repo · write_run_file · prepare_step · run_step · collect_manifest · run_action · cleanup_step · publish_events（只做转发）<br>TemporalRunContext · ScheduledLaunch", cls="blue")))
    + down("StepRequest（StepKey：run_id、step_id、attempt、turn）+ RunContext（心跳、取消）")
    + frame("L3 核心库（与引擎无关）", grid("1fr 1fr 1fr", [
        c("runner", "StepRunner.run() · profiles：镜像 × policy × provider 角色 · harnesses/：claude_code、script · openshell.py：挂载参数、openshell CLI（每个调用有时限）· actions.py：5 个宿主机动作"),
        c("storage", "DockerVolumeStore：init_run · prepare_step · 读 manifest · 项目包和知识源快照<br>EventLog（events.db）"),
        c("core", "flow · fragment · expr · run · prompt · manifest · events · inputs · types<br>纯逻辑，不做 IO"),
      ]) + '<div class="note">runner → storage → core，只向下依赖</div>')
    + grid("1fr 1fr 1fr", [down("openshell sandbox create（policy · provider · 挂载）"), down("docker run 辅助容器", "dash"), down("project-apply：写、git 提交", "dash")])
    + grid("2fr 1fr", [
        frame("L2 OpenShell 0.1.6", row(
            c("gateway", "provider 记录（真实凭证只在这里）· policy · sandbox 生命周期 · 只挂已审批的 volume", cls="green"), arr("下发配置和凭证"),
            frame("sandbox &lt;流程&gt;-&lt;步骤&gt;-&lt;哈希&gt;（两个容器）",
                  c("workload 容器", "不可信 · 网络 none · Landlock · seccomp<br>claude -p 或 bash", cls="dark")
                  + down("Unix socket（唯一出站路径）", "g")
                  + c("supervisor 容器", "可信 · bridge 网络 · policy 判定 · 注入凭证", cls="blue"), "dash"))
            + down("policy 放行 · 换入真实凭证 → 模型 API · GitHub · Jira", "g")),
        frame("L1 计算 + 存储（单机 Docker）",
              c("volume myorch-artifacts", "runs/&lt;run_id&gt;/ → /sandbox/workspace · .myorch/flow.yaml · .myorch/steps/&lt;实例&gt;/out/manifest.json · projects/ · knowledge/ · tools/", cls="data")
              + '<div style="height:8px"></div>'
              + c("宿主机文件", "myorch-data/events.db · myorch-knowledge/", cls="data")),
      ]))

FLOWS["dependencies.html"] = df(row(
    col(c("engines/temporal", "", cls="blue"), down("只能向下依赖，可以跨层"), c("runner"), down(), c("storage"), down(), c("core", "纯逻辑"), style="flex:1 1 0"),
    col(c("temporalio", "只有 engines/temporal 能 import"), '<div style="height:10px"></div>',
        c("core、storage、runner import temporalio", "import-linter 禁止", cls="red"), style="flex:1 1 0"),
    col(frame("不在分层契约里",
        c("入口", "cli.py · ui/ · channels/<br>发命令（经 client.py），读事件和工作区")
        + '<div style="height:8px"></div>'
        + c("宿主机辅助模块", "project · knowledge · flowfile · evals · takeaways · runs · trail · watchdog · localrepo · review_page<br>runner 和 actions 直接用 project、evals、takeaways、knowledge")
        + '<div style="height:8px"></div>'
        + c("延迟 import", "engines/temporal 的 schedules.py、replay.py 在函数里 import channels.relay、cli、flowfile，契约不检查", cls="paused")),
        style="flex:1.4 1 0"), style="gap:20px;align-items:flex-start")
    + '<div class="note"><code>uv run lint-imports</code> 检查两条契约：分层（engines → runner → storage → core）；temporalio 只在 engines.temporal。</div>')

FLOWS["multi-turn.html"] = df(grid("2fr 1fr", [
    col(c("第 1 轮：新 sandbox ①", "<code>claude -p &lt;完整 prompt&gt; --session-id &lt;sid&gt;</code>", cls="blue"),
        down(), c("agent 调 /sandbox/bin/myorch-ask", "写 out/.myorch/question.json，结束这一轮"),
        down("sandbox 删除"),
        c("workflow：waiting_input", "不占 sandbox · 状态在 Temporal · 提问事件发到控制台、Slack、Claude Code · 等回复（limits.input_timeout，默认 24 小时）", cls="you"),
        down("reply signal"),
        c("你回复", "控制台 · Slack · myorch reply", cls="you"),
        down(),
        c("第 2 轮：新 sandbox ②", "<code>claude -p &lt;回复 prompt&gt; --resume &lt;sid&gt;</code>", cls="blue"),
        down(), c("完成", "manifest：ok")),
    col(frame("跨轮保留（volume）", c("out/", "只删旧的 manifest 和 question", cls="data") + '<div style="height:8px"></div>'
              + c(".claude/&lt;实例&gt;/", "会话目录", cls="data") + '<div style="height:8px"></div>'
              + c("repo/ · docs/ · memory/", "工作区", cls="data"))),
  ])
  + '<div class="note">会话 ID = uuid5(run_id/step_id/attempt)：同一个 attempt 共用一个会话；失败重试换 attempt，也换会话；平台中断后的重试续接原会话。</div>')

# ---------------- flow files, loops, project packs (08-flow-syntax, 07-projects) ----------------

FLOWS["flow-expansion.html"] = df(
    c("flows/&lt;流程&gt;.yaml", "控制台、CLI、Slack、定时发起都走同一套展开（core/fragment.py expand_flow · flowfile.read_flow_file）", cls="data")
    + down()
    + frame("发起时在客户端展开，出错就不启动", numbered([
        ("1", "presets", "<code>presets.by</code> 指定的输入选一组参数，没给就用 defaults 里的值。只替换 <code>steps</code> 里的 <code>{{preset.x}}</code>。"),
        ("2", "按项目裁剪", "flow 自己的步骤和 <code>use:</code> 上的 <code>if:</code> 对 <code>project</code> 求值，为假就去掉，依赖它的步骤继承它的 <code>needs</code>。输入的 <code>if:</code> 为假时变成 hidden，保留默认值。<code>with:</code> 里的 <code>{{project.x}}</code> 先填好，按类型传给片段。"),
        ("3", "use: 片段", "读 <code>flows/lib/*.yaml</code>，按 <code>params</code> 检查 <code>with</code>，替换 <code>{{params.x}}</code>，按片段里的 <code>if:</code>（只看 params）去掉不要的步骤并修剪 <code>needs</code>。片段的 ID 来自 <code>use</code> 那一项的 <code>id</code>。"),
        ("4", "项目值", "<code>{{project.x}}</code> 从项目包的 <code>project.yaml</code>（叠加模板）读，<code>{{project.prompts.&lt;slot&gt;}}</code> 是 <code>prompts/&lt;slot&gt;.md</code> 的原文，发起时现读。流程用到项目值或 <code>if:</code> 却没给 <code>--project</code> 时报错。"),
        ("5", "检查", "Flow 模型校验：ID、needs 图、表达式、字段范围。"),
      ]))
    + down("提交展开后的完整 YAML")
    + c("Temporal 启动 FlowWorkflow", "运行时才渲染的变量留到执行每一步时：{{inputs}} · {{steps}} · {{run}} · {{loop}} · {{replica}} · {{approvals}}", cls="blue")
    + '<div class="note">想先看结果：<code>uv run myorch expand flows/&lt;流程&gt;.yaml --project &lt;项目&gt;</code>。控制台「流程」页显示的也是这个结果。</div>')

FLOWS["loop-round.html"] = df(
    c("循环的 needs 都满足了", "when 为假 → 整个循环跳过")
    + down("第 1 轮")
    + frame("第 N 轮（core/run.py _advance_loop）", numbered([
        ("1", "跑循环体", "按 needs 执行，实例 ID 是 <code>x.rN</code>，并行副本是 <code>x.rN.xK</code>。"),
        ("2", "都结束了？", "有步骤失败、被驳回或被挡住 → 循环失败。"),
        ("3", "until", "按这一轮的输出算。为真 → 循环成功，往下走。"),
        ("4", "还有轮数？", "N &lt; max_rounds → 下一轮。下一轮看得到上一轮的 manifest（例如 fix 读 verify 的结论）。"),
      ]))
    + down("跑满了，until 仍然为假：看 on_exhausted")
    + grid("1fr 1fr 1.4fr", [
        c("accept", "接受当前结果，循环成功"),
        c("fail", "循环失败，运行失败", cls="red"),
        c("ask（默认）", "先跑 reflect 给建议，再问你：1 再跑一轮 · 2 接受 · 3 终止。24 小时不回复算失败", cls="you"),
      ])
    + '<div class="note">审批步骤的 <code>changes</code> 能把循环重新打开：你提修改意见后，循环最多再跑 <code>rounds</code> 轮，后面的步骤重置后重跑。</div>')

FLOWS["pack-lifecycle.html"] = df(
    row(c("① 改项目包", "myorch-knowledge/&lt;项目&gt;/<br>手改，git 提交", cls="data"), arr("project sync"),
        c("② 生成快照", "叠加模板和项目，加 project.json、risk-rules.json。不带 project.yaml、image/、providers/、sources"), arr(),
        c("③ volume 里的快照", "projects/&lt;项目&gt;/&lt;内容哈希&gt;/，不再修改<br>.version 指向最新。保留最新 3 个和 30 天内的", cls="data"), arr("发起"),
        c("④ 运行", "mandate 记下用的版本<br>每一步只读挂到 /sandbox/workspace/project/", cls="blue"))
    + down("learn · onboard · postmortem 的建议，development 的 review 循环里的项目包建议")
    + frame("写回", row(c("agent 提出建议", "每条带证据"), arr(), c("你挑编号", "choose 步骤", cls="you"), arr(),
        c("project-apply", "宿主机动作：检查改动范围<br>写回 myorch-knowledge，git 提交", cls="green"), arr(),
        c("重新生成快照", "这个运行后面的步骤用新版本<br>以后的运行也用新版本")))
    + '<div class="note"><code>{{project.x}}</code> 在发起时从 project.yaml 和 prompts/ 现读，不读快照。脚本读的 project.json 在快照里，<code>project sync</code> 以后才变。</div>')

# ---------------- English ----------------
BLOCK = re.compile(r"(<(?:/?)(?:div|b|span|li|ul|ol|p|hr|svg|defs|marker|path|h\d)\b[^>]*>)")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def texts(body):
    """The texts of a drawing that contain Chinese (see the module docstring)."""
    return [p.strip() for p in BLOCK.split(body)[::2] if CJK.search(p)]


def english(body, missing):
    """The drawing with each Chinese text replaced from EN; texts without a translation go to `missing`."""
    parts = BLOCK.split(body)
    for i in range(0, len(parts), 2):
        p = parts[i]
        if CJK.search(p):
            key = p.strip()
            if key in EN:
                parts[i] = p.replace(key, EN[key])
            else:
                missing.append(key)
    return "".join(parts)


out = sys.argv[1]
pages = {"services-overview.html": o.render(), "openshell-internals.html": s.render(),
         "compose-services.html": k.render(), **FLOWS}
if "--missing" in sys.argv:
    seen = set()
    for name, body in pages.items():
        for t in texts(body):
            if t not in EN and t not in seen:
                seen.add(t)
                print(f"    {t!r}: '',  # {name}")
    sys.exit(0)
missing = []
en_pages = {name: english(body, missing) for name, body in pages.items()}
if missing:
    sys.exit("docs/diagrams/en.py has no English for these texts (add them, then run again):\n"
             + "\n".join(f"  {t!r}" for t in dict.fromkeys(missing)))
HEAD = '<meta charset="utf-8">\n<!-- Generated by docs/diagrams/gen.py. Do not edit by hand. Embedded into {} by docs/build-html.py. -->\n'
os.makedirs(f"{out}/en", exist_ok=True)
for name, body in pages.items():
    open(f"{out}/{name}", "w").write(HEAD.format("docs/zh/index.html") + CSS + body + "\n")
    open(f"{out}/en/{name}", "w").write(HEAD.format("docs/index.html") + CSS + en_pages[name] + "\n")
unused = set(EN) - {t for body in pages.values() for t in texts(body)}
if unused:
    print(f"note: {len(unused)} entries in docs/diagrams/en.py are no longer used", file=sys.stderr)
