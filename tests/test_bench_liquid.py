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
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""The bench TLS-350 with an interstitial (liquid sensor) card, 2026-10-08.

The PLLD sensor board came out of slot 2 and an Interstitial/Liquid Sensor
Interface went in, nothing wired to any of its eight inputs. Three captures:

- `bench-2026-10-08-sensor`, the card fitted and NO cold start since: the
  slot still named and typed for the PLLD board, the liquid codes bare.
- `bench-2026-10-08-cold`, every code in both formats straight out of the
  cold start that followed.
- its `transcripts/liquid1` to `liquid3`: sensor 1 switched on as each of
  the seven types and switched off again, every category, a label, the
  same in computer format, and the edges -- each switch-on followed by its
  switch-off and S00300 in the same run.

Not committed, like every capture: each class skips without its data.
"""
import json
import os
import re
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_console import at_cold_start, bench_console   # noqa
from test_bench_swept import display_body, framed, packed_body, tail  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(HERE, "console_capture")
COLD = os.path.join(CAP, "bench-2026-10-08-cold")
SWAPPED = os.path.join(CAP, "bench-2026-10-08-sensor")
NINTH = os.path.join(CAP, "bench-2026-10-09")
SOH = b"\x01"


def liquid_bench(cold=True):
    """The bench's cage on 2026-10-08: the interstitial card in slot 2 in
    place of the PLLD sensor board, the WPLLD comm card in comm 3, nothing
    on any liquid input and the two metered resistors still on the ground
    temperature inputs. `cold=False` is the console before its cold start,
    which last reset with the PLLD board in slot 2 and comm 3 empty."""
    c = bench_console()
    if not cold:
        c.comm_slots = {1: "rs232", 2: "ssat"}
        c.cold_boot()
    c.modules["plld"] = 0
    c.modules["liquid"] = 1
    c.modules["wplldcom"] = 1
    c.comm_slots = {1: "rs232", 2: "ssat", 3: "wplldcom"}
    c.out_of_paper = False
    for n in range(1, 9):
        c.sensor_state[("liquid", str(n))] = "open"
    c.thermistors.update({1: 9995.0, 2: 1000000.0})
    return c


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def body(code, data):
    return packed_body(data) if code[0] in "is" else display_body(data)


@unittest.skipUnless(os.path.isdir(os.path.join(COLD, "raw")),
                     "the 2026-10-08 cold capture is not here")
class TheColdStartedInterstitialBench(unittest.TestCase):
    """All 485 codes in both formats, against the emulator at the instant
    the bench came back from its cold start."""

    #: what differs, each for a reason that is not a defect this can see
    KNOWN = {
        # the cards' own ID resistors, read off another console's cards
        "I10200", "i10200",
        # the BATTERY IS OFF pair the cold start PROCEDURE logs
        "I11100", "i11100", "I11400", "i11400",
        # the clock to the second, and the PC board's own run counters
        "I5FA00", "i5FA00", "I90300", "i90300",
        # the A/D reference channels, which drift by a count or two: the
        # rows are held to their bands by the class below instead
        "IB0100", "iB0100",
        # the metered resistors on the thermistor inputs, read a few ohms
        # off the table they were fitted to, and HIGH REF 965 against 966
        "IB2100", "iB2100",
    }
    FLOOR = 956

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        cls.rows, cls.tails = {}, {}
        raw = os.path.join(COLD, "raw")
        with at_cold_start(c):
            for name in sorted(os.listdir(raw)):
                code = name[:-4].replace("c_", "")
                real = read(os.path.join(raw, name))
                ours = framed(h, SOH + code.encode("ascii"))
                cls.rows[code] = (body(code, real), body(code, ours))
                if code[0] == "I":
                    cls.tails[code] = (tail(real), tail(ours))

    def test_what_differs_is_named(self):
        wrong = {code for code, (a, b) in self.rows.items() if a != b}
        self.assertEqual(sorted(wrong - self.KNOWN), [])
        self.assertEqual(sorted(self.KNOWN - wrong), [],
                         "these match now: take them off KNOWN")

    def test_every_reply_ends_as_the_bench_s_does(self):
        wrong = sorted(code for code, (a, b) in self.tails.items() if a != b)
        self.assertEqual(wrong, [])

    def test_the_floor(self):
        same = sum(1 for a, b in self.rows.values() if a == b)
        self.assertGreaterEqual(same, self.FLOOR)


LATE = os.path.join(CAP, "bench-2026-10-08-late")


@unittest.skipUnless(os.path.isdir(os.path.join(LATE, "raw")),
                     "the 2026-10-08 late capture is not here")
class TheProgrammedInterstitialBench(unittest.TestCase):
    """All 485 codes in both formats again, at the end of the day: L 2 on
    and labelled SUMP2, L 1 and L 4 labelled and off, metered resistors on
    L 1 and L 2, custom alarms on with KPM on PAPER OUT, the checks armed
    by a Setup visit, the beeper off (`bench-2026-10-08-late`)."""

    KNOWN = {
        # the day's alarms, which a console built in a test never had
        "I11100", "i11100", "I11200", "i11200", "I11300", "i11300",
        "I11400", "i11400", "I12100", "i12100", "I30200", "i30200",
        # the cards' resistors, the glass's second, the PC board's counters
        "I10200", "i10200", "I5FA00", "i5FA00", "I90300", "i90300",
        # metered resistors read through the A/D, and drifting references
        "IB0100", "iB0100", "IB2100", "iB2100",
    }
    FLOOR = 948
    AT = (2006, 1, 16, 11, 0, 0)

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        for cmd in ("S530000149", "S5BD001", "S5BE0001011KPM", "S70201SUMP1",
                    "S70202SUMP2", "S70204SUMP4", "S701021"):
            h.handle(SOH + cmd.encode() + b"\r")
        c.sensor_state[("liquid", "1")] = "normal"
        c.sensor_state[("liquid", "2")] = "normal"
        c.tank_checks = True
        held = time.time()
        cls.rows = {}
        raw = os.path.join(LATE, "raw")
        with unittest.mock.patch("time.time", lambda: held):
            c.clock_offset = time.mktime(cls.AT + (0, 0, -1)) - held
            c._last_tick = held
            for name in sorted(os.listdir(raw)):
                code = name[:-4].replace("c_", "")
                real = read(os.path.join(raw, name))
                ours = framed(h, SOH + code.encode("ascii"))
                cls.rows[code] = (body(code, real), body(code, ours),
                                  code[0] == "I" and tail(real) != tail(ours))

    def test_what_differs_is_named(self):
        wrong = {code for code, (a, b, t) in self.rows.items()
                 if a != b or t}
        self.assertEqual(sorted(wrong - self.KNOWN), [])
        self.assertEqual(sorted(self.KNOWN - wrong), [],
                         "these match now: take them off KNOWN")

    def test_the_floor(self):
        same = sum(1 for a, b, t in self.rows.values() if a == b and not t)
        self.assertGreaterEqual(same, self.FLOOR)

    def test_the_beeper_wants_its_149(self):
        """`S530001` with no verification code: `?`, and the beeper left
        as it was; `S530000149` answers the report (2026-10-08)."""
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        self.assertIn("\r\n?\r\n", h.handle(SOH + b"S530001\r").decode())
        self.assertIn("ENABLED", h.handle(SOH + b"I53000").decode())
        self.assertIn("DISABLED",
                      h.handle(SOH + b"S530000149\r").decode())


@unittest.skipUnless(os.path.isdir(os.path.join(COLD, "raw")),
                     "the 2026-10-08 cold capture is not here")
class TheLiquidDiagnostic(unittest.TestCase):
    """IB0100 row by row: every input, the counter, the value exact; the two
    reference channels inside the band the bench's five reads covered."""

    ROW = re.compile(r"^\s+(\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$")

    def rows(self, text):
        return [m.groups() for m in map(self.ROW.match, text.split("\r\n"))
                if m]

    def test_the_rows(self):
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        real = self.rows(read(os.path.join(COLD, "raw", "IB0100.bin"))
                         .decode("latin-1"))
        with at_cold_start(c):
            ours = self.rows(h.handle(SOH + b"IB0100").decode("latin-1"))
        self.assertEqual([(n, k, v) for n, k, _h, _l, v in ours],
                         [(n, k, v) for n, k, _h, _l, v in real])
        for _n, _k, high, low, _v in ours:
            self.assertTrue(941 <= int(high) <= 943, high)
            self.assertTrue(157 <= int(low) <= 158, low)


