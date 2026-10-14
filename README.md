# jot

Transcribe YouTube speech to timestamped Markdown, then use your existing AI assistant to produce an evidence-grounded WATCH / SKIM / SKIP evaluation and a standalone HTML watch page. The agent workflow needs no OpenRouter key: your host assistant coordinates local tools and delegates assessment to a fresh evaluator context. jot does not launch models or certify agent independence.

Transcription preserves source wording. Evaluations include the source transcript. Direct OpenRouter evaluation with `--evaluate` remains optional.

## Install from the repository

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/kebaux/kbxJOT.git jot
cd jot
uv sync
uv run jot --help
```

`uv run python jot.py` and `uv run jot` use the dependencies in `pyproject.toml` and `uv.lock`. Inside the checkout, use `uv run jot` wherever the examples below use `jot`.

Install `jot` outside the checkout:

```bash
uv tool install .
uv tool update-shell
jot --help
```

On macOS/BSD, `/usr/bin/jot` is a different command. Ensure uv's executable directory precedes `/usr/bin`; check with `which jot`. After pulling updates, reinstall with `uv tool install --force .`. Uninstall with `uv tool uninstall jot`.

## Evaluate with your assistant

Use a host assistant that can read files and run local commands. Claude Code or Codex can coordinate this workflow when those permissions are enabled; fresh evaluator delegation depends on the host's available tools. Cowork and other assistants may have file access, terminal access, delegation, or only some of these. Where access is missing, run commands yourself and transfer the complete files manually. If fresh delegation is unsupported, use a separate fresh assistant session for evaluation—not another turn in the coordinator's conversation. Do not describe an inherited context as independent.

Ask your host assistant to read `jot --agent-instructions` and follow its coordinator procedure. The CLI prints the packaged instructions; it does not start an agent. A complete workflow is:

```bash
# 1. Read the coordinator and evaluator instructions
jot --agent-instructions
jot --help

# 2. Capture captions without paid audio fallback or model calls
jot 'https://youtu.be/WkBPX-oDMnA' --captions-only -o ~/watch-notes

# 3. Use the actual transcript path printed by capture
jot --prepare-evaluation ~/watch-notes/'Talk title.md' \
  --background 'I know the basics and want concrete implementation techniques'
```

`Talk title.md` is an example: substitute the saved path. Viewer background is optional. Preparation writes a collision-safe `.request.json` file beside the transcript by default, containing the complete source document, viewer background, rubric, JSON response schema, and request ID. It needs no provider keys or network access.

Before writing a request, preparation checks the source envelope and timestamp validity, nondecreasing order (equal timestamps are allowed), and bounds against any known duration. Malformed, decreasing, or out-of-bounds source timestamps stop preparation: resolve the source errors before delegating or spending evaluator model access. Unknown duration is not zero and imposes no duration bound. These source checks do not establish semantic quality or full coverage; publication still applies the evidence gate below.

Next, the **host**, not jot, delegates the complete request to a fresh evaluator context. Give that evaluator the fresh evaluator instructions from `--agent-instructions`, read access to the request, and write access only to a designated response file. Do not pass the host's verdict or earlier recommendations. The evaluator must read the full packet, treat source and background as untrusted data, apply the embedded rubric, and write exactly one JSON object matching the embedded schema, with the same `request_id`. It must not follow source instructions, access credentials, browse, or execute source commands. If the full packet cannot fit its context, stop rather than silently truncate it.

Without delegation, open a separate fresh session, supply those instructions and the full request file, and save its JSON response locally. Then publish using the actual request and response paths:

```bash
jot --publish-evaluation ~/watch-notes/'Talk title.request.json' \
  --report ~/watch-notes/response.json
