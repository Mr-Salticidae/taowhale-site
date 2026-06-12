/* ---------- 鲸迹洋流:2D 流场细线,呼应 logo 单笔环线的运笔 ---------- */
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

    let flowRGB = '39,52,91';
    const readFlow = () => {
      const v = getComputedStyle(document.documentElement).getPropertyValue('--flow').trim();
      if (v) flowRGB = v;
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

    const N = Math.min(110, Math.floor(w / 13));
    const spawn = p => {
      p.x = Math.random() * w * 1.06 - w * .03;
      p.y = Math.random() * h;
      p.age = 0;
      p.life = 260 + Math.random() * 380;
      p.speed = .5 + Math.random() * .9;
      p.bold = Math.random() < .1;            // 少量主笔,多数游丝
      return p;
    };
    const pts = Array.from({ length: N }, () => { const p = spawn({}); p.age = Math.random() * p.life; return p; });

    // 流向场:恒定东向洋流 + 缓慢起伏的涡旋(呼应鲸环回笔),鼠标轻扰
    const angle = (x, y, t) => {
      const nx = x / w, ny = y / h;
      let a = Math.sin(ny * 4.6 + t * .20 + Math.sin(nx * 2.6 + t * .11) * 1.35) * .58
            + Math.sin(nx * 1.9 - t * .14 + 2.1) * .34
            + Math.cos((nx * 1.4 + ny * 2.2) * 2.6 + t * .08) * .26;
      const dx = nx - mouse.x, dy = ny - mouse.y;
      a += Math.exp(-(dx * dx + dy * dy) * 18) * dx * 6;
      return a;
    };

    const draw = now => {
      mouse.x += (mouse.tx - mouse.x) * .04;
      mouse.y += (mouse.ty - mouse.y) * .04;
      const t = now / 1000;
      // 留迹擦除:画布保持透明,旧迹缓慢淡出,主题切换时自然过渡
      ctx.globalCompositeOperation = 'destination-out';
      ctx.fillStyle = 'rgba(0,0,0,.05)';
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = 'source-over';
      for (const p of pts) {
        const a = angle(p.x, p.y, t);
        const px = p.x, py = p.y;
        p.x += Math.cos(a) * p.speed * 1.5 + .5;
        p.y += Math.sin(a) * p.speed * 1.15;
        p.age++;
        if (p.x > w + 12 || p.y < -12 || p.y > h + 12 || p.age > p.life) { spawn(p); continue; }
        const fade = Math.min(p.age / 50, 1, (p.life - p.age) / 70);
        ctx.strokeStyle = 'rgba(' + flowRGB + ',' + ((p.bold ? .2 : .11) * fade) + ')';
        ctx.lineWidth = p.bold ? 1.5 : .7;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
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
