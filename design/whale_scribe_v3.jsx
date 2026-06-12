/* 主屏专用:Canvas 2D"书写循环"鲸环 ——
   空白起笔 → 沿字体中轴线一笔写出 → 停留 2s → 同向逐笔擦除 → 留白 → 无限循环。
   材质:矢量笔触拼贴(参考"蓝色漩涡"风格)——沿笔迹排布的多层色调椭圆笔触 +
   少量飞溅圆点,块面大、留白缝,简洁不碎。颜色沿笔迹蓝→黄渐变(起笔蓝,收笔黄)。 */
const SCRIBE_VB = { x: 273.79, y: 475.32, w: 54.15, h: 33.63 };
/* 书写中轴线(与字体轮廓逐像素校准):A=螺旋卷(细笔),B=斜线+大环(粗笔) */
const SCRIBE_A = { start: [284.9, 495.2], width: 5, beziers: [
  [283.9, 494.0, 282.3, 493.6, 280.9, 493.9],
  [278.4, 494.4, 276.4, 496.0, 276.4, 498.2],
  [276.5, 500.5, 278.2, 502.6, 280.9, 502.8],
  [282.3, 503.0, 283.6, 503.3, 284.6, 504.0],
] };
const SCRIBE_B = { start: [284.6, 504.0], width: 7, beziers: [
  [287.5, 502.6, 290.0, 498.5, 293.2, 494.3],
  [296.4, 490.1, 299.5, 486.3, 303.0, 483.5],
  [306.8, 480.6, 312.5, 478.7, 317.0, 479.6],
  [321.2, 480.5, 324.4, 484.0, 324.4, 488.4],
  [324.4, 492.5, 323.0, 498.0, 319.0, 502.3],
  [315.5, 505.9, 310.0, 507.0, 305.0, 506.4],
  [301.0, 505.9, 297.5, 504.9, 294.8, 503.5],
] };

function scribeSamples() {
  const pts = [];
  let len = 0, px = SCRIBE_A.start[0], py = SCRIBE_A.start[1];
  pts.push({ x: px, y: py, len: 0, w: SCRIBE_A.width });
  for (const seg of [SCRIBE_A, SCRIBE_B]) {
    let x0 = seg.start[0], y0 = seg.start[1];
    for (const b of seg.beziers) {
      for (let i = 1; i <= 22; i++) {
        const t = i / 22, u = 1 - t;
        const x = u*u*u*x0 + 3*u*u*t*b[0] + 3*u*t*t*b[2] + t*t*t*b[4];
        const y = u*u*u*y0 + 3*u*u*t*b[1] + 3*u*t*t*b[3] + t*t*t*b[5];
        len += Math.hypot(x - px, y - py);
        pts.push({ x, y, len, w: seg.width });
        px = x; py = y;
      }
      x0 = b[4]; y0 = b[5];
    }
  }
  for (let i = 0; i < pts.length; i++) {
    let sum = 0, n = 0;
    for (let j = Math.max(0, i - 8); j <= Math.min(pts.length - 1, i + 8); j++) { sum += pts[j].w; n++; }
    pts[i].sw = sum / n;
  }
  return { pts, total: len };
}

/* 沿笔迹渐变:蓝 → 黄,返回 [r,g,b] */
const SCRIBE_STOPS = {
  light: [[0, [29, 32, 131]], [.5, [61, 73, 208]], [.75, [126, 139, 242]], [.9, [232, 194, 90]], [1, [252, 206, 15]]],
  dark:  [[0, [72, 81, 205]], [.5, [97, 110, 238]], [.75, [155, 166, 255]], [.9, [238, 202, 96]], [1, [252, 206, 15]]],
};
function scribeRGB(stops, t) {
  let i = 1;
  while (i < stops.length - 1 && t > stops[i][0]) i++;
  const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
  const k = Math.min(1, Math.max(0, (t - t0) / (t1 - t0 || 1)));
  return [c0[0] + (c1[0] - c0[0]) * k, c0[1] + (c1[1] - c0[1]) * k, c0[2] + (c1[2] - c0[2]) * k];
}
/* 色调分层(参考图的深浅蓝叠拼):light 提亮 / dark 压深 / base 原色 */
function scribeTone(rgb, tone) {
  const to = tone === 'light' ? [255, 255, 255] : [13, 16, 72];
  const k = tone === 'base' ? 0 : .3;
  return 'rgb(' + Math.round(rgb[0] + (to[0] - rgb[0]) * k) + ',' +
    Math.round(rgb[1] + (to[1] - rgb[1]) * k) + ',' +
    Math.round(rgb[2] + (to[2] - rgb[2]) * k) + ')';
}
const scribeHash = i => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

