"""Fresh, tool-free evaluations through the installed Claude Code CLI."""

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import sys
import threading
import time

from jot_agent import _strict_json


_TIMEOUT_SECONDS = 600
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
# Session and IDE coordinates must not connect the evaluator to its caller.
# Authentication, model selection, and other legitimate CLI configuration stay intact.
_SESSION_ENV_VARS = frozenset({
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_PARENT_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_IDE_HOST",
})


def find_evaluator() -> str:
    """Locate the installed Claude Code executable or explain how to make it available."""
    executable = shutil.which("claude")
    if executable is None:
        raise ValueError(
            "Claude Code is required for evaluation. Install the Claude Code CLI, "
            "make `claude` available on PATH, and sign in with `claude auth login`."
        )
    # PATH can contain relative entries; the child runs outside the caller's directory.
    return str(Path(executable).resolve())


def _run_evaluator(command, request, directory, environment):
    """Exchange one request with a contained process, with bounded output and lifetime.

    communicate() buffers both output streams without a size limit. Dedicated
    pipe workers also work on Windows, where selectors cannot watch these pipes.
    Only stdout is retained; stderr is counted and discarded.
    """
    job = None
    process = None
    workers = []
    primary_error = None
    completed = queue.Queue(maxsize=3)
    stdout = bytearray()
    deadline = time.monotonic() + _TIMEOUT_SECONDS

    def write_request():
        try:
            if job is not None:
                # The Windows launcher cannot spawn Claude until job assignment.
                process.stdin.write(b"\0")
                process.stdin.flush()
            process.stdin.write(request)
            process.stdin.close()
        except BrokenPipeError:
            # Early CLI exit is diagnosed using its status/output, not source text.
            completed.put(None)
        except BaseException as exc:
            completed.put(exc)
        else:
            completed.put(None)

    def read_output(stream, name):
        try:
            size = 0
            while True:
                chunk = stream.read1(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_OUTPUT_BYTES:
                    raise ValueError(
                        f"Claude Code evaluation exceeded the {name} limit of "
                        f"{_MAX_OUTPUT_BYTES} bytes; no evaluation was accepted."
                    )
                if name == "stdout":
                    stdout.extend(chunk)
        except BaseException as exc:
            completed.put(exc)
        else:
            completed.put(None)

    try:
        if os.name == "nt":
            from jot_evaluator_windows import WindowsJob

            job = WindowsJob()
            # A stdlib launcher waits for one byte before it creates the CLI.
            # Assigning the launcher to the job first closes the spawn/assign race.
            launcher = (
                "import os, subprocess, sys\n"
                "if os.read(0, 1) != b'\\0': sys.exit(125)\n"
                "sys.exit(subprocess.call(sys.argv[1:]))\n"
            )
            command = [sys.executable, "-c", launcher, *command]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=directory,
            env=environment,
            start_new_session=os.name == "posix",
        )
        if job is not None:
            job.assign(process.pid)
        for target, args in (
            (write_request, ()),
            (read_output, (process.stdout, "stdout")),
            (read_output, (process.stderr, "stderr")),
        ):
            worker = threading.Thread(target=target, args=args, daemon=True)
            worker.start()
            workers.append(worker)
        for _ in workers:
            try:
                error = completed.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise subprocess.TimeoutExpired(command, _TIMEOUT_SECONDS) from None
            if error is not None:
                raise error
        process.wait(timeout=max(0, deadline - time.monotonic()))
        return stdout, process.returncode
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            # Cleanup is unconditional, including Ctrl-C and unexpected exceptions.
            # Do not poll the root before killing: it may have left helpers behind.
            if job is not None:
                job.close()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                # Closing an unreleased Windows gate also handles assignment failure.
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                process.wait()
                for worker in workers:
                    worker.join()
                process.stdout.close()
                process.stderr.close()
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise


def invoke_evaluator(packet: dict, instructions: str) -> dict:
    """Return a fresh Claude Code structured response, never a partial or failed result.

    The caller must validate the returned report before publishing it. Invocation
    and protocol failures raise ValueError or OSError without exposing source text
    or authentication details.
    """
    schema = packet.get("response_schema")
    if not isinstance(schema, dict):
        raise ValueError("Evaluation request requires a JSON object response_schema.")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Evaluation requires the packaged trusted evaluator instructions.")
    try:
        request = (json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        # Claude's native schema checker does not load the Draft 2020-12
        # metaschema. Omit only that declaration at this boundary; the complete
        # trusted schema and all evidence checks still run locally.
        serialized_schema = json.dumps(
            {key: value for key, value in schema.items() if key != "$schema"},
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("Evaluation request must contain valid, finite UTF-8 JSON values.") from None

    command = [
        find_evaluator(),
        "--print",
        "--safe-mode",
        "--tools", "",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", serialized_schema,
        "--system-prompt", instructions,
    ]
    environment = {key: value for key, value in os.environ.items() if key not in _SESSION_ENV_VARS}
    try:
        with tempfile.TemporaryDirectory(prefix="jot-evaluator-") as directory:
            stdout, returncode = _run_evaluator(command, request, directory, environment)
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"Claude Code evaluation timed out after {_TIMEOUT_SECONDS:g} seconds. "
            "Check your connection and Claude Code authentication before trying again."
        ) from None
    except FileNotFoundError:
        raise ValueError(
            "The Claude Code executable became unavailable. Reinstall it or correct PATH, "
            "then sign in with `claude auth login`."
        ) from None
    except OSError as exc:
        raise OSError(
            exc.errno,
            "Cannot run the Claude Code evaluator. Check executable permissions, temporary "
            "directory access, and that `claude --version` runs successfully.",
        ) from None

    if returncode != 0:
        raise ValueError(
            f"Claude Code evaluation failed (exit status {returncode}). "
            "Check `claude auth login`, your connection, and your Claude Code configuration."
        )
    try:
        envelope = _strict_json(stdout.decode("utf-8"), "Claude Code response")
    except (ValueError, UnicodeError):
        raise ValueError(
            "Claude Code returned invalid JSON. Check `claude --version` and update the CLI "
            "if necessary; no evaluation was accepted."
        ) from None
    if (
        not isinstance(envelope, dict)
        or envelope.get("type") != "result"
        or envelope.get("subtype") != "success"
        or envelope.get("is_error") is not False
    ):
        raise ValueError(
            "Claude Code did not complete evaluation successfully. Check authentication, "
            "connection, and CLI configuration; no evaluation was accepted."
        )
    response = envelope.get("structured_output")
    if not isinstance(response, dict):
        raise ValueError(
            "Claude Code returned no structured evaluation object. Update the CLI to a version "
            "supporting `--json-schema`; no evaluation was accepted."
        )
    return response
