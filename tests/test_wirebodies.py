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
"""The report BODIES, on a console that has sensors on it.

FIDELITY L1, which is the entry every other finding in that block sat behind.
The census sweep proves a code ANSWERS; it fits every card and programmes
**zero sensor positions**, so `_devices()` hands back an empty list for every
family and every per-device loop body is skipped. Tracing that sweep leaves
thirty-seven of `wiresensors`' fifty-five functions never called -- every row
builder, every header, every value formatter -- and a heading printed over an
empty table looks exactly like a heading printed over a right one.

So this file's whole contribution is `a_programmed_site`: the same full cage
with every family's config screen switched ON and a label behind each
position. The tests are then the ones test_wirecolumns already runs on an
empty console, asked again where the rows exist -- 526 of the manual's own
lines reached against 463 -- plus the ones that found the defects below.

**What it caught first time out.** `S707`, Set Vapor Sensor Location Label,
was missing from `DEVICE_PREFIXED`, so `Console.text` did not strip the
device number the panel stores in front of a label and every vapour sensor
printed `V 1 OTHER   01VAPOR WELL #1` where its liquid, groundwater, 2-wire
and 3-wire siblings printed theirs clean. Six more codes were missing with
it. No test could see any of them, because no test had a labelled sensor.
"""
import os
import re
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, wire, wiretables              # noqa: E402
from tls350sim.console import Console, DEVICE_PREFIXED       # noqa: E402
from tls350sim.wire import Handler                           # noqa: E402

REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "reference")

# Each sensor family as its own config screen numbers it: the config code the
# position is switched on in, the label code, the card, and how many of that
# card's positions this fixture wires up. `Console.CONFIG_LABEL` holds the
# same pairing -- this table adds the card and the count, which is what a
# fixture needs and a lookup does not.
FAMILIES = (
    ("701", "702", "liquid", 2, "LIQUID SUMP"),
    ("706", "707", "vapor", 2, "VAPOR WELL"),
    ("711", "712", "gw", 2, "GW WELL"),
    ("741", "742", "2wire", 2, "2W SUMP"),
    ("746", "747", "3wire", 2, "3W SUMP"),
    ("721", "722", "smart", 4, "SMART SUMP"),
    ("801", "802", "io", 2, "EXT INPUT"),
    ("806", "807", "relay", 2, "RELAY"),
    ("7C4", "7C5", "pumpmon", 2, "PUMP RELAY"),
)

# the four smart sensor categories that have a diagnostic of their own, one
# per position, so a per-category report has both a device to draw and a
# device to leave out
SMART_KINDS = (("01", "03"), ("02", "08"), ("03", "05"), ("04", "06"))


def a_programmed_site():
    """A full cage with every family switched on and labelled.

    `a_full_site` in test_wirecolumns fits the cards; this one connects the
    detection systems, which is the difference between a report that prints a
    heading and a report that prints a report.
    """
    console = Console()
    presets.load(console, "Truck stop, four tanks and BIR")
    for card in ("liquid", "vapor", "gw", "2wire", "3wire", "vlld", "pump",
                 "pumpmon", "io", "relay", "smart", "plld", "wplld", "probe",
                 "universal"):
        console.modules.setdefault(card, 4)
    for config, label, _module, count, name in FAMILIES:
        for n in range(1, count + 1):
            # `_configured` reads the last character, which is the on/off flag
            console.values[f"S{config}{n:02d}"] = f"{n:02d}1"
            console.values[f"S{label}{n:02d}"] = (
                f"{n:02d}" + f"{name} #{n}".ljust(20))
    for device, category in SMART_KINDS:
        console.values[f"S723{device}"] = device + category
    console.tick()
    return console, Handler(console, verbose=False)


def send(handler, command):
    return handler.handle(
        (chr(1) + command + chr(13)).encode()).decode("latin-1")


def body(handler, command):
    return (send(handler, command).replace(chr(1), "").replace(chr(3), "")
        .strip(chr(13) + chr(10)))


class TheFixtureProgrammesWhatItClaimsTo(unittest.TestCase):
    """A floor under every test below.

    The defect this file exists to catch is invisible on an unprogrammed
    console, so a fixture that quietly stopped programming would turn the
    rest of the file green for the exact reason it was written.
    """

    def test_every_family_has_connected_devices(self):
        c, _h = a_programmed_site()
        for config, _label, module, count, _name in FAMILIES:
            got = c.configured_devices(config, c.capacity(module))
            self.assertEqual(got, list(range(1, count + 1)),
                             f"{module} ({config})")

    def test_the_smart_card_carries_four_categories(self):
        c, _h = a_programmed_site()
        kinds = [c.sensor_type("smart", n) for n in range(1, 5)]
        self.assertEqual(kinds, [k for _d, k in SMART_KINDS])


class NoLabelCarriesItsDeviceNumber(unittest.TestCase):
    """The class the vapour sensor was one instance of.

    The panel stores a label with the device number in front of it -- that is
    what the inquire response echoes -- and `Console.text` strips it back off
    for anything that prints it, but only for a code listed in
    `DEVICE_PREFIXED`. A label code missing from that list prints its own
    device number as the first two characters of the site's name for the
    sump, and does it on every report that names the device.

    Asserted across every family at once rather than on the one that was
    wrong, because the list is where the defect lives and one family's entry
    says nothing about the next one's.
    """

    def test_every_family_label_code_is_prefixed(self):
        for _config, label, _module, _count, _name in FAMILIES:
            self.assertIn(int(label, 16), DEVICE_PREFIXED,
                          f"S{label} stores a device prefix nothing strips")

    def test_the_stored_label_reads_back_without_it(self):
        c, _h = a_programmed_site()
        for _config, label, _module, count, name in FAMILIES:
            for n in range(1, count + 1):
                self.assertEqual(c.text(label, n), f"{name} #{n}")

    def test_no_report_prints_a_label_with_its_number_glued_on(self):
        """The end of it: sweep the reports and look at the paper.

        `112` is the one that showed it -- the status report names every
        device on the console in one table -- but the assertion is over
        every report, because a label reaches the paper from more than one
        place.
        """
        _c, h = a_programmed_site()
        glued = []
        for _config, _label, _module, count, name in FAMILIES:
            for n in range(1, count + 1):
                wanted = f"{n:02d}{name} #{n}"
                for token in ("112", "101", "102"):
                    if wanted in body(h, f"I{token}00"):
                        glued.append(f"I{token}: {wanted}")
        self.assertEqual(glued, [])


