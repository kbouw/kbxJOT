# jot coordinator instructions

Your role is to run jot and deliver its generated HTML watch page. jot owns caption capture, source checks, fresh evaluator invocation, evidence validation, and publication. Do not generate or repair evaluator responses yourself, delegate evaluation through your own agent tools, or invoke manual request/publication steps.

## Procedure

1. Learn the installed interface with `jot --help`. In a repository checkout, use `uv run jot` for every command below. Printing instructions and help needs no evaluator access.
2. Run `jot URL --captions-only --evaluate [-o DIR]` using the user's saved evaluator. If no evaluator is configured, explain `jot setup` for terminal onboarding or `jot config evaluator codex` / `jot config evaluator claude` for an explicit default. A user-requested one-run choice uses `--evaluator codex` or `--evaluator claude`; do not silently choose a provider. Add `--background TEXT` only when the user supplies their knowledge or learning goals; do not insert your own verdict.

   Allow at least 15 minutes for the outer command: the evaluator alone has a 10-minute deadline, separate from caption capture. Wait for jot to finish instead of treating silence as failure. If the user cancels, jot terminates the evaluator process tree; do not start a replacement evaluation yourself.
3. jot requires the selected CLI to be installed and authenticated (`codex login` or `claude auth login`). It starts a fresh noninteractive evaluator outside the project. Claude Code is tool-free with customizations, MCP and session persistence disabled. Codex is ephemeral and read-only with restrictive capabilities and user config/rules ignored, but its CLI may still expose built-in tools; do not describe Codex as tool-free. Credentials and billing remain with the chosen CLI; no additional evaluation API key is required. This does not reuse your conversation or guarantee the evaluator is the same model as your host assistant.
4. If capture, evaluator invocation, or validation fails, stop and return the actual diagnostic. Do not substitute coordinator self-evaluation, bypass validation, or modify evidence to make a response pass. jot does not automatically retry an invalid response. A preserved transcript or request is not a completed report.
5. On success, deliver the actual HTML path printed by jot as an accessible file or clickable link. If running remotely, provide a downloadable file, not a path on your machine. The Markdown companion is optional for the user. Do not open a browser unless requested.

English captions are required by the default workflow. Caption absence, empty captions, or fetch failures stop without downloading audio or calling a transcription provider. Ask the user before paid audio transcription or another transcription service. Explicit audio options are outside the default path.

The saved HTML is self-contained for reading and sharing. Video playback needs internet; if an embedded video is blocked, use the page's direct YouTube link.

## Failure and validation boundary

jot validates the request source before model invocation and validates the structured response against the packaged schema, request ID, exact source quotes and timestamps, viewer-background novelty rules, and evidence-supported ranges. Invalid output cannot be published through the evaluation command. These checks do not establish factual truth, semantic quality, or complete model reading.

Failed request/report writes, including encoding failures, remove newly created incomplete files when filesystem cleanup succeeds. If cleanup fails, diagnostics include both errors. Existing files and successfully saved source transcripts remain intact; an HTML write failure identifies any preserved evaluation Markdown.
