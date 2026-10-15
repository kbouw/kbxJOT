"""Fresh evaluations: tool-free Claude Code or restricted, read-only Codex CLI."""

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
from jot_config import get_evaluator


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
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
})


def find_evaluator(evaluator: str | None = None) -> str:
    """Locate the selected CLI or explain how to install and authenticate it."""
    name = get_evaluator(evaluator)
    executable = shutil.which(name)
    if executable is None:
        raise ValueError(
            f"{'Claude Code' if name == 'claude' else 'Codex'} is required for evaluation. "
            f"Install the {'Claude Code' if name == 'claude' else 'Codex'} CLI, "
            f"make `{name}` available on PATH, and sign in with "
            f"`{'claude auth login' if name == 'claude' else 'codex login'}`."
        )
    # PATH can contain relative entries; the child runs outside the caller's directory.
    return str(Path(executable).resolve())


def _run_evaluator(command, request, directory, environment, *, timeout=None):
    """Exchange one request with a contained process, with bounded output and lifetime.

    communicate() buffers both output streams without a size limit. Dedicated
    pipe workers also work on Windows, where selectors cannot watch these pipes.
    Only stdout is retained; stderr is counted and discarded.
    """
    if timeout is None:
        timeout = _TIMEOUT_SECONDS
    job = None
    process = None
    workers = []
    primary_error = None
    completed = queue.Queue(maxsize=3)
    stdout = bytearray()
    deadline = time.monotonic() + timeout

    def write_request():
        try:
            if job is not None:
                # The Windows launcher cannot spawn the CLI until job assignment.
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
                        f"Evaluator exceeded the {name} limit of "
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
                raise subprocess.TimeoutExpired(command, timeout) from None
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


