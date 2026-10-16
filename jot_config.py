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


class EvaluatorSetupRequired(ValueError):
    """Carry discovered readiness to a host assistant without choosing a provider."""

    def __init__(self, evaluators: list[dict] | None = None):
        super().__init__("Choose an evaluator to complete setup.")
        self.report = {"status": "setup_required", "evaluators": evaluators or []}


def get_evaluator(override: str | None = None) -> str:
    """Use an explicit override first, otherwise require a valid saved default."""
    if override is not None:
        return _validate_evaluator(override)
    settings = _read_settings()
    if "evaluator" not in settings:
        raise EvaluatorSetupRequired()
    return settings["evaluator"]


def ensure_evaluator(override: str | None = None) -> str:
    """Start onboarding only when evaluation has no explicit or saved provider."""
    try:
        return get_evaluator(override)
    except EvaluatorSetupRequired:
        return setup()


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


def setup(evaluator: str | None = None) -> str:
    """Discover readiness, obtain an explicit choice, and save it without credentials.

    Terminals prompt directly. Noninteractive hosts receive structured readiness
    and complete setup with the user's choice through ``setup(evaluator=...)``.
    """
    if evaluator is not None:
        _validate_evaluator(evaluator)
    _read_settings()
    from jot_evaluator import _run_evaluator

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    prompting = interactive and evaluator is None
    if prompting:
        print("Let's set up the evaluator before continuing.")
        print("Claude is tool-free. Codex uses a fresh read-only sandbox; some built-in tools remain exposed.")
    available = {}
    for name in (EVALUATORS if evaluator is None else (evaluator,)):
        executable = shutil.which(name)
        authenticated = False
        if executable is not None:
            command = [
                str(Path(executable).resolve()),
                *(["auth", "status"] if name == "claude" else ["login", "status"]),
            ]
            try:
                with tempfile.TemporaryDirectory(prefix="jot-auth-check-") as directory:
                    _, returncode = _run_evaluator(command, b"", directory, os.environ.copy(), timeout=15)
                authenticated = returncode == 0
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        available[name] = {
            "name": name, "installed": executable is not None, "authenticated": authenticated,
        }
        if prompting:
            if executable is None:
                print(f"  {name}: not installed; install {_NAMES[name]} and add `{name}` to PATH.")
            else:
                status = "authentication check passed" if authenticated else "authentication check failed"
                print(f"  {name}: installed; {status}.")
                if not authenticated:
                    print(f"    Sign in with `{_LOGIN[name]}`.")
    if evaluator is None and not interactive:
        raise EvaluatorSetupRequired(list(available.values()))
    try:
        choice = evaluator if evaluator is not None else input("Default evaluator [claude/codex]: ").strip()
    except EOFError:
        raise ValueError("Setup cancelled; no evaluator was saved.") from None
    _validate_evaluator(choice)
    if not available[choice]["authenticated"]:
        raise ValueError(
            f"{_NAMES[choice]} is not ready. Install `{choice}` if missing, run `{_LOGIN[choice]}`, "
            "then complete setup again. No evaluator was saved."
        )
    set_evaluator(choice)
    print(f"Saved default evaluator: {choice}.")
    return choice
