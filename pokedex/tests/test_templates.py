"""Checks over the template files themselves, rather than their output."""

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"


class CommentSyntaxTests(SimpleTestCase):
    def test_no_multiline_hash_comments(self):
        """``{# #}`` cannot span lines -- Django prints it to the page instead.

        This shipped: the winner's badge on a battle result carried a two-line
        {# #} note, and the note rendered above the word "Winner". Nothing else
        catches it, because the template still parses and still returns 200.
        Multi-line notes must use {% comment %}.
        """
        offenders = []

        for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\{#(.*?)#\}", source, re.S):
                if "\n" in match.group(1):
                    line = source[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(TEMPLATE_ROOT)}:{line}")

        self.assertEqual(
            offenders,
            [],
            "multi-line {# #} comments render as visible text; use {% comment %}",
        )
