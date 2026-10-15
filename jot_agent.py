"""Source requests, jot-owned evaluator execution, and evidence-gated publication.

Fresh evaluator processes receive only the trusted protocol and source packet.
Local checks establish structural and exact-evidence validity, not factual truth.
"""

import hashlib
import json
import math
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from jot_html import _watch_url, _youtube_id, add_evaluation, build_evaluation_html, parse_transcript_document


_ASSETS = Path(__file__).resolve().parent / "jot_assets"
_SIGNAL_LABELS = {
    "substance": "Substantive explanation", "stories": "Stories and anecdotes",
    "analogies": "Analogies", "repetition": "Repetition", "marketing": "Marketing",
    "housekeeping": "Introductions and housekeeping",
}


def read_asset(name: str) -> str:
    """Read one packaged workflow asset, without provider or configuration access."""
    return (_ASSETS / name).read_text(encoding="utf-8")


def _strict_json(text: str, label: str):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label}: duplicate JSON key {key!r}.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"{label}: non-finite JSON constant {value} is not allowed.")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            invalid_constant(value)
        return number

    try:
        return json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant, parse_float=finite_float)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def _digest(packet: dict) -> str:
    # An unsigned digest detects mismatches, not the identity of the packet author.
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trusted_protocol() -> tuple[str, dict]:
    rubric = read_asset("evaluation-rubric.md")
    schema = _strict_json(read_asset("evaluation-response.schema.json"), "Packaged response schema")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"Packaged response schema is invalid: {exc.message}") from exc
    return rubric, schema


def _filename(title: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title)
    return re.sub(r"\s+", " ", title).strip(" .")[:120] or "transcript"


def _write_new(path: Path, text: str) -> None:
    # Only remove files created here; exclusive-open failures leave existing files intact.
    stream = path.open("x", encoding="utf-8", newline="")
    try:
        with stream:
            stream.write(text)
    except Exception as write_error:
        try:
            path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise OSError(
                f"Cannot write {path}: {write_error}; "
                f"cannot remove incomplete file: {cleanup_error}"
            ) from write_error
        raise


def prepare_evaluation(source: Path, background: str | None = None, output_dir: Path | None = None) -> Path:
    """Write a complete, collision-safe request beside a plain saved transcript.

    Preserve UTF-8 source bytes (including BOM and line endings) in the packet.
    Bad source/protocol raises ValueError before creating an output directory.
    """
    source = Path(source).expanduser().resolve()
    document = source.read_bytes().decode("utf-8")
    _, metadata, transcript = parse_transcript_document(document)
    _segments(transcript, _duration(metadata["Duration"]))
    rubric, schema = _trusted_protocol()
    packet = {"version": 1, "source_document": document, "viewer_background": background,
              "rubric": rubric, "response_schema": schema}
    packet["request_id"] = _digest(packet)
    serialized = json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    directory = Path(output_dir).expanduser().resolve() if output_dir is not None else source.parent
    directory.mkdir(parents=True, exist_ok=True)
    stem = _filename(source.stem)
    counter = 0
    while True:
        suffix = f" ({counter})" if counter else ""
        target = directory / f"{stem}{suffix}.request.json"
        try:
            _write_new(target, serialized)
        except FileExistsError:
            counter += 1
        else:
            return target


def _timestamp(value: str, label: str) -> int:
    parts = value.split(":")
    if not re.fullmatch(r"(?:\d{2,}:)?\d{2}:\d{2}", value) or int(parts[-1]) >= 60 or (len(parts) == 3 and int(parts[-2]) >= 60):
        raise ValueError(f"{label}: invalid timestamp {value!r}; expected [MM:SS] or [HH:MM:SS].")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _duration(value: str) -> float | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = float(value)
        return number if math.isfinite(number) else None
    if re.fullmatch(r"(?:\d{2,}:)?\d{2}:\d{2}", value):
        return _timestamp(value, "Duration")
    return None


def _segments(transcript: str, duration: float | None) -> dict[int, list[str]]:
    segments = {}
    current = None
    content = []
    # Split only physical newlines: quotes retain every other source character.
    for line in re.split(r"\r\n|\r|\n", transcript):
        marker = re.match(r"^\[([^\]]+)\]", line)
        if marker and re.match(r"\d+:", marker.group(1)):
            seconds = _timestamp(marker.group(1), "Transcript")
            if current is not None and seconds < current:
                raise ValueError("Transcript timestamps are not ordered: a timestamp decreases.")
            if duration is not None and seconds > duration:
                raise ValueError(f"Transcript timestamp {seconds} exceeds known Duration {duration:g}.")
            if current is not None:
                segments.setdefault(current, []).append("\n".join(content))
            current = seconds
            content = [line[marker.end():]]
        else:
            content.append(line)
    if current is not None:
        segments.setdefault(current, []).append("\n".join(content))
    return segments


