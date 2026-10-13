# jot

Turn a YouTube video's speech into timestamped Markdown. Opt in to an OpenRouter evaluation to decide whether to **WATCH, SKIM, or SKIP**, then keep the evidence in a portable watch page.

Ordinary transcription preserves caption wording rather than rewriting or removing filler. Evaluation uses the same timestamped source and saves it alongside the report.

## Install from the repository

Requires Python 3.10 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/kbouw/kbxJOT.git jot
cd jot
uv sync
uv run python jot.py --help
```

For development, use `uv run python jot.py` or `uv run jot`; `pyproject.toml` and `uv.lock` manage the dependencies.

To install the command outside the checkout:

```bash
uv tool install .
uv tool update-shell
jot --help
```

On macOS/BSD, `/usr/bin/jot` is a different utility. Put uv's executable directory before `/usr/bin` on your `PATH`; `which jot` shows which command will run. After pulling updates, reinstall with `uv tool install --force .`. Uninstall with `uv tool uninstall jot`.

### Configuration

Caption-based transcription needs no API key. Evaluation requires an [OpenRouter key](https://openrouter.ai/keys). The audio fallback and speaker diarization require an [AssemblyAI key](https://www.assemblyai.com/) and **ffmpeg** on your `PATH`:

```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg
```

Copy the configuration template and edit the copy:

```bash
mkdir -p ~/.config/jot
cp .env.example ~/.config/jot/.env
```

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required only for `--evaluate` |
| `OPENROUTER_MODEL` | OpenRouter model ID; defaults to `anthropic/claude-sonnet-4.5` |
| `ASSEMBLYAI_API_KEY` | Optional audio transcription fallback and diarization |

jot loads the **first existing** environment file in this order:

1. `$JOT_ENV_FILE`
2. The configuration directory's `.env` (normally `~/.config/jot/.env`)
3. `./.env` in the current directory
4. `.env` next to `jot.py`

`JOT_CONFIG_DIR` overrides the configuration directory; otherwise `$XDG_CONFIG_HOME/jot` is used when `XDG_CONFIG_HOME` is set. Values in the selected file override existing environment variables. To select the checkout's file explicitly:

```bash
JOT_ENV_FILE=.env uv run jot 'https://youtu.be/WkBPX-oDMnA' --evaluate
```

Keep keys private; neither saved Markdown nor watch pages need API keys to read.

## Usage

```bash
# Timestamped transcript
jot 'https://youtu.be/WkBPX-oDMnA'

# Watch-value evaluation, with optional viewer context
jot 'https://youtu.be/WkBPX-oDMnA' --evaluate
jot 'https://youtu.be/WkBPX-oDMnA' --evaluate \
  --background 'I know the basics and want concrete implementation techniques'

# Select the output directory
jot 'https://youtu.be/WkBPX-oDMnA' --evaluate -o ~/watch-notes