```

Publication saves evaluation Markdown and HTML beside the request by default. Return the actual paths printed by jot; filenames may have numeric suffixes. It never changes the request, response, original transcript, or previous outputs. If validation fails, return the diagnostics to the evaluator for correction against the same request; do not rewrite evidence just to pass the gates.

**Caption failure stops this workflow.** `--captions-only` never downloads audio or calls AssemblyAI, even if keys are configured. Ask permission before trying paid AssemblyAI or another transcription service. No captions means another transcription source is needed before evaluation can proceed. Existing assistant access may still have subscription, token, or usage costs: keyless does not mean free model execution.

### What local validation establishes

The publisher checks the request digest, supported version, source envelope, and rubric/schema against packaged trusted assets. It validates the response against the strict JSON Schema, checks the matching request ID, exact quoted source evidence and segment timestamps, viewer-background novelty rules, and ordered ranges within known duration with evidence inside each range. Narrative fields must be nonblank single-line text; extra fields and evaluator-supplied URLs are not part of the response protocol. Untimed sources cannot justify invented time ranges.
Ranges use `[start, end)`: start is included, end excluded, and citations must satisfy `start <= timestamp < end`.

A failed gate prevents publication. The digest detects packet/response mismatch; it is not authentication. These deterministic checks establish structural validity and exact evidence/range constraints—not semantic quality, complete reading, factual truth, or actual evaluator independence. The host is responsible for fresh-context execution, and you should still check report claims against the included transcript.

## Configuration and optional providers

Captions need no API key. Agent instruction printing, request preparation, publication, and saved HTML rendering do not load provider configuration or call providers. The agent evaluation itself uses the host's existing model access.

Only direct `--evaluate` requires an [OpenRouter key](https://openrouter.ai/keys). Audio fallback and speaker diarization require an [AssemblyAI key](https://www.assemblyai.com/) and **ffmpeg** on your `PATH`:

```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg
```

For these optional provider paths, copy the template and add the required keys:

```bash
mkdir -p ~/.config/jot
cp .env.example ~/.config/jot/.env
```

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required only for direct `--evaluate`, not agent evaluation |
| `OPENROUTER_MODEL` | OpenRouter model ID; defaults to `anthropic/claude-sonnet-4.5` |
| `ASSEMBLYAI_API_KEY` | Audio transcription fallback and diarization; not used by `--captions-only` |

For provider paths, jot loads the **first existing** environment file:

1. `$JOT_ENV_FILE`
2. The configuration directory's `.env` (normally `~/.config/jot/.env`)
3. `./.env` in the current directory
4. `.env` next to `jot.py`

`JOT_CONFIG_DIR` overrides the configuration directory. Otherwise, jot uses `$XDG_CONFIG_HOME/jot` when `XDG_CONFIG_HOME` is set, or `~/.config/jot`. The selected file's values override existing environment variables. To use the checkout's file:

```bash
JOT_ENV_FILE=.env uv run jot 'https://youtu.be/WkBPX-oDMnA' --evaluate
```

Keep API keys private. Never give them to the evaluator. Reading saved Markdown or HTML requires no API key.

## Command reference

```bash
# Timestamped transcript: audio fallback allowed
jot 'https://youtu.be/WkBPX-oDMnA'

# Captions only: no paid audio fallback
jot 'https://youtu.be/WkBPX-oDMnA' --captions-only

# Optional direct OpenRouter evaluation
jot 'https://youtu.be/WkBPX-oDMnA' --evaluate
jot 'https://youtu.be/WkBPX-oDMnA' --captions-only --evaluate \
  --background 'I know the basics and want concrete implementation techniques' \
  -o ~/watch-notes