def _validate(request: dict, response: dict) -> tuple[str, dict]:
    fields = {"version", "source_document", "viewer_background", "rubric", "response_schema", "request_id"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("Request must contain exactly version, source_document, viewer_background, rubric, response_schema, request_id.")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ValueError("Request version must be integer 1.")
    if not isinstance(request["source_document"], str):
        raise ValueError("Request source_document must be text.")
    background = request["viewer_background"]
    if background is not None and not isinstance(background, str):
        raise ValueError("Request viewer_background must be text or null.")
    rubric, schema = _trusted_protocol()
    if request["rubric"] != rubric:
        raise ValueError("Request rubric differs from the packaged trusted rubric; prepare a new request.")
    # Compare canonical JSON too: Python equality would equate true with 1.
    if json.dumps(request["response_schema"], sort_keys=True, allow_nan=False) != json.dumps(schema, sort_keys=True):
        raise ValueError("Request response_schema differs from the packaged trusted schema; prepare a new request.")
    digest = _digest({key: value for key, value in request.items() if key != "request_id"})
    if request["request_id"] != digest:
        raise ValueError("Request digest mismatch: the packet was changed after preparation.")
    title, metadata, transcript = parse_transcript_document(request["source_document"])
    errors = sorted(Draft202012Validator(schema).iter_errors(response), key=lambda error: str(list(error.absolute_path)))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "response"
        raise ValueError(f"Response {location}: {error.message}")
    if response["request_id"] != digest:
        raise ValueError("Response request_id does not match this request.")
    if not (background and background.strip()) and response["novelty"]["personal"] != "unknown":
        raise ValueError("Response novelty.personal must be unknown without viewer background.")
    duration = _duration(metadata["Duration"])
    segments = _segments(transcript, duration)

    def validate_evidence(evidence, location):
        for citation in evidence:
            timestamp, quote = citation["timestamp"], citation["quote"]
            if segments:
                if timestamp is None:
                    raise ValueError(f"{location}: null citation timestamp is only valid for untimed sources.")
                spans = segments.get(timestamp, [])
            else:
                if timestamp is not None:
                    raise ValueError(f"{location}: cannot invent a timestamp for an untimed source.")
                spans = [transcript]
            if not any(quote in span for span in spans):
                raise ValueError(f"{location}: quote is not an exact source span at timestamp {timestamp!r}: {quote!r}.")

    for index, takeaway in enumerate(response["takeaways"]):
        validate_evidence(takeaway["evidence"], f"takeaways[{index}]")
    for category, signal in response["signal"].items():
        validate_evidence(signal["evidence"], f"signal.{category}")
    for category in ("worth_watching", "skippable"):
        for index, interval in enumerate(response[category]):
            location = f"{category}[{index}]"
            if not segments:
                raise ValueError(f"{location}: cannot invent timestamp ranges for an untimed source.")
            if interval["end"] <= interval["start"]:
                raise ValueError(f"{location}: end must be greater than start.")
            if duration is not None and interval["end"] > duration:
                raise ValueError(f"{location}: range exceeds known Duration {duration:g}.")
            validate_evidence(interval["evidence"], location)
            for citation in interval["evidence"]:
                if not interval["start"] <= citation["timestamp"] < interval["end"]:
                    raise ValueError(f"{location}: evidence timestamp must lie in [start, end).")
    return title, metadata


def _literal(text: str) -> str:
    # Escape CommonMark syntax. The shared renderer also decorates plain URLs
    # and times, so keep these untrusted spans in literal inline code instead.
    # Only validated citation/range values may become navigation links.
    fragments = []
    position = 0
    for match in re.finditer(r"https?://[^\s]+|\d+:\d+(?::\d+)?", text, re.IGNORECASE):
        fragments.append(re.sub(r"([\\`*_{}\[\]()#+.!|>~<:&-])", r"\\\1", text[position:match.start()]))
        span = match.group()
        delimiter = "`" * (1 + max((len(run) for run in re.findall(r"`+", span)), default=0))
        fragments.append(f"{delimiter} {span} {delimiter}")
        position = match.end()
    fragments.append(re.sub(r"([\\`*_{}\[\]()#+.!|>~<:&-])", r"\\\1", text[position:]))
    return "".join(fragments)


def _clock(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _report(response: dict, metadata: dict) -> str:
    source = _watch_url(_youtube_id(metadata["Source"]))
    lines = ["## Verdict", "", f"**{response['verdict']['decision']}** — {_literal(response['verdict']['reason'])}",
             "", f"Audience: {_literal(response['verdict']['audience'])}. Confidence: {response['verdict']['confidence']}.",
             "", "## What you can learn", ""]

    def evidence(citations):
        for citation in citations:
            timestamp = citation["timestamp"]
            if timestamp is not None:
                timestamp = int(timestamp)
            label = "Untimed source" if timestamp is None else f"[{_clock(timestamp)}]({source}&t={timestamp})"
            lines.extend([f"> {label} — {_literal(citation['quote'])}", ""])

    if not response["takeaways"]:
        lines.extend(["No substantive takeaways identified.", ""])
    for takeaway in response["takeaways"]:
        lines.extend([f"- **{_literal(takeaway['idea'])}** — {_literal(takeaway['why'])}", ""])
        evidence(takeaway["evidence"])
    novelty = response["novelty"]
    lines.extend(["## Novelty", "", _literal(novelty["assessment"]), "",
                  f"Personal novelty: {novelty['personal']}. {_literal(novelty['reason'])}", "",
                  "## Signal versus fluff", ""])
    for category, label in _SIGNAL_LABELS.items():
        signal = response["signal"][category]
        rating = signal["rating"].replace("_", " ").upper()
        lines.extend([f"- **{label}: {rating}** — {_literal(signal['reason'])}", ""])
        evidence(signal["evidence"])
    lines.extend(["## Worth watching", "", "Range boundaries are approximate; intervals include the start and exclude the end.", ""])
    for category, label in (("worth_watching", "Worth watching"), ("skippable", "Skippable")):
        if not response[category]:
            lines.extend([f"{label}: no supported ranges identified.", ""])
        for interval in response[category]:
            start, end = int(interval["start"]), int(interval["end"])
            lines.extend([f"- {label}: [{_clock(start)}–{_clock(end)}]({source}&t={start}) (approximate) — {_literal(interval['reason'])}", ""])
            evidence(interval["evidence"])
    lines.extend(["## Limits", ""])
    lines.extend(f"- {_literal(limit)}" for limit in response["limits"])
    lines.extend(["- Speech-only assessment: visuals, demonstrations, and delivery are not observed.",
                  "- No independent fact checking or world-level novelty verification.",
                  "- Local validation checks packet integrity, response structure, and exact evidence/ranges; it does not establish full coverage, semantic quality, or evaluator independence."])
    return "\n".join(lines)


def publish_evaluation(request_path: Path, response_path: Path, output_dir: Path | None = None) -> tuple[Path, Path]:
    """Validate completely, then save a new evaluation Markdown and HTML pair.

    Invalid packets/evidence raise ValueError with no output. Files are never
    overwritten. If HTML saving fails, OSError identifies the preserved Markdown.
    """
    request_path = Path(request_path).expanduser().resolve()
    response_path = Path(response_path).expanduser().resolve()
    request = _strict_json(request_path.read_bytes().decode("utf-8"), "Request")
    response = _strict_json(response_path.read_bytes().decode("utf-8"), "Response")
    title, metadata = _validate(request, response)
    document = add_evaluation(request["source_document"], _report(response, metadata))
    html_document = build_evaluation_html(document)
    directory = Path(output_dir).expanduser().resolve() if output_dir is not None else request_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    stem, counter = _filename(title), 0
    while True:
        suffix = f" ({counter})" if counter else ""
        markdown_path = directory / f"{stem}{suffix}.evaluation.md"
        html_path = markdown_path.with_suffix(".html")
        if html_path.exists():
            counter += 1
            continue
        try:
            _write_new(markdown_path, document)
        except FileExistsError:
            counter += 1
            continue
        break
    try:
        _write_new(html_path, html_document)
    except OSError as exc:
        raise OSError(f"Cannot save HTML companion {html_path}: {exc}\nMarkdown preserved at: {markdown_path}") from exc
    return markdown_path, html_path


def evaluate_transcript(
    source: Path, background: str | None = None, output_dir: Path | None = None,
    *, evaluator: str | None = None,
) -> tuple[Path, Path]:
    """Run a fresh evaluator and publish only a fully validated response.

    Preserve the source and request on failure. Invalid model output never
    becomes a report; successful request/response artifacts remain auditable.
    """
    from jot_evaluator import invoke_evaluator

    request_path = prepare_evaluation(source, background, output_dir)
    packet = _strict_json(request_path.read_text(encoding="utf-8"), "Request")
    _, metadata, transcript = parse_transcript_document(packet["source_document"])
    segments = _segments(transcript, _duration(metadata["Duration"]))
    # Offer exact citation candidates with numeric times, avoiding model
    # timestamp arithmetic. The original source remains the validation authority.
    citation_lookup = [
        {"timestamp": timestamp, "quote": line.strip()}
        for timestamp, spans in (segments.items() if segments else [(None, [transcript])])
        for span in spans for line in re.split(r"\r\n|\r|\n", span) if line.strip()
    ]
    response = invoke_evaluator(
        {**packet, "citation_lookup": citation_lookup},
        read_asset("evaluator-instructions.md"),
        evaluator=evaluator,
    )
    serialized = json.dumps(response, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    counter = 0
    while True:
        suffix = f" ({counter})" if counter else ""
        response_path = request_path.with_name(f"{request_path.stem}{suffix}.response.json")
        try:
            _write_new(response_path, serialized)
        except FileExistsError:
            counter += 1
        else:
            break
    return publish_evaluation(request_path, response_path, output_dir)