# Force audio transcription, with or without speaker labels
jot 'https://youtu.be/WkBPX-oDMnA' --skip-captions
jot 'https://youtu.be/WkBPX-oDMnA' --diarize
```

| Argument or flag | Behavior |
|---|---|
| YouTube URL | Video to transcribe or evaluate |
| `--evaluate` | Request an OpenRouter watch-value report and save Markdown plus HTML |
| `--background TEXT` | Describe existing knowledge or learning goals; requires `--evaluate` |
| `--diarize` | Use AssemblyAI audio transcription with speaker labels |
| `--skip-captions` | Use AssemblyAI directly instead of checking captions |
| `-o DIR`, `--output DIR` | Output directory; defaults to `./transcripts` for a video |
| `--render-html FILE` | Render a saved evaluation Markdown file without an API request |
| `--help` | Show command help |

The default path checks English YouTube captions first. If unavailable, jot extracts temporary audio using yt-dlp and ffmpeg and sends it to AssemblyAI. `--diarize` and `--skip-captions` force this audio path. Caption availability and accuracy vary; automatically generated captions can contain mistakes. Audio transcription and evaluation may incur provider charges. **Evaluation is never enabled by default.**

Caption fetch failures retain their cause in a warning before audio fallback;
unavailable captions are reported separately. Audio configuration is checked before
downloading. Empty captions can fall back to audio, but empty final speech is
rejected before evaluation or saving. Missing duration is displayed as `Unknown`,
not `00:00`; caption provenance distinguishes manual and automatically generated text.

Transcripts are saved as `<video title>.md`. Evaluations save `<video title>.evaluation.md` and `<video title>.evaluation.html`, including the source transcript. Existing video outputs are preserved by adding a numeric filename suffix.

If Markdown saves but its HTML companion fails, jot exits with an error and reports
the preserved Markdown path. Use `--render-html` to retry the companion without
another evaluation request.

## What an evaluation tells you

The report gives a WATCH / SKIM / SKIP recommendation, intended audience, reasoning, and confidence. It covers concrete takeaways with timestamped quotes, novelty, high-value sections and skippable ranges, signal versus fluff, and evidence limitations. Stories and analogies are assessed separately from repetition, marketing, and housekeeping rather than automatically treated as padding.

`--background` helps assess novelty **to you**. Without it, personal novelty is unknown. Novelty **to the field** and factual claims are not independently verified. This evaluates speech, not slides or visual demonstrations; confidence and timestamp boundaries are model judgments, not measured statistics.

The full transcript must fit the selected model's context. Provider errors or incomplete responses stop evaluation rather than saving a partial report. Provider charges depend on the model and input/output length; consult the provider's current terms. The source speech is sent to OpenRouter for evaluation and, on the audio path, audio is sent to AssemblyAI for transcription.

## Portable watch pages

Each evaluation's HTML companion embeds the report, collapsible source transcript, Libron font files, Flexoki palettes, CSS, and JavaScript in **one file**. Share that file alone: no companion Markdown, fonts, application server, or keys are required. It is a saved snapshot, not a live evaluation.

The reading order is Verdict, What you can learn, Worth watching, Signal versus fluff, Novelty, and collapsed Limits. Compact two-column section navigation keeps the page easy to scan. Takeaway headlines have their own line; High, Moderate, and Low ratings use filled, outlined, and dashed badges. Signal rows separate the category and rating from their explanation.

The destination theme toggle says **Dark** in light mode and **Light** in dark mode. The first visit follows your system preference; manual choices persist across pages and reloads when browser storage is available. Without storage, switching still works on the current page. Without JavaScript, the page follows system preference and hides the toggle. The Flexoki palette uses CSS `light-dark()`, requiring a modern browser that supports it.

Click a timestamp in the report or transcript to seek the embedded YouTube video. A range starts at its first timestamp; playback does not stop automatically at the range's end. Timestamp links still work as normal YouTube links without JavaScript.

Re-render existing evaluation Markdown without another model request:

```bash
jot --render-html 'Talk title.evaluation.md'
uv run python jot.py --render-html 'Talk title.evaluation.md' -o ~/watch-notes
```

This writes or replaces the HTML companion, leaving Markdown unchanged. Without `-o`, it writes beside the input file. `--render-html` cannot be combined with a video URL or transcription/evaluation flags.

**Reading works offline; playback requires internet.** YouTube may reject `file://` embeds or restrict embedding/autoplay for a video. Use the direct YouTube link, host the HTML on a static website, or serve your output directory locally:

```bash
python3 -m http.server 8765 --directory ~/watch-notes
# Open http://localhost:8765/ and choose the HTML file.
```

YouTube controls inside the iframe belong to YouTube, not jot. Inspect report claims and recommendations against the included transcript before relying on them.

## Licenses

jot's code is licensed under the [MIT License](LICENSE). Bundled Libron v0.31 fonts retain their [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt); the Flexoki palette retains its [MIT license](jot_assets/Flexoki-LICENSE.txt). Both third-party notices are distributed with the package and embedded in generated watch pages.
