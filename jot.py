#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.27.0",
#     "python-dotenv>=1.0.0",
#     "pymupdf>=1.24.0",
#     "thefuzz>=0.22.0",
#     "youtube-transcript-api>=1.0.0",
#     "yt-dlp>=2024.0.0",
#     "assemblyai>=0.35.0",
#     "anthropic>=0.42.0",
# ]
# ///
"""
jot - source content from anywhere.

Routes automatically:
    YouTube URLs  ->  transcription (timestamped markdown)
    Everything else  ->  PDF book search & download

Usage:
    jot "Book Title"                          Search for a PDF book
    jot "Book Title" --author "Name"          Search with author filter
    jot https://youtube.com/watch?v=XYZ       Transcribe a YouTube video
    jot https://youtu.be/XYZ --diarize        Transcribe with speaker labels
    jot --download 3                          Download PDF result #3
    jot --open 3                              Download + open result #3
    jot --verify 3                            Check PDF content of result #3
    jot --block 4                             Block domain of result #4
    jot --allow 2                             Allow domain of result #2
    jot --results                             Re-display last search results
    jot --domains                             Show allow/block domain lists

Requires .env file with:
    BRAVE_API_KEY          (PDF search — https://brave.com/search/api/)
    ANTHROPIC_API_KEY      (transcript paragraph breaks — https://console.anthropic.com/)
    ASSEMBLYAI_API_KEY     (transcription fallback — https://www.assemblyai.com/)
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

# ---------------------------------------------------------------------------
# Dependencies — fail fast with install instructions
# ---------------------------------------------------------------------------

_MISSING: list[str] = []

try:
    import httpx
except ImportError:
    _MISSING.append("httpx")

try:
    from dotenv import load_dotenv
except ImportError:
    _MISSING.append("python-dotenv")

try:
    import fitz  # pymupdf
except ImportError:
    _MISSING.append("pymupdf")

try:
    from thefuzz import fuzz
except ImportError:
    _MISSING.append("thefuzz")

if _MISSING:
    print(f"Missing dependencies: {', '.join(_MISSING)}")
    print(f"Install with: pip install {' '.join(_MISSING)}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """User config/data directory for jot's state files.

    Honours JOT_CONFIG_DIR, then XDG_CONFIG_HOME, else ~/.config/jot.
    Kept out of the package install location so state survives upgrades
    and works when jot is installed as a uv tool (read-only site-packages).
    """
    override = os.environ.get("JOT_CONFIG_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg).expanduser() / "jot") if xdg else (Path.home() / ".config" / "jot")
    base.mkdir(parents=True, exist_ok=True)
    return base


APP_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = _config_dir()
CACHE_FILE = CONFIG_DIR / ".last_results.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
DOMAINS_FILE = CONFIG_DIR / "domains.md"

# .env is searched in several locations (first match wins): an explicit
# JOT_ENV_FILE, the config dir, the current working directory, then
# alongside the source (handy for `uv run jot.py` during development).
ENV_FILE = CONFIG_DIR / ".env"
_ENV_CANDIDATES = [
    os.environ.get("JOT_ENV_FILE"),
    CONFIG_DIR / ".env",
    Path.cwd() / ".env",
    APP_DIR / ".env",
]

_DEFAULT_DOWNLOAD_DIR = "~/Books"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"download_dir": _DEFAULT_DOWNLOAD_DIR}


def _save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


_cfg = _load_config()
_config = {"download_dir": Path(_cfg["download_dir"]).expanduser().resolve()}

_PROG = "jot"

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
HEAD_TIMEOUT = 8.0
DOWNLOAD_TIMEOUT = 60.0
MAX_RESULTS = 15
VERIFY_BYTES = 200_000
MATCH_THRESHOLD = 45

# Terminal formatting
def _hyperlink(url: str, text: str) -> str:
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    CYAN    = "\033[36m"
    STRIKE  = "\033[9m"

# ---------------------------------------------------------------------------
# YouTube URL detection
# ---------------------------------------------------------------------------

_YT_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/live/"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/"),
    re.compile(r"(?:https?://)?youtu\.be/"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/v/"),
]

def _is_youtube_url(text: str) -> bool:
    return any(p.search(text) for p in _YT_PATTERNS)

def _extract_video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else None

# ---------------------------------------------------------------------------
# Domain list management (domains.md)
# ---------------------------------------------------------------------------

_DEFAULT_DOMAINS = (
    "# Jot Domain Lists\n"
    "#\n"
    "# Manually edit this file or use:\n"
    "#   jot --block N    (add domain of result N to blocked list)\n"
    "#   jot --allow N    (add domain of result N to allowed list)\n"
    "\n"
    "## Allowed\n"
    "- beej.us\n"
    "- archive.org\n"
    "- gutenberg.org\n"
    "- arxiv.org\n"
    "- openlibrary.org\n"
    "- researchgate.net\n"
    "\n"
    "## Blocked\n"
    "- pdfcoffee.com\n"
    "- pdfdrive.com\n"
    "- zlibrary.to\n"
    "- libgen.is\n"
    "- freepdfbook.com\n"
)


def _ensure_domains_file() -> None:
    if DOMAINS_FILE.exists():
        return
    # On first run after install, carry over a curated domains.md that ships
    # alongside the source (if present) so existing customizations survive.
    seed = APP_DIR / "domains.md"
    if seed.exists() and seed.resolve() != DOMAINS_FILE.resolve():
        try:
            DOMAINS_FILE.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
            return
        except OSError:
            pass
    DOMAINS_FILE.write_text(_DEFAULT_DOMAINS, encoding="utf-8")


def _parse_domains() -> tuple[set[str], set[str]]:
    _ensure_domains_file()
    text = DOMAINS_FILE.read_text(encoding="utf-8")
    allowed: set[str] = set()
    blocked: set[str] = set()
    current: set[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## allowed"):
            current = allowed
        elif stripped.lower().startswith("## blocked"):
            current = blocked
        elif stripped.startswith("- ") and current is not None:
            domain = stripped[2:].strip().lower()
            if domain:
                current.add(domain)
    return allowed, blocked


def _add_domain(section: str, domain: str) -> bool:
    _ensure_domains_file()
    allowed, blocked = _parse_domains()
    target = allowed if section == "allowed" else blocked
    opposite = blocked if section == "allowed" else allowed
    domain = domain.lower()
    if domain in target:
        return False
    if domain in opposite:
        _remove_domain_from_file("allowed" if section == "blocked" else "blocked", domain)
    text = DOMAINS_FILE.read_text(encoding="utf-8")
    marker = "## Allowed" if section == "allowed" else "## Blocked"
    idx = text.lower().find(marker.lower())
    if idx == -1:
        text += f"\n{marker}\n- {domain}\n"
    else:
        insert_at = idx + len(marker)
        text = text[:insert_at] + f"\n- {domain}" + text[insert_at:]
    DOMAINS_FILE.write_text(text, encoding="utf-8")
    return True


def _remove_domain_from_file(section: str, domain: str) -> None:
    text = DOMAINS_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = "## allowed" if section == "allowed" else "## blocked"
    in_section = False
    new_lines = []
    for line in lines:
        if line.strip().lower().startswith("## "):
            in_section = line.strip().lower().startswith(marker)
        if in_section and line.strip().lower() == f"- {domain.lower()}":
            continue
        new_lines.append(line)
    DOMAINS_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.lower()

# ---------------------------------------------------------------------------
# Cache management (PDF search results)
# ---------------------------------------------------------------------------

def _save_results(query: str, results: list[dict]) -> None:
    data = {
        "query": query,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_results() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _get_result_by_id(result_id: int) -> dict | None:
    data = _load_results()
    if not data:
        return None
    for r in data["results"]:
        if r["id"] == result_id:
            return r
    return None

# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------

def _search_brave(query: str, api_key: str) -> list[dict]:
    variations = [
        f'"{query}" filetype:pdf',
        f'"{query}" free pdf download',
        f'{query} pdf',
    ]
    seen_urls: set[str] = set()
    all_results: list[dict] = []

    with httpx.Client(timeout=15.0) as client:
        for q in variations:
            try:
                resp = client.get(
                    BRAVE_SEARCH_URL,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": api_key,
                    },
                    params={"q": q, "count": MAX_RESULTS},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("web", {}).get("results", []):
                    url = item.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            "url": url,
                            "title": item.get("title", ""),
                            "description": item.get("description", ""),
                        })
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    print(f"{C.YELLOW}Rate limited on query variation, continuing...{C.RESET}")
                    time.sleep(1)
                else:
                    print(f"{C.YELLOW}Search error ({e.response.status_code}): {e}{C.RESET}")
            except httpx.RequestError as e:
                print(f"{C.YELLOW}Network error: {e}{C.RESET}")

    return all_results

# ---------------------------------------------------------------------------
# PDF verification
# ---------------------------------------------------------------------------

def _head_check(url: str, client: httpx.Client) -> tuple[bool, float | None]:
    try:
        resp = client.head(url, follow_redirects=True, timeout=HEAD_TIMEOUT)
        ct = resp.headers.get("content-type", "").lower()
        is_pdf = "application/pdf" in ct or url.lower().endswith(".pdf")
        size = resp.headers.get("content-length")
        size_mb = round(int(size) / (1024 * 1024), 1) if size else None
        return is_pdf, size_mb
    except Exception:
        return url.lower().endswith(".pdf"), None


def _verify_pdf_content(url: str, expected_title: str) -> tuple[float, str]:
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as client:
            with client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                chunks = []
                total = 0
                for chunk in resp.iter_bytes(8192):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= VERIFY_BYTES:
                        break
            raw = b"".join(chunks)

        doc = fitz.open(stream=raw, filetype="pdf")
        text = ""
        if doc.page_count > 0:
            text = doc[0].get_text()[:500]
        doc.close()

        if text.strip():
            score = fuzz.token_set_ratio(expected_title.lower(), text.lower())
            snippet = text.strip().replace("\n", " ")[:120]
        else:
            url_text = unquote(url.split("?")[0].split("/")[-1])
            url_text = url_text.replace("-", " ").replace("_", " ").replace(".pdf", "")
            score = fuzz.token_set_ratio(expected_title.lower(), url_text.lower())
            snippet = f"(from URL: {url_text[:100]})"

        return score, snippet

    except Exception as e:
        return 0, f"Verification failed: {e}"

# ---------------------------------------------------------------------------
# Display (PDF)
# ---------------------------------------------------------------------------

def _display_results(data: dict) -> None:
    allowed, blocked = _parse_domains()
    query = data["query"]
    results = data["results"]
    ts = data.get("timestamp", "")

    print(f"\n{C.BOLD}Results for: \"{query}\"{C.RESET}")
    if ts:
        print(f"{C.DIM}Searched: {ts}{C.RESET}")
    print()

    if not results:
        print(f"  {C.YELLOW}No results found.{C.RESET}")
        return

    for r in results:
        rid = r["id"]
        domain = r["domain"]
        title = r["title"][:70]
        size = r.get("file_size_mb")
        is_pdf = r.get("is_pdf", False)

        if domain in blocked:
            tag = f"{C.RED}[BLOCKED]{C.RESET}"
            title_fmt = f"{C.DIM}{C.STRIKE}{title}{C.RESET}"
        elif domain in allowed:
            tag = f"{C.GREEN}[TRUSTED]{C.RESET}"
            title_fmt = title
        else:
            tag = "        "
            title_fmt = title

        if size:
            size_str = f"{size:>6.1f} MB"
        else:
            size_str = f"{'—':>6}   "

        pdf_icon = f"{C.GREEN}PDF{C.RESET}" if is_pdf else f"{C.DIM}???{C.RESET}"

        url = r["url"]
        domain_link = _hyperlink(url, f"{C.CYAN}{domain:<25}{C.RESET}")
        print(f"  {C.BOLD}{rid:>2}{C.RESET}  {tag}  {pdf_icon}  {size_str}  {domain_link}  {title_fmt}")

    print(f"\n{C.DIM}Commands:{C.RESET}")
    print(f"  {_PROG} --download N    {_PROG} --open N    {_PROG} --block N    {_PROG} --allow N    {_PROG} --verify N")
    print()


def _display_domains() -> None:
    allowed, blocked = _parse_domains()
    print(f"\n{C.BOLD}Domain Lists{C.RESET} ({DOMAINS_FILE})\n")
    print(f"  {C.GREEN}Allowed ({len(allowed)}):{C.RESET}")
    for d in sorted(allowed):
        print(f"    - {d}")
    print(f"\n  {C.RED}Blocked ({len(blocked)}):{C.RESET}")
    for d in sorted(blocked):
        print(f"    - {d}")
    print()

# ---------------------------------------------------------------------------
# File opener (cross-platform)
# ---------------------------------------------------------------------------

def _open_file(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
        print(f"{C.DIM}Opened with system viewer.{C.RESET}")
    except Exception as e:
        print(f"{C.YELLOW}Could not open file: {e}{C.RESET}")
        print(f"{C.DIM}Open manually: {path}{C.RESET}")

# ---------------------------------------------------------------------------
# PDF commands
# ---------------------------------------------------------------------------

def cmd_search(query: str, author: str | None, api_key: str) -> None:
    full_query = f"{query} {author}" if author else query
    print(f"\n{C.BOLD}Searching:{C.RESET} {full_query}")
    print(f"{C.DIM}Running query variations...{C.RESET}\n")

    raw_results = _search_brave(full_query, api_key)

    if not raw_results:
        print(f"{C.YELLOW}No results found. Try different keywords or check your API key.{C.RESET}")
        return

    allowed, blocked = _parse_domains()

    print(f"{C.DIM}Checking {len(raw_results)} URLs...{C.RESET}")
    enriched: list[dict] = []

    with httpx.Client(follow_redirects=True) as client:
        for i, r in enumerate(raw_results):
            domain = _domain_from_url(r["url"])
            is_pdf, size_mb = _head_check(r["url"], client)
            enriched.append({
                "id": i + 1,
                "title": r["title"],
                "url": r["url"],
                "description": r["description"],
                "domain": domain,
                "is_pdf": is_pdf,
                "file_size_mb": size_mb,
                "verified": False,
                "match_score": None,
            })

    def _sort_key(item: dict) -> tuple:
        is_blocked = item["domain"] in blocked
        is_trusted = item["domain"] in allowed
        is_pdf = item.get("is_pdf", False)
        return (is_blocked, not is_trusted, not is_pdf)

    enriched.sort(key=_sort_key)

    for i, r in enumerate(enriched):
        r["id"] = i + 1

    _save_results(full_query, enriched)
    _display_results({"query": full_query, "timestamp": datetime.now(timezone.utc).isoformat(), "results": enriched})


def cmd_download(result_id: int, open_after: bool = False) -> None:
    r = _get_result_by_id(result_id)
    if not r:
        print(f"{C.RED}No result with ID {result_id}. Run a search first.{C.RESET}")
        return

    data = _load_results()
    query = data["query"] if data else "unknown"
    url = r["url"]
    domain = r["domain"]

    _, blocked = _parse_domains()
    if domain in blocked:
        print(f"{C.RED}Domain '{domain}' is blocked. Use --allow {result_id} first if you trust it.{C.RESET}")
        return

    print(f"\n{C.BOLD}Downloading:{C.RESET} {r['title'][:80]}")
    print(f"{C.DIM}{url}{C.RESET}\n")

    print(f"{C.DIM}Verifying PDF content...{C.RESET}")
    score, snippet = _verify_pdf_content(url, query)

    if score < MATCH_THRESHOLD:
        print(f"{C.YELLOW}Low match confidence ({score}%){C.RESET}")
        print(f"{C.DIM}Page 1 text: \"{snippet}\"{C.RESET}")
        confirm = input(f"Doesn't look like a match. Download anyway? [y/N] ").strip().lower()
        if confirm != "y":
            print("Skipped.")
            return
    else:
        print(f"{C.GREEN}Match confidence: {score}%{C.RESET}")
        if snippet:
            print(f"{C.DIM}Page 1: \"{snippet[:80]}...\"{C.RESET}")

    dl_dir = _config["download_dir"]
    dl_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r'[^\w\s-]', '', query)[:80].strip()
    safe_name = re.sub(r'\s+', '_', safe_name)
    out_path = dl_dir / f"{safe_name}.pdf"

    counter = 1
    while out_path.exists():
        out_path = dl_dir / f"{safe_name}_{counter}.pdf"
        counter += 1

    print(f"\n{C.DIM}Downloading to {out_path}...{C.RESET}")

    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_bytes(32768):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            bar_len = 30
                            filled = int(bar_len * downloaded / total)
                            bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
                            print(f"\r  [{bar}] {pct:5.1f}%  {downloaded / 1e6:.1f}/{total / 1e6:.1f} MB", end="", flush=True)
                        else:
                            print(f"\r  {downloaded / 1e6:.1f} MB downloaded...", end="", flush=True)

        print(f"\n\n{C.GREEN}Saved:{C.RESET} {out_path}")

        try:
            doc = fitz.open(str(out_path))
            pages = doc.page_count
            doc.close()
            print(f"{C.DIM}{pages} pages{C.RESET}")
        except Exception:
            print(f"{C.YELLOW}Warning: Could not validate downloaded PDF.{C.RESET}")

        if open_after:
            _open_file(out_path)

    except Exception as e:
        print(f"\n{C.RED}Download failed: {e}{C.RESET}")
        if out_path.exists():
            out_path.unlink()


def cmd_verify(result_id: int) -> None:
    r = _get_result_by_id(result_id)
    if not r:
        print(f"{C.RED}No result with ID {result_id}. Run a search first.{C.RESET}")
        return

    data = _load_results()
    query = data["query"] if data else ""

    print(f"\n{C.BOLD}Verifying:{C.RESET} {r['title'][:80]}")
    print(f"{C.DIM}{r['url']}{C.RESET}\n")

    score, snippet = _verify_pdf_content(r["url"], query)

    if score >= MATCH_THRESHOLD:
        print(f"{C.GREEN}Match confidence: {score}%{C.RESET}")
    else:
        print(f"{C.YELLOW}Match confidence: {score}%{C.RESET}")

    if snippet:
        print(f"{C.DIM}Page 1 text:{C.RESET}")
        print(f"  {snippet}")
    print()


def cmd_block(result_id: int) -> None:
    r = _get_result_by_id(result_id)
    if not r:
        print(f"{C.RED}No result with ID {result_id}. Run a search first.{C.RESET}")
        return
    domain = r["domain"]
    if _add_domain("blocked", domain):
        print(f"{C.RED}Blocked:{C.RESET} {domain}")
    else:
        print(f"{C.DIM}{domain} is already blocked.{C.RESET}")


def cmd_allow(result_id: int) -> None:
    r = _get_result_by_id(result_id)
    if not r:
        print(f"{C.RED}No result with ID {result_id}. Run a search first.{C.RESET}")
        return
    domain = r["domain"]
    if _add_domain("allowed", domain):
        print(f"{C.GREEN}Allowed:{C.RESET} {domain}")
    else:
        print(f"{C.DIM}{domain} is already allowed.{C.RESET}")

# ---------------------------------------------------------------------------
# YouTube transcription helpers
# ---------------------------------------------------------------------------

def _format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def _sanitize_filename(title: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "", title)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:120] if len(sanitized) > 120 else sanitized


def _get_video_metadata(url: str) -> dict:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", "Untitled"),
        "channel": info.get("channel", info.get("uploader", "Unknown")),
        "duration": info.get("duration", 0),
        "upload_date": info.get("upload_date", ""),
    }


def _try_youtube_captions(video_id: str) -> list[dict] | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
        transcript = None
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"]).fetch()
        except Exception:
            pass
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(["en"]).fetch()
            except Exception:
                pass
        if transcript is None:
            return None
        return transcript.to_raw_data()
    except Exception as e:
        print(f"  Warning: Caption fetch failed: {e}", file=sys.stderr)
        return None


_PARAGRAPH_BREAK_PROMPT = (
    "Below is a transcript chunk with embedded timestamp markers like <<00:00>> or <<01:23:45>>. "
    "Clean it up and insert paragraph breaks.\n\n"
    "Tasks:\n"
    "1. Insert paragraph breaks (blank lines) at natural points: topic changes, speaker "
    "transitions, shifts in thought, or after questions. Aim for paragraphs of roughly "
    "3-8 sentences each — readable like a blog post.\n"
    "2. Remove filler words and verbal hesitations such as: uh, uhh, um, umm, erm, "
    "hmm, like (when used as filler), you know (when used as filler), I mean (when "
    "used as filler), sort of/kind of (when used as filler), basically (when used as "
    "filler). Also clean up false starts and repeated stutters (e.g. 'the the' -> 'the').\n\n"
    "Rules:\n"
    "- Do NOT change the meaning or substance of what is said\n"
    "- Do NOT rewrite sentences or rephrase — only remove filler words\n"
    "- Keep all <<timestamp>> markers exactly where they are\n"
    "- Return ONLY the cleaned text with paragraph breaks added, no commentary\n\n"
    "Transcript:\n\n"
)


def _chunk_marked_text(marked_text: str, max_chunk_chars: int = 12000) -> list[str]:
    if len(marked_text) <= max_chunk_chars:
        return [marked_text]
    chunks = []
    remaining = marked_text
    while remaining:
        if len(remaining) <= max_chunk_chars:
            chunks.append(remaining)
            break
        split_at = max_chunk_chars
        for sep in [". ", "? ", "! "]:
            idx = remaining.rfind(sep, max_chunk_chars // 2, max_chunk_chars)
            if idx != -1:
                split_at = idx + len(sep)
                break
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return chunks


def _add_paragraph_breaks(marked_text: str) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""
    client = anthropic.Anthropic(api_key=api_key)
    chunks = _chunk_marked_text(marked_text)
    processed = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            print(f"    {C.DIM}Processing chunk {i + 1}/{len(chunks)}...{C.RESET}")
        result = []
        with client.messages.stream(
            model="claude-sonnet-4-5-20250929",
            max_tokens=16000,
            messages=[{"role": "user", "content": _PARAGRAPH_BREAK_PROMPT + chunk}],
        ) as stream:
            for text in stream.text_stream:
                result.append(text)
        processed.append("".join(result))
    return "\n\n".join(processed)


def _captions_to_markdown(segments: list[dict]) -> str:
    if not segments:
        return ""
    parts = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        ts_raw = _format_timestamp(seg["start"]).strip("[]")
        parts.append(f"\u00ab{ts_raw}\u00bb{text}")
    marked_text = " ".join(parts)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        print(f"  {C.DIM}Using Claude to add paragraph breaks...{C.RESET}")
        broken_text = _add_paragraph_breaks(marked_text)
        if broken_text:
            raw_paragraphs = [p.strip() for p in broken_text.split("\n\n") if p.strip()]
            marker_re = re.compile(r"\u00ab(\d{1,2}:\d{2}(?::\d{2})?)\u00bb")
            result_paragraphs = []
            for para in raw_paragraphs:
                m = marker_re.search(para)
                ts = f"[{m.group(1)}]" if m else ""
                clean = marker_re.sub("", para).strip()
                if clean:
                    result_paragraphs.append(f"{ts} {clean}" if ts else clean)
            print(f"  {C.GREEN}Split into {len(result_paragraphs)} paragraphs{C.RESET}")
            return "\n\n".join(result_paragraphs)

    print(f"  {C.YELLOW}Warning: ANTHROPIC_API_KEY not set — paragraph breaks will be minimal{C.RESET}")
    all_text = " ".join(seg["text"].strip() for seg in segments if seg["text"].strip())
    ts = _format_timestamp(segments[0]["start"])
    return f"{ts} {all_text}"


def _download_audio(url: str, output_dir: str) -> str:
    import yt_dlp
    output_path = os.path.join(output_dir, "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
        ],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    audio_file = os.path.join(output_dir, "audio.mp3")
    if not os.path.exists(audio_file):
        for f in os.listdir(output_dir):
            if f.startswith("audio."):
                audio_file = os.path.join(output_dir, f)
                break
    return audio_file


def _transcribe_assemblyai(audio_path: str, diarize: bool = True) -> dict:
    import assemblyai as aai
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        print(
            f"{C.RED}Error: ASSEMBLYAI_API_KEY not set.{C.RESET}\n"
            f"Get a free key at: https://www.assemblyai.com/dashboard/signup\n"
            f"Then add it to your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)
    aai.settings.api_key = api_key
    config = aai.TranscriptionConfig(speaker_labels=diarize, punctuate=True, format_text=True)
    print(f"  {C.DIM}Uploading audio to AssemblyAI...{C.RESET}")
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        print(f"  {C.RED}Transcription failed: {transcript.error}{C.RESET}", file=sys.stderr)
        sys.exit(1)
    result = {"text": transcript.text, "words": []}
    if diarize and transcript.utterances:
        result["utterances"] = [
            {"speaker": u.speaker, "text": u.text, "start": u.start, "end": u.end}
            for u in transcript.utterances
        ]
    elif transcript.words:
        result["words"] = [
            {"text": w.text, "start": w.start, "end": w.end}
            for w in transcript.words
        ]
    return result


def _assemblyai_to_markdown(result: dict, diarize: bool = True) -> str:
    lines = []
    if diarize and "utterances" in result:
        for utt in result["utterances"]:
            ts = _format_timestamp(utt["start"] / 1000)
            speaker = utt["speaker"]
            text = utt["text"].strip()
            lines.append(f"{ts} **Speaker {speaker}:** {text}")
    elif result.get("words"):
        words = result["words"]
        if not words:
            return result.get("text", "")
        para_start = words[0]["start"]
        para_words = []
        for i, w in enumerate(words):
            if i > 0 and para_words:
                gap = w["start"] - words[i - 1]["end"]
                if gap >= 1500:
                    ts = _format_timestamp(para_start / 1000)
                    lines.append(f"{ts} {' '.join(para_words)}")
                    para_words = []
                    para_start = w["start"]
            para_words.append(w["text"])
        if para_words:
            ts = _format_timestamp(para_start / 1000)
            lines.append(f"{ts} {' '.join(para_words)}")
    else:
        return result.get("text", "")
    return "\n\n".join(lines)


def _build_transcript_markdown(metadata: dict, transcript_body: str, url: str, method: str) -> str:
    title = metadata["title"]
    channel = metadata["channel"]
    duration_s = metadata.get("duration", 0)
    duration_str = _format_timestamp(duration_s).strip("[]")
    upload_date = metadata.get("upload_date", "")
    if upload_date:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# {title}

| | |
|---|---|
| **Channel** | {channel} |
| **Source** | <{url}> |
| **Uploaded** | {upload_date} |
| **Duration** | {duration_str} |
| **Transcribed** | {now} |
| **Method** | {method} |

---

## Transcript

{transcript_body}
"""