TRANSCRIPTS = os.path.join(COLD, "transcripts")
STAMP = re.compile(r"([A-Z]{3} +\d+, \d{4} +\d+:\d\d [AP]M)")
CSTAMP = re.compile(r"^\x01[is][0-9A-Z@]{5}(\d{10})")


def when(reply):
    m = CSTAMP.match(reply)
    if m:
        return time.strptime(m.group(1), "%y%m%d%H%M")
    m = STAMP.search(reply)
    if m:
        return time.strptime(re.sub(" +", " ", m.group(1)),
                             "%b %d, %Y %I:%M %p")
    return None


def steps():
    for name in ("liquid1.jsonl", "liquid2.jsonl", "liquid3.jsonl"):
        with open(os.path.join(TRANSCRIPTS, name), encoding="utf-8") as fh:
            for line in fh:
                yield json.loads(line)


@unittest.skipUnless(os.path.isfile(os.path.join(TRANSCRIPTS,
                                                 "liquid3.jsonl")),
                     "the 2026-10-08 liquid transcripts are not here")
class TheLiquidSetsReplayed(unittest.TestCase):
    """Both transcripts in the order they were sent, the console's clock on
    each bench reply's own stamp, every reply compared."""

    #: by step: the sensor STATUS lags. Straight after a sensor is
    #: switched on the bench answered SENSOR NORMAL on I30100 once or
    #: twice, and `0000` on i30101, while I10100, i10100 and the glass
    #: already carried the alarm -- for NORMALLY CLOSED and DUAL POINT
    #: HYDROSTATIC both reads did. The stamps are to the minute, so nothing
    #: here says how long it lags; FIDELITY S46.
    LAGGED = {16, 28, 29, 40, 41, 52, 64, 76, 128, 152}
    #: the IB01 rows, whose reference channels drift (`TheLiquidDiagnostic`)
    #: and the cold start PROCEDURE's BATTERY IS OFF pair on I11100
    KNOWN = {6, 33, 45, 57, 69, 81, 93, 147, 195, 196}

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        held = [time.time()]
        cls.results = []
        with unittest.mock.patch("time.time", lambda: held[0]):
            c._last_tick = held[0]
            for rec in steps():
                cmd, real = rec["cmd"], rec["reply"]
                st = when(real)
                if st:
                    target = time.mktime(st[:5] + (30,) + st[6:8] + (-1,))
                    held[0] = target - c.clock_offset
                    c.tick()
                sent = SOH + cmd.encode("latin-1")
                if cmd[0] in "Ss":
                    sent += b"\r"
                ours = h.handle(sent).decode("latin-1")
                c.tick()
                a = body(cmd, real.encode("latin-1"))
                b = body(cmd, ours.encode("latin-1"))
                if cmd.startswith("I5FA"):
                    # the glass's own clock carries seconds
                    a, b = [[r.split("M", 1)[1] if ":" in r[:20] else r
                             for r in rows] for rows in (a, b)]
                cls.results.append((cmd, a, b))

    def test_every_reply_but_the_named(self):
        wrong = {n for n, (_c, a, b) in enumerate(self.results) if a != b}
        self.assertEqual(sorted(wrong - self.LAGGED - self.KNOWN), [])
        self.assertEqual(sorted((self.LAGGED | self.KNOWN) - wrong), [],
                         "these match now: take them off")

    def test_the_lag_is_the_word_alone(self):
        for n in self.LAGGED:
            cmd, a, b = self.results[n]
            if cmd == "i30101":
                self.assertEqual((a, b), (["010000"], ["010003"]))
                continue
            self.assertEqual(cmd, "I30100")
            self.assertTrue(a[-1].endswith("SENSOR NORMAL"), a[-1])
            self.assertEqual(a[-1][:31], b[-1][:31])


