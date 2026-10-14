# jot

Turn a YouTube video into a portable **HTML watch page**: a WATCH / SKIM / SKIP assessment, timestamped takeaways, sections worth watching, and the source transcript—all in one file you can open or share.

**Set up once, then give your assistant a video URL and receive the HTML file.** jot—not the assistant—captures captions, launches a fresh evaluator, validates its evidence, and creates the page.

## What you need

- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10 or newer.
- An installed, authenticated [Claude Code CLI](https://code.claude.com/docs/en/setup). Evaluation uses its existing model access; no additional jot API key is required. Subscription limits or usage charges still apply.
- A host assistant that can run local commands and deliver files. It does **not** need subagent delegation. Its model can differ from the Claude evaluator.

Claude Code is currently the supported evaluator. The invocation is verified with Claude Code 2.1.277 and requires its `--safe-mode`, tool-disabling, and structured-output flags. A local Qwen model or a Codex session alone does not supply this evaluator access.

## Set up once

Install Claude Code using its setup guide, then sign in:

```bash
claude auth login
```

Install jot:

```bash
git clone https://github.com/kebaux/kbxJOT.git jot
cd jot
uv sync
```

Open your host assistant in this `jot` folder and allow local command and file access. Use `uv run jot` inside the checkout to avoid the unrelated macOS command named `jot`.

## Create a watch page

Paste this request into your assistant, replacing `YOUTUBE_URL`:

> Create a jot HTML watch page for this video: YOUTUBE_URL. Read `uv run jot --agent-instructions`, run jot's captions-only evaluation workflow, and deliver the actual HTML file or a clickable link I can open. If jot fails, report the diagnostic; do not evaluate the video yourself.

Optionally add your background or learning goal—for example, “I know the basics and want concrete implementation techniques.”

Prefer the terminal? From the project folder, run:

```bash
uv run jot 'YOUTUBE_URL' --captions-only --evaluate
```

jot prints the actual saved HTML path. Transcripts and evaluation artifacts stay in `./transcripts` by default; `-o DIR` changes the destination. You do not need to handle request packets or response JSON.

## Use the result

Open the delivered `.evaluation.html` file in a modern browser. Share the HTML file alone: no companion assets, keys, or server are needed for reading. A remote assistant must provide a downloadable file, not a path on its own machine.

- **English captions unavailable?** The default evaluation workflow stops, even when captions exist in another language. It never silently downloads audio or uses paid transcription.
- **Evaluator or validation failure?** jot stops without publishing a report. The saved transcript remains available. There is no fallback to host-assistant self-evaluation or automatic retry.
- **Reading works offline; playback needs internet.** If the embedded video is blocked, use the page's direct YouTube link.

The evaluator runs in a fresh noninteractive process with customizations, tools, and MCP disabled, without your host conversation. Local validation checks response structure, exact source quotes, timestamps, and ranges—not factual truth, semantic quality, or complete model reading. Assessments are speech-only; check important claims against the transcript.

## Advanced use

Run `uv run jot --help` for transcript-only capture, explicit audio options, and rendering saved reports. Paid audio transcription requires separate setup and permission; it is not part of default evaluation. Coordinator usage is available through `uv run jot --agent-instructions`.

**Workflow change:** `--evaluate` now invokes Claude Code and the evidence-validation gate, not OpenRouter. The old manual preparation/publication CLI actions have been removed; jot owns those stages.

## Licenses

Code uses the [MIT License](LICENSE). Libron v0.31 uses the [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt). Flexoki uses the [MIT license](jot_assets/Flexoki-LICENSE.txt). Third-party notices ship with the package and generated HTML.
