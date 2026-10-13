"""Portable, offline-rendered watch notes from jot's saved evaluation Markdown."""

from __future__ import annotations

import base64
from functools import lru_cache
from html import escape, unescape
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_TIME_POINT = r"(?:\d{1,3}:[0-5]\d:[0-5]\d|\d{1,3}:[0-5]\d)"
_TIMESTAMPS = re.compile(
    rf"(?<![\w:])({_TIME_POINT})(?:\s*[-–—]\s*({_TIME_POINT}))?(?![\w:])"
)
_PLAIN_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_REQUIRED_METADATA = ("Channel", "Source", "Duration", "Transcribed", "Method")
# Reading order of the rendered report; the saved Markdown keeps the evaluator's order.
_SECTION_ORDER = (
    "verdict", "what you can learn", "worth watching", "signal versus fluff", "novelty", "limits",
)
_LEVEL_LABEL = re.compile(r"(.*?\S)\s*:\s*(high|moderate|medium|low)", re.IGNORECASE)
_TAKEAWAY_SEPARATOR = re.compile(r"\s*(?::|[-–—])\s*")


def _youtube_id(url: str) -> str | None:
    """Accept actual YouTube hosts and exact video paths, never lookalike hosts."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            return None
        host = parsed.hostname
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = parsed.path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                values = parse_qs(parsed.query).get("v", [])
                if len(values) != 1:
                    return None
                candidate = values[0]
            else:
                parts = parsed.path.split("/")
                if len(parts) != 3 or parts[1] not in {"embed", "shorts", "live"}:
                    return None
                candidate = parts[2]
        else:
            return None
        return candidate if _VIDEO_ID.fullmatch(candidate) else None
    except ValueError:
        return None


def _seconds(timestamp: str) -> int:
    seconds = 0
    for part in timestamp.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def _watch_url(video_id: str, seconds: int | None = None) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url if seconds is None else f"{url}&t={seconds}"


def _safe_link(url: str) -> bool:
    # markdown-it normalizes entities; unescape also protects direct use here.
    decoded = unescape(url).strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return False
    try:
        return urlsplit(decoded).scheme.lower() in {"", "http", "https", "mailto"}
    except ValueError:
        return False


def _text_token(text: str) -> Token:
    token = Token("text", "", 0)
    token.content = text
    return token


def _seek_link(token: Token, video_id: str, start: int) -> None:
    token.attrSet("href", _watch_url(video_id, start))
    token.attrSet("class", "timestamp")
    token.attrSet("data-start", str(start))
    token.attrSet("aria-label", f"Request video at {start // 60}:{start % 60:02d}")
    token.attrSet("rel", "noopener noreferrer")


def _timestamp_tokens(text: str, video_id: str) -> list[Token]:
    tokens: list[Token] = []
    end = 0
    for match in _TIMESTAMPS.finditer(text):
        if match.start() > end:
            tokens.append(_text_token(text[end:match.start()]))
        opening = Token("link_open", "a", 1)
        _seek_link(opening, video_id, _seconds(match.group(1)))
        tokens.extend((opening, _text_token(match.group()), Token("link_close", "a", -1)))
        end = match.end()
    if end < len(text):
        tokens.append(_text_token(text[end:]))
    return tokens


def _plain_tokens(text: str, video_id: str) -> list[Token]:
    """Keep literal URL text intact instead of matching times within its query."""
    tokens: list[Token] = []
    end = 0
    for match in _PLAIN_URL.finditer(text):
        tokens.extend(_timestamp_tokens(text[end:match.start()], video_id))
        url = match.group().rstrip(".,;!?)")
        opening = Token("link_open", "a", 1)
        opening.attrSet("href", url)
        opening.attrSet("rel", "noopener noreferrer")
        tokens.extend((opening, _text_token(url), Token("link_close", "a", -1)))
        end = match.start() + len(url)
    tokens.extend(_timestamp_tokens(text[end:], video_id))
    return tokens


def _inline_text(tokens: list[Token]) -> str:
    return "".join(
        token.content if token.type not in {"softbreak", "hardbreak"} else " "
        for token in tokens
        if token.type in {"text", "code_inline", "image", "softbreak", "hardbreak"}
    )


def _decorate_inline(tokens: list[Token], video_id: str) -> list[Token]:
    decorated: list[Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "link_open":
            closing = index + 1
            while closing < len(tokens) and tokens[closing].type != "link_close":
                closing += 1
            linked_tokens = tokens[index + 1:closing]
            label = _inline_text(linked_tokens)
            href = token.attrGet("href") or ""
            match = _TIMESTAMPS.search(label)
            if _youtube_id(href) == video_id and match:
                _seek_link(token, video_id, _seconds(match.group(1)))
            else:
                token.attrSet("rel", "noopener noreferrer")
            # Existing links, particularly to other videos, are atomic: never
            # insert nested anchors or redirect their timestamp labels.
            for linked_token in tokens[index:closing + 1]:
                if linked_token.type == "image":
                    src = linked_token.attrGet("src") or ""
                    decorated.append(_text_token(f"[Image: {linked_token.content}] ({src})"))
                else:
                    decorated.append(linked_token)
            index = closing + 1
            continue
        if token.type == "text":
            decorated.extend(_plain_tokens(token.content, video_id))
        elif token.type == "image":
            # Reports never fetch remote images; retain their label and address.
            src = token.attrGet("src") or ""
            decorated.append(_text_token(f"[Image: {token.content}] ({src})"))
        else:
            decorated.append(token)
        index += 1
    return decorated


def _parse_document(document: str) -> tuple[str, dict[str, str], str, str, str]:
    """Parse the saved-Markdown envelope shared with jot._build_transcript_markdown.

    The first line is '# title', followed by a two-column metadata table with
    Channel, Source, Duration, Transcribed and Method; Uploaded is optional.
    Source may be a plain URL, an angle autolink or a Markdown link. Duration is
    display text (including Unknown), not a required numeric measurement.
    Exactly one '## Watch-value evaluation' precedes the final '## Transcript'.
    A terminal '---' separates the nonempty evaluation from nonempty source
    speech. Evaluation may contain its own Transcript heading; the final one
    owns the source. Ordinary transcripts omit the evaluation and are not input
    to this renderer. Return normalized metadata, preserving report/source text.
    """
    if not isinstance(document, str) or not document.strip():
        raise ValueError("Invalid saved evaluation: the document is empty.")
    document = document.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = document.splitlines()
    title_match = re.fullmatch(r"#\s+(.+?)\s*", lines[0])
    if not title_match or not title_match.group(1).strip():
        raise ValueError("Invalid saved evaluation: expected a '# title' first line.")
    evaluation_matches = list(re.finditer(r"^## Watch-value evaluation\s*$", document, re.MULTILINE))
    transcript_matches = list(re.finditer(r"^## Transcript\s*$", document, re.MULTILINE))
    if len(evaluation_matches) != 1 or not transcript_matches:
        raise ValueError("Invalid saved evaluation: expected Watch-value evaluation and Transcript sections.")
    evaluation_heading = evaluation_matches[0]
    # The final Transcript heading belongs to the source, not a similarly named
    # section the evaluator might have produced in its own Markdown.
    transcript_heading = transcript_matches[-1]
    if transcript_heading.start() <= evaluation_heading.end():
        raise ValueError("Invalid saved evaluation: Transcript must follow the evaluation.")
    metadata: dict[str, str] = {}
    for line in document[:evaluation_heading.start()].splitlines()[1:]:
        row = re.fullmatch(r"\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*", line)
        if not row:
            continue
        key = row.group(1).strip()
        if key.startswith("**") and key.endswith("**"):
            key = key[2:-2].strip()
        if not key or re.fullmatch(r":?-+:?", key):
            continue
        if key in metadata:
            raise ValueError(f"Invalid saved evaluation: duplicate {key} metadata.")
        metadata[key] = row.group(2).strip().replace(r"\|", "|")
    for key in _REQUIRED_METADATA:
        if not metadata.get(key):
            raise ValueError(f"Invalid saved evaluation: missing {key} metadata.")
    source = metadata["Source"]
    if source.startswith("<") and source.endswith(">"):
        source = source[1:-1]
    else:
        linked_source = re.fullmatch(r"\[[^\]]*\]\(([^\s)]+)\)", source)
        if linked_source:
            source = linked_source.group(1)
    video_id = _youtube_id(source)
    if not video_id:
        raise ValueError("Invalid saved evaluation: Source must be a YouTube URL with an 11-character video ID.")
    evaluation = document[evaluation_heading.end():transcript_heading.start()].strip("\n")
    separator = re.search(r"(?:^|\n)---[ \t]*\n*\Z", evaluation)
    if not separator:
        raise ValueError("Invalid saved evaluation: expected '---' before Transcript.")
    evaluation = evaluation[:separator.start()].strip("\n")
    transcript = document[transcript_heading.end():].strip("\n")
    if not evaluation.strip() or not transcript.strip():
        raise ValueError("Invalid saved evaluation: evaluation and transcript must both contain text.")
    metadata["Source"] = source
    return title_match.group(1), metadata, evaluation, transcript, video_id


def _section_label(section: list[Token]) -> str:
    return _inline_text(section[1].children or []).strip().lower()


def _section_rank(section: list[Token]) -> float:
    label = _section_label(section)
    # Unrecognised sections keep their saved order, just ahead of Limits.
    return _SECTION_ORDER.index(label) if label in _SECTION_ORDER else len(_SECTION_ORDER) - 1.5


def _split_sections(tokens: list[Token]) -> tuple[list[Token], list[list[Token]]]:
    """Split at the report-section heading level; earlier content is preamble.

    Some evaluators wrap their sections under their own '# Title' heading, so the
    section level is the Verdict heading's, falling back to the outermost heading.
    """
    headings = [
        (index, token) for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.level == 0
    ]
    if not headings:
        return tokens, []
    top = next(
        (token.tag for index, token in headings
         if _inline_text(tokens[index + 1].children or []).strip().lower() == "verdict"),
        min((token.tag for _, token in headings), key=lambda tag: int(tag[1:])),
    )
    preamble: list[Token] = []
    sections: list[list[Token]] = []
    for token in tokens:
        if token.type == "heading_open" and token.level == 0 and token.tag == top:
            sections.append([])
        (sections[-1] if sections else preamble).append(token)
    return preamble, sections


def _level_badges(children: list[Token]) -> list[Token]:
    """Turn a bold 'Category: HIGH' label into the category plus a level badge."""
    badged: list[Token] = []
    index = 0
    while index < len(children):
        token = children[index]
        badged.append(token)
        if (
            token.type == "strong_open"
            and index + 2 < len(children)
            and children[index + 1].type == "text"
            and children[index + 2].type == "strong_close"
        ):
            match = _LEVEL_LABEL.fullmatch(children[index + 1].content.strip())
            if match:
                children[index + 1].content = f"{match.group(1)}:"
                level = match.group(2)
                kind = "moderate" if level.lower() == "medium" else level.lower()
                badge = Token("span_open", "span", 1)
                badge.attrSet("class", f"level level-{kind}")
                badged.extend((
                    children[index + 1], children[index + 2], _text_token(" "),
                    badge, _text_token(level), Token("span_close", "span", -1),
                ))
                index += 3
                continue
        index += 1
    return badged


def _split_takeaway(children: list[Token]) -> None:
    """Set a leading bold takeaway on its own line, above its explanation."""
    if not children or children[0].type != "strong_open":
        return
    close = next((i for i, token in enumerate(children) if token.type == "strong_close"), None)
    if close is None or close + 1 >= len(children) or children[close + 1].type != "text":
        return
    rest = children[close + 1]
    separator = _TAKEAWAY_SEPARATOR.match(rest.content)
    label_end = children[close - 1]
    if separator:
        rest.content = rest.content[separator.end():]
    elif label_end.type == "text" and label_end.content.rstrip().endswith((":", "—", "–")):
        rest.content = rest.content.lstrip()
    else:
        return
    children[0].attrSet("class", "takeaway")


def _render_section(markdown: MarkdownIt, section: list[Token]) -> str:
    def render(tokens: list[Token]) -> str:
        return markdown.renderer.render(tokens, markdown.options, {})

    # section[:3] is heading_open, its inline content and heading_close.
    if _section_label(section) == "limits" and len(section) > 3:
        body = (
            '<details class="accordion"><summary>Read the limits</summary>'
            f'<div class="accordion-body">{render(section[3:])}</div></details>'
        )
        return f'<section class="report-section">{render(section[:3])}{body}</section>'
    return f'<section class="report-section">{render(section)}</section>'



@lru_cache(maxsize=1)
def _embedded_assets() -> tuple[str, str]:
    assets = Path(__file__).resolve().parent / "jot_assets"
    faces = []
    for suffix, weight, style in (
        ("Regular", 400, "normal"), ("Italic", 400, "italic"),
        ("Bold", 700, "normal"), ("BoldItalic", 700, "italic"),
    ):
        encoded = base64.b64encode((assets / f"Libron-{suffix}.woff2").read_bytes()).decode("ascii")
        faces.append(
            "@font-face{font-family:Libron;"
            f"font-style:{style};font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        )
    licenses = (assets / "Libron-LICENSE.txt").read_text(encoding="utf-8")
    licenses += "\n\nFlexoki palette — https://stephango.com/flexoki\n\n"
    licenses += (assets / "Flexoki-LICENSE.txt").read_text(encoding="utf-8")
    return "\n".join(faces), licenses


_CSS = """
:root{--bg:light-dark(#FFFCF0,#100F0F);--soft:light-dark(#F2F0E5,#1C1B1A);--ink:light-dark(#100F0F,#CECDC3);--body:light-dark(#282726,#CECDC3);--muted:light-dark(#6F6E69,#878580);--line:light-dark(#E6E4D9,#282726);--edge:light-dark(#DAD8CE,#343331);--focus:light-dark(#205EA6,#4385BE);--radius:2px;color-scheme:light dark}
:root[data-theme=light]{color-scheme:light}:root[data-theme=dark]{color-scheme:dark}
*{box-sizing:border-box}html{scroll-padding-top:80px}body{margin:0;background:var(--soft);color:var(--ink);font-family:Libron;font-size:19px;line-height:1.6;-webkit-font-smoothing:antialiased}button,input,select,textarea,code,pre,kbd,samp{font-family:Libron}a{color:inherit;text-underline-offset:4px;text-decoration-thickness:1px}a:hover{color:var(--focus)}a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--focus);outline-offset:4px;border-radius:2px}.skip-link{position:absolute;left:20px;top:-80px;z-index:10;background:var(--bg);padding:10px 16px}.skip-link:focus{top:8px}
.topbar{background:var(--bg);border-bottom:1px solid var(--line);padding:15px max(24px,calc((100vw - 1360px)/2));display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:15px}.brand{font-size:22px;font-weight:700;letter-spacing:-.04em}.header-actions{display:flex;align-items:center;gap:16px}.header-actions>a{min-height:44px;display:flex;align-items:center;font-size:15px}.theme-toggle{min-height:32px;min-width:60px;padding:2px 8px;font-size:13px;line-height:1.4;background:var(--bg);color:var(--ink);border:1px solid var(--ink);border-radius:var(--radius);cursor:pointer}.theme-toggle:hover{background:var(--soft)}.theme-toggle[hidden]{display:none}.page{background:var(--bg);box-shadow:0 0 0 1px var(--edge);max-width:1360px;margin:0 auto;padding:56px 32px 80px}.eyebrow{margin:0 0 16px;font-size:13px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted)}h1{font-size:clamp(38px,4.4vw,66px);line-height:1.08;font-weight:400;letter-spacing:-.035em;margin:0;max-width:1080px;text-wrap:balance}.hero{padding-bottom:42px;border-bottom:1px solid var(--line);margin-bottom:40px}.hero-bottom{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:24px}.hero-bottom p{font-size:15px;color:var(--muted);margin:0}.verdict{display:inline-flex;align-items:center;min-height:30px;border:1px solid var(--ink);background:var(--bg);color:var(--ink);border-radius:var(--radius);padding:2px 12px;font-size:13px;font-weight:700;letter-spacing:.07em}
.layout{display:grid;grid-template-columns:minmax(280px,420px) minmax(0,1fr);gap:64px;align-items:start}.video-pane{position:sticky;top:24px;scroll-margin-top:24px}.video-frame{border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;background:var(--soft);width:100%;aspect-ratio:16/9;min-height:202px}.video-frame iframe{width:100%;height:100%;min-height:200px;border:0;display:block}.video-title{font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin:0 0 12px;color:var(--muted)}.player-status{font-size:14px;line-height:1.45;color:var(--body);margin:14px 0 10px}.playback-note{font-size:13px;line-height:1.5;color:var(--muted);margin:0 0 14px}.direct-link{font-size:15px;display:inline-flex;align-items:center;min-height:44px}.metadata{border-top:1px solid var(--line);padding-top:20px;margin:24px 0 16px;font-size:14px}.metadata>div{display:grid;grid-template-columns:85px minmax(0,1fr);gap:12px;margin:7px 0}.metadata dt{color:var(--muted)}.metadata dd{margin:0;overflow-wrap:anywhere}.section-nav{border-top:1px solid var(--line);padding-top:12px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}.section-nav a{font-size:13px;line-height:1.3;min-width:0;min-height:28px;padding:2px 0;display:inline-flex;align-items:center;color:var(--body)}.section-nav a:hover{color:var(--focus)}
.report{min-width:0}.report>h2{font-weight:400;font-size:30px;line-height:1.2;margin:0 0 30px;letter-spacing:-.02em}.evaluation{color:var(--body)}.evaluation h3,.evaluation h4,.evaluation h5,.evaluation h6{color:var(--ink);font-weight:700;line-height:1.25;margin:42px 0 16px;scroll-margin-top:28px;text-wrap:balance}.evaluation h3{font-size:28px}.evaluation h4{font-size:24px}.evaluation h5,.evaluation h6{font-size:21px}.evaluation>:first-child{margin-top:0}.evaluation p{margin:0 0 20px}.evaluation ul,.evaluation ol{padding-left:24px;margin:16px 0 28px}.evaluation li{padding-left:6px;margin:12px 0}.evaluation li::marker{color:var(--muted)}.evaluation strong{font-weight:700;color:var(--ink)}.evaluation blockquote{border-left:2px solid var(--line);padding-left:20px;margin-left:0;color:var(--muted)}.evaluation hr{border:0;border-top:1px solid var(--line);margin:32px 0}.evaluation code{font-size:.9em;color:var(--ink);background:var(--soft);border:1px solid var(--line);padding:1px 5px;border-radius:var(--radius)}.evaluation pre{overflow:auto;padding:18px;background:var(--soft);border:1px solid var(--line);border-radius:var(--radius)}.evaluation pre code{border:0;background:none;padding:0}.evaluation table{display:block;overflow:auto;border-collapse:collapse;width:100%;font-size:16px;margin:24px 0}.evaluation th,.evaluation td{border:1px solid var(--line);padding:10px 14px;text-align:left}.evaluation th{background:var(--soft)}.timestamp{white-space:nowrap;font-variant-numeric:tabular-nums;display:inline-block;min-height:24px;padding:0 3px;color:var(--focus);background:transparent;border-radius:0;text-decoration:underline;text-decoration-color:var(--focus);text-decoration-thickness:1px;text-underline-offset:4px;font-size:.92em}.timestamp:hover{background:var(--soft);color:var(--ink)}.timestamp[aria-current=true]{outline:1px solid var(--ink);background:var(--ink);color:var(--bg)}
.transcript-section{margin-top:44px;border-top:1px solid var(--line);padding-top:24px;scroll-margin-top:28px}.transcript-section h2{font-size:28px;font-weight:400;line-height:1.3;margin:0 0 12px}.transcript-section>p{font-size:15px;color:var(--muted);margin:0 0 16px}.transcript-section details{border:1px solid var(--line);border-radius:var(--radius)}summary{cursor:pointer;min-height:48px;padding:12px 18px;font-size:16px;background:var(--soft)}.transcript-text{margin:0;padding:24px 20px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:17px;line-height:1.8;color:var(--body);max-height:720px;overflow:auto;tab-size:4}.transcript-text .timestamp{min-height:28px}.footer{border-top:1px solid var(--line);margin-top:56px;padding-top:20px;font-size:14px;color:var(--muted);display:flex;gap:24px;justify-content:space-between;align-items:start}.footer p{margin:0}.licenses{max-width:680px}.licenses summary{background:none;color:var(--ink);padding:0;font-size:14px;min-height:44px}.licenses pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.5;padding:16px;background:var(--soft);border:1px solid var(--line)}
.report-section+.report-section{border-top:1px solid var(--line);margin-top:40px;padding-top:36px}.evaluation .report-section>:first-child{margin-top:0}.accordion{border:1px solid var(--line);border-radius:var(--radius)}.accordion-body{padding:22px 20px 2px}.evaluation .takeaway{display:block;margin-bottom:6px}.level{display:inline-block;color:var(--ink);background:var(--bg);border:1px solid var(--ink);border-radius:var(--radius);padding:0 9px;font-size:12px;font-weight:700;line-height:22px;letter-spacing:.08em;text-transform:uppercase;vertical-align:.12em;white-space:nowrap}.level-high{background:var(--ink);color:var(--bg)}.level-moderate{border-style:solid;box-shadow:inset 0 0 0 1px var(--ink)}.level-low{border-style:dashed}
.signal-heading{display:block;margin-bottom:6px}
@media(min-width:1100px){.evaluation .timestamp{min-height:26px}}@media(max-width:1000px){.layout{gap:36px;grid-template-columns:minmax(250px,340px) minmax(0,1fr)}.page{padding:40px 24px 60px}}@media(max-width:760px){.topbar{padding:12px 20px}.page{padding:32px 20px 48px}.hero{padding-bottom:28px;margin-bottom:28px}.layout{display:flex;flex-direction:column;gap:36px}.video-pane{position:static;width:100%;scroll-margin-top:16px}.video-frame{max-width:640px}.metadata{margin-top:16px}.section-nav{padding-top:8px}.report{width:100%}.report>h2{font-size:28px}.evaluation h3{font-size:25px}.evaluation h4{font-size:22px}.timestamp{min-height:32px;line-height:32px}.footer{flex-direction:column;gap:12px}.transcript-text{padding:18px 14px}}
@media(max-width:760px),(pointer:coarse){a{min-width:44px;min-height:44px;display:inline-block}.evaluation .timestamp,.transcript-text .timestamp,.timestamp{min-height:44px;line-height:44px}}
@media(max-width:760px),(pointer:coarse){.theme-toggle{min-height:44px}.section-nav a{min-height:44px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""

# Run before the stylesheet to avoid flashing the wrong palette. Storage is
# optional: restricted/local-file contexts can still use the in-memory toggle.
_THEME_JS = """
(() => {
  const root = document.documentElement;
  const storageKey = 'jot.theme';
  let theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch { /* Browser storage is unavailable; use the system preference. */ }
  root.dataset.theme = theme;
  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('theme-toggle');
    toggle.hidden = false;
    toggle.textContent = theme === 'dark' ? 'Light' : 'Dark';
    toggle.setAttribute('aria-label', 'Switch to ' + toggle.textContent.toLowerCase() + ' theme');
    toggle.addEventListener('click', () => {
      theme = theme === 'dark' ? 'light' : 'dark';
      root.dataset.theme = theme;
      toggle.textContent = theme === 'dark' ? 'Light' : 'Dark';
      toggle.setAttribute('aria-label', 'Switch to ' + toggle.textContent.toLowerCase() + ' theme');
      try { localStorage.setItem(storageKey, theme); }
      catch { /* The selection remains active even without persistent storage. */ }
    });
  }, {once: true});
})();
"""

_JS = """
(() => {
  const frame = document.getElementById('video-player');
  const status = document.getElementById('player-status');
  let selected = null;
  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('a[data-start]') : null;
    if (!target || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const seconds = target.dataset.start;
    if (!frame || !/^\\d+$/.test(seconds)) return;
    event.preventDefault();
    const url = new URL('https://www.youtube.com/embed/' + frame.dataset.videoId);
    url.searchParams.set('start', seconds);
    url.searchParams.set('autoplay', '1');
    url.searchParams.set('rel', '0');
    frame.src = url.href;
    if (selected) selected.removeAttribute('aria-current');
    target.setAttribute('aria-current', 'true');
    selected = target;
    const time = Number(seconds);
    const hours = Math.floor(time / 3600);
    const minutes = Math.floor((time % 3600) / 60);
    const label = (hours ? hours + ':' + String(minutes).padStart(2, '0') : String(minutes)) + ':' + String(time % 60).padStart(2, '0');
    status.textContent = 'Seek requested: ' + label + '. YouTube may require playback permission; this page cannot confirm playback.';
    frame.closest('.video-pane').scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  });
  if (location.protocol === 'file:') {
    document.getElementById('playback-note').textContent = 'Opened as a local file. YouTube may reject file playback: serve this page over HTTP or use the direct YouTube link. The report, transcript and fonts work offline.';
  }
})();
"""


def build_evaluation_html(document: str) -> str:
    """Return one shareable HTML page containing the complete saved evaluation.

    Requires jot_assets beside this module only while rendering; the returned page
    embeds Libron v0.31 and its license. Rendering never accesses the network.
    Malformed saved-document structure or a non-YouTube Source raises ValueError.
    YouTube playback is the page's sole external resource and may require HTTP.
    """
    title, metadata, evaluation, transcript, video_id = _parse_document(document)
    markdown = MarkdownIt("commonmark", {"html": False, "typographer": False}).enable(["table", "strikethrough"])
    markdown.validateLink = _safe_link
    tokens = markdown.parse(evaluation)
    headings = [token for token in tokens if token.type == "heading_open"]
    offset = 3 - min((int(token.tag[1:]) for token in headings), default=3)
    verdict = "Evaluation"
    in_verdict = False
    heading_count = 0
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            heading_content = tokens[index + 1]
            label = _inline_text(heading_content.children or [])
            heading_count += 1
            token.attrSet("id", f"section-{heading_count}")
            in_verdict = label.strip().lower() == "verdict"
        elif token.type == "inline" and in_verdict:
            match = re.search(r"\b(WATCH|SKIM|SKIP)\b", _inline_text(token.children or []), re.IGNORECASE)
            if match:
                verdict = match.group().upper()
                in_verdict = False
        if token.type in {"heading_open", "heading_close"}:
            token.tag = f"h{min(6, int(token.tag[1:]) + offset)}"
        if token.type == "inline" and token.children is not None:
            token.children = _level_badges(_decorate_inline(token.children, video_id))
    preamble, sections = _split_sections(tokens)
    sections.sort(key=_section_rank)
    for section in sections:
        if _section_label(section) == "what you can learn":
            for token in section:
                if token.type == "inline" and token.children:
                    _split_takeaway(token.children)
        elif _section_label(section) == "signal versus fluff":
            for token in section:
                children = token.children
                if token.type != "inline" or not children or children[0].type != "strong_open":
                    continue
                end = next((i for i, child in enumerate(children) if child.type == "span_close"), None)
                if end is None:
                    continue
                # Keep the category and rating together; start its explanation below.
                heading = Token("span_open", "span", 1)
                heading.attrSet("class", "signal-heading")
                rest = children[end + 1:]
                if rest and rest[0].type == "text":
                    separator = _TAKEAWAY_SEPARATOR.match(rest[0].content)
                    if separator:
                        rest[0].content = rest[0].content[separator.end():]
                token.children = [
                    heading, *children[:end + 1], Token("span_close", "span", -1), *rest,
                ]
    ordered = preamble + [token for section in sections for token in section]
    shortcuts = [
        (token.attrGet("id"), _inline_text(ordered[index + 1].children or []))
        for index, token in enumerate(ordered) if token.type == "heading_open"
    ]
    report_html = markdown.renderer.render(preamble, markdown.options, {}) + "".join(
        _render_section(markdown, section) for section in sections
    )
    transcript_html = markdown.renderer.renderInline(_plain_tokens(transcript, video_id), markdown.options, {})
    title_tokens = markdown.parseInline(title)
    for token in title_tokens:
        if token.children is not None:
            token.children = _decorate_inline(token.children, video_id)
    title_html = markdown.renderer.render(title_tokens, markdown.options, {})
    plain_title = _inline_text(title_tokens[0].children or [])
    rows = []
    for key, value in metadata.items():
        if key == "Source":
            rendered = f'<a href="{escape(value, quote=True)}" rel="noopener noreferrer">{escape(value)}</a>'
        else:
            rendered = escape(value)
        rows.append(f"<div><dt>{escape(key)}</dt><dd>{rendered}</dd></div>")
    navigation = '<a href="#evaluation">Evaluation</a>'
    navigation += "".join(
        f'<a href="#{anchor}">{escape(label)}</a>' for anchor, label in shortcuts if label.strip()
    )
    navigation += '<a href="#transcript">Source transcript</a>'
    fonts, license_text = _embedded_assets()
    watch_url = escape(_watch_url(video_id), quote=True)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{escape(plain_title)} — jot</title>
<script>{_THEME_JS}</script>
<style>{fonts}\n{_CSS}</style>
</head>
<body>
<a class="skip-link" href="#evaluation">Skip to evaluation</a>
<header class="topbar"><div class="brand">jot</div><div class="header-actions"><a href="#transcript">Source transcript ↓</a><button id="theme-toggle" class="theme-toggle" type="button" hidden>Dark</button></div></header>
<main class="page">
<header class="hero"><p class="eyebrow">A considered watch</p><h1>{title_html}</h1>
<div class="hero-bottom"><span class="verdict">{escape(verdict)}</span><p>Evaluation and source transcript · {escape(metadata['Duration'])}</p></div></header>
<div class="layout">
<aside class="video-pane" aria-label="Video and report information">
<p class="video-title">Watch alongside the notes</p>
<div class="video-frame"><iframe id="video-player" data-video-id="{video_id}" src="https://www.youtube.com/embed/{video_id}?rel=0" title="YouTube video: {escape(plain_title, quote=True)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>
<p id="player-status" class="player-status" role="status" aria-live="polite" aria-atomic="true">Choose a timestamp to request that moment in the video.</p>
<p id="playback-note" class="playback-note">Video playback needs an internet connection. YouTube may require this page to be served over HTTP rather than opened as a file. Use the direct link if embedding is unavailable.</p>
<a class="direct-link" href="{watch_url}" rel="noopener noreferrer">Open directly on YouTube ↗</a>
<noscript><p class="playback-note">JavaScript is off. Timestamp links open the requested moment on YouTube.</p></noscript>
<dl class="metadata">{''.join(rows)}</dl>
<nav class="section-nav" aria-label="Report sections">{navigation}</nav>
</aside>
<article class="report" aria-labelledby="evaluation">
<h2 id="evaluation">Watch-value evaluation</h2>
<div class="evaluation">{report_html}</div>
<section class="transcript-section" id="transcript" aria-labelledby="transcript-heading">
<h2 id="transcript-heading">Source transcript</h2>
<p>Original captions, preserved as supplied. Timestamp ranges are the evaluator’s suggested sections, not independently measured claims.</p>
<details class="accordion"><summary>Read the source transcript</summary><pre class="transcript-text">{transcript_html}</pre></details>
</section>
</article>
</div>
<footer class="footer"><p>jot · A portable reading copy. Evaluation wording is preserved from the saved report.</p>
<details class="licenses"><summary>Libron v0.31 &amp; Flexoki · licenses</summary><pre>{escape(license_text)}</pre></details></footer>
</main>
<script>{_JS}</script>
</body>
</html>
'''