class TheLineLabelsWereNotInTheLabelSweep(unittest.TestCase):
    """FIDELITY L17. `FAMILIES` above is every SENSOR family, and the sweep
    that proved no label carries its device number ran over exactly those.
    A line is not a sensor: the two line labels are `782` for PLLD and `7A2`
    for WPLLD -- VLLD has none, its `752` is a tank number -- and only the
    PLLD one was in `DEVICE_PREFIXED`.

    So `S7A201WEST LINE` answered `i7A201...WEST LINE` where its PLLD twin
    answered `01EAST LINE` -- the record two characters short of the
    manual's `<SOH>i7A2WWYYMMDDHHmmWWaaaa...`, and a dump and restore that
    does not round trip. The same blind spot is why the census test above
    could only fail on a sensor.
    """

    LINES = (("782", "plld", "EAST LINE"),
             ("7A2", "wplld", "WEST LINE"))

    def test_a_line_label_is_stored_with_its_number_and_read_without_it(self):
        for code, _kind, label in self.LINES:
            self.assertIn(int(code, 16), DEVICE_PREFIXED,
                          f"S{code} stores a device prefix nothing strips")
            c, h = a_programmed_site()
            reply = send(h, "S" + code + "01" + label)
            self.assertNotIn("9999", reply)
            self.assertEqual(c.values.get("S" + code + "01"), "01" + label)
            self.assertEqual(c.text(code, 1), label)
            # "<SOH>i7A2WWYYMMDDHHmmWWaaaa...": the record repeats the line
            # number, which is the whole of what DEVICE_PREFIXED records
            self.assertIn("01" + label, send(h, "i" + code + "01"))


class TheRowsUnderTheHeadingsAreTheManualsOwn(unittest.TestCase):
    """test_wirecolumns' sweep, asked where the rows exist.

    Same comparison, same recorded lines: what changes is that the console
    underneath has devices, so the sample lines that are ROWS are reached
    instead of skipped.
    """

    def lines_of(self, spec):
        seen = [spec[key] for key in ("title", "heading") if spec.get(key)]
        return seen + list(spec.get("sample") or [])

    def test_no_line_drifts_from_the_page(self):
        _c, h = a_programmed_site()
        drifted, matched = [], 0
        for code, spec in sorted(wiretables.TITLES.items()):
            lines = body(h, f"I{code}00").splitlines()[2:]
            for want in self.lines_of(spec):
                if len(want.split()) < 2:
                    continue
                if any(line.rstrip() == want.rstrip() for line in lines):
                    matched += 1
                    continue
                for line in lines:
                    if line.split() == want.split():
                        drifted.append(f"{code} p.{spec['page']}\n"
                                       f"  manual |{want}|\n"
                                       f"  ours   |{line}|")
        self.assertEqual(drifted, [], "\n".join(drifted))
        # test_wirecolumns' own floor is on the unprogrammed console;
        # this fixture reaches 526 against its 463, and the gap IS L1
        self.assertGreater(matched, 515, "sample lines printed")


class EveryCodeSurvivesADeviceBehindIt(unittest.TestCase):
    """The census again, on the console it should always have been asked on.

    test_coverage sweeps every documented inquiry on a full cage and proves
    none of them raises. Every one of those answers took the empty-device
    path. This is the same sweep down the other branch.
    """

    def test_nothing_raises_and_nothing_answers_empty(self):
        _c, h = a_programmed_site()
        raised, silent = [], []
        for token in sorted(wire.KNOWN):
            if not (wire.DOCUMENTED.get(token) or {}).get("inquire"):
                continue
            for letter in ("I", "i"):
                try:
                    reply = body(h, f"{letter}{token}00")
                except Exception as exc:                    # noqa: BLE001
                    raised.append(f"{letter}{token}00 raised {exc!r}")
                    continue
                if not reply:
                    silent.append(f"{letter}{token}00 answered nothing")
        self.assertEqual(raised, [], "\n".join(raised))
        self.assertEqual(silent, [], "\n".join(silent))


class TheVapourValveDiagnosticIsOneCategorysOwn(unittest.TestCase):
    """FIDELITY L4's tail, and it needed this fixture before it needed a fix.

    `IB61` is the VAPOR VALVE DIAGNOSTIC. It took its device list from every
    smart sensor the CAGE could hold -- not even the configured ones -- so a
    mag sensor and a vacuum sensor each answered it with a valve position and
    a battery state invented for them, and four positions nobody had wired up
    answered too. `_smart_devices` is the filter `B33` has always used.

    The block's geometry is 576013-635 Rev AA p.540's own, measured off the
    word boxes: every value line runs x=72 to x=221.9, which at that manual's
    6.0 points per character is exactly 24 columns with the value against the
    right of them. 577013-937 Rev J Figure 42 -- a real console's IB6100 --
    measures the same 24 at its own pitch.
    """

    def valve_block(self):
        _c, h = a_programmed_site()
        return body(h, "IB6100").splitlines()[2:]

    def test_only_the_vapour_valve_answers(self):
        """One `s n:` line, and it is the position category 08 is on. The
        fixture puts a mag sensor on 1, the valve on 2, an atmospheric
        sensor on 3 and a vacuum sensor on 4."""
        named = [ln for ln in self.valve_block() if ln.startswith("s ")]
        self.assertEqual(named, ["s 2:SMART SUMP #2"])

    def test_the_mag_sensor_answers_its_own_report_instead(self):
        """The other half of the filter: keeping the valve report off the mag
        sensor must not take the mag sensor off ITS report."""
        _c, h = a_programmed_site()
        self.assertIn("s 1:SMART SUMP #1", body(h, "IB3300"))
        self.assertNotIn("s 1:", body(h, "IB6100"))

    def test_every_value_stands_against_column_24(self):
        """The value lines only: the `s n:` device line above the block is
        the report's, not the block's, and is not in the 24-column grid."""
        lines = self.valve_block()
        block = lines[lines.index("VAPOR VALVE") + 1:
                      lines.index("SENSOR FAULTS:")]
        self.assertGreaterEqual(len(block), 6)
        for line in block:
            self.assertEqual(len(line), 24, repr(line))

    def test_the_block_is_the_page_line_for_line(self):
        """p.540, with this console's own label, serial and temperatures in
        place of the sample's -- everything else character for character."""
        lines = self.valve_block()
        at = lines.index("VAPOR VALVE")
        self.assertEqual(lines[at - 1], "")
        self.assertEqual(lines[at - 2], "s 2:SMART SUMP #2")
        self.assertEqual([ln[:16] for ln in lines[at + 1:at + 6]],
                         ["SERIAL NUMBER   ", "VALVE POSITION: ",
                          "OPEN CAP:       ", "CLOSE CAP:      ",
                          "AMBNT TEMP:     "])
        self.assertEqual(lines[at + 7], "SENSOR FAULTS:")
        # every fault name on the page carries one leading space, NONE too
        self.assertEqual(lines[at + 8], " NONE")

    def test_a_wired_valve_prints_no_battery_line(self):
        """`BATTERY: ... (only if wireless)`. Nothing here makes a valve a
        wireless one, so it reports 0=Unknown and the line is not drawn --
        which is what 577013-937 Figure 42's real printout does."""
        self.assertNotIn("BATTERY", "\n".join(self.valve_block()))

    def test_the_packed_form_still_carries_the_battery_byte(self):
        """B61's computer format has the field whether or not the display
        draws the line, so dropping the line must not drop the byte."""
        _c, h = a_programmed_site()
        packed_body = body(h, "iB6100")
        # ss NNNNNNNN P B O C F nn TTTTTTTT tttttttt, after the timestamp
        record = packed_body[packed_body.index("iB6100") + 6 + 10:]
        self.assertEqual(record[:2], "02")            # the valve's position
        self.assertEqual(record[10:11], "0")          # P, closed
        self.assertEqual(record[11:12], "0")          # B, unknown
        self.assertEqual(record[12:14], "11")         # both capacitors

    def test_it_draws_no_station_header(self):
        """p.540 draws none, and this console gave it four -- because the
        builder could not read `IB61ss` and recorded nothing for the code."""
        self.assertIsNotNone(wiretables.TITLES.get("B61"))
        self.assertFalse(wiretables.station_header("B61"))
        self.assertNotIn("STATION HEADER", "\n".join(self.valve_block()))