@unittest.skipUnless(os.path.isfile(os.path.join(TRANSCRIPTS,
                                                 "resistors.jsonl")),
                     "the 2026-10-08 resistor transcript is not here")
class MeteredResistorsOnTheInputs(unittest.TestCase):
    """0.999k on input 1, 99.4k on 2, 219.3k on 3, each sensor switched on
    as all seven types: the state the bench settled on is one this console
    offers that type, and this console's band for it holds what IB01 read.
    Then the status's lag, which is about one second."""

    #: ssss of i301 -> state, `wiresensors._status_value` the other way
    STATUS = {"0000": "normal", "0002": "fuel", "0003": "out",
              "0004": "short", "0008": "low"}

    @classmethod
    def setUpClass(cls):
        cls.runs = []
        run = None
        path = os.path.join(TRANSCRIPTS, "resistors.jsonl")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                m = re.match(r"on, type (\d)", r["note"])
                if m:
                    run = {"type": m.group(1), "input": r["input"],
                           "lag": []}
                    cls.runs.append(run)
                elif run and r["note"].startswith("lag"):
                    data = r["reply"].split("&&")[0]
                    run["lag"].append((float(r["note"].split("+")[1][:-1]),
                                       data[-4:]))
                elif run and r["cmd"].startswith("IB01") and \
                        r["note"] == f"type {run['type']}":
                    row = r["reply"].split("\r\n")[-3]
                    run["value"] = float(row.split()[-1])
                elif r["cmd"].startswith("S701") and "off" in r["note"]:
                    run = None

    def test_all_twenty_one(self):
        self.assertEqual(len(self.runs), 21)

    def test_each_state_is_the_type_s_and_its_band_holds_the_reading(self):
        from tls350sim import readings
        from tls350sim.console import LIQUID_TYPE_STATES
        for run in self.runs:
            state = self.STATUS[run["lag"][-1][1]]
            kind = run["type"]
            if state != "normal":
                self.assertIn(state, LIQUID_TYPE_STATES[kind], run)
            ranges = readings.spans(readings.BANDS["liquid"][kind][state])
            self.assertTrue(any(low <= run["value"] <= high
                                for low, high in ranges),
                            (kind, state, run["value"], ranges))

    #: the metered resistors, ohms, by input
    RESISTORS = {"01": 999.0, "02": 99400.0, "03": 219300.0}

    def wired(self, run):
        """A twin with that run's resistor on its input, switched on as
        that run's type."""
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        n = int(run["input"])
        c.wire_sensor("liquid", n, self.RESISTORS[run["input"]])
        h.handle(SOH + f"S703{n:02d}{run['type']}\r".encode())
        h.handle(SOH + f"S701{n:02d}1\r".encode())
        c.tick()
        return c, h, n

    def test_each_type_reads_its_own_state_off_the_one_resistor(self):
        """The resistor on the input, and the state the console reads off
        it by the type -- every one of the 21 settled statuses, the type
        changed over the same resistance. CLOSED S61."""
        wrong = []
        for run in self.runs:
            _c, h, n = self.wired(run)
            reply = h.handle(SOH + f"i301{n:02d}".encode()).decode("latin-1")
            if reply.split("&&")[0][-4:] != run["lag"][-1][1]:
                wrong.append((run["input"], run["type"]))
        self.assertEqual(wrong, [])

    #: ssss of i301 with the checks armed and the sensor unlabelled: 0001
    #: is the SETUP DATA WARNING alone, which is the input reading normal
    ARMED = dict(STATUS, **{"0001": "normal", "0005": "water",
                            "0006": "waterout", "0007": "high",
                            "0009": "warn"})

    def sweep(self, name, ohms):
        """[(input, type, bench state, ours)] that disagree, off one
        `scripts/typesweep.py` log, the metered `ohms` by input."""
        settled = {}
        with open(os.path.join(NINTH, name), encoding="utf-8") as fh:
            run = None
            for line in fh:
                rec = json.loads(line)
                m = re.match(r"on, type (\d)", rec.get("note", ""))
                if m and rec.get("input") in ohms:
                    run = (rec["input"], m.group(1))
                elif run and rec.get("note", "").startswith("lag"):
                    settled[run] = rec["reply"].split("&&")[0][-4:]
        self.assertEqual(len(settled), 7 * len(ohms))
        wrong = []
        for (inp, kind), status in sorted(settled.items()):
            c = liquid_bench()
            n = int(inp)
            c.wire_sensor("liquid", n, ohms[inp])
            c.values[f"S703{n:02d}"] = f"{n:02d}{kind}"
            if c.sensor_condition("liquid", n) != self.ARMED[status]:
                wrong.append((inp, kind, self.ARMED[status],
                              c.sensor_condition("liquid", n)))
        return wrong

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep47k.jsonl")),
                         "the 2026-10-10 sweep is not here")
    def test_47k_and_471k_read_as_the_bench_read_them(self):
        """46.6k and 471k, metered, on inputs 1 and 2, each type in turn
        (2026-10-10, `sweep47k.jsonl`): the state the console reads off
        the input, all fourteen. CLOSED S62."""
        self.assertEqual(self.sweep("sweep47k.jsonl",
                                    {"01": 46600.0, "02": 471000.0}), [])

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep4k.jsonl")),
                         "the 2026-10-10 second sweep is not here")
    def test_4k_to_68k_read_as_the_bench_read_them(self):
        """4.66k, 21.73k, 32.55k and 67.7k on inputs 3 to 6 (2026-10-10,
        `sweep4k.jsonl`), all twenty-eight -- the interceptor's WATER and
        WATER OUT among them. CLOSED S62."""
        self.assertEqual(self.sweep("sweep4k.jsonl",
                                    {"03": 4660.0, "04": 21730.0,
                                     "05": 32550.0, "06": 67700.0}), [])

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep89k.jsonl")),
                         "the 2026-10-10 third sweep is not here")
    def test_89k_reads_as_the_bench_read_it(self):
        """21.73k and 67.7k in series on input 7, read 90580 (2026-10-10,
        `sweep89k.jsonl`): the interceptor's second FUEL range and the DW
        sump's LOW LIQUID. CLOSED S63."""
        self.assertEqual(self.sweep("sweep89k.jsonl", {"07": 89430.0}), [])

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep79k.jsonl")),
                         "the 2026-10-10 fourth sweep is not here")
    def test_79k_reads_as_the_bench_read_it(self):
        """46.6k and 32.55k in series on input 8, read 80136 (2026-10-10,
        `sweep79k.jsonl`): the DW sump LOW LIQUID again. CLOSED S63."""
        self.assertEqual(self.sweep("sweep79k.jsonl", {"08": 79150.0}), [])

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep75k.jsonl")),
                         "the 2026-10-10 fifth sweep is not here")
    def test_75k_reads_as_the_bench_read_it(self):
        """74.6k on input 1, read 75315 (2026-10-10, `sweep75k.jsonl`): the
        discriminating sensors normal, the interceptor OUT and the DW sump
        LOW LIQUID. CLOSED S63."""
        self.assertEqual(self.sweep("sweep75k.jsonl", {"01": 74600.0}), [])

    @unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "sweep72k.jsonl")),
                         "the 2026-10-10 sixth sweep is not here")
    def test_72k_reads_as_the_bench_read_it(self):
        """67.7k and 4.66k in series on input 1, read 73299 (2026-10-10,
        `sweep72k.jsonl`): the DW sump LOW LIQUID yet again. CLOSED S63."""
        self.assertEqual(self.sweep("sweep72k.jsonl", {"01": 72600.0}), [])

    def test_the_input_reads_as_the_card_reads_it(self):
        """IB01's VALUE, a percent and a bit over the meter's reading, for
        all three resistors, within the card's own spread: the 1k read 988
        to 1008 over its seven runs."""
        from tls350sim import readings
        for run in self.runs:
            c, _h, n = self.wired(run)
            ours = readings.sensor_value(c, "liquid", n, "normal")
            self.assertAlmostEqual(ours / run["value"], 1.0, delta=0.025,
                                   msg=run)

    def test_the_status_settles_within_about_a_second(self):
        for run in self.runs:
            settled = run["lag"][-1][1]
            late = [t for t, v in run["lag"] if v != settled]
            self.assertTrue(all(t < 1.0 for t in late), run)


