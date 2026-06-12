# -*- coding: utf-8 -*-
"""从 TAOWHALELOGONEW.pdf 提取品牌矢量资产为独立 SVG（fill 使用 currentColor 便于主题换色）。"""
import fitz

doc = fitz.open("TAOWHALELOGONEW.pdf")
page = doc[0]


def pt(p):
    return f"{round(p.x, 2)} {round(p.y, 2)}"


def drawing_to_path(d):
    """fitz drawing items → SVG path d（自动判断子路径起点）"""
    segs = []
    cur = None
    for it in d["items"]:
        kind = it[0]
        if kind == "c":
            p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
            if cur is None or (abs(cur.x - p1.x) > 0.01 or abs(cur.y - p1.y) > 0.01):
                segs.append(f"M {pt(p1)}")
            segs.append(f"C {pt(p2)} {pt(p3)} {pt(p4)}")
            cur = p4
        elif kind == "l":
            p1, p2 = it[1], it[2]
            if cur is None or (abs(cur.x - p1.x) > 0.01 or abs(cur.y - p1.y) > 0.01):
                segs.append(f"M {pt(p1)}")
            segs.append(f"L {pt(p2)}")
            cur = p2
        elif kind == "re":
            r = it[1]
            segs.append(f"M {round(r.x0,2)} {round(r.y0,2)} H {round(r.x1,2)} V {round(r.y1,2)} H {round(r.x0,2)} Z")
            cur = None
    segs.append("Z")
    return " ".join(segs)


def export(name, rect_filter, pad=1.0):
    sel = [d for d in page.get_drawings() if rect_filter(d["rect"])]
    if not sel:
        print(name, "EMPTY")
        return
    x0 = min(d["rect"].x0 for d in sel) - pad
    y0 = min(d["rect"].y0 for d in sel) - pad
    x1 = max(d["rect"].x1 for d in sel) + pad
    y1 = max(d["rect"].y1 for d in sel) + pad
    paths = "\n".join(f'  <path d="{drawing_to_path(d)}"/>' for d in sel)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{round(x0,2)} {round(y0,2)} '
           f'{round(x1-x0,2)} {round(y1-y0,2)}" fill="currentColor" fill-rule="nonzero">\n{paths}\n</svg>\n')
    with open(name, "w", encoding="utf-8") as f:
        f.write(svg)
    print(name, f"{len(sel)} drawings, viewBox {round(x1-x0,1)}x{round(y1-y0,1)}")


# 鲸鱼符号：中文纵排组合里的大尺寸符号 (274.8,476.3)-(326.9,508.0)
export("mark.svg", lambda r: 270 < r.x0 < 330 and 470 < r.y0 < 510)
# 英文字标 TAOWHALE：第一组字母 y 120.4-142.6
export("wordmark-en.svg", lambda r: 120 < r.y0 < 123 and 142 < r.y1 < 143)
# 中文字标 鲸海拾贝：纵排组合下方 y 521-544
export("wordmark-cn.svg", lambda r: 515 < r.y0 < 545 and r.y1 < 560)

# 页4 藏青底色
p4 = doc[3]
for d in p4.get_drawings():
    if d["rect"].width > 500 and d["rect"].height > 700 and d.get("fill"):
        c = d["fill"]
        print("navy bg:", "#%02X%02X%02X" % tuple(round(v * 255) for v in c))
        break
# 页1 主体灰黑
c = page.get_drawings()[0]["fill"]
print("ink:", "#%02X%02X%02X" % tuple(round(v * 255) for v in c))