class TheTankProfileIsItsOwnFact(unittest.TestCase):
    """576013-818 Rev AB pp.12-31 and 12-32, replayed.

    The TSG prints a real console's dump of three LINEAR tanks and one 1 PT,
    and it is the only paper anywhere that shows both full-volume commands
    answering the same console::

        I60A00
        TANK   PRODUCT LABEL          TANK PROFILE   GALLONS
         1     UNLEADED                   LINEAR      10000
         4                                1 PT            0

        I60400
        TANK   PRODUCT LABEL             GALLONS
         1     UNLEADED                   10000
         4                                    0

    with the TSG's own gloss between them: "The 1 Point Full Volume command
    604 gives no indication that the profile is linear!"

    Three things follow, and this console had all three wrong. 604 and 60A
    report ONE number, so a tank programmed through either answers both.
    Neither one's presence says anything about the profile. And 60A prints
    the profile where 604 does not, which is why the TSG says running 60A is
    the only way to find out.
    """

    def a_linear_site(self):
        c, h = a_programmed_site()
        for tank, gallons in ((1, 10000.0), (2, 6000.0), (3, 8000.0)):
            c.values[f"S604{tank:02d}"] = f"{tank:02d}" + struct.pack(
                ">f", gallons).hex().upper()
            c.tank_profiles[tank] = "03"
        return c, h

    def test_604_answers_for_a_linear_tank(self):
        _c, h = self.a_linear_site()
        rows = body(h, "I60400").splitlines()
        self.assertIn("TANK   PRODUCT LABEL             GALLONS", rows)
        for tank, gallons in ((1, "10000"), (2, "6000"), (3, "8000")):
            self.assertTrue(
                any(ln.startswith(f"{tank:2d} ") and ln.endswith(gallons)
                    for ln in rows), f"tank {tank} missing from I604")

    def test_60A_prints_the_profile_column_604_does_not(self):
        _c, h = self.a_linear_site()
        rows = body(h, "I60A00").splitlines()
        self.assertIn("TANK   PRODUCT LABEL          TANK PROFILE   GALLONS",
                      rows)
        self.assertEqual(
            len([ln for ln in rows if "LINEAR" in ln]), 3)
        self.assertTrue(any("1 PT" in ln for ln in rows))
        self.assertNotIn("PROFILE", body(h, "I60400"))

    def test_a_volume_written_through_either_answers_both(self):
        """The alias, from the other side: a tank whose volume was set with
        s60A is on I604's report too."""
        c, h = a_programmed_site()
        for tank in list(c.programmed_tanks()):
            c.values.pop(f"S604{tank:02d}", None)
        c.values["S60A01"] = "01" + struct.pack(">f", 9728.0).hex().upper()
        self.assertIn("9728", body(h, "I60400"))
        self.assertIn("9728", body(h, "I60A00"))

    def test_a_report_writes_1_PT_where_the_panel_writes_1PT(self):
        """p.81's I217 and p.253's I60A both print `1 PT`; the panel screen
        on 576013-623 p.96 prints `1PT` with no space, because it has 24
        columns to fit a label and a value into."""
        from tls350sim.console import Console
        self.assertEqual(Console.PROFILE_NAME["00"], "1PT")
        self.assertEqual(Console.PROFILE_REPORT_NAME["00"], "1 PT")
        _c, h = a_programmed_site()
        self.assertIn("1 PT", body(h, "I21700"))

    def test_217_draws_the_heading_the_page_draws(self):
        """It drew none at all: `wiretables.heading` has nothing for 217,
        because the builder cannot shape a block whose title is followed by
        a DEVICE header before its heading. p.81, off the word boxes."""
        _c, h = a_programmed_site()
        rows = body(h, "I21700").splitlines()
        self.assertIn("TANK   PRODUCT LABEL           PROFILE", rows)


