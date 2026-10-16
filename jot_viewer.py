"""Serve one report on loopback so embedded media receives an HTTP referrer."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
import sys
import webbrowser


def _create_server(path: Path) -> ThreadingHTTPServer:
    """Read the selected report once; never map request paths to the filesystem."""
    path = Path(path)
    if path.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("Report must be an HTML file (.html or .htm).")
    if not path.is_file():
        raise FileNotFoundError(f"Report file not found: {path}")
    content = path.read_text(encoding="utf-8").encode("utf-8")

    class ReportHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._respond(send_body=True)

        def do_HEAD(self) -> None:
            self._respond(send_body=False)

        def _respond(self, *, send_body: bool) -> None:
            try:
                parsed = urlsplit(self.path)
                allowed = (
                    not parsed.scheme
                    and not parsed.netloc
                    and parsed.path in {"/", "/report.html"}
                )
            except ValueError:
                allowed = False
            body = content if allowed else b"Not found\n"
            try:
                self.send_response(200 if allowed else 404)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8" if allowed else "text/plain; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Closing a browser tab must not produce a request traceback.
                pass

        def log_message(self, format: str, *args: object) -> None:
            # Request URLs can contain sensitive report information.
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), ReportHandler)


def open_report(path: Path) -> None:
    """Serve before launching the browser; stop and join the server on Ctrl-C."""
    with _create_server(path) as server:
        url = f"http://127.0.0.1:{server.server_port}/report.html"
        print(f"Report: {url}\nPress Ctrl-C to stop serving.", flush=True)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            try:
                opened = webbrowser.open(url)
            except Exception:
                # Browser launchers vary by platform; the URL remains usable.
                print("Could not open the browser. Open the report URL above manually.",
                      file=sys.stderr, flush=True)
            else:
                if not opened:
                    print("No browser opened. Open the report URL above manually.",
                          file=sys.stderr, flush=True)
            server_thread.join()
        except KeyboardInterrupt:
            print("\nReport server stopped.", flush=True)
        finally:
            server.shutdown()
            server_thread.join()