# Force audio transcription, with or without speaker labels
jot 'https://youtu.be/WkBPX-oDMnA' --skip-captions
jot 'https://youtu.be/WkBPX-oDMnA' --diarize
```

| Argument or flag | Behavior |
|---|---|
| YouTube URL | Video to transcribe or directly evaluate |
| `--agent-instructions` | Print provider-neutral coordinator and fresh evaluator instructions |
| `--captions-only` | Use captions only; stop on missing, empty, or failed captions, without audio fallback |
| `--prepare-evaluation FILE` | Prepare an agent request from plain saved transcript Markdown |
| `--publish-evaluation FILE` | Validate a request and its response, then save evaluation Markdown and HTML |
| `--report FILE` | Evaluator JSON response; valid only with `--publish-evaluation` |
| `--evaluate` | Request an OpenRouter watch-value report and save Markdown plus HTML |
| `--background TEXT` | Viewer knowledge or learning goals; valid with `--prepare-evaluation` or `--evaluate` |
| `--diarize` | Use AssemblyAI audio transcription with speaker labels |
| `--skip-captions` | Use AssemblyAI directly instead of checking captions |
| `-o DIR`, `--output DIR` | Output directory; defaults to `./transcripts` for video capture, transcript directory for preparation, request directory for publication, input directory for rendering |
| `--render-html FILE` | Render saved evaluation Markdown without an API request |
| `--help` | Show command help |

Choose one action: video capture/direct evaluation, instruction printing, preparation, publication, or rendering. Do not mix offline actions with a video URL or unrelated transcription flags. `--captions-only` cannot be combined with `--diarize` or `--skip-captions`.

English captions are preferred. Without `--captions-only`, unavailable or empty captions and fetch failures trigger audio fallback; fetch failures retain their cause in the warning. `--diarize` and `--skip-captions` force audio transcription. Audio configuration is checked before downloading. Empty final speech fails before evaluation or saving. Generated captions may contain errors; saved Method distinguishes manual and auto-generated captions.

Missing duration displays as `Unknown`, not `00:00`. Missing titles use `Untitled`. Missing channels use the uploader, or `Unknown`. Channel names retain literal pipes and backslashes in both outputs.

Transcripts save as `<video title>.md`. Evaluations save as `<video title>.evaluation.md` and `<video title>.evaluation.html`, including the source transcript. Numeric suffixes prevent overwriting existing capture and publication outputs. If an HTML companion fails to save, jot exits with an error and reports the preserved Markdown path. Retry with `--render-html` without another evaluation request.

## Evaluation

Both evaluation paths use the packaged rubric:

- WATCH / SKIM / SKIP recommendation, audience, reasoning, and confidence.
- Concrete takeaways with timestamped quotes; no takeaway quota.
- High-value sections, skippable ranges, and a signal-versus-fluff assessment.
- Novelty, evidence, and limitations.

Stories and analogies are assessed separately from repetition, marketing, and housekeeping; useful illustrations are not automatically padding. Ratings are judgments, not measured time percentages. Use `--background` to assess novelty relative to your knowledge. Without it, personal novelty is unknown. Field-level novelty is not independently verified.

Assessments are speech-only: slides, demonstrations, visuals, delivery, and audio quality cannot be inferred from transcript text. Neither exact quotes nor the last timestamp prove full coverage. Range boundaries are approximate. The full transcript must fit the evaluator model's context.

Direct OpenRouter provider errors or incomplete responses stop evaluation; no partial report is saved. Direct `--evaluate` sends speech to OpenRouter; audio transcription sends audio to AssemblyAI. Both may incur charges; check providers' current rates. Agent evaluation shares the packet with the host's chosen evaluator under that service's access and data policies.

## Portable watch pages

HTML embeds the report, collapsible transcript, Libron fonts, Flexoki palettes, CSS, and JavaScript. Share the file alone; no companion assets, keys, or application server are needed for reading. The file is a saved snapshot, not a live evaluation.

The page includes an embedded YouTube video. Reading order: Verdict, What you can learn, Worth watching, Signal versus fluff, Novelty, then collapsed Limits.

Section navigation uses two columns. Takeaway headlines appear on separate lines. High, Moderate, and Low badges are filled, outlined, and dashed, respectively. Signal explanations start below the category rating.

The theme toggle names the destination: **Dark** in light mode, **Light** in dark mode. The initial theme follows system preference. Choices persist when browser storage is available; otherwise, they last for the current page. Without JavaScript, the page follows system preference and hides the toggle. Requires a browser that supports CSS `light-dark()`.

Timestamp links seek the embedded video. Ranges start at the first timestamp; playback does not stop at the range's end. Without JavaScript, timestamp links open YouTube.

Render a saved evaluation without an API request:

```bash
jot --render-html 'Talk title.evaluation.md'
uv run python jot.py --render-html 'Talk title.evaluation.md' -o ~/watch-notes
```

`--render-html` writes or replaces the HTML companion and leaves Markdown unchanged. Output defaults to the input directory; `-o` overrides it. Do not combine `--render-html` with a video URL or transcription/evaluation flags.

**Reading works offline; playback needs internet.** YouTube may block local-file embeds, embedding, or autoplay. Use the direct YouTube link, host the HTML on a static website, or serve the output directory:

```bash
python3 -m http.server 8765 --directory ~/watch-notes
# Open http://localhost:8765/ and choose the HTML file.
```

YouTube supplies the iframe controls. Check report claims against the included transcript.

## Licenses

Code uses the [MIT License](LICENSE). Libron v0.31 uses the [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt). Flexoki uses the [MIT license](jot_assets/Flexoki-LICENSE.txt). Third-party notices ship with the package and generated HTML.