class ALinearTankIsAStraightLine(unittest.TestCase):
    """"This method requires only the 100% (full) volume to profile the tank.
    When using the linear tank profile you must enter the inside height of
    the tank in place of the inside diameter... This profile can be used for
    flat-ended cylindrical tanks standing on end and for rectangular tanks."
    576013-623 Rev AN p.7-6.

    Both of those have a constant cross-section, so the volume is the
    straight line through the full one. `height_at` and `volume_at` branched
    on whether a 50-point chart was strapped and nothing else, so a LINEAR
    tank was drawing the same horizontal CYLINDER an unprofiled one does --
    which is 1PT's shape, and 1PT is the profile for "horizontally installed,
    flat-ended cylindrical tanks" on that same page.
    """

    def a_tank(self, profile):
        c, _h = a_programmed_site()
        c.values["S60701"] = "01" + struct.pack(">f", 100.0).hex().upper()
        c.values["S60401"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        c.tank_profiles[1] = profile
        return c

    def test_volume_is_the_straight_line(self):
        c = self.a_tank("03")
        for inches, gallons in ((25.0, 2500.0), (50.0, 5000.0),
                                (75.0, 7500.0), (100.0, 10000.0)):
            self.assertAlmostEqual(c.volume_at(1, inches), gallons, places=6)

    def test_height_is_that_line_backwards(self):
        c = self.a_tank("03")
        for gallons, inches in ((2500.0, 25.0), (5000.0, 50.0),
                                (10000.0, 100.0)):
            self.assertAlmostEqual(c.height_at(1, gallons), inches, places=6)

    def test_one_point_is_still_the_cylinder(self):
        """The other half: making LINEAR linear must not straighten 1PT,
        whose one volume is interpolated through a horizontal cylinder."""
        c = self.a_tank("00")
        self.assertAlmostEqual(c.volume_at(1, 50.0), 5000.0, places=6)
        self.assertLess(c.volume_at(1, 25.0), 2500.0)
        self.assertGreater(c.volume_at(1, 75.0), 7500.0)


class TheAutodialReceiversAreEightAddressesNotEightDevices(unittest.TestCase):
    """FIDELITY L14. Nine of the eleven 52x reports printed nothing at all.

    An autodial receiver is not something somebody wired up, it is one of the
    console's own eight addresses -- "Press CHANGE twice to configure one
    receiver. To configure additional receivers, press the Right-Arrow key,
    then press CHANGE for up to seven more", 576013-623 Rev AN. `52B` and
    `52D` have always walked all eight through `_receivers`; their siblings
    went through the generic table builder, which walks "positions holding a
    value", found none, and answered with a bare stamp -- no title, no
    heading, no rows.

    And once they printed, the LABEL column was a TANK's product name: the
    520-52F band had no letter in `wiretables.BANDS`, so `device_letter` fell
    through to its `"T"` default. The letter is `D`, off a real console's
    tape, which prints its only receiver as `D 8:` under AUTO DIAL TIME SETUP
    and again under AUTO DIAL ALARM SETUP. See FIDELITY T4.
    """

    CODES = ("520", "522", "523", "524", "525", "526", "527", "528", "52E")

    def a_receiver_site(self):
        c, h = a_programmed_site()
        c.values["S52201"] = "01HOME OFFICE"
        return c, h

    def test_each_one_prints_its_title_and_its_heading(self):
        _c, h = self.a_receiver_site()
        silent = []
        for code in self.CODES:
            spec = wiretables.TITLES.get(code) or {}
            lines = [ln.rstrip() for ln in body(h, f"I{code}00").splitlines()]
            for want in (spec.get("title"), spec.get("heading")):
                if want and want.rstrip() not in lines:
                    silent.append(f"{code} p.{spec.get('page')}: |{want}|")
        self.assertEqual(silent, [], "\n".join(silent))

    def test_all_eight_receivers_answer(self):
        _c, h = self.a_receiver_site()
        rows = [ln for ln in body(h, "I52400").splitlines()
                if ln[:6].strip().isdigit()]
        self.assertEqual(len(rows), 8)

    def test_the_label_column_is_the_receivers_not_a_tanks(self):
        """The one that showed it: with a tank called DIESEL on the console,
        every receiver row was headed DIESEL."""
        c, h = self.a_receiver_site()
        text = body(h, "I52400")
        self.assertIn("HOME OFFICE", text)
        self.assertNotIn(c.text("602", 1), text)

    def test_an_unlabelled_receiver_has_a_BLANK_label(self):
        """L14 gave the seven unlabelled ones `RECEIVER 2` to `RECEIVER 8`,
        on the reading that a receiver is one of the console's own addresses
        rather than something somebody wired up. The reading was right and
        the LABEL was not: a real console answers `I52000`, `I52100`,
        `I52200`, `I52300`, `I52400` and `I53500` with eight rows and an
        empty label column on every one of them, exactly as it does for an
        unprogrammed tank. See FIDELITY S17."""
        _c, h = self.a_receiver_site()
        rows = [ln for ln in body(h, "I52400").splitlines()
                if ln[:6].strip().isdigit()]
        self.assertEqual(len(rows), 8)
        self.assertNotIn("RECEIVER 2", body(h, "I52400"))
        for row in rows[1:]:
            self.assertEqual(row[7:27].strip(), "", row)

    def test_the_row_is_the_manuals_own(self):
        """528 p.195, character for character -- this console's receiver has
        the sample's label and the same OFF against the same column."""
        _c, h = self.a_receiver_site()
        self.assertIn(" 1     HOME OFFICE              OFF",
                      body(h, "I52800").splitlines())

    def test_522_numbers_its_rows_DEVICE_where_its_siblings_say_RCVR(self):
        """p.189 heads 522's table `DEVICE  LABEL` and indents the number to
        column 5, where 523 through 528 head theirs `RCVR` and put it at 1.
        Recorded per page rather than shared across the family."""
        _c, h = self.a_receiver_site()
        lines = body(h, "I52200").splitlines()
        self.assertIn("DEVICE  LABEL", lines)
        self.assertIn("     1  HOME OFFICE", lines)


class AConnectedDeviceIsOnTheReportBeforeAnybodyChangesIt(unittest.TestCase):
    """FIDELITY S9, applied to the other half of the same question.

    S9 taught `display_value` that a console is not blank where nobody has
    programmed it: 27 codes carrying a documented default were answering a
    bare stamp, and they answer ENGLISH, DISABLED, 1200 baud now. The table
    builder was never told. It walked positions holding a STORED value, so a
    code with a default had a value to print and no row to print it in --
    72 codes, across twelve device families.

    A device that is connected is on the report whether or not anybody has
    changed the setting on it. A code with no default still prints nothing,
    because `_row` drops a device with nothing to say.
    """

    def test_a_liquid_sensor_reports_its_default_type(self):
        """p.320, and the row is the manual's own columns with this site's
        label in place of the sample's."""
        _c, h = a_programmed_site()
        rows = body(h, "I70300").splitlines()
        self.assertIn("SENSOR  LOCATION               TYPE", rows)
        self.assertIn("     1  LIQUID SUMP #1         TRI-STATE (SINGLE FLOAT)",
                      rows)

    def test_every_connected_tank_is_on_a_defaulted_tank_report(self):
        c, h = a_programmed_site()
        tanks = sorted(c.programmed_tanks())
        rows = [ln for ln in body(h, "I61800").splitlines()
                if ln.startswith("T ")]
        self.assertEqual(len(rows), len(tanks))

    def test_a_profile_table_is_not_a_setting_every_tank_has(self):
        """The half that must not change, and the trap in this one.

        605 is the FOUR POINT chart. A tank on one point has no four point
        chart, so it is not a row of zeros on I605 -- it is absent, the way
        p.248's own sample shows only the tank it profiles. Reading 605's
        zero default as "every tank, defaulted" would have printed a chart
        for four tanks that have none.
        """
        c, h = a_programmed_site()
        for tank in list(c.programmed_tanks()):
            c.values.pop(f"S605{tank:02d}", None)
        self.assertNotIn("GALLONS", body(h, "I60500"))
        c.values["S60501"] = "01" + "".join(
            struct.pack(">f", v).hex().upper()
            for v in (9728.0, 7296.0, 4864.0, 2432.0))
        rows = [ln for ln in body(h, "I60500").splitlines()
                if ln[:3].strip().isdigit()]
        self.assertEqual(len(rows), 1)

    def test_an_unfitted_family_reports_nothing(self):
        """`_connected` asks the cage first, so a console with no liquid
        sensor card reports no liquid sensors rather than inventing eight."""
        c, h = a_programmed_site()
        c.modules["liquid"] = 0
        self.assertFalse(c.has("liquid"))
        self.assertNotIn("SENSOR  LOCATION", body(h, "I70300"))


class EachCodeIsNumberedByItsOwnFamily(unittest.TestCase):
    """`wiretables.BANDS` says which device a code numbers where the panel
    has no function to read it off, and two of its edges were wrong.

    The relay band ran 806 to 80C and 80C is an INPUT -- "Set External Input
    Type" -- so it was reported against a relay. 811 to 813 are inputs too
    ("Set External Input Alternate Modes...") and had no band at all, so they
    fell through to the `"T"` default and headed their rows with a TANK's
    product label. Each code's own echo says which it is: `I80CII` and
    `I811II` against `I80BRR`, and 80B is the last "Set Relay ..." function
    in the manual.
    """

    def test_the_input_codes_are_numbered_as_inputs(self):
        for code in ("801", "805", "80C", "811", "812", "813"):
            self.assertEqual(wiretables.device_letter(code), "I", code)

    def test_the_relay_band_stops_where_the_relays_do(self):
        for code in ("806", "807", "80B"):
            self.assertEqual(wiretables.device_letter(code), "R", code)


class TheProbeDiagnosticsShareOneDeviceHeader(unittest.TestCase):
    """Section 7.4.2 opens each tank's block with the same line and this
    console built it five different times, all of them with single spaces.

    576013-635 Rev AA pp.489, 490 and 495 set it identically, measured off
    the word boxes: the word TANK at column 0, the tank number right against
    7, the product label at 9, the probe's type at 31, and anything after it
    at 38::

        TANK  1  REGULAR UNLEADED      MAG    GRADIENT= 178.1400

    This console printed `TANK 1 REGULAR UNLEADED MAG GRADIENT=  351.6466`,
    so no column landed and the whole line moved whenever a product label
    changed width.
    """

    def a_labelled_site(self):
        c, h = a_programmed_site()
        c.values["S60201"] = "01REGULAR UNLEADED"
        return c, h

    def test_A01_heads_only_the_probe_columns(self):
        """And it heads them from column 31. This console printed a `TANK
        PRODUCT LABEL` over the first two columns that p.489 does not have,
        and put all seven columns somewhere else."""
        _c, h = self.a_labelled_site()
        lines = body(h, "IA0100").splitlines()
        self.assertIn(f"{'':31}{'TYPE':<7}{'CODE':<7}{'LENGTH':<9}"
                      f"{'SERIAL NO.':<12}D/CODE", lines)
        self.assertNotIn("TANK PRODUCT LABEL", "\n".join(lines))
        row = next(ln for ln in lines if ln.startswith("TANK  1"))
        self.assertEqual(row[:31], "TANK  1  REGULAR UNLEADED      ")

    def test_the_gradient_line_is_the_manuals_own(self):
        """p.490, and the figure right against column 56."""
        _c, h = self.a_labelled_site()
        row = next(ln for ln in body(h, "IA0200").splitlines()
                   if ln.startswith("TANK  1"))
        self.assertEqual(row[:47], "TANK  1  REGULAR UNLEADED      MAG    GRADIENT=")
        self.assertEqual(len(row), 56)

    def test_A07_carries_the_name_type_in_the_same_column(self):
        """A07 heads its tank with the probe's NAME type rather than the
        family word -- p.495 reads MAG7 where p.489 reads MAG -- and the
        column is the same one either way."""
        _c, h = self.a_labelled_site()
        lines = body(h, "IA0700").splitlines()
        row = next(ln for ln in lines if ln.startswith("TANK  1"))
        self.assertEqual(row[:31], "TANK  1  REGULAR UNLEADED      ")
        self.assertTrue(row[31:].startswith("MAG"), row)
        # its two rows: the label at 0, the date at 20, the figure right
        # against 37 where the page's own XXXXX.XX placeholder ends
        for line in lines:
            if line.startswith(("ORIG REF", "CURR REF")):
                self.assertEqual(len(line), 37, repr(line))
                self.assertEqual(line[17:20], "   ")


class TenSettingsTheConsoleCouldNotStore(unittest.TestCase):
    """Codes the manual documents a table for and `FIELDS` had no entry for,
    so there was nowhere to put a value and nothing to read back: the report
    printed nothing at all, title and heading included.

    Each is transcribed from 576013-635 Rev AA's own Command Format notes.
    The ranges and defaults the serial manual does NOT state come from
    576013-623 Rev AN's panel screens, and the difference is worth keeping:
    72A's "permitted range is 1 to 500 gallons. Default is 501" puts the
    default deliberately OUTSIDE the range, because that is how an
    unconfigured vac sensor posts its Setup Data Warning.
    """

    def test_each_one_can_be_stored_and_read_back(self):
        from tls350sim.console import FIELDS
        for key in ("S72701", "S72A01", "S72B01", "S72C01", "S74B01",
                    "S74C01", "S74D01", "S74E01", "S7C901", "S80401"):
            self.assertIn(key, FIELDS, key)

    def test_the_vacuum_volume_carries_its_unit(self):
        """p.332 prints `200.0 GALLONS`, one decimal and the word."""
        _c, h = a_programmed_site()
        rows = [ln for ln in body(h, "I72A00").splitlines()
                if ln[:6].strip().isdigit()]
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row.endswith("GALLONS"), row)
        self.assertIn("501.0 GALLONS", rows[0])       # 623 p.26-4's default

    def test_the_relief_valve_pressure_carries_its_sign_and_unit(self):
        """p.334 prints `-9.0 PSI`, and -9 is 623 p.26-4's default."""
        _c, h = a_programmed_site()
        row = next(ln for ln in body(h, "I72C00").splitlines()
                   if ln[:6].strip().isdigit())
        self.assertTrue(row.endswith("-9.0 PSI"), row)

    def test_the_monitor_type_prints_the_word_the_report_prints(self):
        """The manual gives one value two word orders: note 3 reads "1 =
        Pump Relay Monitor" and the report prints PUMP MONITOR RELAY. A
        report has to print the report's."""
        _c, h = a_programmed_site()
        self.assertIn("PUMP MONITOR RELAY", body(h, "I7C900"))

    def test_the_dispense_mode_defaults_to_standard(self):
        """623 p.23-4: "To select Standard (the system default) press
        STEP." Rev Y of the serial manual stops at 4 and Rev AA adds the
        fifth, MANIFOLDED: ALTERNATE-HEIGHT."""
        from tls350sim.console import FIELDS
        self.assertEqual(FIELDS["S80401"]["default"], "1")
        self.assertEqual(len(FIELDS["S80401"]["choices"]), 5)
        _c, h = a_programmed_site()
        self.assertIn("STANDARD", body(h, "I80400"))


