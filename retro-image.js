/**
 * <retro-image> — 1990年代のブラウザの画像読み込みを再現する Web Component。
 *
 *   <retro-image mode="interlace">
 *     <img src="photo.jpg" alt="..." width="720" height="337">
 *   </retro-image>
 *
 * 依存ゼロ。中の <img> はそのまま残るので JS が動かなければ通常の画像として出る。
 * 描画は drawImage だけで完結させている(getImageData を使わないので
 * 別オリジンの画像でも canvas が tainted にならない)。
 *
 * 属性:
 *   mode="scanline|interlace|progressive"  既定 interlace
 *   duration="1400"        演出の長さ(ms)
 *   speed="28.8k"          回線速度から実時間を算出(duration より優先)
 *                          14.4k / 28.8k / 33.6k / 56k / isdn
 *   max-duration="8000"    speed 指定時の上限(ms)
 *   bytes="102400"         転送量を明示(Resource Timing が取れない時)
 *   controls="ラベル"      再生ボタンを出す(値がラベル、省略時は Replay)
 *   manual                 自動再生しない。controls のボタンからだけ再生する
 *   once                   一度再生したらセッション中は再生しない
 *   eager                  ビューポート待ちせず即再生
 *
 * イベント: retro:start / retro:end (bubbles)
 */

// V.90 の下りは規制で 53.3kbps が上限だった。isdn は B チャネル 1 本。
const SPEEDS = {
  '14.4k': 14400,
  '28.8k': 28800,
  '33.6k': 33600,
  '56k': 53000,
  isdn: 64000,
};

// 当時の体感は「28.8k で 2.8KB/s 前後」。プロトコルのオーバーヘッド込みで
// bps / 10 バイト毎秒に落とすとその実測値に合う。
/** @param {number} bps */
const bytesPerSec = (bps) => bps / 10;

const DEFAULT_DURATION = 1400;
const DEFAULT_MAX_DURATION = 8000;

const prefersReducedMotion = () =>
  typeof matchMedia === 'function' &&
  matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------------------------------------------------------------- painters */
/* すべて CSS ピクセル基準の座標系で描く(呼び出し側で dpr を scale 済み)。
   走査線やインターレースの縞は「表示上の粗さ」で見えてほしいので、
   元画像の実ピクセル行ではなく表示サイズを刻む。 */

/** ベースライン JPEG / 非インターレース GIF: 上から順に行が届く。 */
/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {HTMLImageElement} img
 * @param {number} w
 * @param {number} h
 */
function scanlinePainter(ctx, img, w, h) {
  let drawn = 0; // 描画済みの行数(dest)
  const rowSrc = img.naturalHeight / h;

  return {
    reset() {
      drawn = 0;
      ctx.clearRect(0, 0, w, h);
    },
    /** @param {number} p 0〜1 の進み具合 */
    paint(p) {
      const target = Math.round(p * h);
      if (target <= drawn) return;
      const sy = drawn * rowSrc;
      const sh = Math.min((target - drawn) * rowSrc, img.naturalHeight - sy);
      if (sh > 0) {
        ctx.drawImage(img, 0, sy, img.naturalWidth, sh, 0, drawn, w, target - drawn);
      }
      drawn = target;
    },
  };
}

/** GIF89a のインターレース: 8行おき → 4行おき → 2行おき → 残り の 4 パス。
    各パスで届いた行を「次のパスが埋めるまで」縦に引き伸ばす = あのブラインド。 */
/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {HTMLImageElement} img
 * @param {number} w
 * @param {number} h
 */
function interlacePainter(ctx, img, w, h) {
  const PASSES = [
    [0, 8, 8], // start, step, 引き伸ばす行数
    [4, 8, 4],
    [2, 4, 2],
    [1, 2, 1],
  ];
  /** @type {[number, number][]} */
  const events = [];
  for (const [start, step, fill] of PASSES) {
    for (let y = start; y < h; y += step) events.push([y, fill]);
  }

  const rowSrc = img.naturalHeight / h;
  let idx = 0;

  return {
    // パスごとの行数が 1/8, 1/8, 1/4, 1/2 になるので、進捗を events の
    // 添字にそのまま比例させれば「後半のパスほど長い」実際の挙動になる。
    reset() {
      idx = 0;
      ctx.clearRect(0, 0, w, h);
    },
    /** @param {number} p 0〜1 の進み具合 */
    paint(p) {
      const target = Math.min(events.length, Math.round(p * events.length));
      while (idx < target) {
        const [y, fill] = events[idx++];
        const sy = Math.min(y * rowSrc, img.naturalHeight - rowSrc);
        ctx.drawImage(
          img,
          0, sy, img.naturalWidth, rowSrc,
          0, y, w, Math.min(fill, h - y)
        );
      }
    },
  };
}