function WhaleScribe() {
  const ref = useRef(null);
  useEffect(() => {
    const host = ref.current;
    const body = host.querySelector('.body-cv');
    const glow = host.querySelector('.glow-cv');
    const bctx = body.getContext('2d');
    const gctx = glow.getContext('2d');
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0, h = 0, raf;
    const glyph = new Path2D(MARK_D);
    const { pts, total } = scribeSamples();

    const pointAt = L => {
      L = Math.min(Math.max(L, 0), total);
      let lo = 0, hi = pts.length - 1;
      while (lo + 1 < hi) { const mid = (lo + hi) >> 1; (pts[mid].len <= L ? lo = mid : hi = mid); }
      const p0 = pts[lo], p1 = pts[hi];
      const k = (L - p0.len) / ((p1.len - p0.len) || 1);
      return { x: p0.x + (p1.x - p0.x) * k, y: p0.y + (p1.y - p0.y) * k, sw: p0.sw + (p1.sw - p0.sw) * k };
    };

    /* 预生成笔触簇:椭圆笔触(沿切向) + 少量飞溅圆点,确定性伪随机保证帧间稳定 */
    const dabs = [];
    const SPACING = 3.2;
    let di = 0;
    for (let L = 1.2; L < total - .8; L += SPACING, di++) {
      const P = pointAt(L);
      const P1 = pointAt(L - .6), P2 = pointAt(L + .6);
      const ang = Math.atan2(P2.y - P1.y, P2.x - P1.x);
      const nx = -Math.sin(ang), ny = Math.cos(ang);
      const lat = (scribeHash(di + 300) - .5) * .35 * P.sw;
      const tr = scribeHash(di + 200);
      const rgb = { light: scribeRGB(SCRIBE_STOPS.light, L / total), dark: scribeRGB(SCRIBE_STOPS.dark, L / total) };
      const tone = tr < .3 ? 'light' : (tr < .55 ? 'dark' : 'base');
      dabs.push({
        L, kind: 'dab',
        x: P.x + nx * lat, y: P.y + ny * lat, ang,
        rx: (5.5 + scribeHash(di) * 4) / 2,
        ry: P.sw * (.62 + scribeHash(di + 100) * .42) / 2,
        col: { light: scribeTone(rgb.light, tone), dark: scribeTone(rgb.dark, tone) },
      });
      if (scribeHash(di + 400) < .2) {       // 飞溅圆点(字形外,不裁剪)
        const dl = (.75 + scribeHash(di + 600) * .65) * P.sw * (scribeHash(di + 700) < .5 ? 1 : -1);
        dabs.push({
          L: L + 1.4, kind: 'drop',
          x: P.x + nx * dl, y: P.y + ny * dl,
          r: .7 + scribeHash(di + 500) * 1.1,
          col: { light: scribeTone(rgb.light, 'base'), dark: scribeTone(rgb.dark, 'base') },
        });
      }
    }

    const resize = () => {
      w = host.offsetWidth; h = host.offsetHeight;
      for (const c of [body, glow]) { c.width = w * DPR; c.height = h * DPR; }
    };
    resize();

    let theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    const mo = new MutationObserver(() => {
      theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    /* 时间轴(秒):写 2.2 → 停 2 → 擦 2.2 → 留白 1.6,一轮 8s */
    const CYC = 8, T_W = 2.2, T_H = 2, T_E = 2.2;
    const ss = t => t * t * (3 - 2 * t);

    const draw = now => {
      const t = (now / 1000) % CYC;
      let a = -1, b = -1;                      // 书写前沿区间 [a,b]
      if (t < T_W) { a = 0; b = total * ss(t / T_W); }
      else if (t < T_W + T_H) { a = 0; b = total; }
      else if (t < T_W + T_H + T_E) { a = total * ss((t - T_W - T_H) / T_E); b = total; }

      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.clearRect(0, 0, body.width, body.height);
      if (b > 0) {
        const s = (w * DPR) / SCRIBE_VB.w;
        bctx.setTransform(s, 0, 0, s, -SCRIBE_VB.x * s, -SCRIBE_VB.y * s);
        /* 第一遍:椭圆笔触(随前沿弹入/收走,各自缩放渐隐) → 裁剪进字形轮廓 */
        for (const d of dabs) {
          if (d.kind !== 'dab') continue;
          const kin = Math.min(1, Math.max(0, (b - d.L) / 5));
          const kout = a <= 0 ? 1 : Math.min(1, Math.max(0, (d.L - a) / 5));
          let k = Math.min(kin, kout);
          if (k <= .02) continue;
          k = ss(k);
          bctx.globalAlpha = Math.min(1, k * 1.4);
          bctx.fillStyle = d.col[theme];
          bctx.beginPath();
          bctx.ellipse(d.x, d.y, d.rx * k, d.ry * k, d.ang, 0, 6.2832);
          bctx.fill();
        }
        bctx.globalAlpha = 1;
        bctx.globalCompositeOperation = 'destination-in';
        bctx.fill(glyph);
        bctx.globalCompositeOperation = 'source-over';
        /* 第二遍:飞溅圆点贴着字形外缘,不裁剪 */
        for (const d of dabs) {
          if (d.kind !== 'drop') continue;
          const kin = Math.min(1, Math.max(0, (b - d.L) / 5));
          const kout = a <= 0 ? 1 : Math.min(1, Math.max(0, (d.L - a) / 5));
          let k = Math.min(kin, kout);
          if (k <= .02) continue;
          k = ss(k);
          bctx.globalAlpha = k * .9;
          bctx.fillStyle = d.col[theme];
          bctx.beginPath();
          bctx.arc(d.x, d.y, d.r * k, 0, 6.2832);
          bctx.fill();
        }
        bctx.globalAlpha = 1;
      }

      /* 辉光层:复制主画布,元素级 CSS blur 由 GPU 合成 */
      gctx.setTransform(1, 0, 0, 1, 0, 0);
      gctx.clearRect(0, 0, glow.width, glow.height);
      gctx.drawImage(body, 0, 0);

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    window.addEventListener('resize', resize);
    return () => {
      cancelAnimationFrame(raf);
      mo.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, []);
  return (
    <span className="mark-stack" ref={ref} aria-hidden="true">
      <canvas className="lay glow-cv" />
      <canvas className="lay body-cv" />
    </span>
  );
}
