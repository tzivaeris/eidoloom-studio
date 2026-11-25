#!/usr/bin/env python3
"""Draws docs/architecture-light.svg and docs/architecture-dark.svg, the README's picture of how Studio fits together.
Edit the boxes here and run it again:  python3 docs/make_architecture.py"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 980, 580

THEMES = {
    "light": {"text": "#1f2328", "muted": "#59636e", "box": "#f6f8fa", "line": "#d1d9e0",
              "core": "#ddf4ff", "core_line": "#54aeff", "chip": "#ffffff", "accent": "#0969da"},
    "dark": {"text": "#e6edf3", "muted": "#9198a1", "box": "#151b23", "line": "#3d444d",
             "core": "#0c2d4f", "core_line": "#4493f8", "chip": "#0d1117", "accent": "#4493f8"},
}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def draw(t):
    out = []
    add = out.append

    def box(x, y, w, h, fill, stroke, r=10, dash=False):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"'
            + (' stroke-dasharray="5 4"' if dash else "") + "/>")

    def text(x, y, s, size=13, color=None, weight=400, anchor="start"):
        add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color or t["text"]}" '
            f'text-anchor="{anchor}">{esc(s)}</text>')

    def chip(x, y, w, label):
        box(x, y, w, 30, t["chip"], t["line"], r=7)
        text(x + w / 2, y + 20, label, 12.5, anchor="middle")

    def arrow(x1, y1, x2, y2, both=True):
        add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["accent"]}" stroke-width="2" '
            f'marker-end="url(#head)"' + (' marker-start="url(#head)"' if both else "") + "/>")

    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" '
        'role="img" aria-label="How Eidoloom Studio fits together">')
    add(f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{t["accent"]}"/></marker></defs>')

    # the browser
    box(20, 60, 190, 290, t["box"], t["line"])
    text(115, 88, "Your browser", 15, weight=600, anchor="middle")
    text(115, 106, "a computer or a phone", 11.5, t["muted"], anchor="middle")
    for i, label in enumerate(["Editor (Monaco)", "Terminals", "Agent chat", "Previews, players", "Approvals, undo"]):
        chip(35, 122 + i * 42, 160, label)

    # Studio
    box(290, 20, 400, 430, t["box"], t["line"])
    text(490, 46, "studio.py", 15, weight=600, anchor="middle")
    text(490, 64, "one Python process, standard library only", 11.5, t["muted"], anchor="middle")
    box(305, 80, 370, 238, t["core"], t["core_line"])
    text(490, 104, "The harness", 15, t["accent"], weight=600, anchor="middle")
    text(490, 121, "what turns a model into an agent", 11.5, t["muted"], anchor="middle")
    left = ["Agent loop", "Tools", "Approvals", "Undo points"]
    right = ["Context and cache", "Checks after edits", "Reply guards", "Goals and tasks"]
    for i, (a, b) in enumerate(zip(left, right)):
        chip(320, 136 + i * 43, 165, a)
        chip(495, 136 + i * 43, 165, b)
    for i, label in enumerate(["Accounts, sharing", "Files and Git"]):
        chip(305 + i * 190, 334, 180, label)
    for i, label in enumerate(["Terminals", "Waiting room"]):
        chip(305 + i * 190, 374, 180, label)
    text(490, 432, "every request checked against your account and folder", 11.5, t["muted"], anchor="middle")

    # the model server
    box(770, 20, 190, 160, t["box"], t["line"])
    text(865, 46, "Model server", 15, weight=600, anchor="middle")
    text(865, 64, "yours, interchangeable", 11.5, t["muted"], anchor="middle")
    for i, line in enumerate(["Ollama · llama.cpp", "vLLM · LM Studio", "Strata"]):
        text(865, 92 + i * 21, line, 13, anchor="middle")
    text(865, 164, "or Codex (OpenAI)", 11.5, t["muted"], anchor="middle")

    # tool servers
    box(770, 210, 190, 240, t["box"], t["line"])
    text(865, 236, "Tool servers (MCP)", 15, weight=600, anchor="middle")
    text(865, 254, "optional", 11.5, t["muted"], anchor="middle")
    for i, line in enumerate(["Web search, browser", "Pictures, video clips", "Voices, music, sounds",
                              "Video and sound editing", "Blender, Greek checks", "Email, any MCP server"]):
        text(865, 284 + i * 26, line, 13, anchor="middle")

    # the project folder
    box(290, 500, 400, 62, t["box"], t["line"], dash=True)
    text(490, 526, "Your project folder", 15, weight=600, anchor="middle")
    text(490, 546, "on this machine, or in its own Docker sandbox", 11.5, t["muted"], anchor="middle")

    # connections and what travels on them
    arrow(212, 205, 288, 205)
    text(250, 188, "HTTPS", 11, t["muted"], anchor="middle")
    text(250, 226, "live events", 11, t["muted"], anchor="middle")
    arrow(692, 100, 768, 100)
    text(730, 84, "chat API,", 11, t["muted"], anchor="middle")
    text(730, 120, "tool calls", 11, t["muted"], anchor="middle")
    arrow(692, 330, 768, 330)
    text(730, 314, "stdio or", 11, t["muted"], anchor="middle")
    text(730, 350, "HTTP", 11, t["muted"], anchor="middle")
    arrow(490, 452, 490, 498)
    text(500, 480, "files, commands", 11, t["muted"])
    add("</svg>")
    return "\n".join(out) + "\n"


for name, theme in THEMES.items():
    with open(os.path.join(HERE, f"architecture-{name}.svg"), "w", encoding="utf-8") as fh:
        fh.write(draw(theme))
print("wrote docs/architecture-light.svg and docs/architecture-dark.svg")