/** プログレッシブ JPEG: 低周波成分から届くので、粗い全体像が先に出て
    段階的に解像度が上がる。scale と累積データ割合の対応で近似する。 */
/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {HTMLImageElement} img
 * @param {number} w
 * @param {number} h
 */
function progressivePainter(ctx, img, w, h) {
  const off = document.createElement('canvas');
  const octx = off.getContext('2d');

  // [縮尺, この段階に切り替わる累積データ割合, ぼかし px]。
  // 面積比に近い配分にすると「粗い絵はすぐ出るのに鮮明になるまで長い」あの体感になる。
  // 終盤の 2 段は、解像度が上がりきったあとに残る「まだ少し眠い」状態と、
  // 最終スキャンでシャキッと決まる瞬間にあたる。
  // DC 係数は全ブロック分あるので、最初の粗い絵が出るまでにも一割ほどかかる。
  const STAGES = [
    [1 / 16, 0.02, 0],
    [1 / 8, 0.06, 0],
    [1 / 5, 0.12, 0],
    [1 / 3, 0.2, 0],
    [1 / 2, 0.32, 0],
    [3 / 4, 0.5, 0],
    [1, 0.7, 1.4],
    [1, 0.9, 0],
  ];

  let stage = -1;

  return {
    reset() {
      stage = -1;
      ctx.clearRect(0, 0, w, h);
    },
    /** @param {number} p 0〜1 の進み具合 */
    paint(p) {
      let next = -1;
      for (let i = 0; i < STAGES.length; i++) {
        if (p >= STAGES[i][1]) next = i;
      }
      if (next === stage || next < 0) return;
      stage = next;

      const [scale, , blur] = STAGES[stage];
      ctx.imageSmoothingEnabled = true;

      // ぼかすと端の画素が薄まるので、その分だけ外へはみ出して描く。
      const pad = blur * 3;
      ctx.filter = blur ? `blur(${blur}px)` : 'none';

      if (scale === 1 || !octx) {
        // ⚠️ 裏キャンバスの 2d が取れない時もここに落とす（縮小せずそのまま描く）。
        ctx.drawImage(img, -pad, -pad, w + pad * 2, h + pad * 2);
      } else {
        const sw = Math.max(1, Math.round(img.naturalWidth * scale));
        const sh = Math.max(1, Math.round(img.naturalHeight * scale));
        off.width = sw;
        off.height = sh;
        octx.clearRect(0, 0, sw, sh);
        octx.drawImage(img, 0, 0, sw, sh);
        ctx.drawImage(off, 0, 0, sw, sh, -pad, -pad, w + pad * 2, h + pad * 2);
      }
      ctx.filter = 'none';
    },
  };
}

const PAINTERS = {
  scanline: scanlinePainter,
  interlace: interlacePainter,
  progressive: progressivePainter,
};

/* ------------------------------------------------------------------ styles */

const STYLE_ID = 'retro-image-style';
function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  // canvas は絶対配置なので、レイアウトは中の <img> が決める = CLS を出さない。
  // 高さを height:auto にしてあるのは、canvas 自身の縦横比(img と同じ)で決めさせるため。
  // inset:0 で親いっぱいに広げると、ボタンを置いたときにそのぶんまで伸びてしまう。
  el.textContent = `
retro-image { display: inline-block; position: relative; max-width: 100%; }
retro-image > img { max-width: 100%; height: auto; vertical-align: top; }
retro-image > canvas.retro-image__canvas {
  position: absolute; left: 0; top: 0; width: 100%; height: auto;
  pointer-events: none;
}
retro-image > .retro-image__replay {
  display: inline-block; margin-top: 0.5em;
  font: inherit; font-size: 0.8em; line-height: 1.4;
  color: inherit; background: transparent;
  border: 1px solid currentColor; border-radius: 2px;
  padding: 0.3em 0.9em; cursor: pointer; opacity: 0.65;
}
retro-image > .retro-image__replay:hover { opacity: 1; }
@media print {
  retro-image > canvas.retro-image__canvas,
  retro-image > .retro-image__replay { display: none; }
}
`;
  document.head.appendChild(el);
}

/* --------------------------------------------------------------- transfer */