class AnUnlabelledDeviceIsNamedTwice(unittest.TestCase):
    """The setup tables were printing the PANEL's word for a device nobody
    had labelled, and the panel abbreviates to fit 24 columns.

    576013-635 names the same device differently in a SETUP table and in a
    STATUS report, and it is consistent about both::

        I703  `     1  LIQUID SENSOR #1`     I301  `  1  LIQUID # 1`
        I801  `     1  EXTERNAL INPUT #1`    I401  `  1  * EXTERNAL INPUT 1 *`
        I806  `     1  OUTPUT RELAY #1`      I406  `  1  * RELAY 1  *`

    `wiresensors.FAMILY` had the status side right. The setup side went
    through `screens.device_label`, which is the panel's, so `GROUNDWATER #1`
    came out `GRND WATER 1` and `2 WIRE CL SENSOR #1` came out `2-WIRE CL 1`
    -- a hyphen the page does not have, no hash, and no noun.
    """

    def an_unlabelled_site(self):
        c, h = a_programmed_site()
        c.values["S74B01"] = "011"
        for key in list(c.values):
            if key[:4] in ("S702", "S707", "S712", "S742", "S747",
                           "S802", "S807"):
                c.values.pop(key)
        return c, h

    def test_each_family_is_named_as_its_own_setup_page_names_it(self):
        c, h = self.an_unlabelled_site()
        for code, wanted in (("703", "LIQUID SENSOR #1"),
                             ("708", "VAPOR SENSOR #1"),
                             ("713", "GROUNDWATER #1"),
                             ("743", "2 WIRE CL SENSOR #1"),
                             ("748", "3 WIRE CL SENSOR #1"),
                             ("74D", "UNIVERSAL SENSOR #1"),
                             ("801", "EXTERNAL INPUT #1"),
                             ("806", "OUTPUT RELAY #1")):
            self.assertIn(wanted, body(h, f"I{code}00"), code)

    def test_a_site_label_still_wins(self):
        """The default is only a default: a device the site has named prints
        the site's name for it."""
        c, h = self.an_unlabelled_site()
        c.values["S70201"] = "01PUMP ISLAND SUMP"
        text = body(h, "I70300")
        self.assertIn("PUMP ISLAND SUMP", text)
        self.assertNotIn("LIQUID SENSOR #1", text)


