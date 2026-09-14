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
"""Category 36, the VMC's four alarms, and the two reports that carry them.

576013-610 Rev AC Table 29-23 is the VMC's own message table and it is four
rows: VMC COM TIMEOUT, METR NC ALM, FP SHUTDWN WRN and FP SHUTDWN ALM. Every
one of them is a STATUS the controller reports up the S-Link, and four of the
eight words `VMC_STATUS_CODE` already carried -- so the console does not
decide a VMC alarm, it is told it.

`M12` put category 36 in the status table and nothing produced it, and 411
and 412 printed a header with no rows under it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console
from tls350sim.wire import Handler, SOH


def a_forecourt(controllers=2):
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("vmc", 1)
    for n in range(1, controllers + 1):
        c.vmc_serials[n] = str(n) * 6
    return c


class TheFourAlarms(unittest.TestCase):
    def test_each_status_posts_its_own_row_of_table_29_23(self):
        c = a_forecourt()
        for word, nn in (("VMC COMM TIMEOUT", "01"),
                         ("METER NOT CONNECTED", "02"),
                         ("FP SHUTDOWN WARNING", "03"),
                         ("FP SHUTDOWN ALARM", "04")):
            c.set_vmc_status(1, "A", word)
            self.assertIn("36" + nn + "01", c.conditions(), word)

    def test_the_healthy_statuses_post_nothing(self):
        """The other four of the eight are what a working controller says."""
        c = a_forecourt()
        for word in ("IDLE", "RUNNING", "LAST TRANSACTION FAILED",
                     "STATUS UNKNOWN"):
            c.set_vmc_status(1, "A", word)
            self.assertEqual([x for x in c.conditions()
                              if x.startswith("36")], [], word)

    def test_a_controller_alarms_when_either_side_does(self):
        """One device field and two sides, so the record is per controller.
        412's own report has no side column in it either."""
        c = a_forecourt()
        c.set_vmc_status(2, "B", "FP SHUTDOWN ALARM")
        self.assertEqual([x for x in c.conditions() if x.startswith("36")],
                         ["360402"])
        c.set_vmc_status(2, "A", "FP SHUTDOWN ALARM")
        self.assertEqual([x for x in c.conditions() if x.startswith("36")],
                         ["360402"])

    def test_two_different_faults_on_one_controller_are_two_alarms(self):
        c = a_forecourt()
        c.set_vmc_status(1, "A", "VMC COMM TIMEOUT")
        c.set_vmc_status(1, "B", "METER NOT CONNECTED")
        self.assertEqual([x for x in c.conditions() if x.startswith("36")],
                         ["360101", "360201"])

    def test_it_clears_when_the_controller_comes_back(self):
        """The CONDITION goes when the controller answers again. The alarm
        itself latches, like every other alarm that is not a warning, and
        needs acknowledging -- so this is two assertions and not one."""
        c = a_forecourt()
        c.set_vmc_status(1, "A", "VMC COMM TIMEOUT")
        self.assertIn("360101", c.compute_alarms())
        c.set_vmc_status(1, "A", "IDLE")
        self.assertNotIn("360101", c.conditions())
        self.assertIn("360101", c.compute_alarms())     # latched
        c.acknowledge()
        self.assertNotIn("360101", c.compute_alarms())

    def test_no_vmci_module_no_category_36(self):
        c = a_forecourt()
        c.set_vmc_status(1, "A", "FP SHUTDOWN ALARM")
        c.modules.pop("vmc", None)
        self.assertEqual([x for x in c.conditions()
                          if x.startswith("36")], [])

    def test_it_reaches_the_status_report_by_its_own_name(self):
        c = a_forecourt()
        c.set_vmc_status(1, "A", "FP SHUTDOWN ALARM")
        report = Handler(c, verbose=False).handle(
            SOH + b"I10100").decode("latin-1")
        self.assertIn("x 1:FP SHUTDOWN ALARM", report)


