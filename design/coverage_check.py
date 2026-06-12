# -*- coding: utf-8 -*-
"""像素级校验:遮罩描边是否完整覆盖 logo。输出未覆盖像素数 + 标红示意图。
用法: python coverage_check.py "<WRITE_A path>" "<WRITE_B path>" [widthA] [widthB]
"""
import sys
import fitz
from PIL import Image

MARK_D = "M 326.94 488.19 C 326.94 484.34 324.85 480.89 322.33 478.91 C 319.04 476.38 315.83 476.32 314.32 476.38 C 309.56 476.57 305.55 478.17 301.77 481.08 C 289.29 491.13 287.31 500.49 281.63 500.62 C 281.0 500.63 280.24 500.31 279.79 499.9 C 279.19 499.36 278.84 498.55 278.91 497.66 C 279.02 496.36 280.05 495.3 281.35 495.17 C 282.41 495.06 283.36 495.56 283.9 496.37 L 285.68 494.83 C 284.56 493.48 282.86 492.58 280.97 492.58 C 277.74 492.58 274.79 495.06 274.79 498.75 C 274.79 500.31 275.39 501.78 276.39 502.83 C 277.57 504.08 279.03 504.82 280.72 504.85 C 289.87 505.04 289.5 497.74 303.05 487.04 C 309.39 482.03 314.78 481.23 317.93 482.61 C 320.18 483.59 321.81 485.95 321.74 488.58 C 321.61 493.3 319.8 497.2 317.01 500.12 C 313.9 503.37 309.52 505.4 304.66 505.4 C 303.51 505.4 302.39 505.29 301.31 505.07 C 299.08 504.63 297.0 503.75 295.18 502.53 L 294.67 502.97 L 293.94 503.62 C 294.33 503.92 294.73 504.21 295.13 504.48 C 297.47 506.06 300.15 507.16 303.03 507.65 L 303.03 507.65 C 304.17 507.85 305.33 507.95 306.52 507.95 C 319.8 507.95 326.92 496.62 326.94 488.19 Z"
VB = "271 472 60 40"
SCALE = 16


def render(svg_body, name):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VB}"><rect x="271" y="472" width="60" height="40" fill="white"/>{svg_body}</svg>'
    open(name + ".svg", "w", encoding="utf-8").write(svg)
    doc = fitz.open(name + ".svg")
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    pix.save(name + ".png")
    return Image.open(name + ".png").convert("L")


def main():
    wa, wb = sys.argv[1], sys.argv[2]
    swa = sys.argv[3] if len(sys.argv) > 3 else "4.2"
    swb = sys.argv[4] if len(sys.argv) > 4 else "7"
    glyph = render(f'<path d="{MARK_D}" fill="black"/>', "_glyph")
    mask = render(
        f'<path d="{wa}" fill="none" stroke="black" stroke-width="{swa}" stroke-linecap="round"/>'
        f'<path d="{wb}" fill="none" stroke="black" stroke-width="{swb}" stroke-linecap="round"/>',
        "_mask")
    gw, gh = glyph.size
    gp, mp = glyph.load(), mask.load()
    out = Image.new("RGB", (gw, gh), "white")
    op = out.load()
    miss = total = 0
    for y in range(gh):
        for x in range(gw):
            if gp[x, y] < 128:          # glyph 像素
                total += 1
                if mp[x, y] >= 128:     # 未被遮罩覆盖
                    miss += 1
                    op[x, y] = (255, 0, 0)
                else:
                    op[x, y] = (200, 200, 200)
    out.save("coverage_diff.png")
    print(f"glyph px={total}  uncovered px={miss}  ({miss/total*100:.2f}%)")


if __name__ == "__main__":
    main()