class EveryConfigReportSaysONOrOFF(unittest.TestCase):
    """A CONFIG code stores one flag character per position, so its value is
    a property of the DEVICE and not of the code.

    `display_value` decoded the whole slot string, so all nine of these
    printed a bare `1` where every one of their samples prints `ON`:
    576013-635 p.317's `I701`, and 706, 711, 721, 741, 746, 74B, 801 and 806
    alike, all under the same `CONFIGURED` heading.
    """

    CONFIGS = ("701", "706", "711", "721", "741", "746", "801", "806")

    def test_a_switched_on_position_reads_ON(self):
        _c, h = a_programmed_site()
        for code in self.CONFIGS:
            rows = [ln for ln in body(h, f"I{code}00").splitlines()
                    if ln[:6].strip().isdigit()]
            self.assertTrue(rows, code)
            self.assertTrue(rows[0].endswith("ON"), f"{code}: {rows[0]!r}")

    def test_the_row_is_the_manuals_own(self):
        """p.317 and p.339, with the unlabelled names their own pages use."""
        c, h = a_programmed_site()
        for key in list(c.values):
            if key[:4] in ("S702", "S742"):
                c.values.pop(key)
        rows = body(h, "I70100").splitlines()
        self.assertIn("     1  LIQUID SENSOR #1       ON", rows)
        rows = body(h, "I74100").splitlines()
        self.assertIn("     1  2 WIRE CL SENSOR #1    ON", rows)

    def test_a_position_nobody_switched_on_reads_OFF(self):
        c, h = a_programmed_site()
        c.values["S70103"] = "030"
        row = next(ln for ln in body(h, "I70100").splitlines()
                   if ln.lstrip().startswith("3 "))
        self.assertTrue(row.endswith("OFF"), row)


class TheDevicePrefixedSetIsTheManualsOwn(unittest.TestCase):
    """Derived from 576013-635's own templates, so the next missing one is
    caught the day it is added rather than the day a label prints wrong.

    The manual draws each code's computer response as a template, and a code
    whose records repeat the device number draws the repeat:

        <SOH>i702SSYYMMDDHHmmSSaaaaaaaaaaaaaaaaaaaa...

    The token after the timestamp is exactly what `DEVICE_PREFIXED` records.
    Skipped where the reference documents are not on the shelf, which is
    every clone: they are Veeder-Root's and are not in this repository.

    **`SS` used to be a literal here**, and a sensor is the only thing the
    manual calls `SS`: a tank is `TT`, a PLLD line `QQ`, a WPLLD line `WW`,
    a VLLD line `PP`, an input `II`, a relay `RR` and a VMC position `xx`.
    So the derivation that exists to keep this list off the curator's eye
    read 67 of the manual's 313 templates and could only ever fail on a
    sensor. Twenty-seven codes came in when the token was made a group.
    See FIDELITY L17.
    """

    # inquiry-only report codes. `DEVICE_PREFIXED` is about the SETUP data a
    # console STORES -- what `Console.text` and `fieldio.body_of` strip -- and
    # a report that is never stored has nothing to strip. The census answers
    # this better than a prefix does, so the `set` flag is the filter and
    # this stays as documentation of what is being excluded.
    REPORTS = re.compile(r"^([0-3B]|3[0-9A-F])")
    TEMPLATE = re.compile(r"i([0-9A-Fa-f]{3})([A-Za-z]{2})YYMMDDHHmm(\S*)")

    # Two codes the general form finds and the pages rule out, each read
    # rather than pattern-matched:
    #
    # `502` is a revision difference. Rev AA draws
    # `<SOH>i502SSYYMMDDHHmmHHmm&&CCCC<ETX>` with no repeat and Rev Y draws
    # `...SSHHmm...` with one. L13 read Rev AA and called it correctly
    # absent; it stays absent, and the disagreement is the point.
    #
    # `8C4` is an extraction artefact. Rev AA's own Command Format is
    # `<SOH>s8C400hh` -- device 00, one value for the console -- and the
    # repeat comes from Rev Y, whose 8C4 section carries the FUELING
    # POSITION samples that belong to the code before it.
    NOT_PREFIXED = {"502": "Rev AA draws no repeat; Rev Y does",
                    "8C4": "Rev Y's section carries 8C3's samples"}

    def manual_paths(self):
        return [os.path.join(REF, name) for name in os.listdir(REF)
                if name.endswith(".txt") and "576013-635" in name] \
            if os.path.isdir(REF) else []

    def test_every_settable_code_the_manual_prefixes_is_listed(self):
        paths = self.manual_paths()
        if not paths:
            self.skipTest("no reference/576013-635*.txt (public export)")
        prefixed = set()
        for path in paths:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    for code, token, tail in self.TEMPLATE.findall(line):
                        if tail.startswith(token):
                            prefixed.add(code.upper())
        self.assertGreater(len(prefixed), 200, "manual did not parse")
        # a Set is the only thing that is STORED, so it is the only thing
        # with a prefix to strip
        settable = {code for code in prefixed
                    if (wire.DOCUMENTED.get(code) or {}).get("set")}
        self.assertGreater(len(settable), 150, "the census did not match")
        missing = sorted(code for code in settable - set(self.NOT_PREFIXED)
                         if int(code, 16) not in DEVICE_PREFIXED)
        self.assertEqual(missing, [], f"S{missing} repeat their device "
                                      "number and nothing strips it")
        # and the two exceptions are exceptions rather than oversights: each
        # is still found by the scan, so a revision that settles one is loud
        self.assertEqual(sorted(set(self.NOT_PREFIXED) & prefixed),
                         sorted(self.NOT_PREFIXED))