class TheGlass(unittest.TestCase):
    """The screens stepped on the bench's keypad while 5FA read them (2026-10-08,
    `transcripts/glassdiag.jsonl`, `glasssetup.jsonl`): the card in slot 2,
    sensor 2 switched on."""

    def console(self):
        from tls350sim.console import Console
        c = Console(None)
        c.modules = {"probe": 1, "liquid": 1}
        c.values["S70102"] = "021"
        return c

    def test_the_setup_walk(self):
        from tls350sim import screens
        from tls350sim.console import SETUP_MENU
        c = self.console()
        fn = next(f for f in SETUP_MENU
                  if f["function"] == "LIQUID SENSOR SETUP")
        got = [screens.setup_lines(c, fn, st, device=2) for st in fn["steps"]]
        self.assertEqual(got, [
            ["SENSOR CONFIG - MODULE 1", "SLOT 2 - X 2 X X X X X X"],
            ["ENTER SENSOR LOCATION", "L 2:"],
            ["L 2:ENTER SENSOR TYPE", "TRI-STATE (SINGLE FLOAT)"],
            ["L 2:", "CATEGORY : OTHER SENSORS"]])
        c.values["S70202"] = "02SUMP2"
        self.assertEqual(screens.setup_lines(c, fn, fn["steps"][1], 2),
                         ["ENTER SENSOR LOCATION", "L 2:SUMP2"])

    def test_the_diagnostic(self):
        c = self.console()
        c.sensor_state[("liquid", "3")] = "open"
        head = "L 1: (PRODUCT LABEL)"
        self.assertEqual(c.diag_line(head, 3), "L 3:")
        self.assertEqual(c.diag_reading("sensor_liquid", 3),
                         "CNTR= 5 VALUE=1000000000")
        c.values["S70201"] = "01SUMP1"
        self.assertEqual(c.diag_line(head, 1), "L 1:SUMP1")


