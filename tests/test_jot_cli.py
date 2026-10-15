"""Offline boundaries for the YouTube-only command line."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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


    def test_removed_packet_actions_are_not_public_cli_options(self):
        for arguments in (
            ["--prepare-evaluation", "source.md"],
            ["--publish-evaluation", "request.json"],
            ["--report", "response.json"],
        ):
            with self.subTest(arguments=arguments):
                self.assert_rejected(arguments)

    def test_explicit_captions_only_rejects_audio_flags(self):
        for flag in ("--diarize", "--skip-captions"):
            self.assert_rejected(["https://youtu.be/WkBPX-oDMnA", "--captions-only", flag])

    def test_caption_format_keeps_every_timestamp_and_original_wording(self):
        self.assertEqual(jot._captions_to_markdown([
            {"start": 4.8, "text": "um,  original wording"},
            {"start": 65, "text": "[Music] and a mispeling"},
            {"start": 3601, "text": "  keep edge spaces  "},
            {"start": 3602, "text": "  "},
        ]), "[00:04] um,  original wording\n[01:05] [Music] and a mispeling\n[01:00:01]   keep edge spaces  ")


_WORKFLOW_METADATA = {"title": "Workflow Talk", "channel": "Test Channel", "duration": 78}


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
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(os.environ, {
            "ASSEMBLYAI_API_KEY": "fixture-assembly",
        }, clear=True))
        self.stack.enter_context(patch("jot_config.get_evaluator", return_value="claude"))
        self.metadata_adapter = jot._get_video_metadata
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
        self.preflight = self.stack.enter_context(patch(
            "jot_evaluator.find_evaluator", return_value="/installed/claude",
        ))
        self.evaluation_paths = (
            self.directory / "published.evaluation.md",
            self.directory / "published.evaluation.html",
        )
        self.evaluator = self.stack.enter_context(patch(
            "jot_agent.evaluate_transcript", return_value=self.evaluation_paths,
        ))

    def download_fixture(self, url, directory):
        path = Path(directory) / "audio.mp3"
        path.write_bytes(b"local audio fixture")
        return str(path)

    def run_command(self, *, evaluate=False, diarize=False, skip_captions=False,
                    captions_only=False, directory=None, background=None):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                result = jot.cmd_transcribe(
                    "https://youtu.be/WkBPX-oDMnA", diarize, skip_captions,
                    str(directory or self.directory), evaluate, background, captions_only,
                )
            except SystemExit as exc:
                return exc.code, output.getvalue(), error.getvalue(), None
        return 0, output.getvalue(), error.getvalue(), result

    def test_evaluation_delegates_saved_original_captions_and_returns_publication_paths(self):
        for generated, method in ((False, "manual"), (True, "auto-generated")):
            with self.subTest(generated=generated):
                self.tracks.return_value = _caption_tracks(generated=generated)
                folder = self.directory / method
                code, output, error, result = self.run_command(
                    evaluate=True, directory=folder, background="My learning goals",
                )
                self.assertEqual(code, 0, error)
                source = folder / "Workflow Talk.md"
                document = source.read_text()
                self.assertIn(f"YouTube captions ({method})", document)
                self.assertIn("[00:04] um, original evidence", document)
                self.assertNotIn("## Watch-value evaluation", document)
                self.assertEqual(result, self.evaluation_paths)
                for path in self.evaluation_paths:
                    self.assertIn(str(path), output)
                self.download.assert_not_called()
                self.audio.assert_not_called()
                self.assertEqual(list(folder.iterdir()), [source])

    @unittest.skipUnless(shutil.which("sh"), "requires a POSIX shell")
    def test_playback_hint_preserves_path_through_actual_posix_shell(self):
        marker = self.directory / "substitution-marker"
        paths = [
            self.directory / f"$(touch {marker}) report.html",
            self.directory / f"`touch {marker}` report.html",
            self.directory / "apostrophe's report; $HOME & (extra).html",
            self.directory / "line\nbreak\tand spaces.html",
        ]
        for html_path in paths:
            with self.subTest(html_path=html_path):
                self.evaluator.return_value = (self.directory / "report.md", html_path)
                with patch.object(jot.sys, "platform", "linux"):
                    code, output, error, _ = self.run_command(evaluate=True)
                self.assertEqual(code, 0, error)
                command = output.split("For video playback (POSIX shell): ", 1)[1].split(jot.C.RESET, 1)[0]
                parsed = subprocess.run(
                    [shutil.which("sh"), "-c",
                     'uv() { printf "%s\\\\0" uv "$@"; }; ' + command],
                    check=True, capture_output=True,
                )
                self.assertEqual(parsed.stdout.decode().split("\0")[:-1],
                                 ["uv", "run", "jot", "open", str(html_path)])
                self.assertFalse(marker.exists())

    def test_windows_playback_hint_explicitly_targets_powershell(self):
        html_path = Path("C:/Reports/O'Brien ‘left’ ‚low‛ $() `talk` & notes.html")
        self.evaluator.return_value = (self.directory / "report.md", html_path)
        with patch.object(jot.sys, "platform", "win32"):
            code, output, error, _ = self.run_command(evaluate=True)
        self.assertEqual(code, 0, error)
        self.assertIn("For video playback (PowerShell): uv run jot open 'C:/Reports/O''Brien ‘‘left’’ ‚‚low‛‛ $() `talk` & notes.html'",
                      output)

    def test_missing_evaluator_stops_before_network_or_capture(self):
        self.preflight.side_effect = ValueError("Install Claude Code")
        code, output, error, result = self.run_command(evaluate=True)
        self.assertEqual(code, 1)
        self.assertIn("Install Claude Code", error)
        self.assertNotIn("Saved HTML", output)
        self.metadata.assert_not_called()
        self.tracks.assert_not_called()
        self.download.assert_not_called()
        self.evaluator.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_evaluation_defaults_to_no_audio_for_missing_empty_or_failed_captions(self):
        scenarios = (
            {"segments": [], "method": "YouTube captions (manual)"},
            PoTokenRequired("WkBPX-oDMnA"),
            None,
        )
        for index, scenario in enumerate(scenarios):
            with self.subTest(index=index), patch.object(jot, "_try_youtube_captions") as captions:
                if isinstance(scenario, Exception):
                    captions.side_effect = scenario
                else:
                    captions.return_value = scenario
                code, output, error, result = self.run_command(evaluate=True)
                self.assertEqual(code, 1)
                self.download.assert_not_called()
                self.audio.assert_not_called()
                self.evaluator.assert_not_called()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_source_save_failure_never_starts_evaluation(self):
        with patch.object(Path, "open", side_effect=PermissionError("capture write denied")):
            code, output, error, result = self.run_command(evaluate=True)
        self.assertEqual(code, 1)
        self.assertIn("capture write denied", error)
        self.evaluator.assert_not_called()
        self.assertNotIn("Saved HTML", output)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_evaluation_failures_preserve_and_identify_source_without_success(self):
        for failure in (ValueError("invalid report"), OSError("CLI invocation failed")):
            with self.subTest(failure=failure):
                self.evaluator.side_effect = failure
                code, output, error, result = self.run_command(evaluate=True)
                source = self.evaluator.call_args.args[0]
                self.assertEqual(code, 1)
                self.assertIn(str(source), error)
                self.assertIn(str(failure), error)
                self.assertIn("[00:04] um, original evidence", source.read_text())
                self.assertNotIn("Saved evaluation:", output)
                self.assertNotIn("Saved HTML:", output)
                self.assertIsNone(result)
        self.assertEqual(len(list(self.directory.glob("*.md"))), 2)
        self.assertEqual(list(self.directory.glob("*.html")), [])

    def test_plain_capture_returns_source_without_evaluator(self):
        code, output, error, result = self.run_command(captions_only=True)
        self.assertEqual(code, 0, error)
        self.assertEqual(result, self.directory / "Workflow Talk.md")
        self.assertIn("[00:04] um, original evidence", result.read_text())
        self.preflight.assert_not_called()
        self.evaluator.assert_not_called()

    def test_explicit_audio_evaluation_uses_requested_transcription(self):
        for diarize, skip in ((True, False), (False, True)):
            with self.subTest(diarize=diarize, skip=skip):
                folder = self.directory / f"{diarize}-{skip}"
                code, output, error, result = self.run_command(
                    evaluate=True, diarize=diarize, skip_captions=skip, directory=folder,
                )
                self.assertEqual(code, 0, error)
                source = self.evaluator.call_args.args[0]
                self.assertIn("[00:01] Recovered speech", source.read_text())
                self.assertEqual(self.audio.call_args.kwargs, {"diarize": diarize})
                self.assertEqual(Path(self.audio.call_args.args[0]).name, "audio.mp3")
                self.tracks.assert_not_called()

    def test_caption_access_error_retains_plain_capture_audio_fallback(self):
        self.tracks.return_value = _caption_tracks(blocked=True)
        code, output, error, result = self.run_command()
        self.assertEqual(code, 0, error)
        self.assertIn(str(PoTokenRequired("WkBPX-oDMnA")), error)
        self.assertIn("[00:01] Recovered speech", result.read_text())
        self.assertIn("| **Method** | AssemblyAI |", result.read_text())
        self.evaluator.assert_not_called()

    def test_incomplete_provider_metadata_retains_source_defaults(self):
        self.metadata.side_effect = self.metadata_adapter
        scenarios = (
            ({}, "Untitled", "Unknown"),
            ({"title": None, "channel": None}, "Untitled", "Unknown"),
            ({"title": "", "channel": "", "uploader": ""}, "Untitled", "Unknown"),
            ({"title": "Uploader Talk", "channel": "", "uploader": "Uploader"}, "Uploader Talk", "Uploader"),
        )
        with patch("yt_dlp.YoutubeDL") as downloader:
            provider = downloader.return_value.__enter__.return_value
            for index, (metadata, title, channel) in enumerate(scenarios):
                with self.subTest(metadata=metadata):
                    provider.extract_info.return_value = metadata
                    code, output, error, source = self.run_command(directory=self.directory / str(index))
                    self.assertEqual(code, 0, error)
                    self.assertIn(f"# {title}\n", source.read_text())
                    self.assertIn(f"| **Channel** | {channel} |", source.read_text())

    def test_missing_null_and_failed_metadata_keep_unknown_duration(self):
        for scenario in ("missing", "null", "failure"):
            with self.subTest(scenario=scenario):
                self.metadata.side_effect = None
                metadata = dict(_WORKFLOW_METADATA)
                if scenario == "missing":
                    del metadata["duration"]
                elif scenario == "null":
                    metadata["duration"] = None
                else:
                    self.metadata.side_effect = OSError("metadata unavailable")
                self.metadata.return_value = metadata
                code, output, error, source = self.run_command(directory=self.directory / scenario)
                self.assertEqual(code, 0, error)
                self.assertIn("| **Duration** | Unknown |", source.read_text())

    def test_actual_zero_duration_remains_zero(self):
        self.metadata.return_value = {**_WORKFLOW_METADATA, "duration": 0}
        code, output, error, source = self.run_command()
        self.assertEqual(code, 0, error)
        self.assertIn("| **Duration** | 00:00 |", source.read_text())

    def test_empty_audio_results_do_not_publish_or_evaluate(self):
        results = (
            {"text": None, "words": []},
            {"text": ""},
            {"text": "   "},
            {"text": " ", "words": [{"text": " ", "start": 0, "end": 100}]},
            {"text": "", "utterances": [{"speaker": "A", "text": " ", "start": 0, "end": 100}]},
        )
        for result in results:
            with self.subTest(result=result):
                self.audio.return_value = result
                code, output, error, saved = self.run_command(
                    evaluate=True, skip_captions=True, diarize="utterances" in result,
                )
                self.assertEqual(code, 1)
                self.assertEqual(list(self.directory.iterdir()), [])
                self.assertIn("No transcript speech", error)
                self.assertIsNone(saved)
                self.evaluator.assert_not_called()

    def test_missing_audio_key_stops_before_download(self):
        del os.environ["ASSEMBLYAI_API_KEY"]
        for diarize, skip in ((True, False), (False, True)):
            with self.subTest(diarize=diarize, skip=skip):
                code, output, error, result = self.run_command(diarize=diarize, skip_captions=skip)
                self.assertEqual(code, 1)
                self.assertIn("ASSEMBLYAI_API_KEY", error)
                self.download.assert_not_called()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_audio_failure_identifies_cause_without_publication_or_evaluation(self):
        self.audio.side_effect = OSError("fixture transport failure")
        code, output, error, result = self.run_command(evaluate=True, skip_captions=True)
        self.assertEqual(code, 1)
        self.assertIn("fixture transport failure", error)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.evaluator.assert_not_called()



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
