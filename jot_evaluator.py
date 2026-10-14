"""Fresh, tool-free evaluations through the installed Claude Code CLI."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

from jot_agent import _strict_json


_TIMEOUT_SECONDS = 600
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
            with subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=directory,
                env=environment,
                start_new_session=os.name == "posix",
            ) as process:
                try:
                    stdout, _ = process.communicate(request, timeout=_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    # Kill descendants too: an inherited output pipe must not keep
                    # timeout cleanup waiting after the CLI itself has exited.
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    raise ValueError(
                        f"Claude Code evaluation timed out after {_TIMEOUT_SECONDS:g} seconds. "
                        "Check your connection and Claude Code authentication before trying again."
                    ) from None
                returncode = process.returncode
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
