"""Explicit evaluator selection and terminal onboarding; credentials stay with each CLI."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EVALUATORS = ("claude", "codex")
_NAMES = {"claude": "Claude Code", "codex": "Codex"}
_LOGIN = {"claude": "claude auth login", "codex": "codex login"}


def config_dir() -> Path:
    """Resolve jot's configuration directory without creating it."""
    override = os.environ.get("JOT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg).expanduser() / "jot" if xdg else Path.home() / ".config" / "jot"


def _validate_evaluator(name) -> str:
    if not isinstance(name, str) or name not in EVALUATORS:
        raise ValueError("Evaluator must be exactly 'claude' or 'codex'.")
    return name


def _read_settings() -> dict:
    path = config_dir() / "settings.json"

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate setting")
            result[key] = value
        return result

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeError:
        raise ValueError(f"Invalid jot configuration in {path}: settings must be UTF-8 JSON.") from None
    try:
        settings = json.loads(text, object_pairs_hook=unique_keys)
        if not isinstance(settings, dict) or set(settings) - {"evaluator"}:
            raise ValueError("unknown settings")
        if "evaluator" in settings:
            _validate_evaluator(settings["evaluator"])
        return settings
    except (ValueError, UnicodeError):
        raise ValueError(
            f"Invalid jot configuration in {path}. Expected a JSON object containing only "
            "an evaluator set to 'claude' or 'codex'; repair this file before changing settings."
        ) from None


def get_evaluator(override: str | None = None) -> str:
    """Use an explicit override first, otherwise require a valid saved default."""
    if override is not None:
        return _validate_evaluator(override)
    settings = _read_settings()
    if "evaluator" not in settings:
        raise ValueError(
            "No evaluator is configured. Run `jot setup` or "
            "`jot config evaluator claude` / `jot config evaluator codex`, "
            "or pass `--evaluator claude` / `--evaluator codex` for one evaluation."
        )
    return settings["evaluator"]


def set_evaluator(name: str) -> None:
    """Atomically save an explicit default, refusing to overwrite invalid settings."""
    name = _validate_evaluator(name)
    settings = _read_settings()
    settings["evaluator"] = name
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".settings-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "settings.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup() -> None:
    """Guide a terminal user through installation/auth checks and save their choice."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError(
            "`jot setup` requires an interactive terminal. Use `jot config evaluator claude` "
            "or `jot config evaluator codex` to save an explicit default non-interactively."
        )
    # Validate before probing providers or inviting a choice that cannot be saved.
    _read_settings()
    from jot_evaluator import _run_evaluator

    available = {}
    print("Choose the CLI that will evaluate your transcripts. Credentials remain with that CLI.")
    print("Claude is tool-free. Codex uses a fresh read-only sandbox; some built-in tools remain exposed.")
    for name in EVALUATORS:
        executable = shutil.which(name)
        if executable is None:
            print(f"  {name}: not installed; install {_NAMES[name]} and add `{name}` to PATH.")
            available[name] = False
            continue
        command = [
            str(Path(executable).resolve()),
            *(["auth", "status"] if name == "claude" else ["login", "status"]),
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="jot-auth-check-") as directory:
                _, returncode = _run_evaluator(command, b"", directory, os.environ.copy(), timeout=15)
            authenticated = returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            authenticated = False
        available[name] = authenticated
        status = "installed; authentication check passed" if authenticated else "installed; authentication check failed"
        print(f"  {name}: {status}. Sign in with `{_LOGIN[name]}`.")
    try:
        choice = input("Default evaluator [claude/codex] (no automatic choice): ").strip()
    except EOFError:
        raise ValueError("Setup cancelled; no evaluator was saved.") from None
    _validate_evaluator(choice)
    if not available[choice]:
        raise ValueError(
            f"{_NAMES[choice]} is not ready. Install `{choice}` if missing, run `{_LOGIN[choice]}`, "
            "then run `jot setup` again. No evaluator was saved."
        )
    set_evaluator(choice)
    print(f"Saved default evaluator: {choice}.")
