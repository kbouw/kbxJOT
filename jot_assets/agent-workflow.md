# jot coordinator instructions

Your role is to run jot and deliver its generated HTML watch page. jot owns caption capture, source checks, fresh evaluator invocation, evidence validation, and publication. Do not generate or repair evaluator responses yourself, delegate evaluation through your own agent tools, or invoke manual request/publication steps.

## Procedure

1. Learn the installed interface with `jot --help`. In a repository checkout, use `uv run jot` for every command below. Printing instructions and help needs no evaluator access.
2. Run `jot URL --captions-only --evaluate [-o DIR]` without a PTY, capturing stdin/stdout so generation exits after publication rather than entering the interactive viewer. First-run setup starts automatically. In a noninteractive run, an exit code of 2 with JSON `status: "setup_required"` is an onboarding request, not a failed video evaluation:

   - Use the returned evaluator installation/authentication status to ask the user which default they want. If only one is ready, offer it for confirmation. Do not silently infer a provider from your own harness.
   - Run `jot setup --evaluator NAME` yourself with their choice. Do not hand the user a menu of setup commands to type. If installation or sign-in is needed, facilitate those steps with your tools; the user must complete any required browser/account approval, and credentials must remain with the provider CLI.
   - After setup succeeds, rerun the original video command with its original options and deliver the generated HTML. No evaluator or capture ran before this onboarding request, so continuing is not a retry of a failed evaluation.

   A user-requested one-run choice can use `--evaluator codex` or `--evaluator claude` without changing the saved default. Add `--background TEXT` only when the user supplies their knowledge or learning goals; do not insert your own verdict.

   Allow at least 15 minutes for the outer command: the evaluator alone has a 10-minute deadline, separate from caption capture. Wait for jot to finish instead of treating silence as failure. If the user cancels, jot terminates the evaluator process tree; do not start a replacement evaluation yourself.
3. jot requires the selected CLI to be installed and authenticated (`codex login` or `claude auth login`). It starts a fresh noninteractive evaluator outside the project. Claude Code is tool-free with customizations, MCP and session persistence disabled. Codex is ephemeral and read-only with restrictive capabilities and user config/rules ignored, but its CLI may still expose built-in tools; do not describe Codex as tool-free. Credentials and billing remain with the chosen CLI; no additional evaluation API key is required. This does not reuse your conversation or guarantee the evaluator is the same model as your host assistant.
4. If capture, evaluator invocation, or validation fails, stop and return the actual diagnostic. Do not substitute coordinator self-evaluation, bypass validation, or modify evidence to make a response pass. jot does not automatically retry an invalid response. A preserved transcript or request is not a completed report.
5. On success on the user's local machine, start `jot open FILE` yourself with the actual HTML path printed by jot. Use your managed background-process or persistent-terminal tool, with no execution timeout, and keep the viewer alive. Wait for its printed report URL and confirm it responds before delivering that clickable URL as the primary result, plus the saved HTML path. Do not ask the user to run the viewer or confirm opening the browser. Evaluation is already complete; do not wait for the viewer process to exit or rerun evaluation when it stays running. If the viewer fails to start, report that diagnostic and preserve the generated HTML.

English captions are required by the default workflow. Caption absence, empty captions, or fetch failures stop without downloading audio or calling a transcription provider. Ask the user before paid audio transcription or another transcription service. Explicit audio options are outside the default path.

Successful evaluation in an interactive terminal opens the browser and serves the report automatically until Ctrl-C. Noninteractive evaluation exits after publication so you can start its viewer as described above. The viewer serves only the selected report on loopback HTTP; playback needs internet and an embeddable video. If running remotely, do not launch a browser on your machine or deliver your localhost URL: provide a downloadable HTML file instead. The Markdown companion is optional. A double-clicked HTML file remains readable offline; playback on another machine requires its own local viewer, or the direct YouTube link.

## Failure and validation boundary

jot validates the request source before model invocation and validates the structured response against the packaged schema, request ID, exact source quotes and timestamps, viewer-background novelty rules, and evidence-supported ranges. Invalid output cannot be published through the evaluation command. These checks do not establish factual truth, semantic quality, or complete model reading.

Failed request/report writes, including encoding failures, remove newly created incomplete files when filesystem cleanup succeeds. If cleanup fails, diagnostics include both errors. Existing files and successfully saved source transcripts remain intact; an HTML write failure identifies any preserved evaluation Markdown.
