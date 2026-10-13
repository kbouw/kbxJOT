"""Offline behavioral coverage for portable saved-evaluation HTML reports."""

from html.parser import HTMLParser
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from jot_html import build_evaluation_html
from jot import _build_transcript_markdown


_VIDEO_ID = "WkBPX-oDMnA"
_OTHER_VIDEO_ID = "F8fBgDCf2Y4"
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _Element:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.parts = []

    @property
    def text(self):
        return "".join(self.parts)


class _ParsedHTML(HTMLParser):
    """Keep DOM-like observations without depending on renderer classes or IDs."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []
        self.body_parts = []
        self.nested_anchors = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else None
        element = _Element(tag, attrs, parent)
        if tag == "a" and any(item.tag == "a" for item in self.stack):
            self.nested_anchors.append(element)
        self.elements.append(element)
        if tag not in _VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element.parts.append(data)
        tags = {element.tag for element in self.stack}
        if "body" in tags and not tags.intersection({"script", "style", "template"}):
            self.body_parts.append(data)

    @property
    def visible_text(self):
        # Collapsed details still retain their readable transcript content.
        return re.sub(r"\s+", " ", " ".join(self.body_parts)).strip()

    @property
    def anchors(self):
        return [element for element in self.elements if element.tag == "a"]

    @property
    def seek_anchors(self):
        return [element for element in self.anchors if "data-start" in element.attrs]


def _saved_evaluation(
    *,
    title="A useful talk & careful evidence",
    channel="A practitioner channel",
    source=f"https://www.youtube.com/watch?v={_VIDEO_ID}",
    duration="1:20:00",
    transcribed="2026-10-13 16:19 UTC",
    method="YouTube captions (auto-generated)",
    report="## Verdict\n\n**WATCH** — Clear, practical advice.\n\n## Worth watching\n\nRead the evidence at [04:32].",
    transcript="[00:01] Opening words.\n\n[04:32] A concrete example.",
):
    return (
        f"# {title}\n\n"
        "| | |\n|---|---|\n"
        f"| **Channel** | {channel} |\n"
        f"| **Source** | <{source}> |\n"
        "| **Uploaded** | 2026-07-10 |\n"
        f"| **Duration** | {duration} |\n"
        f"| **Transcribed** | {transcribed} |\n"
        f"| **Method** | {method} |\n\n"
        "---\n\n## Watch-value evaluation\n\n"
        f"{report}\n\n---\n\n## Transcript\n\n{transcript}\n"
    )


def _inside(node, ancestor):
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


class EvaluationHTMLTests(unittest.TestCase):
    def render(self, document):
        output = build_evaluation_html(document)
        self.assertIsInstance(output, str)
        return _ParsedHTML(output)

    def test_actual_serializer_and_renderer_preserve_the_shared_envelope(self):
        source = f"https://youtu.be/{_VIDEO_ID}?si=source-token"
        report = (
            "## Verdict\n\n**WATCH** — Producer evidence.\n\n"
            "## Transcript\n\nA similarly named section inside the evaluation.\n\n"
            "## Worth watching\n\nEvidence at [04:32]."
        )
        transcript = "[04:32] Original <script>quoted text</script> & evidence."
        document = _build_transcript_markdown(
            {"title": "Producer title & detail", "channel": "Producer channel", "duration": None},
            transcript, source, "YouTube captions (manual)", report,
        )
        parsed = self.render(document)
        self.assertIn("Producer title & detail", parsed.visible_text)
        self.assertIn("Producer channel", parsed.visible_text)
        self.assertIn("Unknown", parsed.visible_text)
        self.assertIn("YouTube captions (manual)", parsed.visible_text)
        self.assertIn("A similarly named section inside the evaluation.", parsed.visible_text)
        self.assertIn("Original <script>quoted text</script> & evidence.", parsed.visible_text)
        source_links = [anchor for anchor in parsed.anchors if anchor.attrs.get("href") == source]
        self.assertEqual(len(source_links), 1)
        self.assertFalse(parsed.nested_anchors)
        for anchor in self.anchors_with_label(parsed, "04:32"):
            self.assert_seek(anchor, 272)

    def assert_seek(self, anchor, seconds, video_id=_VIDEO_ID):
        self.assertEqual(anchor.attrs.get("data-start"), str(seconds))
        href = urlsplit(anchor.attrs.get("href", ""))
        self.assertEqual(href.scheme, "https")
        self.assertIn(href.hostname, {"youtube.com", "www.youtube.com"})
        self.assertEqual(href.path, "/watch")
        params = parse_qs(href.query)
        self.assertEqual(params.get("v"), [video_id])
        self.assertEqual(params.get("t"), [str(seconds)])

    def anchors_with_label(self, parsed, label):
        return [
            anchor for anchor in parsed.anchors
            if anchor.text.strip().strip("[]") == label
        ]

    def test_valid_video_ids_are_embedded_without_network(self):
        cases = (
            ("kSzbuPFjdQo", "https://www.youtube.com/watch?v=kSzbuPFjdQo"),
            (_VIDEO_ID, f"https://youtu.be/{_VIDEO_ID}?si=share-token"),
            (_OTHER_VIDEO_ID, f"https://youtube.com/watch?v={_OTHER_VIDEO_ID}&feature=share"),
            ("AbC_dE-f012", "https://youtu.be/AbC_dE-f012"),
        )
        for video_id, source in cases:
            with self.subTest(source=source):
                parsed = self.render(_saved_evaluation(source=source))
                frames = [node for node in parsed.elements if node.tag == "iframe"]
                self.assertEqual(len(frames), 1)
                embed = urlsplit(frames[0].attrs.get("src", ""))
                self.assertEqual(embed.scheme, "https")
                self.assertIn(embed.hostname, {
                    "youtube.com", "www.youtube.com", "youtube-nocookie.com",
                    "www.youtube-nocookie.com",
                })
                self.assertEqual(embed.path, f"/embed/{video_id}")
                for anchor in parsed.seek_anchors:
                    self.assert_seek(anchor, int(anchor.attrs["data-start"]), video_id)

    def test_invalid_ids_and_non_youtube_sources_are_rejected(self):
        sources = (
            "https://youtu.be/short",
            "https://youtu.be/WkBPX-oDMnAA",
            "https://youtu.be/WkBPX.oDMnA",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/watch?v=",
            "https://www.youtube.com/watch?v=WkBPX%2FoDMnA",
            f"https://example.com/watch?v={_VIDEO_ID}",
            f"https://youtube.com.evil.example/watch?v={_VIDEO_ID}",
            f"https://notyoutube.com/watch?v={_VIDEO_ID}",
            f"https://www.youtube.com@evil.example/watch?v={_VIDEO_ID}",
            f"javascript:alert('{_VIDEO_ID}')",
            f"file:///watch?v={_VIDEO_ID}",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as raised:
                    build_evaluation_html(_saved_evaluation(source=source))
                self.assertTrue(str(raised.exception).strip())

    def test_timestamp_points_ranges_and_hours_seek_to_range_start(self):
        report = (
            "## Verdict\n\n**WATCH** — Supported advice.\n\n"
            "## Worth watching\n\n"
            "Point [04:32]; short range [07:30-09:50]; "
            "hour-long range [01:02:03–01:04:05]; bare 12:06 and "
            "bare range 14:11-14:29."
        )
        parsed = self.render(_saved_evaluation(
            report=report,
            transcript="[00:01] Opening.\n\n[01:03:04] Later evidence.",
        ))
        expected = {
            "04:32": 272,
            "07:30-09:50": 450,
            "01:02:03–01:04:05": 3723,
            "12:06": 726,
            "14:11-14:29": 851,
            "00:01": 1,
            "01:03:04": 3784,
        }
        for label, seconds in expected.items():
            with self.subTest(label=label):
                anchors = self.anchors_with_label(parsed, label)
                self.assertEqual(len(anchors), 1)
                self.assert_seek(anchors[0], seconds)
        self.assertFalse(parsed.nested_anchors)

    def test_three_saved_report_styles_retain_evidence_and_transcript(self):
        cases = (
            {
                "title": "Understanding is the new bottleneck — Geoffrey Litt",
                "channel": "AI Engineer",
                "source": f"https://youtu.be/{_VIDEO_ID}?si=original-share-token",
                "method": "YouTube captions (auto-generated)",
                "report": (
                    "## Verdict\n\n**WATCH** — Preserve creative understanding.\n\n"
                    "## Worth watching\n\n"
                    f"- **[06:02-11:51]** https://www.youtube.com/watch?v={_VIDEO_ID}&t=362 "
                    "— Complete explainer methodology.\n\n"
                    "## Limits\n\nSlide legibility cannot be verified."
                ),
                "label": "06:02-11:51",
                "seconds": 362,
                "retained": "Complete explainer methodology.",
            },
            {
                "title": "Kent Beck: Software Engineering in the Age of AI",
                "channel": "Prodacity",
                "source": f"https://www.youtube.com/watch?v={_VIDEO_ID}",
                "method": "Local transcription",
                "report": (
                    "## Verdict\n\n**SELECTIVE WATCH** — Familiar ideas with useful framing.\n\n"
                    "## Worth watching\n\n"
                    f"- [01:02:03-01:04:05](https://www.youtube.com/watch?v={_VIDEO_ID}&t=3723) "
                    "— Trade-offs before tools.\n\n"
                    "## Limits\n\nNo controlled comparison was presented."
                ),
                "label": "01:02:03-01:04:05",
                "seconds": 3723,
                "retained": "Trade-offs before tools.",
            },
            {
                "title": "If speed of writing code was your problem — Andrew Murphy",
                "channel": "Engineering talks",
                "source": f"https://youtu.be/{_VIDEO_ID}",
                "method": "YouTube captions (manual)",
                "report": (
                    "## Verdict\n\n**SKIP** — Little new evidence.\n\n"
                    "## What you can learn\n\n"
                    "The bottleneck moved [04:32-04:41], with an anecdote "
                    "[10:21] rather than a measured result.\n\n"
                    "## Limits\n\nConfidence is limited to the spoken claims."
                ),
                "label": "04:32-04:41",
                "seconds": 272,
                "retained": "rather than a measured result.",
            },
        )
        for case in cases:
            with self.subTest(title=case["title"]):
                parsed = self.render(_saved_evaluation(
                    title=case["title"], channel=case["channel"],
                    source=case["source"], method=case["method"],
                    report=case["report"], duration="1:20:00",
                    transcribed="2026-10-13 16:19 UTC",
                    transcript="[00:01] Original opening & context.\n\n[01:03:04] Original final evidence.",
                ))
                anchors = self.anchors_with_label(parsed, case["label"])
                self.assertEqual(len(anchors), 1)
                self.assert_seek(anchors[0], case["seconds"])
                self.assertFalse(parsed.nested_anchors)
                for retained in (
                    case["title"], case["channel"], case["method"],
                    "1:20:00", "2026-07-10", "2026-10-13 16:19 UTC", case["retained"],
                    "Original opening & context.", "Original final evidence.",
                ):
                    self.assertIn(retained, parsed.visible_text)
                details = [node for node in parsed.elements if node.tag == "details"]
                self.assertTrue(any("Original final evidence." in node.text for node in details))

    def test_existing_same_video_linked_ranges_are_not_double_wrapped(self):
        report = (
            "## Verdict\n\n**WATCH** — One precise section.\n\n"
            f"[07:30-09:50](https://youtu.be/{_VIDEO_ID}) "
            "and **[01:02:03–01:04:05]"
            f"(https://www.youtube.com/watch?v={_VIDEO_ID}&t=3723)**."
        )
        parsed = self.render(_saved_evaluation(report=report))
        self.assertFalse(parsed.nested_anchors)
        for label, seconds in (("07:30-09:50", 450), ("01:02:03–01:04:05", 3723)):
            anchors = self.anchors_with_label(parsed, label)
            self.assertEqual(len(anchors), 1)
            self.assert_seek(anchors[0], seconds)

    def test_other_video_timestamp_links_remain_external_without_seeking(self):
        other_url = f"https://www.youtube.com/watch?v={_OTHER_VIDEO_ID}&t=450"
        report = (
            "## Verdict\n\n**WATCH** — Compare the cited talk.\n\n"
            f"See [07:30-09:50]({other_url}), "
            f"[different speaker]({other_url}), and this talk at [04:32]."
        )
        parsed = self.render(_saved_evaluation(report=report))
        other_links = [anchor for anchor in parsed.anchors if anchor.attrs.get("href") == other_url]
        self.assertEqual(len(other_links), 2)
        for anchor in other_links:
            self.assertNotIn("data-start", anchor.attrs)
            self.assertIn("noopener", anchor.attrs.get("rel", "").split())
        self.assertEqual(self.anchors_with_label(parsed, "07:30-09:50")[0].attrs["href"], other_url)
        self.assertTrue(any(anchor.attrs.get("data-start") == "272" for anchor in parsed.seek_anchors))
        self.assertFalse(parsed.nested_anchors)

    def test_sections_render_in_reading_order_with_limits_collapsed(self):
        saved_order = (
            ("Verdict", "**WATCH** — Clear, practical advice."),
            ("What you can learn", "1. **Takeaway**: Detail [04:32]."),
            ("Novelty", "Established ideas."),
            ("Signal versus fluff", "**Analogies: MODERATE** — Efficient."),
            ("Worth watching", "- **[06:02-11:51]** Explainer walkthrough."),
            ("Limits", "Slides were not observed."),
        )
        expected = (
            "Verdict", "What you can learn", "Worth watching",
            "Signal versus fluff", "Novelty", "Limits",
        )
        # Evaluators sometimes wrap every section under their own title heading.
        for wrapper in ("", "# Talk evaluation: a wrapped report\n\n"):
            with self.subTest(wrapped=bool(wrapper)):
                report = wrapper + "\n\n".join(
                    f"## {name}\n\n{body}" for name, body in saved_order
                )
                parsed = self.render(_saved_evaluation(report=report))
                article = next(node for node in parsed.elements if node.tag == "article")
                headings = [
                    node.text.strip() for node in parsed.elements
                    if node.tag in {"h3", "h4", "h5"} and node.text.strip() in expected
                    and _inside(node, article)
                ]
                self.assertEqual(tuple(headings), expected)
                limits = [
                    node for node in parsed.elements
                    if node.tag == "details" and "Slides were not observed." in node.text
                ]
                self.assertEqual(len(limits), 1)
                self.assertNotIn("open", limits[0].attrs)

    def test_signal_explanations_preserve_internal_hyphens_and_colons(self):
        explanation = "Evidence-based explanation: follow-up details at [04:32]."
        for separator in (" ", " — ", " – ", ": "):
            with self.subTest(separator=separator):
                parsed = self.render(_saved_evaluation(
                    report=(
                        "## Verdict\n\n**WATCH** — Useful evidence.\n\n"
                        "## Signal versus fluff\n\n"
                        f"**Substantive explanation: HIGH**{separator}{explanation}"
                    ),
                ))
                self.assertIn(
                    "Evidence-based explanation: follow-up details at",
                    parsed.visible_text,
                )
                self.assert_seek(self.anchors_with_label(parsed, "04:32")[0], 272)

    def test_raw_html_and_metadata_are_text_not_executable_markup(self):
        title = '<script>alert("TITLE_ATTACK")</script> & notes'
        channel = '<img src=x onerror="alert(\'CHANNEL_ATTACK\')"> & team'
        method = '"><svg/onload=alert("METHOD_ATTACK")>'
        report_payload = '<script>alert("REPORT_ATTACK")</script>'
        transcript_payload = '<img src=x onerror="alert(\'TRANSCRIPT_ATTACK\')">'
        parsed = self.render(_saved_evaluation(
            title=title, channel=channel, method=method,
            report=(
                "## Verdict\n\n**WATCH** — Treat quotations as untrusted.\n\n"
                f"{report_payload}\n\n"
                '<iframe src="https://evil.example/FRAME_ATTACK"></iframe>\n\n'
                '<a href="javascript:alert(\'LINK_ATTACK\')">Untrusted raw link</a>'
            ),
            transcript=f"[00:01] {transcript_payload}\n\n[04:32] Safe ending.",
        ))
        for text in (title, channel, method, report_payload, transcript_payload, "Safe ending."):
            self.assertIn(text, parsed.visible_text)
        self.assertFalse(any(node.tag in {"img", "svg", "object", "embed"} for node in parsed.elements))
        frames = [node for node in parsed.elements if node.tag == "iframe"]
        self.assertEqual(len(frames), 1)
        for node in parsed.elements:
            self.assertFalse(any(name.lower().startswith("on") for name in node.attrs))
            if node.tag == "script":
                self.assertFalse(any(marker in node.text for marker in (
                    "TITLE_ATTACK", "CHANNEL_ATTACK", "METHOD_ATTACK", "REPORT_ATTACK",
                    "TRANSCRIPT_ATTACK", "FRAME_ATTACK", "LINK_ATTACK",
                )))
        self.assertFalse(any("evil.example" in node.attrs.get("src", "") for node in parsed.elements))

    def test_unsafe_markdown_links_cannot_execute(self):
        report = (
            "## Verdict\n\n**WATCH** — Untrusted link text survives.\n\n"
            "[Script bait](javascript:alert%281%29)\n\n"
            "[Mixed-case bait](JaVaScRiPt:alert%281%29)\n\n"
            "[Data bait](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)\n\n"
            "[VB bait](vbscript:msgbox%281%29)\n\n"
            "[Safe reference](https://example.com/research?x=1&y=2)"
        )
        parsed = self.render(_saved_evaluation(report=report))
        for anchor in parsed.anchors:
            href = anchor.attrs.get("href", "")
            normalized = re.sub(r"[\x00-\x20]", "", href)
            self.assertNotIn(urlsplit(normalized).scheme.lower(), {"javascript", "vbscript", "data"})
            self.assertFalse(any(name.lower().startswith("on") for name in anchor.attrs))
        safe = self.anchors_with_label(parsed, "Safe reference")
        self.assertEqual(len(safe), 1)
        self.assertEqual(safe[0].attrs["href"], "https://example.com/research?x=1&y=2")
        self.assertIn("noopener", safe[0].attrs.get("rel", "").split())
        for label in ("Script bait", "Mixed-case bait", "Data bait", "VB bait", "Safe reference"):
            self.assertIn(label, parsed.visible_text)

    def test_empty_or_malformed_saved_evaluations_raise_clear_errors(self):
        valid = _saved_evaluation()
        cases = {
            "empty": "",
            "whitespace": " \n\t ",
            "title only": "# Incomplete report\n",
            "missing title": valid.replace("# A useful talk & careful evidence\n", "", 1),
            "missing source": valid.replace(f"| **Source** | <https://www.youtube.com/watch?v={_VIDEO_ID}> |\n", "", 1),
            "missing channel": valid.replace("| **Channel** | A practitioner channel |\n", "", 1),
            "missing duration": valid.replace("| **Duration** | 1:20:00 |\n", "", 1),
            "missing transcribed": valid.replace("| **Transcribed** | 2026-10-13 16:19 UTC |\n", "", 1),
            "missing method": valid.replace("| **Method** | YouTube captions (auto-generated) |\n", "", 1),
            "missing evaluation heading": valid.replace("## Watch-value evaluation", "## Notes", 1),
            "missing transcript heading": valid.replace("## Transcript", "## Captions", 1),
            "empty evaluation": _saved_evaluation(report=""),
            "empty transcript": _saved_evaluation(transcript=""),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError) as raised:
                    build_evaluation_html(document)
                self.assertTrue(str(raised.exception).strip())


if __name__ == "__main__":
    unittest.main()
