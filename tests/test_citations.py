# Tank Monitor Console Simulator -- a training simulator for TLS-350
# compatible tank monitor consoles.
# Copyright (C) 2026 Verbose Software
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License (LICENSE) for more details.
"""Every line the panel can draw, against the manual page that draws it.

This is the standing form of the menu audit. `tools/build_citations.py` walks
every screen the console can show -- including the conditional ones, which
need a console built to satisfy each condition -- looks each line up in the
Veeder-Root manuals, and writes `citations.json`. The manuals are not in this
repository, so the lookup cannot run here; what runs here is the check that
the result still holds:

  * every screen the panel draws is either CITED to a manual and page, or is
    on the UNCITED list, which is the honest record of what has not been found
  * the uncited list cannot grow
  * nothing on either list has gone stale

A new screen with no citation fails this file. That is the point: it is not
possible to add a screen to this simulator quietly.

Regenerate with `python tools/build_citations.py` when the manuals are on the
shelf, and commit what it writes.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False

CITATIONS = json.load(open(os.path.join(HERE, "tests", "citations.json"),
                           encoding="utf-8"))

# What the uncited list stood at when this was last regenerated. It is a
# ratchet: a change that leaves more of the console unaccounted for than it
# found is a change that has to explain itself.
UNCITED_OPTION_CEILING = 0
UNCITED_CEILING = 0


def drawn_lines():
    import allscreens
    state = os.path.join(tempfile.gettempdir(), "_test_citations_state.json")
    return allscreens.distinct_lines(allscreens.enumerate_screens(state))


@unittest.skipUnless(HAVE_TK, "no display")
class EveryLineIsAccountedFor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.lines = drawn_lines()
        except Exception as exc:                    # pragma: no cover
            raise unittest.SkipTest(f"no usable Tk: {exc}")

    def test_every_drawn_line_is_cited_or_recorded_as_uncited(self):
        cited, uncited = CITATIONS["cited"], CITATIONS["uncited"]
        strays = sorted(l for l in self.lines
                        if l not in cited and l not in uncited)
        self.assertEqual(strays, [], "\n".join(
            ["these screens draw a line no manual has been found for, and "
             "which is not on the uncited list either:"]
            + [f"  |{l:<24}|  {self.lines[l][0][:4]}" for l in strays]
            + ["run tools/build_citations.py with the manuals on the shelf"]))

    def test_nothing_is_uncited(self):
        """It is zero, and it stays zero.

        Every screen this console can draw is against a manual and a page.
        A change that cannot cite a screen it adds fails here, and the way
        past it is to find the page or to draw the screen the manual draws --
        not to raise the number.
        """
        self.assertEqual(sorted(CITATIONS["uncited"]), [])
        self.assertEqual(UNCITED_CEILING, 0)

    def test_nothing_cited_has_gone_stale(self):
        gone = sorted(l for l in CITATIONS["cited"] if l not in self.lines)
        self.assertEqual(gone, [], "\n".join(
            ["citations.json cites lines the panel no longer draws:"]
            + [f"  |{l:<24}|" for l in gone[:20]]
            + ["run tools/build_citations.py"]))

    def test_nothing_uncited_has_gone_stale(self):
        gone = sorted(l for l in CITATIONS["uncited"] if l not in self.lines)
        self.assertEqual(gone, [], "\n".join(
            ["citations.json lists uncited lines the panel no longer draws:"]
            + [f"  |{l:<24}|" for l in gone[:20]]
            + ["run tools/build_citations.py"]))

    def test_every_citation_names_a_manual_and_a_page(self):
        for line, where in CITATIONS["cited"].items():
            self.assertTrue(where.get("manual"), line)
            self.assertIsInstance(where.get("page"), int, line)
            self.assertGreater(where["page"], 0, line)


@unittest.skipUnless(HAVE_TK, "no display")
class EveryOptionIsAccountedFor(unittest.TestCase):
    """A screen citation says the console asks the right question.

    This says it offers the right answers. Every label CHANGE can walk onto
    -- an enumeration's choices, a flag's two words, a console setting's list
    -- looked for in the manuals the same way, because "every option, every
    selection" is half of what a menu is.
    """

    def test_every_option_is_cited_or_recorded_as_uncited(self):
        from tls350sim.console import FIELDS
        import allscreens
        cited = CITATIONS["options_cited"]
        uncited = set(CITATIONS["options_uncited"])
        strays = []
        for key, f in sorted(FIELDS.items()):
            kind = f.get("kind")
            if kind == "enum":
                labels = [c[1] if isinstance(c, (list, tuple)) else c
                          for c in f.get("choices") or []]
            elif kind == "flag":
                labels = list(f.get("words") or ("DISABLED", "ENABLED"))
            elif kind == "setting" and f.get("choices"):
                labels = [c if isinstance(c, str) else c[1]
                          for c in f["choices"]]
            else:
                continue
            for lab in labels:
                name = f"{key}={lab}"
                if name not in cited and name not in uncited:
                    strays.append(name)
        self.assertEqual(strays, [], chr(10).join(
            ["these values CHANGE can walk onto are neither cited nor "
             "recorded as uncited:"] + [f"  {x}" for x in strays]
            + ["run tools/build_citations.py"]))

    def test_no_option_is_uncited(self):
        """Zero, and it stays zero: every value CHANGE can walk onto is
        against a manual and a page."""
        self.assertEqual(sorted(CITATIONS["options_uncited"]), [])
        self.assertEqual(UNCITED_OPTION_CEILING, 0)

    def test_an_option_may_be_wider_than_the_display_but_only_just(self):
        """A VALUE is clipped where a function NAME is not.

        Veeder-Root chose short function names so they would fit -- LEAK to
        LK, MONITOR to MON -- and did not do the same for every choice in
        every list. `PETROTECHNIK UPP EXTRA 63 MM` is 28 characters and the
        display shows `TYP: PETROTECHNIK UPP E`, which is what a console
        does. So this is a sanity bound, not the display width: a value
        longer than a whole line and a half is a paraphrase, not a screen.
        """
        for name in list(CITATIONS["options_cited"]) +                 list(CITATIONS["options_uncited"]):
            self.assertLessEqual(len(name.split("=", 1)[1]), 32, name)


class TheCitationFileItself(unittest.TestCase):
    """Runs with or without a display, so the shape is always checked."""

    def test_it_carries_both_lists_and_a_count(self):
        for key in ("cited", "uncited", "screens", "lines"):
            self.assertIn(key, CITATIONS)
        self.assertEqual(CITATIONS["lines"],
                         len(CITATIONS["cited"]) + len(CITATIONS["uncited"]))

    def test_no_line_is_on_both_lists(self):
        both = set(CITATIONS["cited"]) & set(CITATIONS["uncited"])
        self.assertEqual(both, set())

    def test_no_screen_is_wider_than_the_display(self):
        """A template is never longer than the line it came from, and a line
        is 24 columns."""
        for line in list(CITATIONS["cited"]) + list(CITATIONS["uncited"]):
            self.assertLessEqual(len(line), 24, repr(line))
