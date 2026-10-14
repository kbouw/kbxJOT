#!/usr/bin/env python3
"""Capture YouTube speech and publish optional watch-value evaluations.

Captions retain original wording and timestamps. AssemblyAI supplies audio
transcription when permitted; fresh Claude Code evaluation is validated before
saved Markdown and self-contained HTML are published.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from jot_html import _youtube_id
from jot_agent import read_asset

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    """Resolve the user .env location without creating legacy state directories."""
    override = os.environ.get("JOT_CONFIG_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg).expanduser() / "jot") if xdg else (Path.home() / ".config" / "jot")
    return base


APP_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = _config_dir()
# .env is searched in several locations (first match wins): an explicit
# JOT_ENV_FILE, the config dir, the current working directory, then
# alongside source (handy for `uv run python jot.py` during development).
_ENV_CANDIDATES = [
    os.environ.get("JOT_ENV_FILE"),
    CONFIG_DIR / ".env",
    Path.cwd() / ".env",
    APP_DIR / ".env",
]

_PROG = "jot"


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    CYAN    = "\033[36m"
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

def _format_duration(seconds: float | None) -> str:
    """Display unknown duration without confusing it with a measured zero."""
    return "Unknown" if seconds is None else _format_timestamp(seconds).strip("[]")



def _sanitize_filename(title: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "", title)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:120] if len(sanitized) > 120 else sanitized


def _get_video_metadata(url: str) -> dict:
    """Fetch video metadata, using display-safe defaults for absent text fields."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel") or info.get("uploader") or "Unknown",
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date", ""),
    }


def _try_youtube_captions(video_id: str) -> dict | None:
    """Return segments (start in seconds) and their truthful Method label.

    None means English captions are absent/disabled. Selection prefers manual
    captions; fetch/transport/parser errors propagate for the command to report.
    """
    from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, YouTubeTranscriptApi
    try:
        transcript = YouTubeTranscriptApi().list(video_id).find_transcript(["en"])
    except (NoTranscriptFound, TranscriptsDisabled):
        return None
    fetched = transcript.fetch()
    return {
        "segments": fetched.to_raw_data(),
        "method": "YouTube captions (auto-generated)" if transcript.is_generated else "YouTube captions (manual)",
    }


def _captions_to_markdown(segments: list[dict]) -> str:
    """Keep each caption's timestamp and original wording without model cleanup."""
    return "\n".join(
        f"{_format_timestamp(segment['start'])} {segment['text']}"
        for segment in segments if segment["text"].strip()
    )


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
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        opts["ffmpeg_location"] = ffmpeg_bin
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
    """Return speech text plus timed words or diarized utterances, in milliseconds.

    Credentials configure the SDK for this invocation. Missing configuration or
    provider error status raises; SDK/transport errors propagate. The command
    owns diagnostics and exit status. Empty speech remains empty for validation.
    """
    import assemblyai as aai
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY is required for audio transcription.")
    aai.settings.api_key = api_key
    config = aai.TranscriptionConfig(speaker_labels=diarize, punctuate=True, format_text=True)
    print(f"  {C.DIM}Uploading audio to AssemblyAI...{C.RESET}")
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")
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
    """Render milliseconds to timestamped speech, skipping empty speech records.

    Prefer speaker utterances when requested, otherwise use words and 1.5-second
    pauses. Text-only results retain their untimed text; no timing is invented.
    An empty result returns an empty string. Malformed records raise to the caller.
    """
    lines = []
    utterances = result.get("utterances") if diarize else None
    if utterances:
        for utt in utterances:
            text = utt["text"].strip()
            if text:
                ts = _format_timestamp(utt["start"] / 1000)
                lines.append(f"{ts} **Speaker {utt['speaker']}:** {text}")
    else:
        words = [word for word in result.get("words", []) if word["text"].strip()]
        if words:
            para_start = words[0]["start"]
            para_words = []
            for i, word in enumerate(words):
                if i > 0 and para_words and word["start"] - words[i - 1]["end"] >= 1500:
                    lines.append(f"{_format_timestamp(para_start / 1000)} {' '.join(para_words)}")
                    para_words = []
                    para_start = word["start"]
                para_words.append(word["text"])
            if para_words:
                lines.append(f"{_format_timestamp(para_start / 1000)} {' '.join(para_words)}")
    return "\n\n".join(lines) if lines else result.get("text") or ""


def _build_transcript_markdown(
    metadata: dict, transcript_body: str, url: str, method: str,
) -> str:
    """Serialize captured source speech into saved Markdown.

    The envelope contract is documented at jot_html._parse_document. Metadata
    contains title/channel and optional duration (seconds or None)/upload_date.
    Timestamped speech is preserved, not reformatted here.
    """
    title = metadata["title"]
    channel = metadata["channel"].replace("\\", r"\\").replace("|", r"\|")
    duration_str = _format_duration(metadata.get("duration"))
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

