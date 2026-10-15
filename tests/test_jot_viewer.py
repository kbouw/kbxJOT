"""Single-report HTTP isolation and foreground viewer lifecycle."""

from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPConnection
from io import StringIO
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock, patch

import jot_viewer


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.report = self.root / "selected report.html"
        self.html = "<!doctype html><title>Résumé</title><p>Selected report</p>"
        self.report.write_text(self.html, encoding="utf-8")
        (self.root / "private.html").write_text("PRIVATE CONTENT", encoding="utf-8")
        self.server = jot_viewer._create_server(self.report)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, route, method="GET"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, route)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_serves_only_selected_utf8_content_with_referrer_and_cache_policy(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for route in ("/", "/report.html", "/report.html?view=1"):
            with self.subTest(route=route):
                status, headers, body = self.request(route)
                self.assertEqual(status, 200)
                self.assertEqual(body, self.html.encode("utf-8"))
                self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["Referrer-Policy"], "strict-origin-when-cross-origin")
                self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_rejects_other_files_traversal_and_encoded_paths(self):
        for route in (
            "/private.html", "/linked.html", "/selected%20report.html",
            "/../private.html", "/%2e%2e/private.html", "/report.html/../private.html",
            "/%72eport.html", "/report.html%2f..%2fprivate.html", "/favicon.ico",
            "/report.html/", "http://example.com/report.html",
        ):
            with self.subTest(route=route):
                status, _, body = self.request(route)
                self.assertEqual(status, 404)
                self.assertEqual(body, b"Not found\n")

    def test_selected_report_does_not_expose_sibling_symlinks(self):
        try:
            (self.root / "linked.html").symlink_to(self.root / "private.html")
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this platform.")
        status, _, body = self.request("/linked.html")
        self.assertEqual(status, 404)
        self.assertNotIn(b"PRIVATE CONTENT", body)

    def test_query_does_not_select_a_different_file(self):
        status, _, body = self.request("/report.html?file=private.html&path=../linked.html")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.html.encode("utf-8"))

    def test_head_has_headers_without_body(self):
        status, headers, body = self.request("/report.html", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(self.html.encode("utf-8")))

    def test_content_is_a_snapshot_not_a_later_filesystem_lookup(self):
        self.report.unlink()
        self.report.write_text("PRIVATE CONTENT", encoding="utf-8")
        status, _, body = self.request("/report.html")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.html.encode("utf-8"))


class OpenReportTests(unittest.TestCase):
    def test_validation_happens_before_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = root / "not-html.txt"
            text.write_text("plain text", encoding="utf-8")
            invalid_utf8 = root / "invalid.html"
            invalid_utf8.write_bytes(b"\xff")
            with patch.object(jot_viewer, "HTTPServer") as server:
                for path, error in ((text, ValueError), (root / "absent.html", FileNotFoundError),
                                    (root, ValueError), (invalid_utf8, UnicodeDecodeError)):
                    with self.subTest(path=path), self.assertRaises(error):
                        jot_viewer.open_report(path)
                server.assert_not_called()

    def test_browser_failure_preserves_manual_url_and_closes_server_on_interrupt(self):
        for browser_result in (False, OSError("private launcher details")):
            with self.subTest(browser_result=browser_result):
                server = Mock()
                server.server_port = 45678
                server.__enter__ = Mock(return_value=server)
                server.__exit__ = Mock(return_value=False)
                server.serve_forever.side_effect = KeyboardInterrupt
                output, errors = StringIO(), StringIO()
                browser = Mock()
                if isinstance(browser_result, Exception):
                    browser.side_effect = browser_result
                else:
                    browser.return_value = browser_result
                with patch.object(jot_viewer, "_create_server", return_value=server), \
                     patch.object(jot_viewer.webbrowser, "open", browser), \
                     redirect_stdout(output), redirect_stderr(errors):
                    jot_viewer.open_report(Path("report.html"))
                self.assertIn("http://127.0.0.1:45678/report.html", output.getvalue())
                self.assertIn("Ctrl-C", output.getvalue())
                self.assertIn("manually", errors.getvalue())
                self.assertNotIn("private launcher details", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
