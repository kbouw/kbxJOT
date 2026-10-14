"""Consumer behavior for keyless evaluator packets, evidence gates, and publication."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import hashlib
from html.parser import HTMLParser
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jot
import jot_agent
from jot_html import build_evaluation_html, parse_transcript_document


_URL = "https://www.youtube.com/watch?v=WkBPX-oDMnA"
_BODY = "[00:04] Cache invalidation needs explicit ownership.\n[00:12] A second distinct source segment."
_CATEGORIES = ("substance", "stories", "analogies", "repetition", "marketing", "housekeeping")


class _AnchorParser(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


def _response(request_id, *, timestamp=4, quote="Cache invalidation needs explicit ownership."):
    citation = {"timestamp": timestamp, "quote": quote}
    return {
        "request_id": request_id,
        "verdict": {"decision": "WATCH", "audience": "Engineers responsible for caches",
                    "reason": "The speaker explains a concrete ownership boundary.", "confidence": "moderate"},
        "takeaways": [{"idea": "Assign cache ownership", "why": "Explicit owners reduce stale writes.",
                       "evidence": [copy.deepcopy(citation)]}],
        "novelty": {"assessment": "An application of an established idea", "personal": "unknown",
                    "reason": "Viewer knowledge was not supplied."},
        "signal": {category: {"rating": "not_observed", "reason": "No grounded example identified.", "evidence": []}
                   for category in _CATEGORIES},
        "worth_watching": [] if timestamp is None else [
            {"start": 4, "end": 12, "reason": "The ownership explanation", "evidence": [copy.deepcopy(citation)]}],
        "skippable": [],
        "limits": ["Speech-only assessment; claims have not been independently checked."],
    }


def _resign(packet):
    payload = {key: value for key, value in packet.items() if key != "request_id"}
    packet["request_id"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.source = self.directory / "source.md"
        self.metadata = {"title": "Ownership Talk", "channel": "Test Channel", "duration": 20}
        self.document = jot._build_transcript_markdown(self.metadata, _BODY, _URL, "YouTube captions (manual)")
        self.source.write_bytes(self.document.encode("utf-8"))
        self.request_path = jot_agent.prepare_evaluation(self.source)
        self.packet = json.loads(self.request_path.read_text(encoding="utf-8"))
        self.response_path = self.directory / "response.json"
        self.response = _response(self.packet["request_id"])
        self.output = self.directory / "published"

    def write_packet(self):
        self.request_path.write_text(json.dumps(self.packet, ensure_ascii=False), encoding="utf-8")

    def publish(self):
        self.write_packet()
        self.response_path.write_text(json.dumps(self.response, ensure_ascii=False), encoding="utf-8")
        return jot_agent.publish_evaluation(self.request_path, self.response_path, self.output)

    def rejected(self, cause):
        with self.assertRaisesRegex(ValueError, cause):
            self.publish()
        self.assertFalse(self.output.exists(), "Validation must not even create the output directory")

    def change_source(self, body, duration=20):
        self.document = jot._build_transcript_markdown(
            {**self.metadata, "duration": duration}, body, _URL, "Fixture transcription",
        )
        self.packet["source_document"] = self.document
        _resign(self.packet)
        self.response["request_id"] = self.packet["request_id"]

    def test_complete_packet_digest_and_collision_safe_preparation(self):
        payload = {key: value for key, value in self.packet.items() if key != "request_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(self.packet["request_id"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(self.packet["source_document"], self.document)
        self.assertIsNone(self.packet["viewer_background"])
        self.assertEqual(self.packet["response_schema"]["$schema"], "https://json-schema.org/draft/2020-12/schema")
        original = self.request_path.read_bytes()
        second = jot_agent.prepare_evaluation(self.source, "Known caching concepts")
        self.assertNotEqual(second, self.request_path)
        self.assertEqual(self.request_path.read_bytes(), original)
        self.assertEqual(json.loads(second.read_text())["viewer_background"], "Known caching concepts")

    def test_end_to_end_saved_markdown_and_real_html_match_shared_renderer(self):
        source_bytes = self.source.read_bytes()
        markdown, html = self.publish()
        self.assertEqual(markdown.parent, self.output)
        document = markdown.read_text(encoding="utf-8")
        self.assertEqual(html.read_text(encoding="utf-8"), build_evaluation_html(document))
        self.assertIn("## Watch-value evaluation", document)
        self.assertIn(_BODY, document)
        anchors = _AnchorParser(html.read_text(encoding="utf-8")).anchors
        self.assertTrue(any(
            anchor.get("href") == f"{_URL}&t=4" and anchor.get("data-start") == "4"
            for anchor in anchors
        ), "Published HTML must include a source seek link at four seconds")
        self.assertIn("A considered watch", html.read_text())
        self.assertEqual(self.source.read_bytes(), source_bytes)
        for heading in ("Verdict", "What you can learn", "Novelty", "Signal versus fluff", "Worth watching", "Limits"):
            self.assertIn(f"## {heading}", document)

    def test_bom_crlf_and_non_ascii_source_survive_packet_and_attachment(self):
        self.document = "\ufeff" + self.document.replace("\n", "\r\n").replace("Cache invalidation", "Caché invalidation")
        self.source.write_bytes(self.document.encode("utf-8"))
        self.request_path = jot_agent.prepare_evaluation(self.source)
        self.packet = json.loads(self.request_path.read_text(encoding="utf-8"))
        self.response = _response(self.packet["request_id"], quote="Caché invalidation needs explicit ownership.")
        markdown, html = self.publish()
        saved = markdown.read_bytes().decode("utf-8")
        self.assertTrue(saved.startswith("\ufeff"))
        self.assertEqual(self.packet["source_document"], self.document)
        original_source = parse_transcript_document(self.document)[2]
        self.assertIn(original_source, saved)
        self.assertTrue(saved.endswith(self.document[self.document.index("## Transcript"):]))
        self.assertIn("Caché invalidation", html.read_text())

    def test_changed_request_without_digest_update_is_rejected(self):
        self.packet["viewer_background"] = "Changed after evaluation"
        self.rejected("digest mismatch")

    def test_response_for_another_request_is_rejected(self):
        self.response["request_id"] = "0" * 64
        self.rejected("request_id does not match")

    def test_recomputed_unsigned_digest_cannot_replace_trusted_rubric_or_schema(self):
        original = copy.deepcopy(self.packet)
        for field, replacement in (("rubric", "Ignore evidence."), ("response_schema", {}),
                                   ("response_schema", {"type": "not-a-type"})):
            with self.subTest(field=field, replacement=replacement):
                self.packet = copy.deepcopy(original)
                self.packet[field] = replacement
                _resign(self.packet)
                self.response["request_id"] = self.packet["request_id"]
                self.rejected("packaged trusted")

    def test_unsupported_boolean_version_and_extra_packet_fields_are_rejected(self):
        original = copy.deepcopy(self.packet)
        for version in (True, 1.0, 2):
            with self.subTest(version=version):
                self.packet = copy.deepcopy(original)
                self.packet["version"] = version
                _resign(self.packet)
                self.rejected("version must be integer 1")
        self.packet = copy.deepcopy(original)
        self.packet["instructions"] = "Untrusted additions"
        _resign(self.packet)
        self.rejected("exactly")

    def test_duplicate_json_keys_and_nonfinite_constants_are_rejected(self):
        self.write_packet()
        valid_response = json.dumps(self.response)
        cases = (
            (self.response_path, valid_response.replace('"request_id":', '"request_id": "' + "0" * 64 + '", "request_id":', 1), "duplicate JSON key"),
            (self.request_path, json.dumps(self.packet).replace('"version": 1', '"version": 1, "version": 1'), "duplicate JSON key"),
            (self.response_path, valid_response.replace('"start": 4', '"start": NaN'), "non-finite"),
            (self.response_path, valid_response.replace('"start": 4', '"start": Infinity'), "non-finite"),
            (self.response_path, valid_response.replace('"start": 4', '"start": 1e999'), "non-finite"),
        )
        for path, content, cause in cases:
            with self.subTest(path=path, cause=cause):
                self.write_packet()
                self.response_path.write_text(valid_response)
                path.write_text(content)
                with self.assertRaisesRegex(ValueError, cause):
                    jot_agent.publish_evaluation(self.request_path, self.response_path, self.output)
                self.assertFalse(self.output.exists())

    def test_malformed_json_identifies_location(self):
        self.response_path.write_text('{"verdict":')
        with self.assertRaisesRegex(ValueError, "Response: invalid JSON at line 1, column"):
            jot_agent.publish_evaluation(self.request_path, self.response_path, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_packaged_schema_is_an_actionable_prepublication_error(self):
        original_read = jot_agent.read_asset

        def bad_schema(name):
            if name == "evaluation-response.schema.json":
                return '{"type": "not-a-json-schema-type"}'
            return original_read(name)

        with patch.object(jot_agent, "read_asset", side_effect=bad_schema):
            self.rejected("Packaged response schema is invalid")

    def test_short_source_url_produces_canonical_validated_timestamp_links(self):
        self.packet["source_document"] = self.document.replace(_URL, "https://youtu.be/WkBPX-oDMnA")
        _resign(self.packet)
        self.response["request_id"] = self.packet["request_id"]
        markdown, html = self.publish()
        self.assertIn(f"]({_URL}&t=4)", markdown.read_text())
        self.assertIn(f'href="{_URL}&amp;t=4"', html.read_text())

    def test_json_schema_integral_numbers_render_as_integer_seconds(self):
        self.response["takeaways"][0]["evidence"][0]["timestamp"] = 4.0
        self.response["worth_watching"][0].update(start=4.0, end=12.0)
        self.response["worth_watching"][0]["evidence"][0]["timestamp"] = 4.0
        markdown, _ = self.publish()
        self.assertIn("&t=4)", markdown.read_text())
        self.assertNotIn("&t=4.0", markdown.read_text())

    def test_fabricated_case_changed_whitespace_changed_or_moved_quotes_fail(self):
        original = copy.deepcopy(self.response)
        citations = (
            {"timestamp": 4, "quote": "A fabricated explanation."},
            {"timestamp": 4, "quote": "cache invalidation needs explicit ownership."},
            {"timestamp": 4, "quote": "Cache  invalidation needs explicit ownership."},
            {"timestamp": 12, "quote": "Cache invalidation needs explicit ownership."},
            {"timestamp": None, "quote": "Cache invalidation needs explicit ownership."},
            {"timestamp": 7, "quote": "Cache invalidation needs explicit ownership."},
        )
        for citation in citations:
            with self.subTest(citation=citation):
                self.response = copy.deepcopy(original)
                self.response["takeaways"][0]["evidence"] = [citation]
                self.rejected("exact source span|null citation")

    def test_equal_adjacent_timestamps_keep_both_segments_groundable(self):
        self.change_source("[00:04] First same-second segment.\n[00:04] Second same-second segment.")
        self.response["takeaways"][0]["evidence"] = [
            {"timestamp": 4, "quote": "First same-second segment."},
            {"timestamp": 4, "quote": "Second same-second segment."},
        ]
        self.response["worth_watching"][0]["evidence"] = self.response["takeaways"][0]["evidence"]
        markdown, _ = self.publish()
        self.assertIn("Second same-second segment", markdown.read_text())

    def test_invalid_or_decreasing_source_timestamps_fail_before_publication(self):
        for body in ("[00:60] Cache invalidation needs explicit ownership.",
                     "[01:60:00] Cache invalidation needs explicit ownership.",
                     "[0:04] Cache invalidation needs explicit ownership.",
                     "[00:12] First.\n[00:04] Cache invalidation needs explicit ownership.",
                     "[00:21] Cache invalidation needs explicit ownership."):
            with self.subTest(body=body):
                self.change_source(body)
                self.rejected("invalid timestamp|not ordered|exceeds known Duration")

    def test_out_of_bounds_reversed_and_exclusive_end_ranges_fail(self):
        original = copy.deepcopy(self.response)
        for start, end, cause in ((4, 21, "exceeds known Duration"), (4, 4, "greater than start"),
                                  (12, 4, "greater than start"), (0, 4, r"\[start, end\)"),
                                  (5, 12, r"\[start, end\)")):
            with self.subTest(start=start, end=end):
                self.response = copy.deepcopy(original)
                self.response["worth_watching"][0].update(start=start, end=end)
                self.rejected(cause)

    def test_unknown_duration_does_not_imply_zero_or_full_coverage(self):
        self.change_source(_BODY, duration=None)
        self.response["worth_watching"][0]["end"] = 80
        markdown, html = self.publish()
        self.assertIn("| **Duration** | Unknown |", markdown.read_text())
        self.assertIn("<dd>Unknown</dd>", html.read_text())
        self.assertIn("approximate", markdown.read_text())
        self.assertIn("does not establish full coverage", markdown.read_text())

    def test_measured_zero_duration_is_a_real_bound(self):
        self.change_source("[00:00] Cache invalidation needs explicit ownership.", duration=0)
        self.response = _response(self.packet["request_id"], timestamp=0)
        self.response["worth_watching"][0].update(start=0, end=1)
        self.rejected("exceeds known Duration")

    def test_untimed_evidence_is_grounded_without_fabricating_ranges(self):
        self.change_source("Cache invalidation needs explicit ownership.", duration=None)
        self.response = _response(self.packet["request_id"], timestamp=None)
        markdown, html = self.publish()
        self.assertIn("Untimed source", markdown.read_text())
        self.assertIn("no supported ranges", markdown.read_text())
        self.assertNotIn("&t=", html.read_text())

    def test_untimed_source_rejects_invented_citation_or_range(self):
        self.change_source("Cache invalidation needs explicit ownership.", duration=None)
        self.response = _response(self.packet["request_id"], timestamp=4)
        self.rejected("invent a timestamp")
        self.response = _response(self.packet["request_id"], timestamp=None)
        self.response["worth_watching"] = [{"start": 0, "end": 1, "reason": "Invented timing",
                                            "evidence": [{"timestamp": None, "quote": "Cache invalidation"}]}]
        self.rejected("invent timestamp ranges")

    def test_null_and_blank_background_require_unknown_personal_novelty(self):
        for background in (None, "", " \t "):
            with self.subTest(background=background):
                self.packet["viewer_background"] = background
                _resign(self.packet)
                self.response["request_id"] = self.packet["request_id"]
                self.response["novelty"]["personal"] = "high"
                self.rejected("must be unknown")

    def test_supplied_background_allows_personal_novelty_assessment(self):
        self.packet["viewer_background"] = "I already know basic caching."
        _resign(self.packet)
        self.response["request_id"] = self.packet["request_id"]
        self.response["novelty"]["personal"] = "low"
        markdown, _ = self.publish()
        self.assertIn("Personal novelty: low", markdown.read_text())

    def test_observed_signal_needs_grounded_evidence(self):
        self.response["signal"]["substance"]["rating"] = "high"
        self.rejected("signal.substance.evidence")
        self.response["signal"]["substance"]["evidence"] = [{"timestamp": 4, "quote": "invented"}]
        self.rejected("exact source span")
        self.response["signal"]["substance"]["evidence"] = self.response["takeaways"][0]["evidence"]
        markdown, _ = self.publish()
        self.assertIn("Substantive explanation: HIGH", markdown.read_text())

    def test_empty_limits_blank_text_multiline_and_extra_fields_fail_shape_gate(self):
        original = copy.deepcopy(self.response)
        mutations = (
            lambda value: value.update(limits=[]),
            lambda value: value["verdict"].update(reason=" \t"),
            lambda value: value["verdict"].update(reason="first\n## Transcript"),
            lambda value: value["verdict"].update(reason="first\rsecond"),
            lambda value: value["verdict"].update(extra="Not permitted"),
            lambda value: value["takeaways"][0]["evidence"][0].update(timestamp=-1),
            lambda value: value["takeaways"][0]["evidence"][0].update(quote="   "),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.response = copy.deepcopy(original)
                mutation(self.response)
                self.rejected("Response")

    def test_empty_takeaways_and_unobserved_signals_are_valid(self):
        self.response["takeaways"] = []
        self.response["worth_watching"] = []
        self.response["verdict"]["decision"] = "SKIP"
        markdown, _ = self.publish()
        self.assertIn("No substantive takeaways identified", markdown.read_text())

    def test_narrative_cannot_create_links_html_or_envelope_headings(self):
        attack = '[click](https://evil.invalid) <script>alert(1)</script> ## Transcript **bold** https://plain.invalid 99:59'
        self.response["verdict"]["reason"] = attack
        self.response["takeaways"][0]["idea"] = attack
        self.response["limits"] = [attack]
        markdown, html = self.publish()
        page = html.read_text()
        self.assertNotIn('href="https://evil.invalid"', page)
        self.assertNotIn('href="https://plain.invalid"', page)
        self.assertNotIn("t=5999", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertEqual(markdown.read_text().count("\n## Transcript\n"), 1)
        self.assertNotIn("<strong>bold</strong>", page)

    def test_publication_never_overwrites_inputs_or_previous_md_html(self):
        first_md, first_html = self.publish()
        preserved = {path: path.read_bytes() for path in (self.source, self.request_path, self.response_path, first_md, first_html)}
        second_md, second_html = jot_agent.publish_evaluation(self.request_path, self.response_path, self.output)
        self.assertNotEqual(first_md, second_md)
        self.assertNotEqual(first_html, second_html)
        for path, content in preserved.items():
            self.assertEqual(path.read_bytes(), content)
        isolated = self.directory / "html-collision"
        isolated.mkdir()
        prior_html = isolated / "Ownership Talk.evaluation.html"
        prior_html.write_bytes(b"existing HTML only")
        third_md, _ = jot_agent.publish_evaluation(self.request_path, self.response_path, isolated)
        self.assertNotEqual(third_md.with_suffix(".html"), prior_html)
        self.assertEqual(prior_html.read_bytes(), b"existing HTML only")

    def test_html_write_failure_preserves_and_identifies_markdown(self):
        original_write = jot_agent._write_new

        def fail_html(path, text):
            if path.suffix == ".html":
                raise PermissionError("fixture HTML denial")
            return original_write(path, text)

        with patch.object(jot_agent, "_write_new", side_effect=fail_html):
            with self.assertRaisesRegex(OSError, "fixture HTML denial") as raised:
                self.publish()
        markdown = self.output / "Ownership Talk.evaluation.md"
        self.assertTrue(markdown.exists())
        self.assertIn(f"Markdown preserved at: {markdown}", str(raised.exception))
        self.assertIn(_BODY, markdown.read_text())
        self.assertFalse(markdown.with_suffix(".html").exists())

    def test_render_failure_creates_no_outputs(self):
        with patch.object(jot_agent, "build_evaluation_html", side_effect=ValueError("fixture rendering failure")):
            self.rejected("fixture rendering failure")

    def test_invalid_source_preparation_creates_no_outputs(self):
        self.source.write_text("Not a saved transcript")
        with self.assertRaises(ValueError):
            jot_agent.prepare_evaluation(self.source, output_dir=self.output)
        self.assertFalse(self.output.exists())


class AgentCLITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ, {"OPENROUTER_API_KEY": "configured", "ASSEMBLYAI_API_KEY": "configured"}, clear=True))
        self.env = self.stack.enter_context(patch.object(jot, "load_dotenv"))
        self.download = self.stack.enter_context(patch.object(jot, "_download_audio"))
        self.audio = self.stack.enter_context(patch.object(jot, "_transcribe_assemblyai"))
        self.evaluate = self.stack.enter_context(patch.object(jot, "_evaluate_watch_value"))
        self.metadata = self.stack.enter_context(patch.object(jot, "_get_video_metadata", return_value={
            "title": "CLI Talk", "channel": "Test Channel", "duration": 20,
        }))
        self.captions = self.stack.enter_context(patch.object(jot, "_try_youtube_captions", return_value={
            "method": "YouTube captions (manual)", "segments": [{"start": 4, "text": "Cache invalidation needs explicit ownership."}],
        }))

    def cli(self, arguments):
        output, error = StringIO(), StringIO()
        with patch("sys.argv", ["jot", *arguments]), redirect_stdout(output), redirect_stderr(error):
            try:
                jot.main()
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        return code, output.getvalue(), error.getvalue()

    def assert_no_providers(self):
        self.env.assert_not_called()
        self.metadata.assert_not_called()
        self.captions.assert_not_called()
        self.download.assert_not_called()
        self.audio.assert_not_called()
        self.evaluate.assert_not_called()

    def test_offline_prepare_and_publish_use_real_saved_files_without_config(self):
        source = self.directory / "source.md"
        source.write_text(jot._build_transcript_markdown(
            {"title": "CLI Talk", "channel": "Test", "duration": None}, _BODY, _URL, "manual captions",
        ))
        code, output, error = self.cli(["--prepare-evaluation", str(source), "--background", "Basic caching"])
        self.assertEqual(code, 0, error)
        request = source.with_suffix(".request.json")
        self.assertIn(str(request), output)
        report = self.directory / "response.json"
        report.write_text(json.dumps(_response(json.loads(request.read_text())["request_id"])))
        code, output, error = self.cli(["--publish-evaluation", str(request), "--report", str(report)])
        self.assertEqual(code, 0, error)
        markdown = self.directory / "CLI Talk.evaluation.md"
        self.assertIn(str(markdown), output)
        self.assertIn(str(markdown.with_suffix(".html")), output)
        self.assertTrue(markdown.exists())
        self.assertTrue(markdown.with_suffix(".html").exists())
        self.assert_no_providers()

    def test_mixed_actions_and_unrelated_flags_fail_before_config(self):
        invalid = (
            ["--agent-instructions", "--output", "elsewhere"],
            ["--agent-instructions", _URL],
            ["--agent-instructions", "--prepare-evaluation", "source.md"],
            ["--prepare-evaluation", "source.md", "--evaluate"],
            ["--prepare-evaluation", "source.md", "--captions-only"],
            ["--prepare-evaluation", "source.md", "--diarize"],
            ["--prepare-evaluation", "source.md", "--skip-captions"],
            ["--prepare-evaluation", "source.md", "--render-html", "saved.md"],
            ["--publish-evaluation", "request.json"],
            ["--publish-evaluation", "request.json", "--report", "response.json", "--background", "context"],
            ["--publish-evaluation", "request.json", "--report", "response.json", "--evaluate"],
            ["--report", "response.json"],
            [_URL, "--report", "response.json"],
            [_URL, "--background", "context"],
            [_URL, "--captions-only", "--diarize"],
            [_URL, "--captions-only", "--skip-captions"],
            ["--captions-only"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                code, _, error = self.cli(arguments)
                self.assertEqual(code, 2)
                self.assertIn("error:", error)
                self.assert_no_providers()

    def test_caption_failure_empty_and_missing_never_use_paid_fallback_with_keys(self):
        scenarios = (
            (None, None, "No English captions"),
            ({"method": "manual", "segments": [{"start": 0, "text": "  "}]}, None, "no speech"),
            (None, RuntimeError("fixture blocked captions"), "fixture blocked captions"),
        )
        for captions, failure, cause in scenarios:
            with self.subTest(cause=cause):
                self.captions.return_value = captions
                self.captions.side_effect = failure
                code, _, error = self.cli([_URL, "--captions-only", "-o", str(self.directory)])
                self.assertEqual(code, 1)
                self.assertIn(cause, error)
                self.assertIn("No audio fallback permitted", error)
                self.download.assert_not_called()
                self.audio.assert_not_called()
                self.evaluate.assert_not_called()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_captions_only_saves_provenance_without_model_evaluation(self):
        code, _, error = self.cli([_URL, "--captions-only", "-o", str(self.directory)])
        self.assertEqual(code, 0, error)
        source = self.directory / "CLI Talk.md"
        document = source.read_text()
        self.assertIn("[00:04] Cache invalidation needs explicit ownership.", document)
        self.assertIn("YouTube captions (manual)", document)
        self.assertNotIn("Watch-value evaluation", document)
        self.download.assert_not_called()
        self.audio.assert_not_called()
        self.evaluate.assert_not_called()

    def test_explicit_evaluate_can_combine_with_captions_only(self):
        self.evaluate.return_value = "## Verdict\n\nWATCH\n\n## Limits\n\nSpeech only."
        code, _, error = self.cli([_URL, "--captions-only", "--evaluate", "-o", str(self.directory)])
        self.assertEqual(code, 0, error)
        self.evaluate.assert_called_once()
        self.download.assert_not_called()
        self.audio.assert_not_called()
        self.assertTrue((self.directory / "CLI Talk.evaluation.html").exists())

    def test_offline_html_failure_exits_nonzero_and_names_preserved_markdown(self):
        source = self.directory / "source.md"
        source.write_text(jot._build_transcript_markdown(
            {"title": "Partial Talk", "channel": "Test", "duration": None}, _BODY, _URL, "manual captions",
        ))
        request = jot_agent.prepare_evaluation(source)
        report = self.directory / "response.json"
        report.write_text(json.dumps(_response(json.loads(request.read_text())["request_id"])))
        original_write = jot_agent._write_new

        def fail_html(path, text):
            if path.suffix == ".html":
                raise PermissionError("fixture publication denial")
            return original_write(path, text)

        with patch.object(jot_agent, "_write_new", side_effect=fail_html):
            code, _, error = self.cli(["--publish-evaluation", str(request), "--report", str(report)])
        markdown = self.directory / "Partial Talk.evaluation.md"
        self.assertEqual(code, 2)
        self.assertTrue(markdown.exists())
        self.assertFalse(markdown.with_suffix(".html").exists())
        self.assertIn(f"Markdown preserved at: {markdown}", error)
        self.assertIn("fixture publication denial", error)
        self.assert_no_providers()

    def test_offline_failure_has_actionable_cause_without_provider_use(self):
        code, _, error = self.cli(["--prepare-evaluation", str(self.directory / "missing.md")])
        self.assertEqual(code, 2)
        self.assertIn("Cannot prepare evaluation", error)
        self.assertIn("missing.md", error)
        self.assert_no_providers()


if __name__ == "__main__":
    unittest.main()