@unittest.skipUnless(os.path.isfile(os.path.join(TRANSCRIPTS,
                                                 "customset.jsonl")),
                     "the 2026-10-08 custom alarm transcript is not here")
class TheCustomAlarmLabels(unittest.TestCase):
    """5BE and 5BF on the bench, custom alarms on and PAPER OUT labelled
    `KPM` at the keypad first: PRINTER ERROR added, its outputs changed,
    then removed (`transcripts/customset.jsonl`). Every reply, byte for
    byte under the stamp."""

    def test_every_reply(self):
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        for cmd in ("S5BD001", "S5BE0001011KPM"):
            h.handle(SOH + cmd.encode() + b"\r")
        wrong = []
        with open(os.path.join(TRANSCRIPTS, "customset.jsonl"),
                  encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                cmd = rec["cmd"]
                if not cmd.upper().startswith(("S5B", "I5B")):
                    continue
                sent = SOH + cmd.encode() + (b"\r" if cmd[0] in "Ss"
                                             else b"")
                ours = h.handle(sent).decode("latin-1")
                if cmd[0] == "i":
                    same = (body(cmd, rec["reply"].encode("latin-1"))
                            == body(cmd, ours.encode("latin-1")))
                else:
                    same = (STAMP.sub("", rec["reply"])
                            == STAMP.sub("", ours))
                if not same:
                    wrong.append(cmd)
        self.assertEqual(wrong, [])

    def a_bench(self):
        """The bench as `custom2.jsonl` found it: KPM on PAPER OUT, L 1 and
        L 2 wired, L 2 on and labelled, L 3 open, the checks armed."""
        from tls350sim.wire import Handler
        c = liquid_bench()
        h = Handler(c, verbose=False)
        for cmd in ("S5BD001", "S5BE0001011KPM", "S70202SUMP2", "S701021"):
            h.handle(SOH + cmd.encode() + b"\r")
        c.sensor_state[("liquid", "1")] = "normal"
        c.sensor_state[("liquid", "2")] = "normal"
        c.tank_checks = True
        return c, h

    @unittest.skipUnless(os.path.isfile(os.path.join(TRANSCRIPTS,
                                                     "custom2.jsonl")),
                         "the second custom alarm transcript is not here")
    def test_a_device_s_own_label_and_its_outputs(self):
        """L 3 SENSOR OUT labelled for device 03 through 5BF, its LCD and
        LED switched off in turn, a 5BE label for every device put over it,
        and all of it taken off again (`transcripts/custom2.jsonl`): the
        custom alarm replies and I10100, byte for byte under the stamp."""
        c, h = self.a_bench()
        wrong = []
        with open(os.path.join(TRANSCRIPTS, "custom2.jsonl"),
                  encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                cmd = rec["cmd"]
                sent = SOH + cmd.encode() + (b"\r" if cmd[0] in "Ss"
                                             else b"")
                ours = h.handle(sent).decode("latin-1")
                c.tick()
                if not cmd.upper().startswith(("S5B", "I5B", "I101")):
                    continue
                if cmd[0] == "i":
                    same = (body(cmd, rec["reply"].encode("latin-1"))
                            == body(cmd, ours.encode("latin-1")))
                else:
                    same = (STAMP.sub("", rec["reply"])
                            == STAMP.sub("", ours))
                if not same:
                    wrong.append(cmd)
        self.assertEqual(wrong, [])

    def test_lcd_off_takes_it_out_of_the_display_s_rotation(self):
        """With its LCD output off the bench's `L 3:SUMP 3 OUT` never came
        round on the glass in six reads, and I10100 still listed it; with
        the LED off instead, it came round."""
        c, h = self.a_bench()
        for cmd in ("S5BF0003040310111SUMP 3 OUT", "S701031"):
            h.handle(SOH + cmd.encode() + b"\r")
        c.tick()

        def glass_over(seconds):
            seen = set()
            for _ in range(seconds):
                c.clock_offset += 1.0
                seen.add(h.handle(SOH + b"I5FA00").decode("latin-1"))
            return "".join(seen)

        self.assertNotIn("SUMP 3 OUT", glass_over(8))
        self.assertIn("L 3:SUMP 3 OUT", h.handle(SOH + b"I10100").decode())
        h.handle(SOH + b"S5BF0003040311110SUMP 3 OUT\r")
        self.assertIn("L 3:SUMP 3 OUT", glass_over(8))

    def test_a_labelled_alarm_is_named_by_its_label(self):
        """PAPER OUT labelled `KPM`: the bench's non-priority history filed
        `    SYSTEM                         KPM                  ALARM`,
        PRINTER ERROR beside it under its own name, and the glass showed
        `KPM` (on the bench's glass, 2026-10-08)."""
        from tls350sim.console import Console, describe_alarms
        from tls350sim.wire import Handler
        c = Console(None)
        h = Handler(c, verbose=False)
        for cmd in ("S5BD001", "S5BE0001011KPM"):
            h.handle(SOH + cmd.encode() + b"\r")
        c.out_of_paper = True
        c.tick()
        self.assertEqual([a["screen"] for a in
                          describe_alarms(c.compute_alarms(), c)], ["KPM"])
        rows = [r for r in h.handle(SOH + b"I11200").decode().split("\r\n")
                if "SYSTEM" in r]
        self.assertTrue(rows[0].startswith(
            "    SYSTEM                         KPM                  ALARM"),
            rows)


@unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "customlist.jsonl")),
                     "the 2026-10-09 custom alarm read-back is not here")