class TheManifoldReportAgreesWithTheSamples(unittest.TestCase):
    """FIDELITY S12. I612 and I61D print one table between them, and three
    details of it were read off the wrong page or off no page at all.

    576013-635 Rev AA p.275, dated 2002, is the shape this console follows:

        TANK   PRODUCT LABEL          SIPHON MANIFOLDED TANKS    LINE ...
         2     REGULAR UNLEADED           1                          3

    576013-818 Rev AB chapter 12 prints the same report with ONE column,
    headed MANIFOLDED TANKS -- and those samples are dated 1997, 1998 and
    1999, while 61D (Set Tank LINE Manifolded Partners) is a Version 23
    function. Both are real; they are two firmware eras, and the two-column
    form is not the invention S12 took it for.
    """

    def a_console(self):
        c = Console(None)
        c.values["S60201"] = "UNLEADED SOUTH      "
        c.manifold_together(1, [2])
        return Handler(c, verbose=False)

    def rows(self, handler, command):
        text = body(handler, command)
        return [r for r in text.split(chr(13) + chr(10)) if r.strip()]

    def test_a_tank_in_no_set_prints_NONE(self):
        """All three of chapter 12's samples print the word. The console
        printed a dash, which is on none of them."""
        rows = self.rows(self.a_console(), "I61200")
        self.assertTrue(rows[-1].endswith("NONE"), rows[-1])
        self.assertNotIn("-", rows[-1])

    def test_an_unlabelled_tank_prints_a_blank_label_column(self):
        """The rule every other setup table follows -- see
        `wiretables._row` -- and this one printed the panel's TANK 1."""
        c = Console(None)
        c.manifold_together(1, [2])
        rows = self.rows(Handler(c, verbose=False), "I61200")
        self.assertNotIn("TANK 1", rows[-1])

    def test_the_partner_lands_in_the_consoles_own_column(self):
        """p.275 puts the siphon partner at column 34 and the line partner at
        61; a real console puts them at 36 and 62.

        `tests/console_capture/raw/I61200.bin` is the console -- four tanks
        in no set at all, `NONE` twice per row, ending at 36 and 62 -- and
        `tools/console_corrections.json` carries the two columns and the
        heading that goes with them. This test asked the page and now asks
        the capture; the sample it compares against is the page's own row
        moved to where the console draws it.
        """
        sample = (" 2     REGULAR UNLEADED             1"
                  "                         3")
        rows = self.rows(self.a_console(), "I61200")
        line = next(r for r in rows if r.startswith(" 1 "))
        self.assertEqual(line.index("2", 7), sample.index("1", 7))
        self.assertEqual(len(line), len(sample))

    def test_both_codes_answer_with_the_whole_table(self):
        """635 Rev AA draws the identical table on p.265 and p.275, so 61D
        is not a different report -- it is the same one asked for by its
        other code."""
        h = self.a_console()
        # past the echoed code and the timestamp, which are the reply's and
        # not the report's
        self.assertEqual(self.rows(h, "I61200")[2:],
                         self.rows(h, "I61D00")[2:])

    def test_the_computer_form_still_carries_only_its_own_code(self):
        """The DISPLAY table is shared; the computer payload is not. 635's
        note for 612 is "tt - Tank numbers of other tanks to be SIPHON
        manifolded", and 61D's says LINE."""
        h = self.a_console()
        self.assertIn("0101", body(h, "i61200"))
        self.assertIn("0100", body(h, "i61D00"))


class TheSecurityCodeReportIsThePortTable(unittest.TestCase):
    """504's Display answer is 536's report, because a real console's is.

    576013-635 files 504 as "Set System RS-232 Security Code", Version 1, one
    code for the console, and draws `SYSTEM SECURITY CODE` over a single
    `CODE : 000000`. `tests/console_capture/raw/I50400.bin` -- a running
    TLS-350, and the capture is not in the repository, which is why this
    reproduces the shape rather than diffing the file -- answers
    `232 SECURITY CODE` over `PORT  SECURITY CODE   STATUS` and six port
    rows. That is the report the same manual draws for 536, "Set RS-232
    Security Code per Port", Version 20. See FIDELITY S14.
    """

    def a_console(self):
        c = Console(None)
        return c, Handler(c, verbose=False)

    def rows(self, h, command):
        return [r for r in body(h, command).splitlines() if r.strip()]

    def test_504_answers_the_port_table(self):
        _c, h = self.a_console()
        rows = self.rows(h, "I50400")[2:]
        self.assertEqual(rows[0], "232 SECURITY CODE")
        self.assertEqual(rows[1], "PORT  SECURITY CODE   STATUS")
        self.assertEqual(rows[2:], [
            " %d         000000    DISABLED" % n for n in range(1, 7)])

    def test_the_two_columns_are_two_parts_of_one_record(self):
        """`s536PPsaaaaaa` packs the status first and the report prints it
        second, which is the one code whose columns are not in the record's
        order."""
        _c, h = self.a_console()
        send(h, "S536011123456")
        row = self.rows(h, "I53601")[-1]
        self.assertEqual(row, " 1         123456    ENABLED")

    def test_port_zero_is_not_a_port(self):
        """"PP - Port number (Decimal, 01..03 [..06]; 99=this port)". The
        console answers I53600 with a bare stamp, and 504 -- which has no
        device at all -- is how you ask for every port."""
        _c, h = self.a_console()
        self.assertEqual(self.rows(h, "I53600")[2:], [])

    def test_the_paper_and_the_wire_disagree_about_an_unset_code(self):
        """The setup report prints `CODE : DISABLED` -- real paper, in
        `tests/tape/` -- where the wire prints `000000` beside a STATUS
        column that says DISABLED. Same console, same state."""
        from tls350sim.console import FIELDS
        field = FIELDS["S53601.code"]
        self.assertEqual(field["default"], "DISABLED")
        self.assertEqual(field["wire_default"], "000000")


