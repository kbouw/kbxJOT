"""Configuration behavior without touching a user's real settings or credentials."""

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import jot_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.settings = self.directory / "settings.json"
        self.stack.enter_context(patch.dict(os.environ, {"JOT_CONFIG_DIR": str(self.directory)}, clear=True))

    def test_configuration_directory_precedence_does_not_create_directories(self):
        with patch.dict(os.environ, {"JOT_CONFIG_DIR": "~/explicit", "XDG_CONFIG_HOME": "/xdg"}, clear=True):
            self.assertEqual(jot_config.config_dir(), Path.home() / "explicit")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/xdg"}, clear=True):
            self.assertEqual(jot_config.config_dir(), Path("/xdg/jot"))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(jot_config.config_dir(), Path.home() / ".config/jot")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_missing_default_requires_explicit_setup_or_override(self):
        for text in (None, "{}"):
            if text is not None:
                self.settings.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "jot setup") as raised:
                jot_config.get_evaluator()
            self.assertIn("jot config evaluator", str(raised.exception))
            self.assertEqual(jot_config.get_evaluator("claude"), "claude")

    def test_persisted_default_and_one_off_override_are_independent(self):
        jot_config.set_evaluator("claude")
        self.assertEqual(jot_config.get_evaluator(), "claude")
        self.assertEqual(jot_config.get_evaluator("codex"), "codex")
        self.assertEqual(jot_config.get_evaluator(), "claude")
        jot_config.set_evaluator("codex")
        self.assertEqual(jot_config.get_evaluator(), "codex")
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), {"evaluator": "codex"})

    def test_invalid_names_and_types_never_change_saved_default(self):
        jot_config.set_evaluator("claude")
        original = self.settings.read_bytes()
        for value in ("Claude", "", " codex", "auto", None, True, 0, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    jot_config.set_evaluator(value)
                if value is not None:
                    with self.assertRaises(ValueError):
                        jot_config.get_evaluator(value)
                self.assertEqual(self.settings.read_bytes(), original)

    def test_corrupt_settings_are_not_silently_reset_by_a_valid_set(self):
        for text in (
            "not JSON", "[]", "null", '{"evaluator":null}', '{"evaluator":1}',
            '{"evaluator":true}', '{"evaluator":"other"}', '{"unknown":"claude"}',
            '{"evaluator":"claude","evaluator":"codex"}',
            '{"evaluator":"claude"} trailing',
        ):
            with self.subTest(text=text):
                self.settings.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid jot configuration"):
                    jot_config.get_evaluator()
                with self.assertRaisesRegex(ValueError, "Invalid jot configuration"):
                    jot_config.set_evaluator("claude")
                self.assertEqual(self.settings.read_text(encoding="utf-8"), text)
                self.assertEqual(jot_config.get_evaluator("claude"), "claude")

    def test_non_utf8_configuration_has_actionable_diagnostic_and_is_preserved(self):
        self.settings.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ValueError, "UTF-8 JSON"):
            jot_config.set_evaluator("claude")
        self.assertEqual(self.settings.read_bytes(), b"\xff\xfe")

    def test_failed_atomic_replace_preserves_saved_default_and_removes_temporary(self):
        jot_config.set_evaluator("claude")
        original = self.settings.read_bytes()
        with patch.object(jot_config.os, "replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                jot_config.set_evaluator("codex")
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual([path.name for path in self.directory.iterdir()], ["settings.json"])

    def test_missing_parent_directory_is_created_only_when_saving(self):
        nested = self.directory / "nested" / "jot"
        with patch.dict(os.environ, {"JOT_CONFIG_DIR": str(nested)}):
            self.assertEqual(jot_config.get_evaluator("claude"), "claude")
            self.assertFalse(nested.exists())
            jot_config.set_evaluator("claude")
            self.assertEqual(jot_config.get_evaluator(), "claude")

    def setup_terminal(self, installed, returncodes, choice):
        self.stack.enter_context(patch.object(jot_config.sys.stdin, "isatty", return_value=True))
        self.stack.enter_context(patch.object(jot_config.shutil, "which", side_effect=lambda name: installed.get(name)))
        self.stack.enter_context(patch("jot_evaluator._run_evaluator", side_effect=returncodes))
        self.stack.enter_context(patch("builtins.input", return_value=choice))
        output = io.StringIO()
        self.stack.enter_context(patch.object(output, "isatty", return_value=True))
        self.stack.enter_context(patch("sys.stdout", output))
        return output

    def test_noninteractive_setup_does_not_read_provider_state_or_save(self):
        with patch.object(jot_config.sys.stdin, "isatty", return_value=False):
            with patch.object(jot_config.shutil, "which") as locate:
                with self.assertRaisesRegex(ValueError, "interactive terminal"):
                    jot_config.setup()
        locate.assert_not_called()
        self.assertFalse(self.settings.exists())

    def test_setup_requires_explicit_choice_even_with_only_one_ready_provider(self):
        output = self.setup_terminal({"claude": "/bin/claude"}, [(b"private-auth", 0)], "")
        with self.assertRaises(ValueError):
            jot_config.setup()
        self.assertIn("authentication check passed", output.getvalue())
        self.assertIn("codex: not installed", output.getvalue())
        self.assertNotIn("private-auth", output.getvalue())
        self.assertFalse(self.settings.exists())

    def test_setup_saves_only_the_explicit_authenticated_choice(self):
        output = self.setup_terminal(
            {"claude": "/bin/claude", "codex": "/bin/codex"},
            [(b"private-auth", 0), (b"private-auth", 0)], "codex",
        )
        jot_config.setup()
        self.assertEqual(jot_config.get_evaluator(), "codex")
        self.assertIn("Saved default evaluator: codex", output.getvalue())
        self.assertNotIn("private-auth", self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("private-auth", output.getvalue())

    def test_setup_distinguishes_installed_from_authenticated_without_leaking_output(self):
        for failure in ((b"private-auth", 1), subprocess.TimeoutExpired("claude", 15)):
            with self.subTest(failure=type(failure).__name__):
                with ExitStack() as temporary:
                    old_stack = self.stack
                    self.stack = temporary
                    try:
                        output = self.setup_terminal({"claude": "/bin/claude"}, [failure], "claude")
                        with self.assertRaisesRegex(ValueError, "claude auth login"):
                            jot_config.setup()
                        self.assertIn("installed; authentication check failed", output.getvalue())
                        self.assertNotIn("private-auth", output.getvalue())
                        self.assertFalse(self.settings.exists())
                    finally:
                        self.stack = old_stack


if __name__ == "__main__":
    unittest.main()
