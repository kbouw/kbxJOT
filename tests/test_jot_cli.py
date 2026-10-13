"""Offline boundaries for the YouTube-only command line."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import httpx
from requests import Response, Session
from youtube_transcript_api import PoTokenRequired, Transcript, TranscriptList

import jot


class CLITests(unittest.TestCase):
    def assert_rejected(self, arguments):
        with patch("sys.argv", ["jot", *arguments]), \
                patch.object(jot, "cmd_transcribe") as transcribe, \
                patch.object(jot, "load_dotenv") as load_env, \
                redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                jot.main()
            self.assertEqual(raised.exception.code, 2)
            transcribe.assert_not_called()
            load_env.assert_not_called()

    def test_rejects_non_youtube_sources_before_transcription(self):
        for source in (
            "A book title",
            "https://example.com/watch?v=WkBPX-oDMnA",
            "https://youtube.com.example.com/watch?v=WkBPX-oDMnA",
            "https://youtu.be/WkBPX-oDMnAextra",
            "https://www.youtube.com/watch?v=short",
        ):
            with self.subTest(source=source):
                self.assert_rejected([source])

    def test_requires_source_for_transcription_actions(self):
        for arguments in (["--evaluate"], ["--diarize"], ["--skip-captions"],
                          ["--output", "elsewhere"]):
            with self.subTest(arguments=arguments):
                self.assert_rejected(arguments)

    def test_background_requires_evaluation(self):
        self.assert_rejected(["https://youtu.be/WkBPX-oDMnA", "--background", "Known concepts"])

    def test_offline_render_rejects_transcription_actions(self):
        for action in (
            ["https://youtu.be/WkBPX-oDMnA"], ["--evaluate"],
            ["--diarize"], ["--skip-captions"], ["--background", ""],
        ):
            with self.subTest(action=action):
                self.assert_rejected(["--render-html", "missing.md", *action])


    def test_caption_format_keeps_every_timestamp_and_original_wording(self):
        self.assertEqual(jot._captions_to_markdown([
            {"start": 4.8, "text": "um,  original wording"},
            {"start": 65, "text": "[Music] and a mispeling"},
            {"start": 3601, "text": "  keep edge spaces  "},
            {"start": 3602, "text": "  "},
        ]), "[00:04] um,  original wording\n[01:05] [Music] and a mispeling\n[01:00:01]   keep edge spaces  ")


_WORKFLOW_METADATA = {"title": "Workflow Talk", "channel": "Test Channel", "duration": 78}
_WORKFLOW_REPORT = "## Verdict\n\n**WATCH** — Concrete evidence at [00:04].\n\n## Limits\n\nFixture speech only."


def _caption_tracks(*, generated=False, empty=False, blocked=False):
    response = Response()
    response.status_code = 200
    response.encoding = "utf-8"
    speech = "   " if empty else "um, original evidence"
    response._content = f'<transcript><text start="4.5" dur="1">{speech}</text></transcript>'.encode()
    session = Mock(spec=Session)
    session.get.return_value = response
    url = "https://fixture.invalid/captions?a=1" + ("&exp=xpe" if blocked else "")
    track = Transcript(session, "WkBPX-oDMnA", url, "English", "en", generated, [])
    return TranscriptList(
        "WkBPX-oDMnA", {} if generated else {"en": track},
        {"en": track} if generated else {}, [],
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "fixture-openrouter",
            "ASSEMBLYAI_API_KEY": "fixture-assembly",
        }, clear=True))
        self.metadata = self.stack.enter_context(patch.object(
            jot, "_get_video_metadata", return_value=dict(_WORKFLOW_METADATA),
        ))
        self.tracks = self.stack.enter_context(patch(
            "youtube_transcript_api.YouTubeTranscriptApi.list", return_value=_caption_tracks(),
        ))
        self.download = self.stack.enter_context(patch.object(
            jot, "_download_audio", side_effect=self.download_fixture,
        ))
        self.audio = self.stack.enter_context(patch.object(
            jot, "_transcribe_assemblyai", return_value={
                "text": "Recovered speech",
                "words": [{"text": "Recovered speech", "start": 1000, "end": 2000}],
            },
        ))
        self.http = Mock(spec=httpx.Client)
        self.http.post.return_value = httpx.Response(
            200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": _WORKFLOW_REPORT},
            }]}, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
        )
        client = self.stack.enter_context(patch("jot.httpx.Client", new_callable=MagicMock))
        client.return_value.__enter__.return_value = self.http

    def download_fixture(self, url, folder):
        path = Path(folder) / "audio.mp3"
        path.write_bytes(b"local audio boundary fixture")
        return str(path)

    def run_command(self, *, evaluate=False, diarize=False, skip_captions=False, directory=None):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                jot.cmd_transcribe(
                    "https://youtu.be/WkBPX-oDMnA", diarize, skip_captions,
                    str(directory or self.directory), evaluate, None,
                )
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        return code, output.getvalue(), error.getvalue()

    def test_manual_and_generated_provenance_reaches_saved_evidence(self):
        for generated, expected in ((False, "manual"), (True, "auto-generated")):
            with self.subTest(generated=generated):
                self.tracks.return_value = _caption_tracks(generated=generated)
                folder = self.directory / expected
                code, _, _ = self.run_command(evaluate=True, directory=folder)
                self.assertEqual(code, 0)
                markdown = (folder / "Workflow Talk.evaluation.md").read_text()
                html = (folder / "Workflow Talk.evaluation.html").read_text()
                self.assertIn(f"YouTube captions ({expected})", markdown)
                self.assertIn(f"YouTube captions ({expected})", html)
                self.assertIn("[00:04] um, original evidence", markdown)

    def test_fetch_failure_retains_cause_and_recovers_audio(self):
        self.tracks.return_value = _caption_tracks(blocked=True)
        code, _, error = self.run_command()
        self.assertEqual(code, 0)
        self.assertIn(str(PoTokenRequired("WkBPX-oDMnA")), error)
        markdown = (self.directory / "Workflow Talk.md").read_text()
        self.assertIn("[00:01] Recovered speech", markdown)
        self.assertIn("| **Method** | AssemblyAI |", markdown)

    def test_unknown_duration_is_not_zero_in_saved_page_or_model_input(self):
        for scenario in ("missing", "null", "failure"):
            with self.subTest(scenario=scenario):
                folder = self.directory / scenario
                self.metadata.side_effect = None
                metadata = dict(_WORKFLOW_METADATA)
                if scenario == "missing":
                    del metadata["duration"]
                elif scenario == "null":
                    metadata["duration"] = None
                else:
                    self.metadata.side_effect = OSError("metadata unavailable")
                self.metadata.return_value = metadata
                code, _, _ = self.run_command(evaluate=True, directory=folder)
                self.assertEqual(code, 0)
                markdown_files = list(folder.glob("*.evaluation.md"))
                self.assertEqual(len(markdown_files), 1)
                self.assertIn("| **Duration** | Unknown |", markdown_files[0].read_text())
                self.assertIn("<dd>Unknown</dd>", markdown_files[0].with_suffix(".html").read_text())
                payload = self.http.post.call_args.kwargs["json"]
                self.assertIsNone(json.loads(payload["messages"][1]["content"])["metadata"].get("duration"))

    def test_measured_zero_duration_remains_zero(self):
        self.metadata.return_value = {**_WORKFLOW_METADATA, "duration": 0}
        code, _, _ = self.run_command()
        self.assertEqual(code, 0)
        self.assertIn("| **Duration** | 00:00 |", (self.directory / "Workflow Talk.md").read_text())

    def test_empty_captions_recover_audio_before_evaluation(self):
        self.tracks.return_value = _caption_tracks(empty=True)
        code, _, _ = self.run_command(evaluate=True)
        self.assertEqual(code, 0)
        document = (self.directory / "Workflow Talk.evaluation.md").read_text()
        self.assertIn("[00:01] Recovered speech", document)
        self.assertIn("| **Method** | AssemblyAI |", document)
        payload = self.http.post.call_args.kwargs["json"]
        self.assertEqual(json.loads(payload["messages"][1]["content"])["transcript"],
                         "[00:01] Recovered speech")

    def test_empty_audio_never_publishes_or_requests_evaluation(self):
        cases = (
            {"text": "", "words": []},
            {"text": None, "words": []},
            {"text": " ", "words": [{"text": " ", "start": 0, "end": 100}]},
            {"text": "", "utterances": [{"text": " ", "speaker": "A", "start": 0, "end": 100}]},
        )
        for index, result in enumerate(cases):
            with self.subTest(result=result):
                self.audio.return_value = result
                code, _, _ = self.run_command(
                    evaluate=True, diarize=True, directory=self.directory / str(index),
                )
                self.assertEqual(code, 1)
                self.assertEqual(list(self.directory.iterdir()), [])
                self.http.post.assert_not_called()

    def test_missing_audio_key_stops_before_downloading(self):
        del os.environ["ASSEMBLYAI_API_KEY"]
        for diarize, skip in ((True, False), (False, True)):
            with self.subTest(diarize=diarize, skip=skip):
                code, _, error = self.run_command(diarize=diarize, skip_captions=skip)
                self.assertEqual(code, 1)
                self.assertIn("ASSEMBLYAI_API_KEY", error)
                self.download.assert_not_called()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_audio_failure_has_a_cause_without_publishing_or_evaluating(self):
        self.audio.side_effect = OSError("fixture transport failure")
        code, _, error = self.run_command(evaluate=True, skip_captions=True)
        self.assertEqual(code, 1)
        self.assertIn("fixture transport failure", error)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.http.post.assert_not_called()

    def test_html_write_failure_preserves_and_identifies_markdown(self):
        real_write = Path.write_text
        def fail_html(path, *args, **kwargs):
            if path.suffix == ".html":
                raise PermissionError("fixture HTML write denial")
            return real_write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail_html):
            code, _, error = self.run_command(evaluate=True)
        markdown = self.directory / "Workflow Talk.evaluation.md"
        self.assertEqual(code, 1)
        self.assertIn(str(markdown), error)
        self.assertIn("fixture HTML write denial", error)
        self.assertIn(_WORKFLOW_REPORT, markdown.read_text())
        self.assertIn("[00:04] um, original evidence", markdown.read_text())
        self.assertFalse(markdown.with_suffix(".html").exists())


class AudioContractTests(unittest.TestCase):
    def test_missing_sdk_key_raises_without_process_exit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as raised:
                jot._transcribe_assemblyai("unused.mp3")
        self.assertIn("ASSEMBLYAI_API_KEY", str(raised.exception))

    def test_sdk_error_status_raises_with_provider_cause(self):
        import assemblyai as aai
        with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "fixture"}, clear=True), \
                patch.object(aai.settings, "api_key", None), \
                patch.object(aai, "Transcriber") as transcriber, \
                redirect_stdout(StringIO()):
            transcriber.return_value.transcribe.return_value = SimpleNamespace(
                status=aai.TranscriptStatus.error, error="fixture provider refusal",
            )
            with self.assertRaises(RuntimeError) as raised:
                jot._transcribe_assemblyai("unused.mp3")
        self.assertIn("fixture provider refusal", str(raised.exception))

    def test_audio_units_speaker_labels_and_empty_speech(self):
        self.assertEqual(jot._assemblyai_to_markdown({"text": "", "utterances": [
            {"speaker": "A", "text": " ", "start": 0, "end": 500},
            {"speaker": "B", "text": "first", "start": 1000, "end": 2000},
            {"speaker": "A", "text": "later", "start": 3601000, "end": 3602000},
        ]}), "[00:01] **Speaker B:** first\n\n[01:00:01] **Speaker A:** later")
        self.assertEqual(jot._assemblyai_to_markdown({"text": "", "words": [
            {"text": "one", "start": 1000, "end": 1500},
            {"text": "two", "start": 3000, "end": 3500},
        ]}, diarize=False), "[00:01] one\n\n[00:03] two")
        self.assertEqual(jot._assemblyai_to_markdown({"text": "untimed speech", "words": []}),
                         "untimed speech")


if __name__ == "__main__":
    unittest.main()
