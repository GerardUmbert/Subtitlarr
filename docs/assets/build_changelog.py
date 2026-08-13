"""One-off/maintenance script: converts CHANGELOG.md into docs/changelog.html.
Not run automatically on any deploy — re-run manually after editing
CHANGELOG.md and commit the regenerated HTML alongside it. Written as a
small custom parser (no markdown dependency) tailored to this specific
file's consistent structure: '## [x.y.z]' version headers, '### Section'
subsections, '-' bullets with wrapped continuation lines."""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
OUTPUT = ROOT / "docs" / "changelog.html"

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Changelog — Subtitlarr</title>
<link rel="icon" href="assets/icon.svg">
<link rel="stylesheet" href="assets/site.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="assets/tailwind-config.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js"></script>
</head>
<body>

<header class="site-header">
  <a class="brand" href="index.html">
    <span class="brand-mark">S</span>
    Subtitlarr
  </a>
  <button type="button" class="nav-toggle" id="nav-toggle" aria-expanded="false" aria-controls="site-nav" aria-label="Toggle navigation">
    <span></span><span></span><span></span>
  </button>
  <nav class="site-nav" id="site-nav">
    <a href="index.html">Home</a>
    <a href="install.html">Install</a>
    <a href="api-keys.html">Engine Setup</a>
    <a href="features.html">Features</a>
    <a href="changelog.html" class="active">Changelog</a>
    <a href="docs.html">Docs</a>
  </nav>
  <div class="site-actions">
    <a class="btn" href="https://github.com/GerardUmbert/Subtitlarr">GitHub</a>
  </div>
</header>

<main>
  <h1 class="reveal">Changelog</h1>
  <p class="lede reveal">Generated from <a href="https://github.com/GerardUmbert/Subtitlarr/blob/master/CHANGELOG.md">CHANGELOG.md</a> — that file is the source of truth.</p>
"""

PAGE_TAIL = """</main>

<footer class="site-footer">
  Subtitlarr is open source — <a href="https://github.com/GerardUmbert/Subtitlarr">source on GitHub</a>
</footer>

<script src="assets/site-motion.js"></script>
</body>
</html>
"""


def inline_md(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def parse(md_text: str) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    in_entry = False
    in_section = False
    in_list = False
    bullet_buf: list[str] = []

    def flush_bullet():
        nonlocal bullet_buf
        if bullet_buf:
            joined = " ".join(bullet_buf).strip()
            out.append(f"<li>{inline_md(joined)}</li>")
            bullet_buf = []

    def close_list():
        flush_bullet()
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip()
        version_match = re.match(r"^## \[(.+)\]$", line)
        section_match = re.match(r"^### (.+)$", line)
        bullet_match = re.match(r"^- (.*)$", line)

        if version_match:
            close_list()
            if in_section:
                in_section = False
            if in_entry:
                out.append("</div>")
            out.append('<div class="changelog-entry reveal-scroll">')
            out.append(f'<div class="changelog-version">{html.escape(version_match.group(1))}</div>')
            in_entry = True
            continue

        if section_match:
            close_list()
            out.append(f'<div class="changelog-section-title">{html.escape(section_match.group(1))}</div>')
            in_section = True
            continue

        if bullet_match:
            flush_bullet()
            if not in_list:
                out.append("<ul>")
                in_list = True
            bullet_buf.append(bullet_match.group(1).strip())
            continue

        if line.strip() == "":
            continue

        if in_list and line.startswith("  "):
            # Wrapped continuation line of the current bullet.
            bullet_buf.append(line.strip())
            continue

        close_list()

    close_list()
    if in_entry:
        out.append("</div>")
    return "\n".join(out)


def main():
    md_text = CHANGELOG.read_text(encoding="utf-8")
    # Skip the top '# Changelog' title + intro paragraph — the HTML page
    # already has its own <h1>/lede covering that.
    body_start = md_text.index("## [")
    body_html = parse(md_text[body_start:])
    OUTPUT.write_text(PAGE_HEAD + body_html + PAGE_TAIL, encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
