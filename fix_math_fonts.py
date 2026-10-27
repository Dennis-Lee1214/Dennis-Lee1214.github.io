#!/usr/bin/env python3
"""Webpage HTML Export 导出后运行一次：把数学公式字体改成从 CDN 加载。
用法：在仓库文件夹里运行  python3 fix_math_fonts.py
"""
import pathlib, re

CDN = {
    "tex": "https://cdn.jsdelivr.net/npm/@mathjax/mathjax-tex-font@4/chtml/woff2/",
    "mhc": "https://cdn.jsdelivr.net/npm/@mathjax/mathjax-mhchem-font-extension@4/chtml/woff2/",
}
pat = re.compile(r'url\("[^"]*?(mjx-(tex|mhc)-[a-z0-9]+\.woff2)"\)')
root = pathlib.Path(__file__).resolve().parent
fixed = 0
for f in root.rglob("*.html"):
    text = f.read_text(encoding="utf-8")
    new, n = pat.subn(lambda m: f'url("{CDN[m.group(2)]}{m.group(1)}")', text)
    if n:
        with open(f, "w", encoding="utf-8") as out:
            out.write(new)
        fixed += 1
        print(f"已修复 {n} 处: {f.relative_to(root)}")
print(f"完成，共修复 {fixed} 个文件。" if fixed else "没有需要修复的文件。")