# ---------------------------------------------------------------------------
# YouTube command (transcription)
# ---------------------------------------------------------------------------

def cmd_transcribe(url: str, diarize: bool, skip_captions: bool, output_dir: str) -> None:
    video_id = _extract_video_id(url)
    if not video_id:
        print(f"{C.RED}Error: Could not parse video ID from URL: {url}{C.RESET}", file=sys.stderr)
        sys.exit(1)

    duration_str = _format_timestamp(0).strip("[]")

    print(f"\n{C.BOLD}Transcribing:{C.RESET} {url}")
    print(f"{C.DIM}Fetching video metadata...{C.RESET}")

    try:
        metadata = _get_video_metadata(url)
    except Exception as e:
        print(f"  {C.YELLOW}Warning: Failed to fetch metadata: {e}{C.RESET}", file=sys.stderr)
        metadata = {"title": f"youtube-{video_id}", "channel": "Unknown", "duration": 0}

    duration_str = _format_timestamp(metadata.get("duration", 0)).strip("[]")
    print(f"  {C.BOLD}{metadata['title']}{C.RESET}")
    print(f"  {C.CYAN}{metadata['channel']}{C.RESET}  {C.DIM}{duration_str}{C.RESET}")
    print()

    transcript_body = None
    method = ""

    # Path A: YouTube captions
    if not skip_captions and not diarize:
        print(f"{C.DIM}Checking for YouTube captions...{C.RESET}")
        segments = _try_youtube_captions(video_id)
        if segments:
            print(f"  {C.GREEN}Found {len(segments)} caption segments{C.RESET}")
            transcript_body = _captions_to_markdown(segments)
            method = "YouTube captions (auto-generated)"
        else:
            print(f"  {C.YELLOW}No English captions available{C.RESET}")

    # Path B: AssemblyAI
    if transcript_body is None:
        action = "diarized transcription" if diarize else "transcription"
        print(f"{C.DIM}Starting AssemblyAI {action}...{C.RESET}")
        with tempfile.TemporaryDirectory() as tmp_dir:
            print(f"  {C.DIM}Downloading audio...{C.RESET}")
            audio_path = _download_audio(url, tmp_dir)
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            print(f"  {C.DIM}Audio downloaded ({file_size_mb:.1f} MB){C.RESET}")
            result = _transcribe_assemblyai(audio_path, diarize=diarize)
        transcript_body = _assemblyai_to_markdown(result, diarize=diarize)
        method = f"AssemblyAI {'(diarized)' if diarize else ''}"
        if diarize and "utterances" in result:
            speakers = set(u["speaker"] for u in result["utterances"])
            print(f"  {C.GREEN}Detected {len(speakers)} speaker(s):{C.RESET} {', '.join(sorted(speakers))}")

    # Write output
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_filename(metadata["title"]) + ".md"
    output_path = out_dir / filename

    counter = 1
    while output_path.exists():
        output_path = out_dir / f"{_sanitize_filename(metadata['title'])} ({counter}).md"
        counter += 1

    doc = _build_transcript_markdown(metadata, transcript_body, url, method)
    output_path.write_text(doc, encoding="utf-8")

    size_kb = output_path.stat().st_size / 1024
    print(f"\n{C.GREEN}Saved:{C.RESET} {output_path}")
    print(f"{C.DIM}{size_kb:.1f} KB{C.RESET}")

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Source content from anywhere — PDFs and YouTube transcripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            f'  {_PROG} "Designing Data-Intensive Applications"       Search for a PDF\n'
            f'  {_PROG} "DDIA" --author "Martin Kleppmann"            Search with author\n'
            f"  {_PROG} https://youtube.com/watch?v=XYZ               Transcribe video\n"
            f"  {_PROG} https://youtu.be/XYZ --diarize                Transcribe with speakers\n"
            f"  {_PROG} --download 3                                  Download PDF result #3\n"
            f"  {_PROG} --open 3                                      Download + open #3\n"
            f"  {_PROG} --verify 1                                    Check PDF content\n"
        ),
    )

    parser.add_argument("query", nargs="?", help="Book title to search, or YouTube URL to transcribe")
    parser.add_argument("--author", "-a", help="Author name (PDF search only)")
    parser.add_argument("--download", "-d", type=int, metavar="ID", help="Download PDF result by ID")
    parser.add_argument("--open", type=int, metavar="ID", help="Download PDF + open in viewer")
    parser.add_argument("--block", "-b", type=int, metavar="ID", help="Block domain of result ID")
    parser.add_argument("--allow", type=int, metavar="ID", help="Allow domain of result ID")
    parser.add_argument("--verify", "-v", type=int, metavar="ID", help="Verify PDF content of result ID")
    parser.add_argument("--results", "-r", action="store_true", help="Re-display last PDF search results")
    parser.add_argument("--domains", action="store_true", help="Show allow/block domain lists")
    parser.add_argument("--path", nargs="?", const=True, default=None, metavar="DIR",
                        help="Show or set PDF download directory")
    parser.add_argument("--output", "-o", type=str, help="Output directory (PDFs: ~/Books, transcripts: ./transcripts)")
    parser.add_argument("--diarize", action="store_true", help="Force speaker diarization (YouTube only)")
    parser.add_argument("--skip-captions", action="store_true", help="Skip YouTube captions, use AssemblyAI directly")

    args = parser.parse_args()

    # Load .env from the first candidate location that exists.
    for candidate in _ENV_CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            load_dotenv(path, override=True)
            break

    # PDF output dir override
    if args.output and not (args.query and _is_youtube_url(args.query)):
        _config["download_dir"] = Path(args.output).resolve()

    # --- Dispatch ---

    if args.domains:
        _display_domains()
        return

    if args.path is not None:
        if args.path is True:
            print(f"PDF download directory: {_config['download_dir']}")
        else:
            new_path = Path(args.path).expanduser().resolve()
            cfg = _load_config()
            cfg["download_dir"] = str(new_path)
            _save_config(cfg)
            _config["download_dir"] = new_path
            print(f"PDF download directory set to: {new_path}")
        return

    if args.results:
        data = _load_results()
        if data:
            _display_results(data)
        else:
            print(f"{C.YELLOW}No cached results. Run a search first.{C.RESET}")
        return

    if args.block is not None:
        cmd_block(args.block)
        return

    if args.allow is not None:
        cmd_allow(args.allow)
        return

    if args.verify is not None:
        cmd_verify(args.verify)
        return

    if args.download is not None:
        cmd_download(args.download)
        return

    if args.open is not None:
        cmd_download(args.open, open_after=True)
        return

    if args.query:
        # Route: YouTube URL → transcribe, anything else → PDF search
        if _is_youtube_url(args.query):
            transcript_dir = args.output if args.output else "./transcripts"
            cmd_transcribe(
                url=args.query,
                diarize=args.diarize,
                skip_captions=args.skip_captions,
                output_dir=transcript_dir,
            )
        else:
            api_key = os.environ.get("BRAVE_API_KEY", "")
            if not api_key:
                print(f"{C.RED}BRAVE_API_KEY not set.{C.RESET}")
                print(f"Add it to {ENV_FILE} or export it as an environment variable.")
                print(f"Get a free key at: https://brave.com/search/api/")
                sys.exit(1)
            cmd_search(args.query, args.author, api_key)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
