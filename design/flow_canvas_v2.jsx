/* ---------- 鲸迹洋流:显式轨迹历史 + 每帧全量重绘(无残留),蓝主体 + 黄点缀 ---------- */
function FlowCanvas() {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas.getContext('2d');
    let w, h, raf;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      w = canvas.offsetWidth; h = canvas.offsetHeight;
      canvas.width = w * DPR; canvas.height = h * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      ctx.lineCap = 'round';
    };
    resize();

    let flowRGB = '29,32,131', accentRGB = '252,206,15';
    const readFlow = () => {
      const cs = getComputedStyle(document.documentElement);
      const v = cs.getPropertyValue('--flow').trim();
      const a = cs.getPropertyValue('--flow-accent').trim();
      if (v) flowRGB = v;
      if (a) accentRGB = a;
    };
    readFlow();
    const mo = new MutationObserver(readFlow);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    const mouse = { x: .5, y: .5, tx: .5, ty: .5 };
    const onMouse = e => {
      const r = canvas.getBoundingClientRect();
      mouse.tx = (e.clientX - r.left) / r.width;
      mouse.ty = (e.clientY - r.top) / r.height;
    };
    const host = canvas.parentElement;
    host.addEventListener('mousemove', onMouse);

    const N = Math.min(70, Math.floor(w / 19));
    const TRAIL = 34;                            // 尾迹点数:超出即丢弃,旧线条不可能残留
    const spawn = p => {
      p.x = Math.random() * w * 1.06 - w * .03;
      p.y = Math.random() * h;
      p.age = 0;
      p.life = 420 + Math.random() * 520;
      p.speed = .3 + Math.random() * .55;
      p.bold = Math.random() < .14;              // 少而果断的主笔
      p.accent = p.bold && Math.random() < .35;  // 部分主笔用黄色点缀
      p.trail = [];
      return p;
    };
    const pts = Array.from({ length: N }, () => { const p = spawn({}); p.age = Math.random() * p.life; return p; });

    // 流向场:恒定东向洋流 + 极缓起伏的涡旋(呼应鲸环回笔),鼠标轻扰
    const angle = (x, y, t) => {
      const nx = x / w, ny = y / h;
      let a = Math.sin(ny * 4.6 + t * .12 + Math.sin(nx * 2.6 + t * .07) * 1.35) * .58
            + Math.sin(nx * 1.9 - t * .09 + 2.1) * .34
            + Math.cos((nx * 1.4 + ny * 2.2) * 2.6 + t * .05) * .26;
      const dx = nx - mouse.x, dy = ny - mouse.y;
      a += Math.exp(-(dx * dx + dy * dy) * 10) * dx * 3.5;
      return a;
    };

    const draw = now => {
      mouse.x += (mouse.tx - mouse.x) * .04;
      mouse.y += (mouse.ty - mouse.y) * .04;
      const t = now / 1000;
      ctx.clearRect(0, 0, w, h);                 // 全量清屏
      for (const p of pts) {
        const a = angle(p.x, p.y, t);
        p.x += Math.cos(a) * p.speed * 1.5 + .28;
        p.y += Math.sin(a) * p.speed * 1.15;
        p.age++;
        p.trail.push(p.x, p.y);
        if (p.trail.length > TRAIL * 2) p.trail.splice(0, 2);
        if (p.x > w + 12 || p.y < -12 || p.y > h + 12 || p.age > p.life) { spawn(p); continue; }
        const fade = Math.min(p.age / 60, 1, (p.life - p.age) / 90);
        const rgb = p.accent ? accentRGB : flowRGB;
        const baseA = (p.bold ? .3 : .14) * fade;
        const n = p.trail.length / 2;
        ctx.lineWidth = p.bold ? 1.8 : .8;
        for (let i = 1; i < n; i++) {
          ctx.strokeStyle = 'rgba(' + rgb + ',' + (baseA * i / n) + ')';
          ctx.beginPath();
          ctx.moveTo(p.trail[(i - 1) * 2], p.trail[(i - 1) * 2 + 1]);
          ctx.lineTo(p.trail[i * 2], p.trail[i * 2 + 1]);
          ctx.stroke();
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    window.addEventListener('resize', resize);
    return () => {
      cancelAnimationFrame(raf);
      mo.disconnect();
      window.removeEventListener('resize', resize);
      host.removeEventListener('mousemove', onMouse);
    };
  }, []);
  return <canvas ref={ref} className="hero-canvas flow" />;
}
