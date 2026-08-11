#!/usr/bin/env python3
"""3 方式の途中経過を同じ時点で撮り、README 用の比較画像 docs/stages.png をつくる。

使い方: python3 tools/build.py && python3 tools/make-stages.py
"""
import functools
import http.server
import pathlib
import socketserver
import threading

from playwright.sync_api import sync_playwright

root = pathlib.Path(__file__).resolve().parent.parent
docs = root / "docs"
docs.mkdir(exist_ok=True)
tmp = docs / "_frames"
tmp.mkdir(exist_ok=True)

DURATION = 6000
MARKS = [0.08, 0.22, 0.45, 0.70, 0.95]
MODES = ["scanline", "interlace", "progressive"]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


httpd = socketserver.TCPServer(
    ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root))
)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1120, "height": 900}, color_scheme="dark")
        page.goto(f"{base}/index.html")
        page.wait_for_timeout(700)

        frames = {}
        for mode in MODES:
            page.evaluate(
                """([mode, dur]) => {
                    const h = document.getElementById('hero');
                    h.setAttribute('mode', mode);
                    h.setAttribute('duration', String(dur));
                    h.play();
                }""",
                [mode, DURATION],
            )
            prev = 0
            row = []
            for m in MARKS:
                page.wait_for_timeout(int((m - prev) * DURATION))
                prev = m
                name = f"{mode}-{int(m * 100):02d}.png"
                page.locator("#hero").screenshot(path=str(tmp / name))
                row.append(name)
            frames[mode] = row
            page.wait_for_timeout(int((1 - prev) * DURATION) + 300)
        page.close()

        rows = "".join(
            f'<div class="row"><div class="label">{mode}</div>'
            + "".join(f'<img src="_frames/{n}" alt="">' for n in frames[mode])
            + "</div>"
            for mode in MODES
        )
        sheet_html = docs / "_sheet.html"
        sheet_html.write_text(
            f"""<!doctype html><meta charset="utf-8"><style>
            body {{ margin:0; padding:10px 12px 14px; width:1460px; background:#0E171C;
                    color:#DDE6E2; font:13px ui-monospace, Menlo, monospace; }}
            .head {{ display:flex; padding:0 0 6px 84px; gap:5px; }}
            .head span {{ flex:1; text-align:center; color:#E3A83A; }}
            .row {{ display:flex; align-items:center; gap:5px; padding:2.5px 0; }}
            .label {{ width:84px; color:#E3A83A; }}
            img {{ flex:1; width:0; display:block; border:1px solid #2C3D45; }}
            </style>
            <div class="head">{''.join(f'<span>{int(m * 100)}%</span>' for m in MARKS)}</div>
            {rows}""",
            encoding="utf-8",
        )

        sheet = browser.new_page(viewport={"width": 1460, "height": 700})
        sheet.goto(sheet_html.as_uri())
        sheet.wait_for_timeout(900)
        sheet.locator("body").screenshot(path=str(docs / "stages.png"))
        sheet.close()
        browser.close()
finally:
    httpd.shutdown()

for f in tmp.iterdir():
    f.unlink()
tmp.rmdir()
(docs / "_sheet.html").unlink(missing_ok=True)

print(f"docs/stages.png  {(docs / 'stages.png').stat().st_size / 1024:.1f} KB")
