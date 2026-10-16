# jot

Turn a YouTube video into a portable **HTML watch page**: a WATCH / SKIM / SKIP assessment, timestamped takeaways, sections worth watching, and the source transcript—all in one file you can open or share.

**Set up once, then give your assistant a video URL and receive the HTML file.** jot—not the assistant—captures captions, launches a fresh evaluator, validates its evidence, and creates the page.

## What you need

- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10 or newer.
- An installed, authenticated evaluator: [Codex CLI](https://developers.openai.com/codex/cli) **or** [Claude Code](https://code.claude.com/docs/en/setup). You only need the one you choose.
- A host assistant that can run local commands and deliver files. It does **not** need subagent delegation, and its model can differ from your evaluator.

Evaluation reuses your selected CLI's login; subscription limits or usage charges still apply. No additional jot API key is required. Verified with Codex 0.162.1 and Claude Code 2.1.277.

## Set up once

```bash
git clone https://github.com/kebaux/kbxJOT.git jot
cd jot
uv sync
```

Install your chosen evaluator using its setup guide, then sign in with **one** of:

```bash
codex login
# or, for Claude Code:
claude auth login
```

The first evaluation starts setup automatically. A terminal prompts directly; an assistant checks readiness, asks for your choice, saves it, and continues the original request. You can also run setup yourself:

```bash
uv run jot setup
```

Setup checks installation and authentication, then saves your choice—not credentials. Assistants complete an approved choice with `uv run jot setup --evaluator codex` (or `claude`) without requiring an interactive terminal.

Open your host assistant in the checkout and allow local command and file access. Use `uv run jot` inside the checkout to avoid the unrelated macOS command named `jot`.

### Change the evaluator

| Command | Effect |
|---|---|
| `uv run jot config evaluator` | Show your saved default |
| `uv run jot config evaluator codex` | Save Codex as the default; use `claude` to choose Claude Code |
| `uv run jot 'YOUTUBE_URL' --evaluate --evaluator codex` | Override the default for one evaluation without changing it |

Preferences are saved under `~/.config/jot` (`$XDG_CONFIG_HOME/jot` or `JOT_CONFIG_DIR` can override this). Jot starts onboarding when no default is saved; it never silently chooses or switches providers.

## Create a watch page

Paste this request into your assistant, replacing `YOUTUBE_URL`:

> Create a jot HTML watch page for this video: YOUTUBE_URL. Read `uv run jot --agent-instructions`, run jot's captions-only evaluation workflow, and deliver the actual HTML file or a clickable link I can open. If jot fails, report the diagnostic; do not evaluate the video yourself.

Optionally add your background or learning goal—for example, “I know the basics and want concrete implementation techniques.”

Prefer the terminal? From the project folder, run:

```bash
uv run jot 'YOUTUBE_URL' --captions-only --evaluate
```

jot prints the actual saved HTML path. After a successful evaluation in an interactive terminal, it opens your browser and starts the local viewer automatically. Assistants running locally start the viewer for you and return its clickable URL. Transcripts and evaluation artifacts stay in `./transcripts` by default; `-o DIR` changes the destination.

Evaluation alone can take up to 10 minutes, in addition to caption capture. Allow at least 15 minutes when running jot through an assistant or command runner, and wait for its success path or diagnostic rather than cancelling a slow evaluation.

## Use the result

Share the HTML file alone: the report, transcript, and fonts work offline. A remote assistant must provide a downloadable file, not a path on its own machine.

The local viewer serves only the selected report on `127.0.0.1`. Keep its terminal or assistant-managed process running; Ctrl-C stops a terminal viewer. If the browser does not open, use the printed URL. To reopen a saved report later:

```bash
uv run jot open 'transcripts/talk.evaluation.html'
```

Double-clicked HTML remains readable offline, but YouTube playback requires HTTP and internet. No public hosting or YouTube API key is needed. If the video disallows embedding, use its direct YouTube link.

Takeaways have one **Start** link; watch/skip ranges stay inline. Signal-versus-fluff ratings have no timestamp links or quote rows. Full evidence remains in the saved response JSON and source transcript.

- **English captions unavailable?** The default evaluation workflow stops, even when captions exist in another language. It never silently downloads audio or uses paid transcription.
- **Evaluator or validation failure?** jot stops without publishing a report. The saved transcript remains available. There is no fallback to host-assistant self-evaluation or automatic retry.
- **Timeout, interruption, or excessive evaluator output?** jot stops the evaluator process tree without publishing a report. Evaluation has a 10-minute deadline and an 8 MiB limit on each output stream.

Evaluators run fresh sessions outside your project, without the host conversation. Claude Code is tool-free. **Codex is not tool-free:** it uses a read-only sandbox with restrictive settings; some built-in tools remain exposed.

Local validation checks the full response schema, exact source quotes, timestamps, and ranges—not factual truth, semantic quality, or complete reading. Assessments are speech-only.

## Advanced use

Run `uv run jot --help` for transcript-only capture, explicit audio options, and rendering saved reports. Paid audio transcription requires separate setup and permission; it is not part of default evaluation. Coordinator usage is available through `uv run jot --agent-instructions`.

Refresh an existing report's layout without another evaluation:

```bash
uv run jot --render-html 'transcripts/talk.evaluation.md'
```

This updates the HTML, leaving the saved Markdown and response JSON unchanged.

## Licenses

Code uses the [MIT License](LICENSE). Libron v0.31 uses the [SIL Open Font License 1.1](jot_assets/Libron-LICENSE.txt). Flexoki uses the [MIT license](jot_assets/Flexoki-LICENSE.txt). Third-party notices ship with the package and generated HTML.