class TheKeypadsCustomAlarmsOnTheWire(unittest.TestCase):
    """What the keypad left in the list on 2026-10-09 -- PAPER OUT `KPM`,
    FUEL ALARM for all sensors `U R AWESOM`, SENSOR OUT ALARM for L 2 alone
    `BOOP` -- read back in both formats of 5BD, 5BE and 5BF
    (`customlist.jsonl`): an entry for one device is its number where the
    others carry 00, and the reports name it `L 2:`. Every reply, byte for
    byte under the stamp, off the list the bench packed. CLOSED S56."""

    def test_every_reply(self):
        from tls350sim.wire import Handler
        with open(os.path.join(NINTH, "customlist.jsonl"),
                  encoding="utf-8") as fh:
            recs = {r["cmd"]: r["reply"] for r in map(json.loads, fh)}
        c = liquid_bench()
        h = Handler(c, verbose=False)
        h.handle(SOH + b"S5BD001\r")
        c.values["S5BF00"] = packed_body(
            recs["i5BF00"].encode("latin-1"))[0][2:]
        wrong = []
        for cmd in ("I5BE00", "i5BE00", "I5BF00", "i5BF00", "i5BD00"):
            ours = h.handle(SOH + cmd.encode()).decode("latin-1")
            if cmd[0] == "i":
                same = (body(cmd, recs[cmd].encode("latin-1"))
                        == body(cmd, ours.encode("latin-1")))
            else:
                same = STAMP.sub("", recs[cmd]) == STAMP.sub("", ours)
            if not same:
                wrong.append(cmd)
        self.assertEqual(wrong, [])