/**
 * 実際の転送バイト数を取れるなら取る(同一オリジン、または TAO 付き)。
 * @param {HTMLElement} el
 * @param {HTMLImageElement} img
 */
function resolveBytes(el, img) {
  const attr = Number(el.getAttribute('bytes'));
  if (Number.isFinite(attr) && attr > 0) return attr;

  const src = img.currentSrc || img.src;
  try {
    // サイズは PerformanceResourceTiming にしか無い（基底の PerformanceEntry には無い）。
    const entry = /** @type {PerformanceResourceTiming | undefined} */ (
      performance.getEntriesByType('resource').find((r) => r.name === src)
    );
    const size = entry && (entry.encodedBodySize || entry.transferSize);
    if (size && size > 0) return size;
  } catch {
    /* Resource Timing が使えない環境 */
  }
  // 取れなければ JPEG の目安(約 0.12 バイト/画素)で概算する。
  return Math.round(img.naturalWidth * img.naturalHeight * 0.12) || 40000;
}

/* -------------------------------------------------------------- component */

class RetroImage extends HTMLElement {
  // ⚠️ **`@type` は説明と同じ行に書かない。** 同じ行だとタグとして拾われず、
  //    初期値からの推論（`null` 型）になって、代入する側が全部エラーになる。
  /**
   * 演出をかける <img>。無ければ何もしない。
   * @type {HTMLImageElement | null}
   */
  img = null;
  /**
   * 上に重ねる canvas。
   * @type {HTMLCanvasElement | null}
   */
  _canvas = null;
  /**
   * requestAnimationFrame の id。0 は「動いていない」。
   * @type {number}
   */
  _raf = 0;
  /**
   * 画像が出ないまま終わるのを防ぐ保険。
   * @type {ReturnType<typeof setTimeout> | undefined}
   */
  _failsafe;
  /**
   * 初回再生のきっかけ。
   * @type {IntersectionObserver | undefined}
   */
  _observer;
  /**
   * 再生ボタン（controls 属性がある時だけ生える）。
   * @type {HTMLButtonElement | null}
   */
  _button = null;
  _playing = false;
  _ready = false;