class OneValueUnderTwoNames(unittest.TestCase):
    """506 to 50B and 546 to 54B are six settings, not twelve.

    "Set Periodic Test Needed Warning" at Version 2 and 4, and "Set TANK
    Periodic Test Needed Warning" at Version 15. A console that has both
    answers both with one value, and the capture is a real one doing it:
    `I50700` and `I54700` both print 25, `I50800` and `I54800` both print 30,
    in the same minute. See FIDELITY S17.
    """

    PAIRS = (("506", "546"), ("507", "547"), ("508", "548"),
             ("509", "549"), ("50A", "54A"), ("50B", "54B"))

    def a_console(self):
        c = Console(None)
        return c, Handler(c, verbose=False)

    def line(self, h, command):
        rows = [r for r in body(h, command).splitlines() if r.strip()]
        return rows[-1]

    def test_programming_the_newer_code_answers_the_older(self):
        _c, h = self.a_console()
        send(h, "S5470025")
        self.assertEqual(self.line(h, "I50700"),
                         "PERIODIC TEST WARNING: DAYS =  25")
        self.assertEqual(self.line(h, "I54700"),
                         "TANK PER TST NEEDED WRN: DAYS =  25")

    def test_and_the_older_answers_the_newer(self):
        """Symmetrically, the way 604 and 60A already do."""
        _c, h = self.a_console()
        send(h, "S5080012")
        self.assertEqual(self.line(h, "I54800"),
                         "TANK PER TST NEEDED ALM: DAYS =  12")

    def test_each_pair_keeps_its_own_title(self):
        """One value, two names -- the names are the point."""
        _c, h = self.a_console()
        for old, new in self.PAIRS:
            a = self.line(h, f"I{old}00").split(":")[0]
            b = self.line(h, f"I{new}00").split(":")[0]
            self.assertNotEqual(a, b, old)

    def test_none_of_the_twelve_answers_a_bare_stamp(self):
        """All six of the older ones did, which is how they were found."""
        _c, h = self.a_console()
        for old, new in self.PAIRS:
            for tok in (old, new):
                rows = [r for r in body(h, f"I{tok}00").splitlines()
                        if r.strip()]
                self.assertEqual(len(rows), 3, tok)


class TheLineDisableAlarmAssignments(unittest.TestCase):
    """787, 7A7 and 75B are 52C's payload and 52C's report, for a LINE.

    "Set Pressure Line Leak Disable Alarm Assignments", `AANNTTSS`, one
    assignment a Set -- the same eight characters 52C takes for a receiver.
    They had no renderer, so `I78700` answered a title and an empty body on
    a console with the card in it, where a real one prints a block for every
    line. See FIDELITY S17.
    """

    def a_line_site(self, kind="plld", config="781", count=3):
        c = Console(None)
        c.modules[kind] = 1
        for n in range(1, count + 1):
            c.values[f"S{config}{n:02d}"] = f"{n:02d}1"
        return c, Handler(c, verbose=False)

    def rows(self, h, command, keep=False):
        """The report, from its title down."""
        lines = [ln if keep else ln.rstrip()
                 for ln in body(h, command).splitlines()][2:]
        while lines and not lines[0].strip():
            lines.pop(0)
        return lines

    def test_a_line_with_nothing_assigned_says_so(self):
        """Character for character off `tests/console_capture/raw/
        I78700.bin`: a blank line above every block, and the label column
        padded to twenty on a line nobody has named."""
        _c, h = self.a_line_site()
        rows = self.rows(h, "I78700", keep=True)
        self.assertEqual(rows[0], "PRESSURE LLD SETUP REPORT")
        self.assertEqual(rows[1], "")
        self.assertEqual(rows[2], "Q 1:" + " " * 20)
        self.assertEqual(rows[3], "- NO ALARM ASSIGNMENTS -")
        self.assertEqual(rows[4], "")
        self.assertEqual(rows[5], "Q 2:" + " " * 20)

    def test_all_three_families_answer_under_their_own_title(self):
        for tok, kind, config, letter, title in (
                ("787", "plld", "781", "Q", "PRESSURE LLD SETUP REPORT"),
                ("7A7", "wplld", "7A1", "W", "WPLLD LLD   SETUP REPORT"),
                ("75B", "vlld", "751", "P", "LINE LEAK SETUP REPORT")):
            _c, h = self.a_line_site(kind, config, count=2)
            rows = self.rows(h, f"I{tok}00")
            self.assertEqual(rows[0], title, tok)
            self.assertEqual(rows[2].rstrip(), f"{letter} 1:", tok)

    def test_an_assignment_reaches_the_report(self):
        c, h = self.a_line_site()
        send(h, "S787012105000 1".replace(" ", ""))
        rows = self.rows(h, "I78701")
        self.assertEqual(rows[2], "Q 1:")
        self.assertIn("PER TST NEEDED ALM", rows[3])
        self.assertEqual(c.line_disable_alarms[("plld", 1)],
                         [("21", "05", "00")])

    def test_clearing_it_takes_it_off_again(self):
        c, h = self.a_line_site()
        send(h, "S78701" + "21050001")
        send(h, "S78701" + "21050000")
        self.assertEqual(c.line_disable_alarms[("plld", 1)], [])
        self.assertIn("- NO ALARM ASSIGNMENTS -", body(h, "I78701"))

    def test_device_zero_is_every_line(self):
        c, h = self.a_line_site()
        send(h, "S78700" + "21050001")
        for n in (1, 2, 3):
            self.assertEqual(c.line_disable_alarms[("plld", n)],
                             [("21", "05", "00")])

    def test_a_payload_that_is_not_eight_characters_is_refused(self):
        _c, h = self.a_line_site()
        self.assertIn("9999", send(h, "S78701" + "2105"))
        self.assertIn("9999", send(h, "S78701" + "21050002"))

    def test_the_card_has_to_be_there(self):
        c = Console(None)
        self.assertIn("9999", send(Handler(c, verbose=False), "I78700"))

    def test_the_computer_form_carries_the_count_and_the_assignments(self):
        _c, h = self.a_line_site()
        send(h, "S78701" + "21050001")
        self.assertIn("0101" + "21050001", body(h, "i78701"))


class NoReportInventsATank(unittest.TestCase):
    """A console with nothing programmed reports on nothing.

    A real TLS-350 running 326.01, with four tank positions all reading
    OFF, refuses I23700, I23800, I23900 and I40400 outright. This console
    answered all four, and answered with a row reading `1  TANK 1` holding
    zero gallons -- a tank nobody had programmed, in a report a technician
    is being taught to read.

    The cause was one expression, `sorted(c.tank_level) or [1]`, repeated
    three times in `wirelater.py`: with no live level anywhere it fell back
    to tank 1. The same defect was found and fixed once before from I20100
    -- "the simulator used to fall back to tank 1 whenever nothing was
    programmed" -- and these three were not reached by it, because the
    capture replay compares the BODY of two codes out of nine hundred and
    the rest are checked for framing only.
    """

    def bare(self):
        c = Console(None)
        self.assertEqual(c.programmed_tanks(), {},
                         "this console is meant to have nothing on it")
        return c, Handler(c, verbose=False)

    def test_the_three_inventory_reports_have_no_rows(self):
        _c, h = self.bare()
        for code in ("I23700", "I23800", "I23900", "I40400"):
            out = send(h, code)
            self.assertNotIn("TANK 1", out, code)
            self.assertNotIn("TOTAL:", out, code)

    def test_a_programmed_tank_still_reaches_them(self):
        """The fix must not empty the report on a console that HAS one."""
        c, h = self.bare()
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
        out = send(h, "I23700")
        self.assertIn("REGULAR UNLEADED", out)
        self.assertIn("TOTAL:", out)

    def test_the_fallback_is_gone_from_the_source(self):
        """The expression itself, so a fourth copy cannot be pasted back in
        without this failing."""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "wirelater.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("sorted(c.tank_level) or [1]", src)
