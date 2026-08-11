#!/usr/bin/env python3
"""demo/template.html からデモページを 2 種類つくる。

  index.html            retro-image.js と demo/sunset.jpg を普通に参照する版。
                        GitHub Pages で配信する実物であり、使い方の見本も兼ねる。
  dist/standalone.html  JS と画像を全部埋めた 1 ファイル版。どこにでも置ける。

使い方: python3 tools/build.py
"""
import base64
import pathlib
import re

root = pathlib.Path(__file__).resolve().parent.parent
template = (root / "demo" / "template.html").read_text(encoding="utf-8")
component = (root / "retro-image.js").read_text(encoding="utf-8")
image = (root / "demo" / "sunset.jpg").read_bytes()

BYTES = str(len(image))

# 単一ファイル版を置く先（Artifact など）はページの外枠と最小 reset を用意してくれるので、
# テンプレートは body の中身だけを持つ。自前で配信する index.html にはその外枠が要る。
RESET = """*, *::before, *::after { box-sizing: border-box }
    body, h1, h2, h3, p, pre, figure { margin: 0 }
    img { display: block; max-width: 100% }
    button, select, input { font: inherit }"""


def render(script_tag, img_attr, img_setup, component_inline):
    return (
        template.replace("__SCRIPT_TAG__", script_tag)
        .replace("__IMG_ATTR__", img_attr)
        .replace("__IMG_SETUP__", img_setup)
        .replace("__COMPONENT_INLINE__", component_inline)
        .replace("__BYTES__", BYTES)
    )


# --- 外部参照版 ---
body = render(
    script_tag="",
    img_attr='src="demo/sunset.jpg"',
    img_setup="",
    component_inline="",
)
title = re.search(r"<title>.*?</title>", body, re.S).group(0)
pages = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{title}
<meta name="description" content="1990年代のブラウザの画像読み込みを再現する Web Component。走査線・インターレース・プログレッシブの 3 方式。">
<script type="module" src="retro-image.js"></script>
<style>
    {RESET}
</style>
</head>
<body>
{body.replace(title, "").lstrip()}
</body>
</html>
"""
(root / "index.html").write_text(pages, encoding="utf-8")

# --- 単一ファイル版 ---
# インライン script は module ではないので export 文を落とす
inline = re.sub(r"^export \{ RetroImage \};\s*$", "", component, flags=re.M)
b64 = base64.b64encode(image).decode("ascii")
standalone = render(
    script_tag="",
    img_attr="data-demo",
    img_setup=(
        f'  const IMG_SRC = "data:image/jpeg;base64,{b64}";\n'
        "  // retro-image が upgrade される前に src を入れておく。\n"
        "  document.querySelectorAll('img[data-demo]').forEach((i) => { i.src = IMG_SRC; });\n"
    ),
    component_inline=inline,
)
(root / "dist").mkdir(exist_ok=True)
(root / "dist" / "standalone.html").write_text(standalone, encoding="utf-8")

for path in (root / "index.html", root / "dist" / "standalone.html"):
    leftover = re.findall(r"__[A-Z_]+__", path.read_text(encoding="utf-8"))
    status = f"未置換 {set(leftover)}" if leftover else "ok"
    print(f"{path.relative_to(root)}  {path.stat().st_size / 1024:6.1f} KB  {status}")
