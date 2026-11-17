#!/usr/bin/env python3
"""Turn the Obsidian notes in <vault>/Study into note pages under notes/.

Usage:  python3 tools/build_notes.py "<path to Obsidian vault>"

Every .md file under <vault>/Study becomes notes/<slug>.html, e.g.
  Study/Year 1/COMP1312/COMP1312-Programming I.md -> notes/comp1312-programming-i.html

Handled Obsidian syntax:
  ![[image.png]], ![[image.png|300]]   images (copied to notes/assets/, centred;
                                       large ones all the same width)
  [[Note]], [[Note|text]], [[Note#Heading|text]], [[#Heading]]   links
  > [!note] Title                      callouts
  ==highlight==, %%comment%%
Frontmatter options:
  title: Custom page title
  publish: false        -> skip this note

Only the Python standard library and pandoc are needed.
"""

import os
import re
import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
OUT_DIR = SITE / "notes"
ASSET_DIR = OUT_DIR / "assets"
TEMPLATE = SITE / "tools" / "note-template.html"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`]*)(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]\|#]*)(#[^\]\|]*)?(\|[^\]]*)?\]\]")
CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\][+-]?\s*(.*)$")
MODULE_RE = re.compile(r"^([A-Z]{4}\d{4})[-_ ]+(.*)$")


def slugify(name):
    """File name -> URL slug: 'COMP1312-Programming I' -> 'comp1312-programming-i'."""
    s = name.lower()
    s = re.sub(r"[^\w一-鿿]+", "-", s)
    return s.strip("-_") or "note"


def heading_id(text):
    """Heading text -> anchor id (used for both headings and links to them)."""
    s = re.sub(r"[*_`~=\[\]]", "", text).strip().lower()
    s = re.sub(r"[^\w一-鿿\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-") or "section"


def image_display_width(path):
    """Width in CSS pixels the image should be shown at, at most.

    PNG screenshots from a Retina Mac are saved at 144 dpi, so they count as
    half their pixel width; everything else as its own pixel width.
    Returns None if the size can't be read.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width = struct.unpack(">I", data[16:20])[0]
        scale = 1
        i = 8
        while i + 8 <= len(data):
            length = struct.unpack(">I", data[i:i + 4])[0]
            ctype = data[i + 4:i + 8]
            if ctype == b"pHYs":
                ppm, _, unit = struct.unpack(">IIB", data[i + 8:i + 17])
                if unit == 1 and ppm >= 5000:      # >= ~127 dpi -> 2x screenshot
                    scale = 2
            if ctype == b"IDAT":
                break
            i += 12 + length
        return round(width / scale)
    if data[:2] == b"\xff\xd8":                   # JPEG: read the SOF marker
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            if data[i + 1] in (0xC0, 0xC1, 0xC2):
                return struct.unpack(">H", data[i + 7:i + 9])[0]
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<H", data[6:8])[0]
    return None


def asset_name(filename):
    return re.sub(r"\s+", "-", filename)


def wrap_images(html):
    """Put every image that isn't inside a table in a centring wrapper."""
    parts = re.split(r"(<table>.*?</table>)", html, flags=re.S)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(<img [^>]*>)", r'<span class="img-wrap">\1</span>', parts[i])
    return "".join(parts)


