# jot

Turn a YouTube video into a portable **HTML watch page** using your existing AI assistant. Get a WATCH / SKIM / SKIP assessment, timestamped takeaways, sections worth watching, and the source transcript—all in one file you can open or share.

**Set up once, then ask for a watch page and receive the HTML file.** No additional API key is required; evaluation uses your assistant's existing model access and may count toward its usage or billing.

## What you need

- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10 or newer.
- An AI assistant with permission to run local commands, read and write files, and delegate evaluation to a fresh context. Claude Code or Codex can coordinate this workflow when those capabilities are available.

A chat-only assistant is not enough for this automatic workflow. jot provides the local tools and instructions; your assistant coordinates the evaluation.

## Set up once

```bash
git clone https://github.com/kebaux/kbxJOT.git jot
cd jot
uv sync
```

Open your AI assistant in this `jot` folder and allow local command and file access. Commands inside the checkout use `uv run jot`, avoiding the unrelated macOS command named `jot`.

## Create your first watch page

Paste this request into your assistant, replacing `YOUTUBE_URL` with the video's URL:

> Create a jot HTML watch page for this video: YOUTUBE_URL.
> Read `uv run jot --agent-instructions` and follow the coordinator workflow, using `uv run jot` for commands. Use captions only, delegate evaluation to a fresh context, and publish the validated report. Do not use paid transcription or direct provider evaluation. Deliver the actual generated HTML file or a clickable link I can open—not just the Markdown report. If a required capability is unavailable, explain the blocker.

Your assistant handles caption capture, evaluation, validation, and HTML generation. You do not need to manage intermediate transcripts, request packets, or JSON responses.

For later videos in an assistant context that already knows the workflow, ask:

> Create a jot HTML watch page for this video: YOUTUBE_URL.

Optionally add your background or learning goal—for example, “I know the basics and want concrete implementation techniques.”

## Use the result

Open the delivered `.evaluation.html` file in a modern browser. It is self-contained: share the HTML file alone, with no companion assets or API keys. If your assistant runs remotely, it must provide a downloadable file rather than a path on its own machine.

- **English captions unavailable?** The default workflow requires English captions and stops if they are unavailable, even when captions exist in another language. Another transcription source is needed; paid transcription requires your permission.
- **Reading works offline; video playback needs internet.** If the embedded video is blocked, use the page's direct YouTube link.

The assessment is based on speech, not slides or visual demonstrations. Check important claims against the included transcript; validation does not establish factual truth.

## Advanced use

For available commands, run `uv run jot --help`. The coordinator and fresh evaluator protocol is available through `uv run jot --agent-instructions`. These are reference tools, not extra steps required for each video.

## Licenses

Code uses the [MIT License](LICENSE). Libron v0.31 uses the [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt). Flexoki uses the [MIT license](jot_assets/Flexoki-LICENSE.txt). Third-party notices ship with the package and generated HTML.