class TheLiquidSetupDataWarning(unittest.TestCase):
    """The bench, 2026-10-08, after a visit to Setup Mode armed the
    checks (`transcripts/s35.jsonl`): resistors on inputs 1 to 3, sensor 2
    switched on and unlabelled, the rest off; then L 2 named, L 4 switched
    on unnamed and named, L 3's resistor taken off."""

    def warned(self, c):
        return sorted(int(r[4:6]) for r in c.setup_warnings()
                      if r[:4] == "0302")

    def test_the_sequence(self):
        c = liquid_bench()
        for n in (1, 2, 3):
            c.sensor_state[("liquid", str(n))] = "normal"
        c.values["S70102"] = "021"
        self.assertEqual(self.warned(c), [], "not armed out of a cold start")
        c.tank_checks = True
        self.assertEqual(self.warned(c), [1, 2, 3])
        c.values["S70202"] = "02SUMP2"
        self.assertEqual(self.warned(c), [1, 3])
        c.values["S70104"] = "041"
        self.assertEqual(self.warned(c), [1, 3, 4])
        c.values["S70204"] = "04SUMP4"
        self.assertEqual(self.warned(c), [1, 3])
        c.values["S70201"] = "01SUMP1"
        self.assertEqual(self.warned(c), [1, 3], "an off input warns named")
        c.sensor_state[("liquid", "3")] = "open"
        self.assertEqual(self.warned(c), [1])


@unittest.skipUnless(os.path.isfile(os.path.join(NINTH, "offtank.jsonl")),
                     "the 2026-10-09 chart transcripts are not here")