def word_count(markdown):
    """Words in a note: English words count one each, each Chinese character
    counts as one. Code, maths, images and links' addresses aren't counted."""
    text = re.sub(r"^\s*(```|~~~).*?^\s*\1\s*$", " ", markdown, flags=re.S | re.M)
    text = re.sub(r"\$\$.*?\$\$|\$[^$\n]+\$", " ", text, flags=re.S)
    text = re.sub(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>|`[^`]*`", " ", text)
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    english = len(re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text))
    return english + cjk


def split_frontmatter(text):
    meta = {}
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
        if m:
            for line in m.group(1).splitlines():
                kv = re.match(r"^(\w+):\s*(.*)$", line)
                if kv:
                    meta[kv.group(1).lower()] = kv.group(2).strip().strip("'\"")
            text = text[m.end():]
    return meta, text


def page_title(stem, meta):
    if meta.get("title"):
        return meta["title"]
    m = MODULE_RE.match(stem)
    return f"{m.group(1)} {m.group(2)}" if m else stem


class Converter:
    def __init__(self, vault, notes):
        self.vault = vault
        self.notes = notes                       # lower-case stem -> slug
        self.images = {}                         # filename -> Path in vault
        for p in vault.rglob("*"):
            if p.suffix.lower() in IMAGE_EXTS and ".obsidian" not in p.parts:
                self.images.setdefault(p.name, p)
        self.used_assets = set()
        self.warnings = []

    # -- inline rewriting (outside code) ---------------------------------
    def rewrite_inline(self, line, current):
        # leave inline `code` alone
        parts = re.split(r"(`+[^`]*`+)", line)
        for i in range(0, len(parts), 2):
            parts[i] = WIKILINK_RE.sub(lambda m: self.wikilink(m, current), parts[i])
            parts[i] = re.sub(r"==(\S(?:.*?\S)?)==", r"<mark>\1</mark>", parts[i])
        return "".join(parts)

    def wikilink(self, m, current):
        embed, target, heading, alias = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        heading = heading[1:].strip() if heading else ""
        alias = alias[1:].strip() if alias else ""

        if embed and Path(target).suffix.lower() in IMAGE_EXTS:
            src = self.images.get(Path(target).name)
            if not src:
                self.warnings.append(f"{current}: image not found: {target}")
                return ""
            name = asset_name(src.name)
            self.used_assets.add((name, src))
            # Large images are all shown at the same width (80% of the page,
            # see css/page.css); smaller ones at their own size, never enlarged.
            # A width set in Obsidian is kept when it shrinks the image a lot
            # on purpose (e.g. a formula cropped from a slide).
            width = image_display_width(src)
            size = re.match(r"^(\d+)(?:x(\d+))?$", alias)
            if size and width and int(size.group(1)) < width / 2:
                width = int(size.group(1))
            attrs = "{width=%dpx}" % width if width else ""
            return f"![](assets/{name}){attrs}"

        text = alias or heading or target
        anchor = "#" + heading_id(heading) if heading else ""
        if not target:                          # [[#Heading]] in the same note
            return f"[{text}]({anchor})"
        slug = self.notes.get(target.lower())
        if not slug:
            self.warnings.append(f"{current}: link to unpublished note: {target}")
            return text
        return f"[{text}]({slug}.html{anchor})"

    # -- whole document ----------------------------------------------------
    def preprocess(self, text, current):
        text = re.sub(r"%%.*?%%", "", text, flags=re.S)
        # coloured-circle emoji used as highlight markers are left out of the page
        text = re.sub("[\U0001F534\U0001F535\U0001F7E0-\U0001F7E3\u26AA\u26AB]\uFE0F?", "", text)
        out = []
        fence = None
        seen_ids = {}
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            fm = FENCE_RE.match(line)
            if fence:
                out.append(line)
                if fm and fm.group(2)[0] == fence[0] and len(fm.group(2)) >= len(fence) \
                        and not fm.group(3):
                    fence = None
                i += 1
                continue
            if fm:
                fence = fm.group(2)
                lang = fm.group(3).lower()
                out.append(f"{fm.group(1)}{fm.group(2)}{lang}{fm.group(4)}")
                i += 1
                continue

            cm = CALLOUT_RE.match(line)
            if cm:
                kind, title = cm.group(1).lower(), cm.group(2) or cm.group(1).capitalize()
                body = []
                i += 1
                while i < len(lines) and lines[i].startswith(">"):
                    body.append(re.sub(r"^>\s?", "", lines[i]))
                    i += 1
                out.append(f"::: {{.callout .callout-{kind}}}")
                out.append(f"**{self.rewrite_inline(title, current)}**")
                out.append("")
                out.extend(self.rewrite_inline(b, current) for b in body)
                out.append(":::")
                continue

            hm = HEADING_RE.match(line)
            if hm and "{#" not in line:
                hid = heading_id(hm.group(2))
                n = seen_ids.get(hid, 0)
                seen_ids[hid] = n + 1
                if n:
                    hid = f"{hid}-{n}"
                line = f"{hm.group(1)} {self.rewrite_inline(hm.group(2), current)} {{#{hid}}}"
                out.append(line)
                i += 1
                continue

            line = re.sub(r"^(\s*(?:[-*+]|\d+[.)])[ \t]+)[\u00a0 ]+", r"\1", line)
            out.append(self.rewrite_inline(line, current))
            i += 1
        return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    vault = Path(sys.argv[1]).expanduser()
    study = vault / "Study"
    if not study.is_dir():
        sys.exit(f"Can't find the Study folder in {vault}")
    if not shutil.which("pandoc"):
        sys.exit("pandoc is not installed. Install it with:  brew install pandoc")

    sources = []
    for md in sorted(study.rglob("*.md")):
        meta, body = split_frontmatter(md.read_text(encoding="utf-8"))
        if meta.get("publish", "").lower() == "false":
            continue
        sources.append((md, meta, body))
    notes = {md.stem.lower(): slugify(md.stem) for md, _, _ in sources}

    OUT_DIR.mkdir(exist_ok=True)
    ASSET_DIR.mkdir(exist_ok=True)
    conv = Converter(vault, notes)
    # version tag for the stylesheet link, so browsers fetch it again
    # whenever css/page.css changes instead of using an old cached copy
    cssver = hashlib.md5((SITE / "css" / "page.css").read_bytes()).hexdigest()[:8]
    built = set()

    for md, meta, body in sources:
        slug = notes[md.stem.lower()]
        title = page_title(md.stem, meta)
        markdown = conv.preprocess(body, md.stem)
        words = word_count(body)
        out = OUT_DIR / f"{slug}.html"
        cmd = [
            "pandoc",
            "-f", "commonmark_x+hard_line_breaks",
            "-t", "html5",
            "--template", str(TEMPLATE),
            "--toc", "--toc-depth=3",
            "--katex=../vendor/katex/",
            "--wrap=none",
            "-M", f"pagetitle={title}",
            "-V", f"cssver={cssver}",
            "-V", f"wordcount={words:,}",
            "-o", str(out),
        ]
        result = subprocess.run(cmd, input=markdown, text=True, capture_output=True)
        if result.returncode != 0:
            print(f"FAILED  {md.relative_to(vault)}\n{result.stderr}")
            continue
        html = out.read_text(encoding="utf-8")
        html = html.replace("<table>", '<div class="table-wrap"><table>')
        html = html.replace("</table>", "</table></div>")
        html = wrap_images(html)
        # a display formula is its own line already: drop the extra line breaks
        # around it, which otherwise leave big gaps between stacked formulas
        html = re.sub(r'(<span class="math display">[^<]*</span>)\s*<br />', r"\1", html)
        html = re.sub(r'<br />(\s*<span class="math display">)', r"\1", html)
        out.write_text(html, encoding="utf-8")
        built.add(out.name)
        print(f"ok      notes/{out.name}   <-  {md.relative_to(vault)}")

    # copy images that are used, remove ones that aren't any more
    used = {}
    for name, src in conv.used_assets:
        used[name] = src
        dest = ASSET_DIR / name
        if not dest.exists() or dest.stat().st_size != src.stat().st_size \
                or dest.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dest)
    for f in ASSET_DIR.iterdir():
        if f.name not in used and not f.name.startswith("."):
            f.unlink()
    for f in OUT_DIR.glob("*.html"):
        if f.name not in built:
            f.unlink()
            print(f"removed notes/{f.name}")

    for w in sorted(set(conv.warnings)):
        print("warning:", w)
    print(f"\n{len(built)} notes, {len(used)} images.")


if __name__ == "__main__":
    main()
