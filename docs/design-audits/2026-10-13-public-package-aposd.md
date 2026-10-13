# Design Audit — jot public package

**Date:** 2026-10-13  
**Method:** APoSD principle catalogue and all 17 red flags; two independent cold reviewers; parent validation of every retained defect candidate.  
**Auditor:** CLI/provider reviewer, renderer/format reviewer, integrating assistant.  
**Scope:** Current working-tree/staged public-package design: `jot.py`, `jot_html.py`, both test modules, README, package/lock/config/ignore declarations. Approximately 1,800 hand-authored lines including tests and docs, plus the generated lockfile. This is a current-design audit, not an attribution of every issue to the most recent patch.
**Baseline:** `main`, HEAD `13a20122713c3aea056e40888c7d3658b9ec9e7e`; implementation changes are staged but not committed. Source snapshots were `jot.py#EC5A` and `jot_html.py#A7F7`.  
**Excluded:** Vendored dependencies as audit targets, binary fonts, license boilerplate/ownership investigation, private `.env`, archived personal reports, removed product features, and accepted visual-design choices. Installed dependency code was consulted only to establish adapter contracts.  
**Follow-up:** Remediation is complete as of 2026-10-13; see [Remediation status](#remediation-status). The original audit observations below are preserved as historical evidence. No implementation changes, commits, paid provider requests, or permanent tests were made during the original audit.

## Summary

The cleanup substantially reduced the package's interface and incidental complexity. The two main modules are useful, cohesive owners: a CLI/provider workflow and a deep portable renderer. Their length is not evidence that they should be split.

Four **present behavioral defects** remain at data/error boundaries: caption-fetch failures collapse into absence, missing duration becomes a false zero, manual-caption provenance is discarded, and ordinary transcription can publish an empty result as success. All four were reproduced with deterministic boundary fixtures. Three secondary findings concern the saved-document protocol, partial output publication, and undocumented adapter contracts. They do not justify a provider framework, options bag, serializer hierarchy, or generic transaction abstraction.

The existing 16 tests pass. That result does not cover the failing provider/error scenarios below.

### Module map and depth

| Boundary | Owned knowledge and functionality | Assessment |
|---|---|---|
| `jot.main()` | CLI actions, incompatible combinations, source validation, environment-file precedence, offline rendering dispatch | Small user interface; rejection occurs before provider work. |
| `jot.cmd_transcribe()` | Caption/audio selection, optional evaluation, output naming, publication | Cohesive workflow owner. Keep orchestration here rather than splitting by execution phase. |
| Caption/audio adapters | Provider selection, SDK result conversion, timestamps, speaker labels | Meaningful translation rather than pass-through wrappers, but failure/provenance contracts lose information. |
| `_evaluate_watch_value()` | OpenRouter request/prompt and completion checks | Hides provider protocol; empty/incomplete responses fail visibly. |
| `_build_transcript_markdown()` / `_parse_document()` | Saved document's title, metadata table, headings and separators | Writer and reader intentionally differ, but their shared envelope lacks one explicit contract. |
| `build_evaluation_html(document)` | Parsing, safe Markdown, ordered presentation, seek links, embedded fonts/notices, browser state | Deep interface: one document in, one portable page out. No renderer configuration needed. |
| Renderer-local CSS/JS/template | Classes, IDs, timestamp attributes, theme and player dispatch | One cohesive presentation owner. Separating strings into more files would not itself reduce knowledge leakage. |
| Packaging | Source/assets/licenses needed for distribution | Explicit allowlists are appropriate; not grounds for a custom packaging abstraction. |

## High-priority findings

All priorities below are **P2**: concrete behavior to correct before treating the package as finished for public consumption. These are separate from discretionary refactoring.

### H1 — Error masking — DEFECT: Caption fetch failures disappear as caption absence

**Location:** `jot.py:93–113`, especially `100–107`; consumer at `369–380`.  
**Evidence:** The inner `except Exception: pass` blocks cover both transcript selection and its subsequent `fetch()`. A selected manual transcript whose fetch fails is discarded. If generated selection is absent, the helper returns `None` without retaining the fetch error.

**Runtime observation:** With the actual installed `youtube-transcript-api==1.2.4` `Transcript` and `TranscriptList` classes, a manual transcript URL requiring a PO token made `Transcript.fetch()` raise `PoTokenRequired`. Generated selection raised the real SDK `NoTranscriptFound`. `_try_youtube_captions()` returned `None`, with `stderr == ""`. No HTTP request was made. The command's `None` branch prints `No English captions available` and selects its audio fallback.

**Consumer impact:** A service/fetch/parser failure is indistinguishable from unavailable captions. The user loses an actionable cause before an audio path that may incur charges if configured. This probe establishes error loss and fallback selection semantics, **not that any user was charged**.

**Complexity symptom:** Unknown unknowns: the caller cannot distinguish absence from failure because the adapter removes information it would act on.

**Minimal fix:** Separate selection absence from fetching. Catch the SDK's existing `NoTranscriptFound` where absence is expected; preserve fetch exceptions and their diagnostics at the command boundary while retaining the accepted fallback policy. Do not add retries, another billing toggle, or a provider abstraction.

**Calibration:** The platform already has the needed absence exception. No helper extraction or new parameter is necessary.

### H2 — Nonobvious recovery semantics — DEFECT: Unknown duration is published as zero

**Location:** `jot.py:80–90`, `217–218`, `347–353`; prompt expectation at `277`.  
**Evidence:** Missing extraction duration defaults to `0`, and metadata-extraction failure installs `duration: 0`. Both the display and saved-document serializer format it as `00:00`. The same dictionary goes to the evaluator even though the prompt explicitly says unknown duration is not zero runtime.

**Runtime observation:** An injected metadata-extraction exception, followed by successful caption recovery, produced an evaluation Markdown/HTML pair with `| **Duration** | 00:00 |`. The evaluation boundary received `{"duration": 0, ...}`. The original metadata error was printed, but the recovered output still asserted a false zero duration.

A separate injected `duration: None` caused `TypeError: unsupported operand type(s) for //: 'NoneType' and 'int'` before output. This demonstrates the nullable-value path; the frequency of such values from live YouTube metadata was **not measured**.

**Consumer impact:** Readers and the evaluation receive misleading runtime metadata. An unnormalized null can prevent otherwise recoverable transcription.

**Complexity symptom:** Unknown unknowns/nonobvious code: recovery substitutes a successful-looking measurement for absence.

**Minimal fix:** Normalize unavailable duration as an explicit unknown value at metadata acquisition and recovery. Publish `Unknown` in the existing duration display/serialization locations and retain the unknown value in evaluation metadata. Keep the numeric timestamp formatter numeric; do not make all speech timestamps silently accept missing values.

**Calibration:** Decide the absence representation once. No configurable unknown-label option or general metadata class is justified.

### H3 — Information discarded across an adapter — DEFECT: Manual captions are labeled auto-generated

**Location:** `jot.py:93–110`, `367–368`.  
**Evidence:** The caption adapter prefers manually authored English captions, then returns only `to_raw_data()`. The selected transcript's `is_generated` property is discarded. The command unconditionally assigns `YouTube captions (auto-generated)`.

**Runtime observation:** A real SDK manual transcript fixture (`is_generated=False`) was selected and parsed successfully. The completed evaluation output saved `| **Method** | YouTube captions (auto-generated) |`. A generated-only control fixture saved the same label, correctly for that control.

**Consumer impact:** The saved evidence's provenance is inaccurate. Manual versus generated speech affects how a reader interprets transcription uncertainty.

**Complexity symptom:** Information leakage and nonobvious code: the adapter knows the fact, discards it, and the caller invents a conflicting value.

**Minimal fix:** Preserve the selected caption method alongside its segments, with the adapter owning the provenance label. Publish that returned fact rather than assuming a type in the command.

**Calibration:** Provenance is a returned fact, not a configurable flag. Reuse the existing adapter and caller; no provider hierarchy or options bag.

### H4 — Drifted content-validity decisions — DEFECT: Empty transcription can be saved as success

**Location:** `jot.py:116–121`, `365–373`, `383–385`, `418–425`; evaluation guard at `287–288`.  
**Evidence:** A nonempty segment list passes the caption-selection gate, but whitespace-only caption text is filtered by conversion. The resulting `""` bypasses fallback because the command checks only `transcript_body is None`. Ordinary transcription then publishes the empty body. The evaluation helper separately rejects empty speech.

**Runtime observation:** Supplying `[{'start': 0, 'text': '   '}]` at the caption boundary made `cmd_transcribe(..., evaluate=False)` return normally, save `Audit Talk.md` with an empty `## Transcript`, and print its successful saved-path message.

**Consumer impact:** A successful-looking artifact can contain no speech. The recorded edge fixture establishes the callable boundary behavior, **not the incidence of all-empty captions from live providers**. The final audio-conversion path also has no command-level nonempty gate; that additional path was inspected statically, not independently fault-injected.

**Complexity symptom:** Unknown unknowns/information leakage: segment-list existence and usable speech are treated as equivalent in one path, but not in evaluation.

**Minimal fix:** Define usable transcription at the command boundary after conversion. Empty converted captions should use the already supported fallback; empty final audio output should fail clearly instead of being published as success.

**Calibration:** This is one validity decision in its existing owner, not a new validator framework or provider-specific special case.

## Secondary findings

### S1 — Information leakage: The saved-Markdown envelope has no explicit shared contract

**Location:** Producer at `jot.py:214–239`; report insertion at `413–417`; parser at `jot_html.py:178–231`; fixture at `tests/test_jot_html.py:84–106`.  
**Evidence:** Producer, parser and test fixture independently encode metadata labels, literal headings and separator structure. The command adds the evaluation with string replacement. The parser additionally knows required fields, accepted Source spellings and the final-Transcript rule.

**Symptom:** Change amplification and potentially undiscoverable reader dependencies. **No current disagreement in that envelope was demonstrated.**

**Minimal fix:** Document the envelope once near its parser and reference it from the existing producer. Let the existing serializer accept the actual optional report and emit the complete document, eliminating command-level string surgery. Add a behavioral producer-to-real-renderer test for retained metadata, evaluation, source destination and transcript; retain hand-authored fixtures for malformed-input tests.

**Calibration:** An optional report has one real non-default caller: today's evaluated command path. Do not introduce a document codec, plugin system, static-copy test, or serializer/parser wrappers. A mocked renderer-forwarding test would not test this contract.

### S2 — Unclear publication/error ownership: A failed companion write leaves an undisclosed partial result

**Location:** `jot.py:418–425`.  
**Evidence:** Both documents are prepared before publication, which is good. Markdown is written first; HTML is written second without command-owned handling of the second write's failure.

**Runtime observation:** A fault injected only into `.html` `Path.write_text()` raised `PermissionError`. The Markdown file remained, the HTML file was absent, and neither final saved-path message was printed. The probe caught the exception only to record it; the command has no corresponding handler.

**Symptom:** The command's simple operation interface exports partial-completion semantics that are not documented.

**Minimal fix:** Choose and document a bounded policy in this command. The conservative policy is to preserve the successfully saved Markdown and report its path plus the companion failure clearly. If all-or-nothing publication is chosen instead, stage/clean up only this invocation's files. Do not invent a generic transaction helper, crash-proof multi-file atomicity, or concurrency guarantees without a requirement.

**Calibration:** Preserve useful paid-for evidence unless a stronger publication contract is explicitly chosen. This finding is error-policy debt, not a claim that the documented provider-error gate promised atomic filesystem writes.

### S3 — Hard-to-describe informal interface: Audio adapters hide shape, unit and failure contracts

**Location:** `jot.py:150–180`, `183–211`, consumer at `376–385`.  
**Evidence:** The adapter returns conditional `utterances`/`words`/`text` dictionaries, changes SDK-global credentials, and exits for some errors while other errors propagate. The formatter interprets audio starts/ends as milliseconds (`/1000`), unlike caption starts in seconds. Those material contracts are not stated in interface comments. The command checks an absent AssemblyAI key only after downloading audio.

**Symptom:** Cognitive load and nonlocal preconditions: callers must read both implementations to understand result shapes and termination behavior.

**Minimal fix:** Add concise contracts for time units, optional shapes, empty output and error ownership. Check required audio configuration before unnecessary downloading. When changing behavior, aggregate expected operational failures at the existing command boundary rather than adding catch-all blocks to every helper.

**Calibration:** One existing consumer does not justify a result-class hierarchy or generic provider context. This is not a demand to merge unrelated adapters or catch programming errors as recoverable failures.

## What's working

- `build_evaluation_html(document)` is a deep interface hiding sanitization, token adaptation, ordering, fonts, licensing and browser behavior. Its size is not a splitting criterion.
- The CLI driver owns a complete, useful workflow. A temporal fetch/transform/store class chain would add interfaces without hiding more knowledge.
- One strict YouTube URL parser serves CLI source validation and rendered source/seek identity. Its shared private role merits documentation, not another parser copy.
- Caption formatting is deterministic and preserves original wording and timestamps; no model-mediated repair or filler deletion is needed for evidence.
- OpenRouter evaluation is opt-in, sends source separately from system instructions, and rejects empty/incomplete responses. Preserve these protections.
- HTML generation happens before either evaluation output is written, so a renderer rejection cannot leave a misleading evaluation file.
- Seek links work without JavaScript and modified clicks remain ordinary links. Player status says “Seek requested”, not falsely confirmed playback.
- Theme-storage failure is legitimately recoverable: the in-memory control still works. Current comments explain that decision.
- Display order deliberately differs from saved evaluator order; comments and README record it. Preserve that rationale.
- Tests cover meaningful URL, escaping, timestamp, section-order, text-preservation and CLI-rejection boundaries. Release allowlists contain the runtime font/notices and exclude personal outputs.

## Suggested refactoring order

This is an ordering of findings, not an implementation plan.

1. **H1:** Preserve caption fetch diagnostics while keeping accepted fallback semantics.
2. **H2 and H3:** Correct metadata/provenance at the existing acquisition boundaries; protect them with consumer-visible cases.
3. **H4:** Establish one nonempty-output rule after each acquisition path; test empty caption/audio boundaries.
4. **S2:** State and handle the companion-write failure policy without discarding useful evidence unintentionally.
5. **S1:** Document the saved envelope, remove string surgery locally, and exercise producer-to-consumer behavior.
6. **S3:** Clarify units/error contracts alongside changes to those adapters, rather than as a broad rename/refactor project.

Every recommended change names an existing owner. No speculative configuration, new provider services, generic serialization framework, wrapper hierarchy, or formatting redesign is proposed.

## Verification and rejected candidates

### Commands run in this audit

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 uv run --locked jot --help
PYTHONDONTWRITEBYTECODE=1 uv run --locked jot --render-html '/Users/kbouw/.local/share/jot/saved-reports-20261013-1830/WkBPX-oDMnA/Understanding is the new bottleneck — Geoffrey Litt, Notion.evaluation.md' -o /tmp/jot-design-audit-smoke-20261013
PYTHONDONTWRITEBYTECODE=1 uv run --locked python /tmp/jot-aposd-probes-20261013.py
```

- Full suite: **16 tests, OK, exit 0**.
- Actual CLI help and actual offline rendering: **exit 0**, HTML output produced.
- Boundary/fault probe: **exit 0**; expected command/helper failures were captured as observations, not suppressed in production.
- Probes used real installed SDK transcript-selection/fetch/parsing classes with controlled responses, isolated temporary files, and provider/filesystem fault injection. They did not make live YouTube, OpenRouter or AssemblyAI requests, inspect private `.env`, or incur transcription/evaluation charges.
- The temporary probe script and smoke page were removed after evidence collection. Input scenarios and observations are recorded above; no permanent test/mock layer was added by this audit.

### Rejected: Source metadata URL is not normalized

A reviewer initially reported that angle-bracket/Markdown-link Source spelling leaked into its anchor destination. That was false: `jot_html.py:230` already assigns `metadata['Source'] = source` before returning.

Actual producer output was rendered for `<https://youtu.be/WkBPX-oDMnA>`, the plain URL, and `[source](https://youtu.be/WkBPX-oDMnA)`. All three parsed to the same normalized Source and produced the matching canonical metadata href. This candidate was withdrawn and is not counted as a defect.

### Dependency contracts checked

`uv.lock` pins `youtube-transcript-api 1.2.4`, `yt-dlp 2026.6.9`, `assemblyai 0.64.21`, `httpx 0.28.1`, and `markdown-it-py 4.2.0` in the audited environment. The installed transcript SDK source distinguishes `NoTranscriptFound` during selection from failures during fetching, and retains `is_generated` on both selected/fetched objects. The [v1.2.4 public documentation](https://github.com/jdepoix/youtube-transcript-api/blob/v1.2.4/README.md#list-available-transcripts) also documents that provenance and manual/generated selection.

No live-provider frequency is inferred from injected null/empty/failure cases. This audit is not a browser visual/accessibility sweep, legal ownership audit, or public-registry availability check.

## Audit checklist

- [x] Module map built; current source and informal interfaces reviewed.
- [x] Required principle catalogue and all 17 red flags read by the audit reviewers.
- [x] Depth assessed; cohesive driver and deep renderer retained.
- [x] Format, provenance, unit, URL and failure knowledge traced across owners.
- [x] Duplication/drift checked: H3 and H4 show concrete data/validity disagreement; S1 is intact coupling, not a fabricated drift defect.
- [x] Prior rationale respected: raw evidence, saved/display order distinction, opt-in evaluation, progressive enhancement, theme-storage recovery, and accepted product/UI scope.
- [x] Framework-dependent claims checked against pinned installed SDK behavior and documentation; unmeasured live triggers identified.
- [x] Present defects separated from design debt and prioritized independently of refactoring.
- [x] Proposed remedies passed speculative-parameter/premature-abstraction calibration and build-versus-use gates.
- [x] Error families reviewed: arguments/configuration, metadata, caption selection/fetch, audio extraction, audio service/status/shape, empty speech, evaluation HTTP/JSON/completion, saved parsing, directory/file publication, and offline render.
- [x] Comments/names reviewed for material informal contracts, not taste-only renaming.
- [x] All retained defects reproduced; secondary claims verified by code or fault probe.
- [x] False Source-link candidate rejected after checking current code and runtime output.
- [x] Report persisted with stable H1–H4 and S1–S3 identifiers.
- [x] Single audit covers the current small package; phased programme is not applicable.

### All 17 flags

| # | Flag | Retained conclusion |
|---|---|---|
| 1 | Shallow module | No material issue; tiny helpers hide concrete policies, renderer is deep. |
| 2 | Information leakage | H2/H3/H4 and S1: metadata/provenance/validity and saved-envelope knowledge. |
| 3 | Temporal decomposition | No structural finding; do not split the driver by workflow stage. |
| 4 | Overexposure | Current flags have actual behavior; renderer has no mandatory presentation knobs. |
| 5 | Pass-through method | No redundant forwarding layer found. |
| 6 | Pass-through variable | Values are consumed rather than threaded unused through long chains. |
| 7 | Repetition | S1 envelope rules; explicit release manifests do not justify a new abstraction. |
| 8 | Special/general mixture | Watch-specific presentation belongs in this specialized renderer. |
| 9 | Conjoined methods | S3 informal adapter/formatter contract needs clarity, not automatic merging. |
| 10 | Comment repeats code | No material independent finding; section banners aid navigation. |
| 11 | Implementation-contaminated interface | No material independent finding; absent contracts matter more. |
| 12 | Vague name | Dictionary/tuple shapes require local reading; no taste-only rename/type proposal. |
| 13 | Hard to pick name | No evidence warrants a separate responsibility/naming finding. |
| 14 | Hard to describe | S3 mixed failure/result semantics enlarge the informal interface. |
| 15 | Nonobvious code | H1–H4 and S2 behavior differs from a reader's successful-operation expectation. |
| 16 | Speculative parameter | No invented knobs; S1's optional actual report has one existing non-default caller. |
| 17 | Premature abstraction | No new helper hierarchy/framework; remedies stay in existing owners. |

## Remediation status

Completed 2026-10-13 in the existing modules; no provider framework, new CLI
configuration, retry policy, transaction abstraction, or visual redesign.

| Finding | Resolution | Verification |
|---|---|---|
| H1 | Selection absence is distinct from fetch failure; the command retains the fetch cause before audio fallback. | Real SDK PO-token error regression; actual CLI diagnostic/missing-audio-key smoke. |
| H2 | Missing/null duration remains unknown in metadata, terminal/Markdown/HTML and model input; measured zero remains zero. | Missing/null/recovery/zero regressions; actual CLI metadata-null smoke. |
| H3 | Caption adapter returns selected segments and truthful manual/generated provenance. | Both SDK transcript types checked through saved Markdown and HTML. |
| H4 | Empty converted captions use fallback; empty final audio fails before evaluation/publication. Blank timed speech records do not manufacture a transcript. | Caption recovery and four empty audio shapes; no model request or output for empty final speech. |
| S1 | Existing serializer accepts the actual optional report and owns the complete envelope; parser documents the shared contract. | Real producer-to-renderer test, including Unknown duration and an inner Transcript heading. |
| S2 | Markdown is announced after it saves; a companion failure exits 1, preserves the Markdown and identifies its path. | Regression plus actual filesystem permission-denial CLI smoke. |
| S3 | Audio SDK adapter raises instead of exiting, documents shape/millisecond contracts, and the command checks configuration before downloading and owns fatal diagnostics. | SDK error/missing-key regressions, millisecond/speaker/untimed format checks, command failure cases. |

Verification after implementation:

- `PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest discover -s tests -v`:
  **29 tests, OK, exit 0**.
- Actual `jot.main()` smoke with controlled provider responses: success created both
  files with manual provenance and Unknown duration; a real filesystem permission
  denial exited 1 while preserving/reporting Markdown; caption retrieval failure
  retained its cause and missing audio configuration stopped before downloading.
- Independent post-fix review: **no findings**. Review was static; runtime proof is
  the suite and CLI smoke above.
- No live OpenRouter/AssemblyAI requests or paid transcription/evaluation calls.
- Fresh wheel/sdist built successfully; exact archive allowlists contain 15/17
  files. Isolated installed `jot --help` exited 0 and offline rendering of all
  three saved evaluations matched the accepted HTML byte-for-byte, preserving
  the existing font, layout and Flexoki theme.