  connectedCallback() {
    if (this._ready) return;
    this._ready = true;

    this.img = this.querySelector('img');
    if (!this.img) return;
    if (!this.#canvasAvailable()) return; // canvas が無ければ手の打ちようがない

    injectStyle();
    this.#setupControls();

    // 自動再生を諦める条件。<img> には一切触らないので通常表示のまま出る。
    // ボタンは残すので、見たい人は自分の操作で再生できる。
    if (prefersReducedMotion()) return;
    if (this.hasAttribute('manual')) return;
    if (this.hasAttribute('once') && this.#alreadyPlayed()) return;

    // JS がここまで動いた時点で隠す。以降は必ず自前で描いて戻す責任を負う。
    // visibility なら領域は残るのでレイアウトは動かない。
    this.img.style.visibility = 'hidden';

    if (this.img.complete && this.img.naturalWidth > 0) {
      this.#schedule();
    } else {
      this.img.addEventListener('load', () => this.#schedule(), { once: true });
      this.img.addEventListener('error', () => this.#restore(), { once: true });
      // load も error も来ないまま隠れっぱなしになるのを防ぐ。
      this._failsafe = setTimeout(() => this.#restore(), 10000);
    }
  }

  disconnectedCallback() {
    this._observer?.disconnect();
    // 隠したまま DOM から外れると、framework に differ で挿し直されたときに
    // 画像が消えたままになる。外れる時点で必ず戻しておく。
    this.#restore();
  }

  /**
   * 外から任意のタイミングで再生する。再生中なら頭から描き直す。
   * ユーザーの明示的な操作で呼ばれる想定なので、自動再生を抑える条件
   * (prefers-reduced-motion / once) はここでは効かない。
   */
  play() {
    this.#start();
  }

  /**
   * controls 属性があれば再生ボタンを足す。属性の値がラベルになる
   * (`controls="もう一度見る"`)。JS が動いたときにだけ生えるので、
   * 押しても何も起きないボタンが残ることはない。
   */
  #setupControls() {
    if (!this.hasAttribute('controls') || this._button) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'retro-image__replay';
    button.textContent = this.getAttribute('controls') || 'Replay';
    button.addEventListener('click', () => this.play());
    this.appendChild(button);
    this._button = button;
  }

  #canvasAvailable() {
    try {
      return !!document.createElement('canvas').getContext('2d');
    } catch {
      return false;
    }
  }

  #storageKey() {
    return `retro-image:${this.img?.currentSrc || this.img?.src || ''}`;
  }

  #alreadyPlayed() {
    try {
      return sessionStorage.getItem(this.#storageKey()) === '1';
    } catch {
      return false;
    }
  }

  #markPlayed() {
    if (!this.hasAttribute('once')) return;
    try {
      sessionStorage.setItem(this.#storageKey(), '1');
    } catch {
      /* private mode 等 */
    }
  }

  /** ビューポートに入ってから再生する(eager 指定時は即座に)。 */
  #schedule() {
    clearTimeout(this._failsafe); // 読み込みは終わったので load 待ちの保険は解除
    if (this.hasAttribute('eager') || typeof IntersectionObserver !== 'function') {
      this.#start();
      return;
    }
    this._observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        this._observer?.disconnect();
        this.#start();
      },
      { rootMargin: '64px' }
    );
    this._observer.observe(this);
  }

  #duration() {
    const speed = this.getAttribute('speed');
    const bps = speed ? SPEEDS[/** @type {keyof typeof SPEEDS} */ (speed)] : undefined;
    if (bps && this.img) {
      const ms = (resolveBytes(this, this.img) / bytesPerSec(bps)) * 1000;
      const cap = Number(this.getAttribute('max-duration')) || DEFAULT_MAX_DURATION;
      return Math.min(ms, cap);
    }
    const attr = Number(this.getAttribute('duration'));
    return Number.isFinite(attr) && attr > 0 ? attr : DEFAULT_DURATION;
  }

  #start() {
    const img = this.img;
    if (!img || !img.complete || !img.naturalWidth) return this.#restore();
    injectStyle(); // play() で直接呼ばれた場合にも canvas の配置 CSS を効かせる

    // ビューポート監視は初回再生のきっかけでしかない。ここまで来たら用済みで、
    // 残しておくと play() で始めた再生をスクロールが横から止めてしまう。
    this._observer?.disconnect();

    // 再生中に呼び直されたら、いまの回を捨てて頭から描き直す。
    cancelAnimationFrame(this._raf);
    this._playing = false;

    const rect = img.getBoundingClientRect();
    const w = Math.round(rect.width) || img.naturalWidth;
    const h = Math.round(rect.height) || img.naturalHeight;
    if (!w || !h) return this.#restore();

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = this._canvas || document.createElement('canvas');
    canvas.className = 'retro-image__canvas';
    canvas.setAttribute('aria-hidden', 'true');
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return this.#restore();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    if (!this._canvas) {
      this._canvas = canvas;
      this.insertBefore(canvas, img.nextSibling); // ボタンより前、img の直後に置く
    }

    const mode = this.getAttribute('mode') || 'interlace';
    const painter = (PAINTERS[/** @type {keyof typeof PAINTERS} */ (mode)] || interlacePainter)(
      ctx, img, w, h
    );
    painter.reset();

    const duration = this.#duration();
    this._playing = true;
    img.style.visibility = 'hidden'; // play() で呼び直された時のため
    // 演出が途中で止まっても画像が消えたままにならないようにする。
    clearTimeout(this._failsafe);
    this._failsafe = setTimeout(() => this.#restore(), duration + 5000);
    this.dispatchEvent(
      new CustomEvent('retro:start', { bubbles: true, detail: { mode, duration } })
    );

    /** @type {number | null} */
    let t0 = null;
    /** @param {number} now */
    const tick = (now) => {
      if (t0 === null) t0 = now;
      const p = Math.min(1, (now - t0) / duration);
      painter.paint(p);
      if (p < 1) {
        this._raf = requestAnimationFrame(tick);
      } else {
        this.#finish(mode);
      }
    };
    this._raf = requestAnimationFrame(tick);
  }

  /** @param {string} mode */
  #finish(mode) {
    this._playing = false;
    this.#markPlayed();
    this.#restore();
    this.dispatchEvent(
      new CustomEvent('retro:end', { bubbles: true, detail: { mode } })
    );
  }

  /** <img> を必ず見える状態に戻す。演出をやめる時は全部ここを通す。 */
  #restore() {
    clearTimeout(this._failsafe);
    cancelAnimationFrame(this._raf);
    this._playing = false;
    if (this.img) this.img.style.visibility = '';
    if (this._canvas) {
      this._canvas.remove();
      this._canvas = null;
    }
  }
}

if (!customElements.get('retro-image')) {
  customElements.define('retro-image', RetroImage);
}

export { RetroImage };
