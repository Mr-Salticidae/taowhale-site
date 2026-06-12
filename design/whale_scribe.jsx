/* 主屏专用:Canvas 2D 实现的"书写循环"鲸环 ——
   隐形画笔沿字体中轴线一笔写出 → 停留 → 同向逐笔擦除 → 留白 → 无限循环。
   材质为流动的发光水流渐变(主体蓝 #1D2083,尾部点缀黄 #FCCE0F)。
   弃用 SVG 滤镜/遮罩:其每帧 CPU 重栅格化在 Chromium 中会冻结渲染。 */
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

function scribePath(seg) {
  const p = new Path2D();
  p.moveTo(seg.start[0], seg.start[1]);
  for (const b of seg.beziers) p.bezierCurveTo(b[0], b[1], b[2], b[3], b[4], b[5]);
  return p;
}

function scribeLen(seg) {
  let len = 0, px = seg.start[0], py = seg.start[1];
  let x0 = px, y0 = py;
  for (const b of seg.beziers) {
    for (let i = 1; i <= 24; i++) {
      const t = i / 24, u = 1 - t;
      const x = u*u*u*x0 + 3*u*u*t*b[0] + 3*u*t*t*b[2] + t*t*t*b[4];
      const y = u*u*u*y0 + 3*u*u*t*b[1] + 3*u*t*t*b[3] + t*t*t*b[5];
      len += Math.hypot(x - px, y - py);
      px = x; py = y;
    }
    x0 = b[4]; y0 = b[5];
  }
  return len;
}

/* 时间轴(0-1):segment 在 [w0,w1] 写入,[e0,e1] 擦除;dashOffset L→0→-L */
function scribeOffset(p, L, w0, w1, e0, e1) {
  if (p < w0) return L;
  if (p < w1) return L * (1 - (p - w0) / (w1 - w0));
  if (p < e0) return 0;
  if (p < e1) return -L * (p - e0) / (e1 - e0);
  return -L;
}

function WhaleScribe() {
  const ref = useRef(null);
  useEffect(() => {
    const host = ref.current;
    const body = host.querySelector('.body-cv');
    const glow = host.querySelector('.glow-cv');
    const bctx = body.getContext('2d');
    const gctx = glow.getContext('2d');
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const mask = document.createElement('canvas');
    const mctx = mask.getContext('2d');
    let w = 0, h = 0, raf;

    const pathA = scribePath(SCRIBE_A), pathB = scribePath(SCRIBE_B);
    const lenA = scribeLen(SCRIBE_A), lenB = scribeLen(SCRIBE_B);
    const glyph = new Path2D(MARK_D);

    const resize = () => {
      w = host.offsetWidth; h = host.offsetHeight;
      for (const c of [body, glow, mask]) { c.width = w * DPR; c.height = h * DPR; }
    };
    resize();

    /* 流动水色条带:烘焙一次成 1D 渐变条,之后以 CanvasPattern 平移实现流动 */
    const strips = {};
    const bake = (stops) => {
      const c = document.createElement('canvas');
      c.width = 512; c.height = 1;
      const x = c.getContext('2d');
      const g = x.createLinearGradient(0, 0, 512, 0);
      for (const [o, col] of stops) g.addColorStop(o, col);
      x.fillStyle = g;
      x.fillRect(0, 0, 512, 1);
      return c;
    };
    /* reflect:正序 + 镜像拼一条,首尾同色,平铺无缝 */
    strips.light = bake([[0, '#1D2083'], [.175, '#2F36B8'], [.275, '#5760E0'], [.35, '#FCCE0F'], [.4, '#2F36B8'], [.5, '#1D2083'], [.6, '#2F36B8'], [.65, '#FCCE0F'], [.725, '#5760E0'], [.825, '#2F36B8'], [1, '#1D2083']]);
    strips.dark = bake([[0, '#232A9E'], [.175, '#3A43D0'], [.275, '#6A74FF'], [.35, '#FCCE0F'], [.4, '#3A43D0'], [.5, '#232A9E'], [.6, '#3A43D0'], [.65, '#FCCE0F'], [.725, '#6A74FF'], [.825, '#3A43D0'], [1, '#232A9E']]);
    let theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    const mo = new MutationObserver(() => {
      theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    const CYC = 11.5;          // 一轮:写 0-28% / 停 28-55% / 擦 55-83% / 留白 83-100%
    const draw = now => {
      const t = now / 1000;
      const p = (t % CYC) / CYC;
      const s = (w * DPR) / SCRIBE_VB.w;   // 字形单位 → 画布像素

      /* 1. 遮罩画布:dash 驱动的书写笔迹 */
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.clearRect(0, 0, mask.width, mask.height);
      mctx.setTransform(s, 0, 0, s, -SCRIBE_VB.x * s, -SCRIBE_VB.y * s);
      mctx.lineCap = 'round';
      mctx.lineJoin = 'round';
      mctx.strokeStyle = '#fff';
      mctx.lineWidth = SCRIBE_A.width;
      mctx.setLineDash([lenA, lenA]);
      mctx.lineDashOffset = scribeOffset(p, lenA, 0, .06, .55, .61);
      mctx.stroke(pathA);
      mctx.lineWidth = SCRIBE_B.width;
      mctx.setLineDash([lenB, lenB]);
      mctx.lineDashOffset = scribeOffset(p, lenB, .06, .28, .61, .83);
      mctx.stroke(pathB);

      /* 2. 主画布:流动渐变填充字形,再用笔迹遮罩裁剪 */
      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.clearRect(0, 0, body.width, body.height);
      bctx.setTransform(s, 0, 0, s, -SCRIBE_VB.x * s, -SCRIBE_VB.y * s);
      const pat = bctx.createPattern(strips[theme], 'repeat');
      const m = new DOMMatrix();
      /* 条带宽 512px 映射为 ~1.35 个字形宽,沿笔势方向 -9° 流动 */
      m.translateSelf(SCRIBE_VB.x + ((t * 4.2) % (SCRIBE_VB.w * 1.35)), SCRIBE_VB.y);
      m.rotateSelf(-9);
      m.scaleSelf(SCRIBE_VB.w * 1.35 / 512, 1);
      pat.setTransform(m);
      bctx.fillStyle = pat;
      bctx.fill(glyph);
      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.globalCompositeOperation = 'destination-in';
      bctx.drawImage(mask, 0, 0);
      bctx.globalCompositeOperation = 'source-over';

      /* 3. 辉光画布:复制主画布,元素级 CSS blur 由 GPU 合成 */
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