class TheChartOverTheWire(unittest.TestCase):
    """Tank 1's 50 point chart set over the wire, 2026-10-09: hex refused
    in the display form, the dumped header refused sent back, pairs that
    must sit inside the tank and rise together, a pair already there taken
    again, a pair taken off that must be on the chart, the profile moved to
    50 PTS by a pair and its chart erased by a 604. Every reply of the
    three logs, through one twin, in order (`scripts/replay.py`)."""

    LOGS = ("chart.jsonl", "tanksets.jsonl", "offtank.jsonl")

    def test_every_reply(self):
        from tls350sim.wire import Handler
        c = liquid_bench()
        c.tank_checks = True
        h = Handler(c, verbose=False)
        wrong = []
        for name in self.LOGS:
            with open(os.path.join(NINTH, name), encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    cmd = rec["cmd"]
                    ours = h.handle(SOH + cmd.encode("latin-1")
                                    + (b"\r" if cmd[0] in "Ss" else b""))
                    theirs = rec["reply"].encode("latin-1")
                    if cmd.startswith("I63B"):
                        # byte for byte under the stamp: `lines()` drops the
                        # trailing spaces that tell `T 1:TANKA` sent short
                        # from the label sent padded (CLOSED S64)
                        same = (STAMP.sub("", theirs.decode("latin-1"))
                                == STAMP.sub("", ours.decode("latin-1")))
                    else:
                        same = body(cmd, theirs) == body(cmd, ours)
                    if not same:
                        wrong.append((name, cmd))
        self.assertEqual(wrong, [])


class The121ReadingWord(unittest.TestCase):
    """121's last eight characters are the high word of a double, the
    device's reading as the alarm came: on the bench, 2026-10-09
    (`bench-2026-10-09/float121.jsonl`), a liquid alarm on 1,009 ohms packed
    408F7FDD, on 100,455 ohms 40F88547, on an open input 41CDCD65, and an
    unlabelled sensor's SETUP DATA WARNING a word of zero, `       0`."""

    def setUp(self):
        from tls350sim.wire import Handler
        self.c = liquid_bench()
        self.h = Handler(self.c, verbose=False)
        self.c.tank_checks = True

    def send(self, cmd):
        return self.h.handle(SOH + cmd.encode() + (b"\r" if cmd[0] in "Ss"
                                                   else b"")).decode("latin-1")

    def words(self):
        """{AANNTT: word} off the packed 121, past its 80-column header."""
        data = self.c.alarm_121_records()[80 + 1:]
        out = {}
        while len(data) >= 46:
            rec, data = data[:46], data[46:]
            out[rec[12:18]] = rec[38:46]
        return out

    def test_an_open_input_packs_a_billion(self):
        self.send("S70204SUMP4")
        self.send("S701041")
        self.c.compute_alarms()
        self.assertEqual(self.words()["030404"], "41CDCD65")

    def test_an_unlabelled_warning_packs_a_spaced_zero(self):
        self.c.sensor_state[("liquid", "5")] = "normal"
        self.send("S701051")
        self.c.compute_alarms()
        self.assertEqual(self.words()["030205"], "       0")

    def test_a_wired_input_packs_its_reading(self):
        import struct
        from tls350sim import readings
        self.c.sensor_state[("liquid", "1")] = "fuel"
        self.send("S70201SUMP1")
        self.send("S701011")
        self.c.compute_alarms()
        key = [k for k in self.words() if k[:2] == "03" and k[4:] == "01"]
        self.assertEqual(len(key), 1, self.words())
        word = self.words()[key[0]]
        value = struct.unpack(">d", bytes.fromhex(word + "0" * 8))[0]
        want = readings.sensor_value(self.c, "liquid", 1,
                                     self.c._sensor_state("liquid", 1), 1)
        self.assertAlmostEqual(value / want, 1.0, places=4)

    def test_the_other_categories_keep_their_words(self):
        self.c.out_of_paper = True
        self.c.compute_alarms()
        paper = [w for k, w in self.words().items() if k[:4] == "0101"]
        self.assertEqual(paper, ["40080000"])


@unittest.skipUnless(os.path.isdir(os.path.join(SWAPPED, "raw")),
                     "the 2026-10-08 swapped-card capture is not here")
class TheCardSwappedWithoutAColdStart(unittest.TestCase):
    """The bench before the cold start: what it reset with is what it
    names, types and drives."""

    CODES = ("I70100", "I70200", "I70300", "I70400", "I30100", "I30200",
             "c_i30100", "c_i70100")

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        cls.c = liquid_bench(cold=False)
        cls.h = Handler(cls.c, verbose=False)

    def ask(self, name):
        code = name.replace("c_", "")
        return framed(self.h, SOH + code.encode("ascii"))

    def test_the_liquid_codes_are_bare(self):
        for name in self.CODES:
            real = read(os.path.join(SWAPPED, "raw", name + ".bin"))
            code = name.replace("c_", "")
            self.assertEqual(body(code, self.ask(name)), body(code, real),
                             name)

    def test_slot_2_keeps_the_plld_board_s_name_and_type(self):
        real = display_body(read(os.path.join(SWAPPED, "raw",
                                              "I10200.bin")))
        ours = display_body(self.ask("I10200"))
        row = [r for r in ours if r.startswith("  2")]
        self.assertEqual([r[:36] for r in row],
                         [r[:36] for r in real if r.startswith("  2")])
        records = packed_body(self.ask("c_i10200"))[0]
        self.assertEqual(records[22:26], "021A")


if __name__ == "__main__":
    unittest.main()
