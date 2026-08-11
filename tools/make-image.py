#!/usr/bin/env python3
"""デモ用の画像 demo/sunset.jpg を生成する。

3 方式の違いが見えるように、なだらかな階調（空のグラデーション = 低周波）と
細い線・点（グリッドと星 = 高周波）を一枚に同居させる。
配色はデモページのパレット（琥珀と深い青緑）に合わせる。
"""
import base64
import pathlib

from playwright.sync_api import sync_playwright

root = pathlib.Path(__file__).resolve().parent.parent
W, H = 960, 540

HTML = """
<canvas id="c" width="%d" height="%d"></canvas>
<script>
const c = document.getElementById('c'), x = c.getContext('2d');
const W = c.width, H = c.height, HORIZON = H * 0.62;

// --- 空: 深い青緑から地平線の琥珀へ ---
const sky = x.createLinearGradient(0, 0, 0, HORIZON);
sky.addColorStop(0.00, '#0B141B');
sky.addColorStop(0.42, '#2E3F46');
sky.addColorStop(0.72, '#8A6636');
sky.addColorStop(1.00, '#E9B357');
x.fillStyle = sky; x.fillRect(0, 0, W, HORIZON);

// --- 星: 高周波。インターレースの初期パスでは消える細部 ---
let seed = 20260812;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
for (let i = 0; i < 260; i++) {
  const sy = rnd() * HORIZON * 0.62;
  const a = (1 - sy / (HORIZON * 0.62)) * 0.9 * rnd();
  x.fillStyle = 'rgba(226,235,232,' + a.toFixed(3) + ')';
  const r = rnd() < 0.08 ? 1.6 : 0.9;
  x.fillRect(rnd() * W, sy, r, r);
}

// --- 太陽 ---
const cx = W * 0.5, cy = HORIZON - 4, R = H * 0.2;
const glow = x.createRadialGradient(cx, cy, 0, cx, cy, R * 2.6);
glow.addColorStop(0, 'rgba(255,214,140,0.55)');
glow.addColorStop(1, 'rgba(255,214,140,0)');
x.fillStyle = glow; x.fillRect(0, 0, W, HORIZON);

// 太陽は別レイヤーに描いてから帯を抜く。本体の canvas で destination-out すると
// 空まで一緒に消えて黒帯になる。
const s = document.createElement('canvas');
s.width = Math.ceil(R * 2) + 4; s.height = Math.ceil(R * 2) + 4;
const sx = s.getContext('2d');
const disc = sx.createLinearGradient(0, 0, 0, s.height);
disc.addColorStop(0, '#FFEFC9');
disc.addColorStop(1, '#DE8A1E');
sx.fillStyle = disc;
sx.beginPath(); sx.arc(s.width / 2, s.height / 2, R, 0, Math.PI * 2); sx.fill();
sx.globalCompositeOperation = 'destination-out';   // 当時の CG の常套句
for (let i = 0; i < 7; i++) {
  const yy = s.height * 0.30 + (i / 7) * s.height * 0.72;
  sx.fillRect(0, yy, s.width, 1.5 + i * 1.1);
}

x.save();
x.beginPath(); x.rect(0, 0, W, HORIZON); x.clip();
x.drawImage(s, cx - s.width / 2, cy - s.height / 2);
x.restore();

// --- 地面: 遠近グリッド。細い線が全面に走る ---
const ground = x.createLinearGradient(0, HORIZON, 0, H);
ground.addColorStop(0, '#132028');
ground.addColorStop(1, '#050A0D');
x.fillStyle = ground; x.fillRect(0, HORIZON, W, H - HORIZON);

x.strokeStyle = 'rgba(227,168,58,0.62)';
x.lineWidth = 1;
for (let i = -14; i <= 14; i++) {            // 消失点へ向かう線
  x.beginPath();
  x.moveTo(cx + i * 14, HORIZON);
  x.lineTo(cx + i * 210, H);
  x.stroke();
}
for (let i = 1; i <= 16; i++) {              // 手前ほど間隔が開く横線
  const t = i / 16;
  const y = HORIZON + Math.pow(t, 2.4) * (H - HORIZON);
  x.globalAlpha = 0.28 + t * 0.5;
  x.beginPath(); x.moveTo(0, y); x.lineTo(W, y); x.stroke();
}
x.globalAlpha = 1;

// --- 地平線 ---
x.fillStyle = 'rgba(255,226,170,0.85)';
x.fillRect(0, HORIZON - 1, W, 2);

window.__out = c.toDataURL('image/jpeg', 0.74);
</script>
""" % (W, H)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": W, "height": H})
    p.set_content(HTML)
    p.wait_for_function("() => !!window.__out")
    data_url = p.evaluate("() => window.__out")
    b.close()

raw = base64.b64decode(data_url.split(",", 1)[1])
dest = root / "demo" / "sunset.jpg"
dest.write_bytes(raw)
print(f"{dest.relative_to(root)}  {W}x{H}  {len(raw) / 1024:.1f} KB")