def _codex_schema(schema):
    """Remove unsupported native constraints; the complete schema remains mandatory locally."""
    if isinstance(schema, list):
        return [_codex_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    adapted = {}
    for key, value in schema.items():
        if key in {"$schema", "not", "allOf", "if", "then", "else"}:
            continue
        if key in {"properties", "$defs", "definitions"}:
            adapted[key] = {name: _codex_schema(child) for name, child in value.items()}
        else:
            adapted[key] = _codex_schema(value)
    return adapted


def _decode_codex(stdout):
    """Accept a structured final message only after one successfully completed native turn."""
    response_text = None
    thread_started = turn_started = turn_completed = False
    try:
        for line in stdout.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = _strict_json(line, "Codex event")
            if not isinstance(event, dict) or turn_completed:
                raise ValueError("invalid event sequence")
            kind = event.get("type")
            if kind == "thread.started" and not thread_started and not turn_started:
                thread_started = True
            elif kind == "turn.started" and thread_started and not turn_started:
                turn_started = True
            elif kind in {"item.started", "item.updated", "item.completed"} and turn_started:
                item = event.get("item")
                if not isinstance(item, dict) or item.get("type") == "error":
                    raise ValueError("invalid item")
                if kind == "item.completed" and item.get("type") == "agent_message":
                    response_text = item.get("text")
            elif kind == "turn.completed" and turn_started:
                turn_completed = True
            else:
                # Includes turn.failed/error, even if later output looks successful.
                raise ValueError("failed or unexpected native event")
        if not turn_completed or not isinstance(response_text, str):
            raise ValueError("no successfully completed final message")
        response = _strict_json(response_text, "Codex final response")
        if not isinstance(response, dict):
            raise ValueError("final message is not an object")
        return response
    except (ValueError, UnicodeError):
        raise ValueError(
            "Codex did not return a valid structured evaluation from a successfully completed turn. "
            "Check `codex login`, your connection, and CLI version; no evaluation was accepted."
        ) from None


def invoke_evaluator(packet: dict, instructions: str, *, evaluator: str | None = None) -> dict:
    """Return one selected provider's completed response for mandatory local report validation.

    Claude has no tools. Codex is ephemeral and read-only with restrictive capability settings,
    but the CLI cannot remove every model-advertised built-in tool (notably apply_patch).
    Neither source text nor authentication diagnostics are included in errors.
    """
    evaluator = get_evaluator(evaluator)
    label = "Claude Code" if evaluator == "claude" else "Codex"
    login = "claude auth login" if evaluator == "claude" else "codex login"
    schema = packet.get("response_schema")
    if not isinstance(schema, dict):
        raise ValueError("Evaluation request requires a JSON object response_schema.")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Evaluation requires the packaged trusted evaluator instructions.")
    try:
        request = (json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        # Provider-side subsets are generation constraints, never the local trust boundary.
        native_schema = (
            {key: value for key, value in schema.items() if key != "$schema"}
            if evaluator == "claude" else _codex_schema(schema)
        )
        serialized_schema = json.dumps(native_schema, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Evaluation request must contain valid, finite UTF-8 JSON values.") from None
    executable = find_evaluator(evaluator)
    environment = {key: value for key, value in os.environ.items() if key not in _SESSION_ENV_VARS}
    try:
        with tempfile.TemporaryDirectory(prefix="jot-evaluator-") as directory:
            if evaluator == "claude":
                command = [
                    executable, "--print", "--safe-mode", "--tools", "",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
                    "--output-format", "json", "--json-schema", serialized_schema,
                    "--system-prompt", instructions,
                ]
                working_directory = directory
            else:
                # Schema is outside the empty working root, never a project instruction file.
                schema_path = Path(directory) / "response.schema.json"
                schema_path.write_text(serialized_schema, encoding="utf-8")
                working_directory = Path(directory) / "work"
                working_directory.mkdir()
                command = [
                    executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                    "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                    "--output-schema", str(schema_path),
                ]
                for setting in (
                    'approval_policy="never"', 'web_search="disabled"', "mcp_servers={}",
                    "project_doc_max_bytes=0", "features.skip_host_skill_discovery=true",
                    # This suppresses only the CLI's development-feature startup notice.
                    "suppress_unstable_features_warning=true",
                    "features.shell_tool=false", "features.view_image=false",
                    "features.apps=false", "features.plugins=false", "features.hooks=false",
                    "features.memories=false", "features.multi_agent=false",
                    "features.multi_agent_v2=false", "features.image_generation=false",
                    "features.request_permissions_tool=false", "features.code_mode.enabled=false",
                    "features.token_budget=false", "features.current_time_reminder=false",
                    "features.sleep_tool=false", "features.skill_search=false",
                    "features.skill_mcp_dependency_install=false",
                    "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false",
                    # Built-in provider IDs cannot be overridden in current Codex.
                    # This explicit OpenAI definition keeps native auth/endpoint selection.
                    'model_provider="jot_openai"',
                    'model_providers.jot_openai.name="OpenAI"',
                    "model_providers.jot_openai.requires_openai_auth=true",
                    'model_providers.jot_openai.wire_api="responses"',
                    "model_providers.jot_openai.request_max_retries=0",
                    "model_providers.jot_openai.stream_max_retries=0",
                    "developer_instructions=" + json.dumps(instructions, ensure_ascii=False),
                ):
                    command.extend(("-c", setting))
                command.append("-")
            stdout, returncode = _run_evaluator(command, request, working_directory, environment)
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"{label} evaluation timed out after {_TIMEOUT_SECONDS:g} seconds. "
            f"Check your connection and run `{login}` before trying again."
        ) from None
    except FileNotFoundError:
        raise ValueError(
            f"The {label} executable became unavailable. Reinstall it or correct PATH, "
            f"then sign in with `{login}`."
        ) from None
    except OSError as exc:
        raise OSError(
            exc.errno,
            f"Cannot run the {label} evaluator. Check executable permissions, temporary "
            f"directory access, and that `{evaluator} --version` runs successfully. "
            f"Authenticate with `{login}`.",
        ) from None
    if returncode != 0:
        raise ValueError(
            f"{label} evaluation failed (exit status {returncode}). "
            f"Check `{login}`, your connection, and your {label} configuration."
        )
    if evaluator == "codex":
        return _decode_codex(stdout)
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
            "Claude Code did not complete evaluation successfully. Check `claude auth login`, "
            "connection, and CLI configuration; no evaluation was accepted."
        )
    response = envelope.get("structured_output")
    if not isinstance(response, dict):
        raise ValueError(
            "Claude Code returned no structured evaluation object. Update the CLI to a version "
            "supporting `--json-schema`; no evaluation was accepted."
        )
    return response
