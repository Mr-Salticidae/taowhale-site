/* 主屏专用:Canvas 2D"书写循环"鲸环 ——
   空白起笔 → 沿字体中轴线一笔写出 → 停留 2s → 同向逐笔擦除 → 留白 → 无限循环。
   颜色为沿笔迹的蓝→黄渐变(起笔 #1D2083,收笔 #FCCE0F):书写时蓝色先行,
   擦除时黄色殿后,颜色即过程。全 Canvas 自绘,零 SVG 滤镜,无卡顿。 */
const SCRIBE_VB = { x: 273.79, y: 475.32, w: 54.15, h: 33.63 };
/* 书写中轴线(与字体轮廓逐像素校准,coverage 99.88%):A=螺旋卷(细笔),B=斜线+大环(粗笔) */
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

/* 中轴线按弧长采样:{x,y,len,w}[],宽度在细笔→粗笔交界平滑过渡 */
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
  for (let i = 0; i < pts.length; i++) {       // 宽度滑动平均,交界不突变
    let sum = 0, n = 0;
    for (let j = Math.max(0, i - 8); j <= Math.min(pts.length - 1, i + 8); j++) { sum += pts[j].w; n++; }
    pts[i].sw = sum / n;
  }
  return { pts, total: len };
}

/* 沿笔迹渐变色:多停靠点线性插值,t∈[0,1] 为弧长占比 */
function scribeColor(stops, t) {
  let i = 1;
  while (i < stops.length - 1 && t > stops[i][0]) i++;
  const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
  const k = Math.min(1, Math.max(0, (t - t0) / (t1 - t0 || 1)));
  return 'rgb(' + Math.round(c0[0] + (c1[0] - c0[0]) * k) + ',' +
    Math.round(c0[1] + (c1[1] - c0[1]) * k) + ',' +
    Math.round(c0[2] + (c1[2] - c0[2]) * k) + ')';
}
const SCRIBE_STOPS = {
  light: [[0, [29, 32, 131]], [.5, [61, 73, 208]], [.75, [126, 139, 242]], [.9, [232, 194, 90]], [1, [252, 206, 15]]],
  dark:  [[0, [72, 81, 205]], [.5, [97, 110, 238]], [.75, [155, 166, 255]], [.9, [238, 202, 96]], [1, [252, 206, 15]]],
};

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

    /* 时间轴(秒):写 2.2 → 停 2 → 擦 2.2 → 留白 1.6,一轮 8s;smoothstep 起收缓动 */
    const CYC = 8, T_W = 2.2, T_H = 2, T_E = 2.2;
    const ss = t => t * t * (3 - 2 * t);

    /* 在弧长 L 处取精确插值点(子段内线性),保证前沿亚像素平滑推进 */
    const pointAt = L => {
      let lo = 0, hi = pts.length - 1;
      while (lo + 1 < hi) { const mid = (lo + hi) >> 1; (pts[mid].len <= L ? lo = mid : hi = mid); }
      const p0 = pts[lo], p1 = pts[hi];
      const k = (L - p0.len) / ((p1.len - p0.len) || 1);
      return { x: p0.x + (p1.x - p0.x) * k, y: p0.y + (p1.y - p0.y) * k, sw: p0.sw + (p1.sw - p0.sw) * k };
    };

    const draw = now => {
      const t = (now / 1000) % CYC;
      let a = 0, b = 0;                       // 可见弧长区间 [a,b]
      if (t < T_W) { b = total * ss(t / T_W); }
      else if (t < T_W + T_H) { b = total; }
      else if (t < T_W + T_H + T_E) { a = total * ss((t - T_W - T_H) / T_E); b = total; }
      /* 其余:留白 */

      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.clearRect(0, 0, body.width, body.height);
      if (b - a > .05) {
        const s = (w * DPR) / SCRIBE_VB.w;
        bctx.setTransform(s, 0, 0, s, -SCRIBE_VB.x * s, -SCRIBE_VB.y * s);
        bctx.lineCap = 'round';
        bctx.lineJoin = 'round';
        const stops = SCRIBE_STOPS[theme];
        let prev = pointAt(a);
        let prevL = a;
        for (let i = 0; i < pts.length; i++) {
          if (pts[i].len <= a) continue;
          const cur = pts[i].len >= b ? pointAt(b) : pts[i];
          const curL = Math.min(pts[i].len, b);
          bctx.strokeStyle = scribeColor(stops, ((prevL + curL) / 2) / total);
          bctx.lineWidth = (prev.sw + cur.sw) / 2;
          bctx.beginPath();
          bctx.moveTo(prev.x, prev.y);
          bctx.lineTo(cur.x, cur.y);
          bctx.stroke();
          prev = cur; prevL = curL;
          if (pts[i].len >= b) break;
        }
        /* 裁剪进精确字形轮廓 */
        bctx.globalCompositeOperation = 'destination-in';
        bctx.fill(glyph);
        bctx.globalCompositeOperation = 'source-over';
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
