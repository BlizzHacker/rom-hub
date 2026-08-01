"""Render a captured terminal session to a PNG.

The showcase has to show the *command line*, because that is where the
plugin system is: `plugin browse`, `plugin disable`, a fan-out search, an
import running. A screenshot of a games library shows none of it.

This takes a JSON session file, lays it out as a terminal, and screenshots
it with Playwright:

    {"title": "...",
     "blocks": [{"cmd": "rom-hub plugin list", "out": "<verbatim output>"},
                 ...]}

`out` is the captured stdout+stderr of that command, byte for byte. Nothing
here edits, filters or re-orders it -- the only thing this file decides is
the font and the background. Colour is applied to the prompt and the typed
command only; the output is rendered in one colour, because tinting words
the tool did not tint would be inventing emphasis.

Usage (inside the Playwright image, which already has the browsers):

    python term_shot.py session.json out.png [--width 1180]
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib

from playwright.sync_api import sync_playwright

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; font-family: "DejaVu Sans Mono", "Liberation Mono",
       "Courier New", monospace; }
.card { background: #0d1117; border: 1px solid #26303d; border-radius: 10px;
        overflow: hidden; }
.bar { background: #161b22; border-bottom: 1px solid #26303d; padding: 9px 14px;
       display: flex; align-items: center; gap: 8px; }
.dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.d1 { background: #ff5f56; } .d2 { background: #ffbd2e; } .d3 { background: #27c93f; }
.bartitle { color: #8b949e; font-size: 12.5px; margin-left: 10px;
            letter-spacing: .02em; }
.body { padding: 14px 18px 18px; }
pre { white-space: pre-wrap; word-break: break-word; font-size: 13.5px;
      line-height: 1.5; color: #c9d1d9; }
.cmdline { margin-top: 14px; font-size: 13.5px; line-height: 1.5; }
.cmdline:first-child { margin-top: 0; }
.ps { color: #56d364; }
.cmd { color: #e6edf3; font-weight: 700; }
.out { margin-top: 2px; }
.note { color: #8b949e; font-size: 12px; margin-top: 16px; font-style: italic; }
"""

PAGE = """
<style>{css}</style>
<div class="card">
  <div class="bar">
    <span class="dot d1"></span><span class="dot d2"></span><span class="dot d3"></span>
    <span class="bartitle">{title}</span>
  </div>
  <div class="body">{body}</div>
</div>
"""


def render_html(session: dict, width: int) -> str:
    parts = []
    for block in session["blocks"]:
        cmd = block.get("cmd")
        if cmd:
            parts.append(
                '<div class="cmdline"><span class="ps">$</span> '
                f'<span class="cmd">{html.escape(cmd)}</span></div>'
            )
        out = block.get("out", "")
        if out:
            parts.append(f'<pre class="out">{html.escape(out.rstrip())}</pre>')
    if session.get("note"):
        parts.append(f'<div class="note">{html.escape(session["note"])}</div>')
    return PAGE.format(
        css=CSS,
        title=html.escape(session.get("title", "")),
        body="".join(parts),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session")
    parser.add_argument("out")
    parser.add_argument("--width", type=int, default=1180)
    args = parser.parse_args()

    session = json.loads(pathlib.Path(args.session).read_text(encoding="utf-8"))
    markup = render_html(session, args.width)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            viewport={"width": args.width, "height": 600}, device_scale_factor=2
        )
        page.set_content(markup)
        card = page.locator(".card")
        card.screenshot(path=args.out)
        browser.close()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
