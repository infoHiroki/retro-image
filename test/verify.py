#!/usr/bin/env python3
"""ビルドしたデモページを実ブラウザで動かして検証する。

外部参照版（index.html）と単一ファイル版（dist/standalone.html）の両方に
同じ検査をかける。単一ファイル版は本来ページの外枠を持たないので、
置き先が用意する最小 reset を模したラッパーで包んでから開く。

使い方: python3 tools/build.py && python3 test/verify.py
"""
import functools
import http.server
import pathlib
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

root = pathlib.Path(__file__).resolve().parent.parent
shots = root / "test" / "shots"
shots.mkdir(exist_ok=True)

IMAGE_BYTES = (root / "demo" / "sunset.jpg").stat().st_size

WRAPPER = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>*,*::before,*::after{box-sizing:border-box}
body,h1,h2,h3,p,pre,figure{margin:0}
img{display:block;max-width:100%}button,select,input{font:inherit}</style>
</head><body>
__BODY__
</body></html>"""

failures = []
errors = []


def check(label, ok, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def run(browser, name, url, capture_shots, external_image=True):
    print(f"\n### {name}")
    page = browser.new_page(viewport={"width": 1120, "height": 900})
    page.on("console", lambda m: errors.append(f"[{name}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[{name}] pageerror: {e}"))
    page.goto(url)
    page.wait_for_timeout(700)

    print("\n== 基本 ==")
    check("JS エラーなし", not errors, "; ".join(errors[:3]))
    check("カスタム要素が定義された", page.evaluate("() => !!customElements.get('retro-image')"))
    check(
        "画像が読み込めた",
        page.evaluate(
            "() => [...document.querySelectorAll('retro-image img')]"
            ".every(i => i.complete && i.naturalWidth > 0)"
        ),
    )

    print("\n== 各方式の途中経過 ==")
    for mode in ("scanline", "interlace", "progressive"):
        page.evaluate(
            """(mode) => {
                const hero = document.getElementById('hero');
                hero.setAttribute('mode', mode);
                hero.setAttribute('duration', '4000');
                hero.play();
            }""",
            mode,
        )
        page.wait_for_timeout(950)
        check(
            f"{mode}: 再生中は canvas が出て img が隠れている",
            page.evaluate(
                "() => !!document.querySelector('#hero canvas')"
                " && document.querySelector('#hero img').style.visibility === 'hidden'"
            ),
        )
        if capture_shots:
            page.locator("#hero").screenshot(path=str(shots / f"mid-{mode}.png"))
        page.wait_for_timeout(3400)
        check(
            f"{mode}: 完了後に img が戻り canvas が消える",
            page.evaluate(
                "() => !document.querySelector('#hero canvas')"
                " && document.querySelector('#hero img').style.visibility === ''"
            ),
        )

    print("\n== レイアウト ==")
    before = page.evaluate("() => document.getElementById('hero').getBoundingClientRect().height")
    page.evaluate("() => { const h = document.getElementById('hero'); h.setAttribute('duration','4000'); h.play(); }")
    page.wait_for_timeout(500)
    during = page.evaluate("() => document.getElementById('hero').getBoundingClientRect().height")
    check("再生中も高さが変わらない", abs(before - during) < 1, f"{before:.1f} → {during:.1f}")
    page.wait_for_timeout(4000)

    print("\n== 回線速度 ==")
    capped = page.evaluate(
        """() => new Promise((resolve) => {
            const m = document.getElementById('modem');
            m.addEventListener('retro:start', (e) => resolve(e.detail.duration), { once: true });
            m.play();
        })"""
    )
    check("8秒キャップが効いている", abs(capped - 8000) < 1, f"duration={capped:.0f}ms")

    uncapped = page.evaluate(
        """() => new Promise((resolve) => {
            const m = document.getElementById('modem');
            const cap = document.getElementById('modem-cap');
            cap.checked = false;
            cap.dispatchEvent(new Event('change'));
            m.addEventListener('retro:start', (e) => resolve(e.detail.duration), { once: true });
            m.play();
        })"""
    )
    expected = IMAGE_BYTES * 10 / 28800 * 1000
    check(
        "28.8k の実時間が実バイト数と一致",
        abs(uncapped - expected) < 500,
        f"{uncapped/1000:.1f}s (期待 {expected/1000:.1f}s)",
    )

    if external_image:
        # bytes 属性を外すと Resource Timing から実際の転送量を拾えること
        auto = page.evaluate(
            """() => new Promise((resolve) => {
                const m = document.getElementById('modem');
                m.removeAttribute('bytes');
                m.addEventListener('retro:start', (e) => resolve(e.detail.duration), { once: true });
                m.play();
            })"""
        )
        check(
            "bytes 未指定なら Resource Timing から転送量を拾う",
            abs(auto - expected) < 500,
            f"{auto/1000:.1f}s (期待 {expected/1000:.1f}s)",
        )
        page.evaluate(
            "(b) => document.getElementById('modem').setAttribute('bytes', b)", str(IMAGE_BYTES)
        )

    check(
        "再生中に play() し直すと再スタートする",
        page.evaluate(
            """() => new Promise((resolve) => {
                const m = document.getElementById('modem');
                m.addEventListener('retro:start', () => resolve(true), { once: true });
                m.play();
            })"""
        ),
    )

    # 画面外の要素を play() したあと、スクロールで到達しても
    # ビューポート監視が横から再スタートさせないこと
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    starts = page.evaluate(
        """() => new Promise((resolve) => {
            const el = document.querySelectorAll('[data-race]')[0];
            let n = 0;
            el.addEventListener('retro:start', () => { n += 1; });
            el.play();
            setTimeout(() => {
                el.scrollIntoView();
                setTimeout(() => resolve(n), 800);
            }, 300);
        })"""
    )
    check("画面外で play() 後にスクロールしても再スタートしない", starts == 1, f"start が {starts} 回")
    page.close()

    print("\n== アクセシビリティ ==")
    reduced = browser.new_page(viewport={"width": 1120, "height": 900}, reduced_motion="reduce")
    reduced.goto(url)
    reduced.wait_for_timeout(700)
    check(
        "reduced-motion では img を隠さない",
        reduced.evaluate("() => document.querySelector('#hero img').style.visibility === ''"),
    )
    reduced.close()

    if not capture_shots:
        return

    print("\n== スクリーンショット ==")
    for theme in ("light", "dark"):
        p = browser.new_page(viewport={"width": 1120, "height": 900}, color_scheme=theme)
        p.goto(url)
        p.wait_for_timeout(1200)
        p.evaluate("() => document.querySelectorAll('[data-race]').forEach(e => e.play())")
        p.wait_for_timeout(1400)
        p.screenshot(path=str(shots / f"page-{theme}.png"), full_page=True)
        # 3方式の比較。先に画面へ入れてから再生しないと、
        # スクロールで演出が始まり直した瞬間を撮ってしまう
        p.locator(".race").scroll_into_view_if_needed()
        p.wait_for_timeout(300)
        p.evaluate("() => document.querySelectorAll('[data-race]').forEach(e => e.play())")
        p.wait_for_timeout(1300)
        p.locator(".race").screenshot(path=str(shots / f"race-{theme}.png"))
        p.close()
        print(f"  page-{theme}.png / race-{theme}.png")


index = root / "index.html"
standalone = root / "dist" / "standalone.html"
if not index.exists() or not standalone.exists():
    sys.exit("先に python3 tools/build.py を実行してください")

wrapped = root / "test" / "_standalone-wrapped.html"
wrapped.write_text(
    WRAPPER.replace("__BODY__", standalone.read_text(encoding="utf-8")), encoding="utf-8"
)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# ES module は file:// では CORS で弾かれるので、実際の配信と同じ http で確かめる。
httpd = socketserver.TCPServer(
    ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root))
)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        run(browser, "index.html（外部参照版）", f"{base}/index.html", capture_shots=True)
        run(
            browser,
            "dist/standalone.html（単一ファイル版）",
            f"{base}/test/_standalone-wrapped.html",
            capture_shots=False,
            external_image=False,  # 画像が data URI なので Resource Timing には載らない
        )
        browser.close()
finally:
    httpd.shutdown()
    wrapped.unlink(missing_ok=True)

if errors:
    print("\n== コンソール ==")
    for e in errors[:10]:
        print("  " + e)

print("\n" + ("すべて通過" if not failures else f"失敗 {len(failures)} 件: {failures}"))
sys.exit(1 if failures else 0)