class TheTwoAlarmHistoryReports(unittest.TestCase):
    """576013-635 Rev AA pp.150-151. Identical layouts, incompatible alarm
    tables, and both printed a header and nothing else: the computer form
    said "00 incidents" for every device and the display form had no rows at
    all."""

    def ask(self, c, code):
        return Handler(c, verbose=False).handle(SOH + code).decode("latin-1")

    def rows(self, text):
        return [r for r in text.split("\r\n") if r.strip()]

    def test_412_is_a_block_per_controller_in_the_figure_s_columns(self):
        """The sample's columns, counted off the rendered page at six points
        a character: the VMC digit at column 1, the serial at 5, the stamp at
        13 and the alarm name at 41."""
        c = a_forecourt()
        c.set_vmc_status(1, "A", "METER NOT CONNECTED")
        c.compute_alarms()
        c.set_vmc_status(1, "A", "FP SHUTDOWN ALARM")
        c.compute_alarms()
        c.set_vmc_status(2, "B", "VMC COMM TIMEOUT")
        c.compute_alarms()
        got = self.rows(self.ask(c, b"I41200"))
        self.assertIn("VMC ALARM HISTORY REPORT", got)
        head = got[got.index("VMC ALARM HISTORY REPORT") + 1]
        self.assertEqual(head, "VMC   S/N    ALARMS")
        self.assertEqual(head.index("S/N"), 6)
        self.assertEqual(head.index("ALARMS"), 13)
        body = [r for r in got[got.index(head) + 1:] if r.strip("\x03")]
        self.assertEqual(len(body), 3)
        self.assertEqual(body[0][:13], " 1   111111  ")
        self.assertTrue(body[0].endswith("      METER NOT CONNECTED"))
        # the second incident on the same controller keeps the stamp column
        # and drops the device columns
        self.assertEqual(body[1][:13], " " * 13)
        self.assertTrue(body[1].endswith("      FP SHUTDOWN ALARM"))
        self.assertEqual(body[2][:13], " 2   222222  ")
        for row in body:
            # the stamp is TWENTY-TWO columns and not `clock_words`'s
            # twenty-one: both samples put the time one column further right
            # than a 21-character stamp would. So it fills 13 to 34, the gap
            # is six, and the name lands on 41 on every row.
            self.assertEqual(len(row[13:35].strip()), 22, row)
            self.assertEqual(row[35:41], "      ", row)
            self.assertNotEqual(row[41], " ", row)

    def test_411_is_the_same_shape_with_no_serial_column(self):
        """"DEVICE  ALARMS": the board number right aligned in six, the stamp
        at 8 and the name at 36. Its category is 35, the VMCI board's."""
        c = a_forecourt()
        c.set_module("vmc", 2)          # "More than one VMCI module is
        c.compute_alarms()              # installed", which is 35/01
        got = self.rows(self.ask(c, b"I41100"))
        self.assertIn("VMCI ALARM HISTORY REPORT", got)
        head = got[got.index("VMCI ALARM HISTORY REPORT") + 1]
        self.assertEqual(head, "DEVICE  ALARMS")
        row = got[got.index(head) + 1].rstrip("\x03")
        self.assertEqual(row[:8], "     2  ")
        self.assertTrue(row.endswith("      SETUP DATA WARNING"))
        self.assertEqual(len(row) - len("      SETUP DATA WARNING") - 8, 22)

    def test_the_two_tables_stay_incompatible(self):
        """0002 is "Disabled VMCI Board" on 411 and the meter on 412, which
        is the trap this pair was written around."""
        from tls350sim import sumpreports
        self.assertNotEqual(sumpreports.VMCI_ALARMS["0002"],
                            sumpreports.VMC_ALARMS["0002"])

    def test_the_computer_form_counts_incidents_per_device(self):
        """"xx - VMC Controller Number", "NN - Number of alarm Incidents to
        follow (ASCII Hex)", then a stamp and a type per incident."""
        c = a_forecourt()
        c.set_vmc_status(3, "A", "FP SHUTDOWN WARNING")
        c.compute_alarms()
        body = self.ask(c, b"i41203").split("&&")[0]
        # SOH, i41203, ten digits of clock, then the block
        block = body[1 + 6 + 10:]
        self.assertEqual(block[:4], "0301")          # device 03, one incident
        self.assertEqual(block[14:18], "0003")       # FP SHUTDOWN WARNING
        self.assertEqual(len(block), 4 + 10 + 4)

    def test_a_quiet_controller_still_answers_a_count_of_zero(self):
        c = a_forecourt()
        body = self.ask(c, b"i41201").split("&&")[0]
        self.assertEqual(body[1 + 6 + 10:], "0100")

    def test_no_module_no_report(self):
        c = Console()
        c.modules["rs232"] = 1
        self.assertIn("9999", self.ask(c, b"I41200"))


if __name__ == "__main__":
    unittest.main()
