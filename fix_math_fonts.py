#!/usr/bin/env python3
"""Webpage HTML Export 导出后的自动修复（提交时由 pre-commit hook 自动运行）：
1. 把数学公式字体改成从 CDN 加载（插件没导出这些字体）
2. 去掉左右侧栏（文件目录、搜索、关系图、目录）、底部反向链接和链接悬停预览，正文铺满页面；手机上缩小缩进和边距
手动运行：在仓库文件夹里  python3 fix_math_fonts.py
"""
import pathlib, re

CDN = {
    "tex": "https://cdn.jsdelivr.net/npm/@mathjax/mathjax-tex-font@4/chtml/woff2/",
    "mhc": "https://cdn.jsdelivr.net/npm/@mathjax/mathjax-mhchem-font-extension@4/chtml/woff2/",
}
FONT = re.compile(r'url\("[^"]*?(mjx-(tex|mhc)-[a-z0-9]+\.woff2)"\)')

MARK = 'id="plain-layout"'
LAYOUT = """<style id="plain-layout">
#left-content,#right-content,#navbar,.sidebar-collapse-icon,.footer,.backlinks,.hover-popover{display:none!important}
#main-horizontal,#center-content{width:100%!important;max-width:none!important}
body,.obsidian-document,.markdown-preview-view{--line-width:100%!important;--file-line-width:100%!important;--line-width-adaptive:100%!important;--max-width:100%!important}
.markdown-preview-sizer{max-width:none!important;width:100%!important}
@media (max-width:760px){body,.obsidian-document,.markdown-preview-view{--list-indent:1.1em!important;--file-margins-x:14px!important;--file-margins-y:20px!important;--font-text-size:15px!important;--p-spacing:0.75rem!important}.markdown-preview-view{padding-left:14px!important;padding-right:14px!important}.markdown-rendered li{line-height:1.6}}
</style>"""
OLD_LAYOUT = re.compile(r'<style id="plain-layout">.*?</style>', re.S)

root = pathlib.Path(__file__).resolve().parent
fixed = 0
for f in root.rglob("*.html"):
    if ".git" in f.parts:
        continue
    text = f.read_text(encoding="utf-8")
    new = FONT.sub(lambda m: f'url("{CDN[m.group(2)]}{m.group(1)}")', text)
    if 'id="left-content"' in new and "</head>" in new:
        if MARK in new:
            new = OLD_LAYOUT.sub(lambda m: LAYOUT, new, count=1)
        else:
            new = new.replace("</head>", LAYOUT + "</head>", 1)
    if new != text:
        with open(f, "w", encoding="utf-8") as out:
            out.write(new)
        fixed += 1
print(f"完成，共修复 {fixed} 个文件。" if fixed else "没有需要修复的文件。")