def cmd_transcribe(
    url: str, diarize: bool, skip_captions: bool, output_dir: str,
    evaluate: bool, background: str | None, captions_only: bool = False,
) -> Path | tuple[Path, Path]:
    """Save source speech, then optionally evaluate it with fresh Claude Code.

    Evaluation defaults to captions only unless audio is explicitly requested.
    Plain capture retains audio fallback. Evaluation failure preserves and names
    the captured transcript; publication belongs to jot_agent.
    """
    if captions_only and (diarize or skip_captions):
        print("Error: --captions-only cannot be combined with --diarize or --skip-captions.", file=sys.stderr)
        sys.exit(1)
    if evaluate:
        from jot_evaluator import find_evaluator
        try:
            find_evaluator()
        except (OSError, ValueError) as e:
            print(f"Error: Cannot start evaluation: {e}", file=sys.stderr)
            sys.exit(1)
        captions_only = captions_only or not (diarize or skip_captions)
    video_id = _youtube_id(url)
    if not video_id:
        print(f"{C.RED}Error: Could not parse video ID from URL: {url}{C.RESET}", file=sys.stderr)
        sys.exit(1)


    print(f"\n{C.BOLD}Transcribing:{C.RESET} {url}")
    print(f"{C.DIM}Fetching video metadata...{C.RESET}")

    try:
        metadata = _get_video_metadata(url)
    except Exception as e:
        print(f"  {C.YELLOW}Warning: Failed to fetch metadata: {e}{C.RESET}", file=sys.stderr)
        metadata = {"title": f"youtube-{video_id}", "channel": "Unknown", "duration": None}

    duration_str = _format_duration(metadata.get("duration"))
    print(f"  {C.BOLD}{metadata['title']}{C.RESET}")
    print(f"  {C.CYAN}{metadata['channel']}{C.RESET}  {C.DIM}{duration_str}{C.RESET}")
    print()

    transcript_body = None
    method = ""
    caption_failure = "No English captions available."

    # Path A: YouTube captions
    if not skip_captions and not diarize:
        print(f"{C.DIM}Checking for YouTube captions...{C.RESET}")
        try:
            captions = _try_youtube_captions(video_id)
            if captions is None:
                print(f"  {C.YELLOW}No English captions available{C.RESET}")
            else:
                transcript_body = _captions_to_markdown(captions["segments"])
                method = captions["method"]
                if transcript_body.strip():
                    print(f"  {C.GREEN}Found {len(captions['segments'])} caption segments{C.RESET}")
                else:
                    caption_failure = "Captions contain no speech."
                    fallback = " No audio fallback permitted." if captions_only else " Using audio fallback."
                    print(f"  Warning: {caption_failure}{fallback}", file=sys.stderr)
        except Exception as e:
            caption_failure = f"Caption retrieval failed: {e}"
            print(f"  Warning: {caption_failure}", file=sys.stderr)
            transcript_body = None

    # Path B: AssemblyAI. Configuration is checked before any audio download.
    if not transcript_body:
        if captions_only:
            print(f"Error: --captions-only stopped: {caption_failure} No audio fallback permitted.", file=sys.stderr)
            sys.exit(1)
        if not os.environ.get("ASSEMBLYAI_API_KEY"):
            print("Error: ASSEMBLYAI_API_KEY is required for audio transcription.", file=sys.stderr)
            sys.exit(1)
        action = "diarized transcription" if diarize else "transcription"
        print(f"{C.DIM}Starting AssemblyAI {action}...{C.RESET}")
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                print(f"  {C.DIM}Downloading audio...{C.RESET}")
                audio_path = _download_audio(url, tmp_dir)
                file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                print(f"  {C.DIM}Audio downloaded ({file_size_mb:.1f} MB){C.RESET}")
                result = _transcribe_assemblyai(audio_path, diarize=diarize)
            transcript_body = _assemblyai_to_markdown(result, diarize=diarize)
        except Exception as e:
            print(f"Error: Audio transcription failed: {e}", file=sys.stderr)
            sys.exit(1)
        method = "AssemblyAI (diarized)" if diarize else "AssemblyAI"
        if diarize and result.get("utterances"):
            speakers = {u["speaker"] for u in result["utterances"] if u["text"].strip()}
            if speakers:
                print(f"  {C.GREEN}Detected {len(speakers)} speaker(s):{C.RESET} {', '.join(sorted(speakers))}")
    if not transcript_body.strip():
        print("Error: No transcript speech was produced; no files saved.", file=sys.stderr)
        sys.exit(1)


    # Write output
    out_dir = Path(output_dir).expanduser().resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Error: Cannot create output directory {out_dir}: {e}", file=sys.stderr)
        sys.exit(1)

    stem = _sanitize_filename(metadata["title"])
    output_path = out_dir / f"{stem}.md"
    counter = 1
    doc = _build_transcript_markdown(metadata, transcript_body, url, method)
    while True:
        try:
            with output_path.open("x", encoding="utf-8") as source:
                source.write(doc)
            break
        except FileExistsError:
            output_path = out_dir / f"{stem} ({counter}).md"
            counter += 1
        except OSError as e:
            print(f"Error: Cannot save transcript {output_path}: {e}", file=sys.stderr)
            sys.exit(1)
    print(f"\n{C.GREEN}Saved:{C.RESET} {output_path}")
    if evaluate:
        from jot_agent import evaluate_transcript
        print(f"{C.DIM}Evaluating learning value with Claude Code...{C.RESET}")
        try:
            markdown_path, html_path = evaluate_transcript(output_path, background, out_dir)
        except (OSError, ValueError) as e:
            print(
                f"Error: Evaluation failed: {e}\nTranscript preserved at: {output_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"\n{C.GREEN}Saved evaluation:{C.RESET} {markdown_path}")
        print(f"{C.GREEN}Saved HTML:{C.RESET} {html_path}")
        return markdown_path, html_path

    size_kb = output_path.stat().st_size / 1024
    print(f"{C.DIM}{size_kb:.1f} KB{C.RESET}")
    return output_path

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Capture YouTube speech and optionally publish watch-value evaluations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  jot https://youtu.be/WkBPX-oDMnA --captions-only\n"
            "  jot --agent-instructions\n"
            "  jot https://youtu.be/WkBPX-oDMnA --captions-only --evaluate\n"
            "  jot --render-html transcripts/talk.evaluation.md\n"
        ),
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL to transcribe")
    parser.add_argument("--output", "-o", help="Output directory (capture: ./transcripts; offline files: beside input)")
    parser.add_argument("--diarize", action="store_true", help="Use AssemblyAI speaker diarization")
    parser.add_argument("--skip-captions", action="store_true", help="Use AssemblyAI instead of YouTube captions")
    parser.add_argument("--captions-only", action="store_true", help="Require captions; never download audio or fall back to AssemblyAI")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate with fresh Claude Code; captions only unless --skip-captions or --diarize")
    parser.add_argument("--background", help="What you already know or want to learn (with --evaluate)")
    parser.add_argument("--agent-instructions", action="store_true", help="Print host usage instructions; does not launch evaluation")
    parser.add_argument("--render-html", type=Path, metavar="FILE",
                        help="Render saved evaluation Markdown to portable HTML without API calls")
    args = parser.parse_args()
    actions = (args.url is not None, args.agent_instructions, args.render_html is not None)
    if sum(actions) > 1:
        parser.error("Choose only one action: URL, --agent-instructions, or --render-html")
    if args.background is not None and not args.evaluate:
        parser.error("--background requires --evaluate")
    transcription_flags = any((args.evaluate, args.diarize, args.skip_captions, args.captions_only))
    if any(actions[1:]) and transcription_flags:
        parser.error("Offline actions cannot be combined with transcription or --evaluate flags")
    if args.agent_instructions and any((args.output is not None, args.background is not None)):
        parser.error("--agent-instructions cannot be combined with other action options")
    if args.captions_only and (args.diarize or args.skip_captions):
        parser.error("--captions-only cannot be combined with --diarize or --skip-captions")

    # Offline dispatch precedes .env loading and all provider/configuration use.
    if args.agent_instructions:
        try:
            print(read_asset("agent-workflow.md"), end="")
        except OSError as e:
            parser.error(f"Cannot read agent instructions: {e}")
        return
    if args.render_html is not None:
        from jot_html import build_evaluation_html
        source_path = args.render_html.expanduser().resolve()
        target_dir = Path(args.output).expanduser().resolve() if args.output else source_path.parent
        html_path = target_dir / source_path.with_suffix(".html").name
        try:
            html_doc = build_evaluation_html(source_path.read_text(encoding="utf-8"))
            target_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html_doc, encoding="utf-8")
        except (OSError, ValueError) as e:
            parser.error(f"Cannot render evaluation: {e}")
        print(f"Saved HTML: {html_path}")
        return
    if args.url is None:
        if transcription_flags or args.output is not None:
            parser.error("A YouTube video URL is required")
        parser.print_help()
        return
    if _youtube_id(args.url) is None:
        parser.error("Expected a valid YouTube video URL")

    # Captions and Claude Code use no provider .env; only audio needs it.
    audio_permitted = args.diarize or args.skip_captions or not (args.captions_only or args.evaluate)
    if audio_permitted:
        for candidate in _ENV_CANDIDATES:
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.exists():
                load_dotenv(path, override=True)
                break
    cmd_transcribe(
        url=args.url,
        evaluate=args.evaluate,
        background=args.background,
        diarize=args.diarize,
        skip_captions=args.skip_captions,
        output_dir=args.output or "./transcripts",
        captions_only=args.captions_only,
    )


if __name__ == "__main__":
    main()
