# jot

Transcribe YouTube speech to timestamped Markdown. Use `--evaluate` to generate an OpenRouter WATCH / SKIM / SKIP report and a standalone HTML watch page.

Transcription preserves source wording. Evaluations include the timestamped source transcript.

## Install from the repository

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/kbouw/kbxJOT.git jot
cd jot
uv sync
uv run python jot.py --help
```

`uv run python jot.py` and `uv run jot` use the dependencies in `pyproject.toml` and `uv.lock`.

Install `jot` outside the checkout:

```bash
uv tool install .
uv tool update-shell
jot --help
```

On macOS/BSD, `/usr/bin/jot` is a different command. Ensure uv's executable directory precedes `/usr/bin`; check with `which jot`. After pulling updates, reinstall with `uv tool install --force .`. Uninstall with `uv tool uninstall jot`.

### Configuration

Captions need no API key. Evaluation requires an [OpenRouter key](https://openrouter.ai/keys). Audio fallback and speaker diarization require an [AssemblyAI key](https://www.assemblyai.com/) and **ffmpeg** on your `PATH`:

```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg
```

Copy the template, then add the required keys:

```bash
mkdir -p ~/.config/jot
cp .env.example ~/.config/jot/.env
```

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required only for `--evaluate` |
| `OPENROUTER_MODEL` | OpenRouter model ID; defaults to `anthropic/claude-sonnet-4.5` |
| `ASSEMBLYAI_API_KEY` | Optional audio transcription fallback and diarization |

jot loads the **first existing** environment file:

1. `$JOT_ENV_FILE`
2. The configuration directory's `.env` (normally `~/.config/jot/.env`)
3. `./.env` in the current directory
4. `.env` next to `jot.py`

`JOT_CONFIG_DIR` overrides the configuration directory. Otherwise, jot uses `$XDG_CONFIG_HOME/jot` when `XDG_CONFIG_HOME` is set, or `~/.config/jot`. The selected file's values override existing environment variables. To use the checkout's file:

```bash
JOT_ENV_FILE=.env uv run jot 'https://youtu.be/WkBPX-oDMnA' --evaluate
```

Keep API keys private. Reading saved Markdown or HTML requires no API key.

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

English captions are preferred. Unavailable or empty captions and fetch failures trigger audio fallback. Fetch failures retain their cause in a warning. `--diarize` and `--skip-captions` force audio transcription.

Audio configuration is checked before downloading. Empty final speech fails before evaluation or saving. Generated captions may contain errors; the saved Method distinguishes manual and auto-generated captions.

Missing duration displays as `Unknown`, not `00:00`. Missing titles use `Untitled`. Missing channels use the uploader, or `Unknown`. Channel names retain literal pipes and backslashes in both outputs.

Transcripts save as `<video title>.md`. Evaluations save as `<video title>.evaluation.md` and `<video title>.evaluation.html`, including the source transcript. Numeric suffixes prevent overwriting existing video outputs.

If the HTML companion fails to save, jot exits with an error and reports the preserved Markdown path. Retry with `--render-html` without another evaluation request.

## Evaluation

- WATCH / SKIM / SKIP recommendation, audience, reasoning and confidence.
- Concrete takeaways and timestamped quotes.
- High-value sections, skippable ranges and signal-versus-fluff assessment.
- Novelty and evidence limitations.

Stories and analogies are assessed separately from repetition, marketing and housekeeping.

Use `--background` to assess novelty relative to your knowledge. Without it, personal novelty is unknown. Field-level novelty and factual claims are not independently verified. Evaluation covers speech, not slides or visual demonstrations. Confidence and timestamps are model judgments, not measurements.

The transcript must fit the model's context. Provider errors or incomplete responses stop evaluation; no partial report is saved. Evaluation sends speech to OpenRouter; audio transcription sends audio to AssemblyAI. Both may incur charges; check the providers' current rates.

## Portable watch pages

HTML embeds the report, collapsible transcript, Libron fonts, Flexoki palettes, CSS and JavaScript. Share the file alone; no companion assets, keys or application server are needed. The file is a saved snapshot, not a live evaluation.

The page includes an embedded YouTube video. Reading order: Verdict, What you can learn, Worth watching, Signal versus fluff, Novelty, then collapsed Limits.

Section navigation uses two columns. Takeaway headlines appear on separate lines. High, Moderate and Low badges are filled, outlined and dashed, respectively. Signal explanations start below the category and rating.

The theme toggle names the destination: **Dark** in light mode, **Light** in dark mode. The initial theme follows your system preference. Choices persist when browser storage is available; otherwise, they last for the current page. Without JavaScript, the page follows system preference and hides the toggle. Requires a browser that supports CSS `light-dark()`.

Timestamp links seek the embedded video. Ranges start at the first timestamp; playback does not stop at the range's end. Without JavaScript, timestamp links open YouTube.

Render a saved evaluation without an API request:

```bash
jot --render-html 'Talk title.evaluation.md'
uv run python jot.py --render-html 'Talk title.evaluation.md' -o ~/watch-notes
```

`--render-html` writes or replaces the HTML companion and leaves Markdown unchanged. Output defaults to the input directory; `-o` overrides it. Do not combine `--render-html` with a video URL or transcription/evaluation flags.

**Reading works offline; playback needs internet.** YouTube may block local-file embeds, embedding or autoplay. Use the direct YouTube link, host the HTML on a static website, or serve the output directory:

```bash
python3 -m http.server 8765 --directory ~/watch-notes
# Open http://localhost:8765/ and choose the HTML file.
```

YouTube supplies the iframe controls. Check report claims against the included transcript.

## Licenses

Code uses the [MIT License](LICENSE). Libron v0.31 uses the [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt). Flexoki uses the [MIT license](jot_assets/Flexoki-LICENSE.txt). Third-party notices ship with the package and generated HTML.
