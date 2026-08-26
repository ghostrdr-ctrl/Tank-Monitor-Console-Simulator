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
"""One window: the console on top, the bench underneath.

LOOK. The display is a monochrome LCD, not a colour screen, pale grey-green
glass, near-black characters, a fixed 20x2 character cell, and the faint
"ghost" of every unlit segment behind the text, which is what makes an old
calculator or gauge display recognisable. The keys are the console's two
12-key pads: operating keys left, alphanumeric right, dark faces with light
legends, laid out in the manual's order.

I could not photograph a real console from here: the manual's figures are
vector art, not images, so the LAYOUT is taken from the manual's Figure 2-1
and its key descriptions, and the finish follows what a TLS-350 looks like.
Tell me what is off and it is a few constants at the top of this file.
"""
import time

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import APP_NAME, DISCLAIMER, PUBLISHER, __version__
from . import fieldio, masks
from . import bench, paths, update, updateui, xport
from .clock import clock_hhmm, clock_words
from . import presets
from . import printer
from . import screens
from . import versions
from .console import (BAY_NAME, BAY_SLOTS, DEVICE_WORD, FUNCTION_REQUIRES,
                      MODULE_BAY, MODULE_CAPACITY, MODULE_LABEL, MODULE_PART,
                      MODULES, SLOT_POSITIONS, SOFTWARE_MODULES,
                      SOFTWARE_NAME, describe_alarms)

# The display is 24 characters by 2 lines: the console's own status screen
# reads "JAN 29, 2003 11:05:14 AM" across the top line, which is 24.
COLS, ROWS = 24, 2
HEADER = -1          # the function's own screen, before its first step
MODE_SCREEN = -2     # the mode's own screen, before any function

# The console's own prompt, and it is exactly 24 characters, which is why the
# FUNCTION one is clipped and the STEP one is not: "PRESS <STEP> TO CONTINUE"
# fits the display, "PRESS <FUNCTION> TO CONTINUE" does not. Both forms are
# printed that way in the manuals' screen diagrams -- 576013-623 Rev AN prints
# "PRESS <STEP> TO CONTINUE" 253 times, and 576013-939 Rev F draws
# the FUNCTION form under RECONCILIATION MODE.
CONT_STEP = "PRESS <STEP> TO CONTINUE"
CONT_FUNCTION = "PRESS <FUNCTION> TO CONT"

# ---- finish -----------------------------------------------------------------
# The dimensions and colours below were measured from a photograph of a real
# console; the drawing itself is original. Every element is a Tk canvas
# primitive and no image is loaded at runtime, so the photograph is not
# distributed with this program.
#
# The console is an OFF-WHITE box with NAVY BLUE trim and a GREEN display, and
# the keys are WHITE with black legends on a black grid, not dark keys with
# light legends. An earlier pass measured a dark, inverted rendering and got
# all three of those backwards.
CASE = "#ececea"          # off-white enclosure
CASE_EDGE = "#c9c9c5"
BLUE = "#1c3f91"          # base pinstripes
LABEL = "#1a1a1a"         # the black legends beside the indicators
VFD_BG = "#0e1a10"        # dark glass
VFD_GHOST = "#1d3320"     # unlit segments
VFD_INK = "#6dfb8a"       # green characters

# The strip across the top of the panel where a real console carries its
# maker's branding. Left bare here, but it keeps its height so that every
# dimension below it is unchanged.
BRAND_PLATE_H = 34
VFD_EDGE = "#8f9490"
KEYPAD_BG = "#101010"     # the black grid the keys sit in
KEYPAD_EDGE = "#101010"
KEY_FACE = "#f6f6f4"      # white keys
KEY_TEXT = "#111111"      # black legends
KEY_ALARM = "#e01b1b"     # the red ALARM TEST key, top left
KEY_MAINT = "#2f5fb0"     # the blue Maintenance Tracker key
LED_OFF = "#5a5a56"
PANEL_BG = "#2a2c30"

# ---- the printer behind the left-hand door ---------------------------------
# In the photograph the left door carries a smoked charcoal cover with a
# rounded top that curves forward over the mechanism, and the paper comes out
# of a slot along its bottom edge, just above the navy pinstripes. The cover
# is translucent, you can see the roll behind it, so it is drawn as a dark
# face with a lighter band down the left where the light catches the curve.
COVER = "#26262a"         # the smoked cover
COVER_LIT = "#3c3d43"     # where the curve catches the light
COVER_DARK = "#141416"    # the shadow under the lip and inside the slot
COVER_EDGE = "#0c0c0e"
SLOT = "#0a0a0b"          # the paper exit
TEAR_BAR = "#9a9a96"      # the metal tear-off strip in the slot
PAPER = "#f5f2e7"         # the roll: warm white, not white
PAPER_EDGE = "#d8d3c1"
PAPER_INK = "#26251f"     # a tired ribbon, not black
PAPER_COLS = 40           # what the roll is wide, in characters
SLIP_PAD = 5              # the margin either side of those characters
SLIP_TEAR = 13            # the torn edge along the bottom
DOOR_W = 300              # the left door, before the window has a size
DOOR_SHARE = 0.36         # and the share of the window it takes after that
DOOR_RATIO = 0.80         # width over height, off the photograph

MODES = ["NORMAL", "SETUP", "DIAGNOSTIC", "RECONCILIATION"]
# Table 2-1, Character Assignments for Numeric Keys. The Setup Manual walks it
# in words: "to enter an 'A' in a station header ... you press the key once.
# Push the key again to change the character to a 'B', again to enter a 'C',
# and again to enter a '2'." So the letters come first and the digit is the
# last press, not the first, and the Operator's Quick Help 576013-939 says it
# a second time: "Select a character by successive presses of the key. Press
# once for 'A'. Press again for 'B', again for 'C' and a fourth time to enter
# a '2'."
LETTERS = {"1": "QZ.1", "2": "ABC2", "3": "DEF3", "4": "GHI4", "5": "JKL5",
           "6": "MNO6", "7": "PRS7", "8": "TUV8", "9": "WXY9", "0": " -,0*"}

# "the '&', '=', and '%' characters are available only when entering the Modem
# Setup String": a fifth press that exists on three keys, on one screen.
MODEM_EXTRA = {"1": "&", "2": "=", "3": "%"}
MODEM_STRING_CODE = "886"

class SimApp(tk.Tk):
    def __init__(self, console, port=10001):
        super().__init__()
        self.console = console
        self.port = port
        self.title(f"{APP_NAME}  --  serial on 127.0.0.1:{port}")
        self.configure(bg=PANEL_BG)
        self.reset_panel()
        self._build()
        # The printer door's height is set by _fit_door, not by its
        # children, so it must be sized BEFORE the window asks itself how
        # tall to be -- measured first, the door is a 1px sliver and the
        # window opens too short to show the console's own face.
        self._fit_door()
        self.update_idletasks()
        want_h = min(self.winfo_reqheight(), self.winfo_screenheight() - 90)
        want_w = min(max(self.winfo_reqwidth(), 900), self.winfo_screenwidth() - 60)
        self.geometry(f"{want_w}x{want_h}+40+30")
        self.minsize(880, 460)
        self._fit_door()
        self._render()
        self._place_bench_window()
        self._poll()
        self.bind("<Key>", self._on_key)
        self.bind("<Configure>", self._on_resize)
        # One wheel for the application: the router finds the right canvas
        # under the pointer, in this window or the bench window.
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<F2>", self._show_bench)

    # =====================================================================
    # layout
    # =====================================================================

    def reset_panel(self):
        """Everything the panel itself remembers, back to power-on.

        A console keeps two kinds of state: what is programmed, which lives
        in the Console, and where the operator is standing, which lives here.
        This is the second kind. It is a method rather than a run of lines in
        __init__ so that a test can put the panel back to power-on without
        building another window -- Tk does not enjoy being started seventy
        times in one process.
        """
        self.mode = 0
        self.func = 0
        self.step = HEADER
        self.device = 1
        self.confirm = None       # the PRESS <STEP> TO CONTINUE screen
        self.slot = 0             # which position the config screen is on
        self.sub = None           # the diagnostic screen ENTER descended into
        self.locked = False       # waiting for the System Security Code
        self.armed = False        # an archive answer toggled to YES
        self._profile_pending = None   # a tank profile waiting to be confirmed
        self._point = {}          # a 50 point pair being strapped in
        self.dlv = 0              # which delivery Delivery Maintenance is on
        self._insert = {}         # a delivery being entered by hand
        self.chart_open = False   # the tank chart passcode, once given
        self.sure = False         # and the ARE YOU SURE? screen after it
        self.boot_restore = None  # the cold-start RESTORE SETUP DATA prompt
        self.isd_override = None  # the shutdown-override confirmation walk
        self.isdflow = None       # the grade-hose mapping flows of 577013-800
        self._alarm_presses = 0   # ALARM/TEST x3 reaches the override
        self.slip = None          # the paper hanging out of the printer
        self.slip_out = False     # whether any is hanging out of it
        self._slip_lines = 0      # how long what is on it has got
        self._last_size = None    # the window size <Configure> last reported
        self._resizing = False    # inside _on_resize already
        self.busy_until = 0.0     # the console mid-archive, answering nothing
        self.editing = False
        self.buf = ""
        # Where the cursor is sitting in `buf`. A TLS-350 does not blank the
        # field when you press CHANGE: "If you enter an incorrect character,
        # you may use the arrow keys to move the cursor to the character,
        # press CHANGE, and enter the correct character." So the value stays
        # on the screen, one character of it flashes, and that is where the
        # next key press lands.
        self.cur = 0
        # "Select either AM or PM by using the arrow keys": a time is edited
        # on the twelve hour clock the screen shows, so the half of the day
        # is a field of its own beside the digits.
        self.meridiem = ""
        self.msg = ""
        self._tap = None
        self._cycle = 0
        self.mt_key = False       # Contractor's ID key in the MT Comm card
        self._posted = set()      # alarms the printer has already reported
        self._last_key = time.time()   # for the 15 minute return to Operating
        # what the START/STOP LEAK TEST steps are set to, which is a front
        # panel selection rather than anything the console stores
        self.load = 0             # which tanker load the panel is showing
        self.sel = {"scope": "ALL TANKS", "stop_mode": "TIMED DURATION",
                    "rate": "0.2 GPH", "hours": "2",
                    "line_scope": "ALL LINES", "line_rate": "3.0 GPH",
                    "dlv_mode": "EDIT/VIEW",
                    # Reconciliation Mode opens on the shift report
                    # for the current period, which is what the
                    # manual's first screen of each function shows
                    "report_type": "SHIFT", "variance_period": "DAILY",
                    "which": "CURRENT", "adjust_type": "SHIFT"}
        self._blink = False
        # whether STEP has been pressed off the Operating Mode status display
        self._entered = False

    def _build(self):
        self._build_menu()
        outer = tk.Frame(self, bg=PANEL_BG)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        # The notice is packed BEFORE the console face and anchored to the
        # bottom: in Tk whatever expands takes the room that is left, and a
        # notice packed after it gets none and is quietly unmapped.
        self._build_notice(outer)
        self._build_console(outer)
        self._build_bench_window()

    def _build_bench_window(self):
        """The bench, in a window of its own beside the console.

        A console on a wall does not have a control room bolted to its
        base. Stacked under the face, the bench forced one very tall
        window and neither half ever had enough room; as a companion
        window it can be sized, moved to another monitor, or closed
        entirely while practising keypad work. Closing hides it; the
        Bench menu or F2 brings it back.
        """
        win = tk.Toplevel(self)
        self.bench_win = win
        win.title(f"Site Bench  --  {APP_NAME}")
        win.configure(bg=bench.BG)
        win.minsize(560, 380)
        win.protocol("WM_DELETE_WINDOW", self._hide_bench)
        self._build_bench(win)

    def _hide_bench(self):
        self.bench_win.withdraw()
        self.log("-- bench window closed (Bench menu or F2 reopens it)")

    def _show_bench(self, _e=None):
        self.bench_win.deiconify()
        self.bench_win.lift()

    def _place_bench_window(self):
        """First position: under the console if the screen is tall
        enough for both, otherwise beside it."""
        self.update_idletasks()
        x, y = self.winfo_x(), self.winfo_y()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        want_h = 470
        if y + h + 40 + want_h <= sh:
            self.bench_win.geometry(f"{w}x{want_h}+{x}+{y + h + 40}")
        elif x + w + 20 + 640 <= sw:
            self.bench_win.geometry(f"640x{min(h, sh - 80)}+{x + w + 12}+{y}")
        else:
            self.bench_win.geometry(f"{max(w - 80, 560)}x{want_h}"
                                    f"+{x + 40}+{min(y + 60, sh - want_h - 60)}")

    def _build_menu(self):
        """A menu bar, for the things that are not the console.

        The console face has no room for a Help button and should not grow
        one, so the update check and the About box live in the window chrome
        where they belong.

        The startup check is off until someone turns it on. A training tool
        that reaches out to the internet on launch, unasked, at a site whose
        network is somebody's responsibility, is a tool that gets banned.
        """
        bar = tk.Menu(self)

        # ---- Console: the things you do to the box itself ----
        conm = tk.Menu(bar, tearoff=0)
        conm.add_command(label="Reset console...",
                         command=self._reset_console_asked)
        conm.add_separator()
        loadm = tk.Menu(conm, tearoff=0)
        for name in presets.PRESETS:
            loadm.add_command(label=name,
                              command=lambda n=name:
                              self._load_preset_named(n))
        conm.add_cascade(label="Load example site", menu=loadm)
        conm.add_command(label="Rescan programming",
                         command=self._refresh_site)
        bar.add_cascade(label="Console", menu=conm)

        # ---- Bench: the things you do to the bench around it ----
        benm = tk.Menu(bar, tearoff=0)
        benm.add_command(label="Show bench window", accelerator="F2",
                         command=self._show_bench)
        benm.add_separator()
        speedm = tk.Menu(benm, tearoff=0)
        # A 12 hour leak test is not worth sitting through, so the bench
        # can run the console's clock fast. Everything follows it: the
        # status line, the serial timestamps, the test timers, and the
        # product a leaking tank loses.
        self.preset = tk.StringVar(value=list(presets.PRESETS)[0])
        self.speed = tk.StringVar(value="x1 real time")
        self._speeds = {"x1 real time": 1.0, "x60  (a minute an hour)": 60.0,
                        "x600": 600.0, "x3600  (a second an hour)": 3600.0,
                        "x36000": 36000.0}
        for name in self._speeds:
            speedm.add_radiobutton(label=name, variable=self.speed,
                                   value=name,
                                   command=self._set_clock_speed)
        benm.add_cascade(label="Console clock", menu=speedm)
        benm.add_separator()
        self.live_paper = tk.BooleanVar(value=True)
        benm.add_checkbutton(label="Paper comes out of the console",
                             variable=self.live_paper,
                             command=self._set_live_paper)
        self.no_paper = tk.BooleanVar(value=self.console.out_of_paper)
        benm.add_checkbutton(label="Printer out of paper",
                             variable=self.no_paper,
                             command=self._set_paper)
        benm.add_command(label="Tear off the slip", command=self.cut_paper)
        bar.add_cascade(label="Bench", menu=benm)

        # ---- Switches: the physical switches a real console has ----
        swm = tk.Menu(bar, tearoff=0)
        self._sw_breaker = tk.BooleanVar(value=self.console.powered)
        swm.add_checkbutton(label="Main power breaker (AC)",
                            variable=self._sw_breaker,
                            command=self._set_breaker)
        swm.add_separator()
        self._sw_batt_switch = tk.BooleanVar(
            value=self.console.battery_switch)
        swm.add_checkbutton(label="Battery Backup switch (S1)",
                            variable=self._sw_batt_switch,
                            command=self._set_battery_switch)
        self._sw_battery = tk.BooleanVar(value=self.console.battery_present)
        swm.add_checkbutton(label="Battery fitted",
                            variable=self._sw_battery,
                            command=self._set_battery)
        self._sw_cover = tk.BooleanVar(value=not self.console.cover_open)
        swm.add_checkbutton(label="Power area safety cover fitted",
                            variable=self._sw_cover,
                            command=self._set_cover)
        swm.add_separator()
        # the 4-position DIP next to the battery switch (576013-635 p.7):
        # 1 front-panel security, 2 RS-232 security, 3 display power
        self._sw_panel_sec = tk.BooleanVar(value=self.console.panel_security)
        swm.add_checkbutton(label="DIP SW2-1: front panel security",
                            variable=self._sw_panel_sec,
                            command=self._set_panel_security)
        self._rs232_sec = tk.BooleanVar(value=self.console.rs232_security)
        swm.add_checkbutton(label="DIP SW2-2: RS-232 security",
                            variable=self._rs232_sec,
                            command=self._set_rs232_security)
        self._sw_display = tk.BooleanVar(value=self.console.display_blanked)
        swm.add_checkbutton(label="DIP SW2-3: display off",
                            variable=self._sw_display,
                            command=self._set_display_blank)
        bar.add_cascade(label="Switches", menu=swm)

        helpm = tk.Menu(bar, tearoff=0)
        helpm.add_command(label="Check for updates...",
                          command=lambda: updateui.check_for_updates(self))
        self.startup_check = tk.BooleanVar(value=update.check_on_startup())
        helpm.add_checkbutton(
            label="Check for updates at startup",
            variable=self.startup_check,
            command=lambda: update.set_check_on_startup(
                self.startup_check.get()))
        helpm.add_separator()
        helpm.add_command(label=f"About {APP_NAME}",
                          command=lambda: updateui.about(self))
        bar.add_cascade(label="Help", menu=helpm)
        self.config(menu=bar)

        if update.check_on_startup():
            # Late enough that the window is up and the serial port is
            # listening: an update box is not what you want to meet first.
            self.after(2500,
                       lambda: updateui.check_for_updates(self, silent=True))

    def _build_notice(self, parent):
        """What the licence and the trademark owner require, in small type.

        GPL-3.0 section 5(d) asks an interactive program to show that it is
        free software and carries no warranty. The second line is the
        trademark notice. Both sit under the bench rather than on the console
        face, so the face stays a console.
        """
        tk.Label(parent, bg=PANEL_BG, fg="#8a8a86", font=("Segoe UI", 7),
                 justify="left", wraplength=1000, anchor="w",
                 text=f"""{APP_NAME} {__version__}  --  Copyright (C) 2026 {PUBLISHER}.  Free software under the GNU GPL v3, with ABSOLUTELY NO WARRANTY.
{DISCLAIMER}""").pack(side="bottom", fill="x", pady=(6, 0))

    def _build_console(self, parent):
        """The console face.

        Off-white box, a bare brand plate, green display,
        the ALARM / WARNING / POWER column, then the two keypads: white keys with
        black legends on a black grid, with the red ALARM TEST key at the top
        left of the operating pad and two unlabelled positions beside it.
        """
        case = tk.Frame(parent, bg=CASE, highlightthickness=1,
                        highlightbackground=CASE_EDGE)
        case.pack(fill="x")

        # The console is two doors. The left one is the printer, the right one
        # is the panel, and the seam between them runs down the middle of the
        # photograph.
        doors = tk.Frame(case, bg=CASE)
        doors.pack(fill="x")
        bay = tk.Frame(doors, bg=CASE, width=DOOR_W)
        bay.pack(side="left", fill="y")
        bay.pack_propagate(False)
        self._build_printer(bay)
        panel = tk.Frame(doors, bg=CASE)
        panel.pack(side="left", fill="both", expand=True)

        # ---- the brand plate, left bare ----
        # A real console carries its maker's wordmark and a model flash across
        # this strip. Both are trademarks, and neither one teaches anything
        # about operating the console, so this simulator draws neither. The
        # plate keeps its full height, so the face below it is unchanged.
        head = tk.Frame(panel, bg=CASE, height=BRAND_PLATE_H)
        head.pack(fill="x", padx=18, pady=(10, 2))
        head.pack_propagate(False)

        # ---- the display over the indicator column and the two keypads ----
        # In the photograph the display sits ABOVE the keypads and its right
        # edge finishes level with the right edge of the alphanumeric pad,
        # it does not run out past the keys on either side. So the two are
        # stacked in one block, the display anchored to that block's right,
        # and the type is sized afterwards from how wide the keypads came
        # out: 24 characters across the width of the keys, and no wider.
        stack = tk.Frame(panel, bg=CASE)
        stack.pack(padx=18, pady=(2, 4), expand=True)
        row = tk.Frame(stack, bg=CASE)
        row.pack(side="bottom")

        leds = tk.Frame(row, bg=CASE)
        leds.grid(row=0, column=0, padx=(0, 16), sticky="n")
        self.led = {}
        for key, text, colour in (("alarm", "ALARM", "#e01b1b"),
                                  ("warn", "WARNING", "#f2d029"),
                                  ("power", "POWER", "#3fbf55")):
            h = tk.Frame(leds, bg=CASE)
            h.pack(anchor="e", pady=9)
            tk.Label(h, text=text, bg=CASE, fg=LABEL,
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 7))
            cv = tk.Canvas(h, width=17, height=17, bg=CASE, highlightthickness=0)
            oval = cv.create_oval(2, 2, 15, 15, fill=LED_OFF, outline="#3a3a38")
            cv.pack(side="left")
            self.led[key] = (cv, oval, colour)

        NL = chr(10)
        ops = tk.Frame(row, bg=KEYPAD_BG)
        ops.grid(row=0, column=1, padx=(0, 16), sticky="n")
        # exactly as photographed, left to right and top to bottom
        # All twelve positions are keys. The two beside ALARM TEST carry no
        # printed legend on the photographed unit, but they are not blanks,
        # the manual names them: the WHITE Maintenance Report key and the BLUE
        # Maintenance Tracker key. They are keyed here in the manual's colours
        # so they can be found and pressed.
        oplayout = [
            ("ALARM" + NL + "TEST", self.k_alarm),
            ("", self.k_white),                      # Maintenance Report (white)
            ("MODE", self.k_mode),
            ("", self.k_blue),                       # Maintenance Tracker (blue)
            ("BACKUP", self.k_backup), ("FUNC-" + NL + "TION", self.k_function),
            ("PRINT", self.k_print), ("CHANGE", self.k_change), ("STEP", self.k_step),
            ("PAPER" + NL + "FEED", self.k_paper), ("ENTER", self.k_enter),
            ("TANK" + NL + "SENSOR", self.k_tank),
        ]
        for i, (label, cmd) in enumerate(oplayout):
            face, txt = KEY_FACE, KEY_TEXT
            if label.startswith("ALARM"):
                face, txt = KEY_ALARM, "#ffffff"
            elif cmd is self.k_blue:
                face, txt = KEY_MAINT, "#ffffff"
            self._key(ops, label, cmd, i, face, txt, w=7)

        alpha = tk.Frame(row, bg=KEYPAD_BG)
        alpha.grid(row=0, column=2, sticky="n")
        keys = [("QZ." + NL + "1", "1"), ("ABC" + NL + "2", "2"), ("DEF" + NL + "3", "3"),
                ("GHI" + NL + "4", "4"), ("JKL" + NL + "5", "5"), ("MNO" + NL + "6", "6"),
                ("PRS" + NL + "7", "7"), ("TUV" + NL + "8", "8"), ("WXY" + NL + "9", "9"),
                (chr(0x2190) + NL + "+/-", "+"), ("0", "0"), (chr(0x2192) + NL + ".", ",")]
        for i, (label, k) in enumerate(keys):
            self._key(alpha, label, lambda kk=k: self.k_alnum(kk), i,
                      KEY_FACE, KEY_TEXT, w=6)

        # Now the display, measured off reference_console.png. On a right-hand
        # door 546 pixels wide in that photograph the glass runs x 655..926
        # and the two keypads run 700..926: the display's RIGHT edge finishes
        # exactly level with the right edge of the alphanumeric pad, and its
        # left edge stands out past the keys by about a fifth of their width,
        # over the indicator lights. So it is right-aligned to the keys and
        # sized at 1.2 times their width, and the type is whatever fills that.
        wrap = tk.Frame(stack, bg=CASE)
        wrap.pack(side="top", anchor="e", pady=(0, 15))
        bezel = tk.Frame(wrap, bg=VFD_EDGE)
        bezel.pack()
        pad = 3
        # Tk works out what a frame of gridded keys asks for lazily, and an
        # unmeasured keypad reports one pixel, so make it settle first.
        self.update_idletasks()
        room = int((ops.winfo_reqwidth() + alpha.winfo_reqwidth() + 16) * 1.20)
        lcd_font = tkfont.Font(family="Consolas", size=20, weight="bold")
        for size in range(20, 7, -1):
            lcd_font.configure(size=size)
            if lcd_font.measure("0") * COLS + pad * 2 <= room:
                break
        # The glass in the photograph is 271 x 40 for its 24 x 2 characters:
        # a cell 11.3 wide by 20 tall, and a strip nearly seven times wider
        # than it is high. Tk's own line spacing is looser than that, so the
        # rows are set from the character width instead and the bezel is kept
        # thin, which is what makes it read as a two-line LCD rather than as
        # a text box.
        cw = lcd_font.measure("0")
        # (nothing on this display has a descender: it is capitals, digits
        # and punctuation, so the rows can sit as close as the ascent)
        ch = max(int(cw * 1.60), lcd_font.metrics("ascent") + 2)
        self.lcd = tk.Canvas(bezel, width=cw * COLS + pad * 2,
                             height=ch * ROWS + pad * 2, bg=VFD_BG,
                             highlightthickness=0, bd=0)
        self.lcd.pack(padx=2, pady=2)
        self._ghost_ids, self._text_ids = [], []
        for r in range(ROWS):
            y = pad + r * ch
            self._ghost_ids.append(self.lcd.create_text(
                pad, y, anchor="nw", font=lcd_font, fill=VFD_GHOST,
                text=chr(0x2588) * COLS))
            self._text_ids.append(self.lcd.create_text(
                pad, y, anchor="nw", font=lcd_font, fill=VFD_INK, text=""))

        # ---- navy pinstripes across the base ----
        stripes = tk.Canvas(panel, height=52, bg=CASE, highlightthickness=0)
        stripes.pack(fill="x", padx=18, pady=(6, 8))

        def draw_stripes(_e=None):
            stripes.delete("all")
            w = stripes.winfo_width() or 900
            for i in range(9):
                y = 2 + i * 5
                stripes.create_line(0, y, w, y, fill=BLUE, width=1 + (i // 4))

        stripes.bind("<Configure>", draw_stripes)

        self.hint = tk.Label(panel, text="", bg=CASE, fg="#4a4a48",
                             font=("Segoe UI", 8), justify="left", wraplength=820)
        self.hint.pack(anchor="w", padx=18, pady=(0, 8))

    # =====================================================================
    # the printer, behind the left-hand door
    # =====================================================================
    def _build_printer(self, bay):
        """The left door, drawn from the photograph.

        A smoked charcoal cover with a rounded top, curving forward over the
        mechanism, the paper slot along its bottom edge with the tear bar in
        it, and the navy pinstripes carrying on across the base from the right
        door. Everything is drawn in proportion to the canvas, so the door
        keeps its shape whatever the window does.
        """
        self.printer_bay = bay
        self.printer = tk.Canvas(bay, bg=CASE, highlightthickness=0, bd=0)
        self.printer.pack(fill="both", expand=True)
        self.printer.bind("<Configure>", lambda _e: self._draw_printer())
        # the cutout the paper comes out of: left, right, and the lip it
        # hangs from. The paper is exactly as wide as this.
        self._slot = (10, DOOR_W - 10, 0)

    @staticmethod
    def _blend(a, b, t):
        """A colour t of the way from a to b, for shading a curved face."""
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = (int(a[i:i + 2], 16) for i in (1, 3, 5))
        r2, g2, b2 = (int(b[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (int(r1 + (r2 - r1) * t),
                                  int(g1 + (g2 - g1) * t),
                                  int(b1 + (b2 - b1) * t))

    def _shaded(self, cv, x1, y1, x2, y2, r, top, bottom, shadow=None):
        """A shape with a domed top, shaded down its height, drawn row by row.

        A polygon with smoothing on rounds a wide top into an egg, and this
        cover's top is an arc over a straight-sided box: each row is drawn at
        the width the arc gives it and in the colour that row of the curve
        would be, which is what makes it read as one moulded piece rather
        than as a black rectangle.
        """
        span = max(1, y2 - y1)
        for i in range(span):
            y = y1 + i
            inset = 0.0
            if i < r and r:
                inset = r - (r * r - (r - i) ** 2) ** 0.5
            left, right = x1 + inset, x2 - inset
            if right - left < 1:
                continue
            colour = self._blend(top, bottom, i / span)
            if shadow:
                cv.create_line(left + 4, y + 4, right + 4, y + 4, fill=shadow)
            cv.create_line(left, y, right + 1, y, fill=colour)

    @staticmethod
    def _at(stops, t):
        """The number at t down a list of (position, number) stops."""
        last_at, last = stops[0]
        for at, value in stops:
            if t <= at:
                span = at - last_at
                return last + (value - last) * ((t - last_at) / span
                                                if span else 0)
            last_at, last = at, value
        return last

    def _ramp(self, stops, t):
        """The colour at t down a list of (position, colour) stops."""
        last_at, last = stops[0]
        for at, colour in stops:
            if t <= at:
                span = at - last_at
                return self._blend(last, colour,
                                   (t - last_at) / span if span else 0)
            last_at, last = at, colour
        return last

    def _draw_printer(self):
        cv = self.printer
        cv.delete("all")
        w = cv.winfo_width() or DOOR_W
        h = cv.winfo_height() or 320
        if h < 40:
            return
        # the seam between the two doors, down the right-hand edge
        cv.create_line(w - 1, 0, w - 1, h, fill=CASE_EDGE)

        # The pinstripes go on first, because in the photograph they run
        # behind the printer cover and out the other side of it.
        base = h - int(h * 0.17)
        for i in range(9):
            y = base + i * 5
            if y < h - 1:
                cv.create_line(0, y, w, y, fill=BLUE, width=1 + (i // 4))

        # ---- the cover, measured and sampled off reference_console.png ----
        # In the photograph it is a straight-sided smoked door, the corners
        # turned at the top only, standing from 16% to 90% of the door's
        # height. What makes it read as smoked plastic rather than as a black
        # box is entirely the shading: the top lip PROJECTS forward, so it is
        # near black with one bright line of reflection just under its front
        # edge; under the lip the face falls into shadow; and from a third of
        # the way down it lightens, because from there you are looking
        # THROUGH it at the mechanism. The left and right thicknesses of the
        # moulding stay dark all the way down, which is why the lighter part
        # is a panel in the middle and not the whole width.
        x1, x2 = int(w * 0.20), int(w * 0.80)
        y1, y2 = int(h * 0.13), int(h * 0.92)
        span, tall = x2 - x1, y2 - y1
        mid = (x1 + x2) / 2.0

        # The silhouette, read off the photograph: the lip at the top is the
        # widest part, the face below it steps in and stays there, and the
        # base flares back out. Half-widths as a fraction of the lip's.
        shape = [(0.000, 0.70), (0.015, 0.88), (0.045, 0.97), (0.085, 1.00),
                 (0.180, 1.00), (0.225, 0.93), (0.500, 0.92), (0.850, 0.93),
                 (0.915, 1.00), (1.000, 1.00)]
        # ... and the shading down it, sampled off the same photograph: near
        # black at the projecting lip with one bright line of reflection under
        # its front edge, shadow beneath the lip, and from a third of the way
        # down the light of the mechanism coming through the smoke.
        face = [(0.000, "#141417"), (0.032, "#8c8c94"), (0.075, "#42424a"),
                (0.150, "#1a1a1f"), (0.210, "#242429"), (0.420, "#2c2c32"),
                (0.880, "#2f2f35"), (0.940, "#17171b"), (1.000, "#0d0d10")]
        window = "#7a7a83"                  # the mechanism seen through it

        def half(t):
            return self._at(shape, t) * span / 2.0

        # the shadow it throws on the case, up and to the left as photographed
        for i in range(0, tall, 2):
            t = i / tall
            hw = half(t) + 5
            cv.create_line(mid - hw - 7, y1 + i - 4, mid + hw - 7,
                           y1 + i - 4, fill="#dfdfdb")
        for i in range(tall):
            y, t = y1 + i, i / tall
            hw = half(t)
            if hw < 2:
                continue
            shade = self._ramp(face, t)
            cv.create_line(mid - hw, y, mid + hw + 1, y, fill=shade)
            # The panel you can see through. It is a panel and not the whole
            # width because the mouldings down each side stay solid, and the
            # right-hand one is the thicker of the two.
            if 0.20 < t < 0.905:
                lit = self._blend(shade, window, min(1.0, (t - 0.20) * 9.0))
                cv.create_line(mid - hw * 0.72, y, mid + hw * 0.60, y,
                               fill=lit)
                # the moulding down the left edge catches the light
                cv.create_line(mid - hw, y, mid - hw + 2, y,
                               fill=self._blend(shade, "#8a8a92", 0.45))
        # the reflection under the front edge of the lip, and the shut line
        # where the lip ends and the face begins
        cv.create_line(mid - half(0.03) * 0.8, y1 + int(tall * 0.03),
                       mid + half(0.03) * 0.8, y1 + int(tall * 0.03),
                       fill="#9a9aa0")
        cv.create_line(mid - half(0.185), y1 + int(tall * 0.185),
                       mid + half(0.185), y1 + int(tall * 0.185),
                       fill="#101014")
        # the latch on the left edge, which the photograph shows proud
        cv.create_rectangle(mid - half(0.24) - 5, y1 + int(tall * 0.20),
                            mid - half(0.24) + 2, y1 + int(tall * 0.32),
                            fill="#2a2a30", outline="#0d0d10")

        # The paper comes out at the bottom: a recess in the face with the
        # metal tear bar across the front of it.
        wide = half(0.93) * 0.90
        cut1, cut2 = int(mid - wide), int(mid + wide)
        cv.create_rectangle(cut1, y2 - int(tall * 0.115), cut2,
                            y2 - int(tall * 0.020), fill=SLOT,
                            outline="#0a0a0c")
        cv.create_rectangle(cut1 + 2, y2 - int(tall * 0.058), cut2 - 2,
                            y2 - int(tall * 0.032), fill=TEAR_BAR,
                            outline="#6f6f6c")
        self._slot = (cut1, cut2, y2 - int(tall * 0.020))
        self._place_slip()

    def _build_slip(self):
        """The paper itself: built once, and shown when something prints.

        It hangs out of the slot and down over the front of the console, the
        way a roll does when nobody has torn it off yet, so it is placed over
        the window rather than packed into the layout. The CUT button is ON
        the paper by the tear edge, because that is where your hand goes.
        """
        slip = tk.Frame(self, bg=PAPER_EDGE, highlightthickness=0)
        body = tk.Frame(slip, bg=PAPER)
        body.pack(side="top", fill="both", expand=True, padx=1, pady=(1, 0))
        # Forty characters is the width of the roll, and the roll is the
        # width of the cutout it comes out of, so the type is whatever size
        # makes those two the same, see _slip_type.
        self.slip_font = tkfont.Font(family="Consolas", size=8)
        self._slip_row = self.slip_font.metrics("linespace")
        self.slip_text = tk.Text(body, width=PAPER_COLS, height=6, bg=PAPER,
                                 fg=PAPER_INK, font=self.slip_font,
                                 wrap="none", relief="flat", bd=0,
                                 padx=SLIP_PAD, pady=5, cursor="arrow",
                                 highlightthickness=0)
        self.slip_text.configure(yscrollcommand=self._slip_scrolled)
        self.slip_text.pack(fill="both", expand=True)
        # No scrollbar beside it: the paper is the width of the cutout and
        # nothing else may be. A thumb is drawn ON the paper's right margin
        # instead, and the wheel and a drag both wind it.
        self.slip_thumb = tk.Canvas(body, width=5, bg=PAPER, bd=0,
                                    highlightthickness=0)
        for widget in (self.slip_text, self.slip_thumb):
            widget.bind("<MouseWheel>", self._slip_wheel)
        self.slip_thumb.bind("<B1-Motion>", self._slip_drag)
        self.slip_thumb.bind("<Button-1>", self._slip_drag)
        # the torn bottom edge, and the cut button sitting on the paper
        self.slip_tear = tk.Canvas(slip, height=SLIP_TEAR, width=10,
                                   bg=PANEL_BG, highlightthickness=0, bd=0)
        self.slip_tear.pack(side="top", fill="x")
        self.slip_tear.bind("<Configure>", lambda _e: self._draw_tear())
        self.slip_cut = tk.Button(slip, text=chr(0x2702) + " CUT",
                                  command=self.cut_paper, bg=PAPER,
                                  fg="#7a2b2b", bd=1, relief="ridge",
                                  activebackground="#e9e4d4",
                                  activeforeground="#7a2b2b", cursor="hand2",
                                  font=("Segoe UI", 7, "bold"), padx=4, pady=0)
        # clear of the thumb on the right margin, whether it is there or not
        self.slip_cut.place(in_=body, relx=1.0, rely=1.0, anchor="se",
                            x=-9, y=-3)
        return slip

    def _slip_type(self, width):
        """The largest type that puts forty columns across that much paper.

        The roll does not change width when the window does, it is still
        forty characters, so what changes is how big those characters are.
        """
        best = 6
        for size in range(6, 15):
            self.slip_font.configure(size=size)
            if self.slip_font.measure("0") * PAPER_COLS > width - SLIP_PAD * 2:
                break
            best = size
        self.slip_font.configure(size=best)
        self._slip_row = self.slip_font.metrics("linespace")
        return best

    def _slip_wheel(self, event):
        self.slip_text.yview_scroll(-1 * (event.delta // 120), "units")
        return "break"

    def _slip_drag(self, event):
        """Wind the paper by dragging its thumb."""
        height = max(1, self.slip_thumb.winfo_height())
        self.slip_text.yview_moveto(max(0.0, min(1.0, event.y / height)))
        return "break"

    def _slip_scrolled(self, first, last):
        """Show how much paper there is, on the paper's own right margin."""
        first, last = float(first), float(last)
        cv = self.slip_thumb
        cv.delete("all")
        if first <= 0.0 and last >= 1.0:
            cv.place_forget()
            return
        # placed over the text rather than beside it, so the paper stays
        # exactly the width of the cutout
        cv.place(in_=self.slip_text, relx=1.0, rely=0, anchor="ne",
                 x=-2, relheight=1.0)
        h = max(1, cv.winfo_height())
        cv.create_rectangle(1, 1, 4, h - 1, fill=PAPER, outline=PAPER_EDGE)
        cv.create_rectangle(1, int(h * first), 4, max(int(h * last), 6),
                            fill="#b9b2a0", outline="")

    def _draw_tear(self):
        """Very small teeth along the bottom, so it reads as torn paper."""
        cv = self.slip_tear
        cv.delete("all")
        w = cv.winfo_width() or 260
        h = SLIP_TEAR
        teeth, x, up = [0, 0], 0, True
        while x <= w:
            teeth += [x, h - 7 if up else h - 1]
            up = not up
            x += 4
        teeth += [w, 0]
        cv.create_polygon(teeth, fill=PAPER, outline=PAPER_EDGE)

    def _place_slip(self):
        """Hang the paper out of the cutout, at the size the window allows.

        The paper is exactly as wide as the slot it came out of and it always
        carries forty columns, so the type is sized to make both true and
        everything scales with the window together. It hangs long enough to
        clear the bottom of the case, so the torn edge is against the bench
        and never against the console's own face, and no longer than there is
        window for, past that it scrolls.
        """
        if not getattr(self, "slip", None) or not self.slip_out:
            return
        try:
            cut1, cut2, y = self._slot
            width = max(90, cut2 - cut1)
            self._slip_type(width)
            top = self.printer.winfo_rooty() - self.winfo_rooty() + y
            left = self.printer.winfo_rootx() - self.winfo_rootx() + cut1
            trim = SLIP_TEAR + 12            # the torn edge and the borders
            row = self._slip_row
            clear = self.printer.winfo_height() - y + 6      # past the case
            least = max(2, (clear + row - 1) // row)
            most = max(least, (self.winfo_height() - top - trim) // row)
            rows = max(least, min(self._slip_lines, most))
            self.slip.place(x=max(2, left), y=top, width=width,
                            height=rows * row + trim)
            self.slip.lift()
        except tk.TclError:
            pass

    def _fit_door(self):
        """The printer door takes its share of the window, as on the console.

        The left door is nearly half the width of the real box; here it is
        held to a share of the window between what a 40 column roll needs and
        what would start to crowd the panel.
        """
        want = max(300, min(520, int(self.winfo_width() * DOOR_SHARE)))
        # ...and it is taller than it is wide, which on the real console is
        # what gives the printer door its shape. The panel beside it does not
        # need the height, so the door sets it and the panel spreads into it.
        tall = max(int(want / DOOR_RATIO), 430)
        if (want, tall) != (self.printer_bay.winfo_reqwidth(),
                            self.printer_bay.winfo_reqheight()):
            self.printer_bay.configure(width=want, height=tall)

    def _on_resize(self, event):
        """The door and the paper follow the window.

        Both guards are load-bearing. Tk sends <Configure> for changes this
        handler makes itself -- re-sizing the door, re-typing the slip -- so
        without them the handler feeds itself: place the slip, which alters
        what the window asks for, which is another <Configure>, which places
        the slip again, and the event queue never empties.

        It converged by luck for a long time. Adding a menu bar was enough to
        stop it converging, and a user dragging the window edge with paper
        hanging out of the printer would have found the same loop without any
        help from a menu bar. So: ignore a <Configure> that reports a size we
        have already dealt with, and never re-enter.
        """
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_size or self._resizing:
            return
        self._last_size = size
        self._resizing = True
        try:
            self._fit_door()
            self._place_slip()
        finally:
            self._resizing = False

    def cut_paper(self):
        """Tear the slip off. What was on it stays on the roll in the bench."""
        self.slip_out = False
        if getattr(self, "slip", None):
            self.slip.place_forget()
        self.log("-- paper cut")

    def _key(self, parent, label, cmd, i, face, txt, w):
        cmd = self._guard(cmd)
        b = tk.Button(parent, text=label, command=cmd, width=w, height=2,
                      bg=face, fg=txt, activebackground="#33343a",
                      activeforeground=txt, relief="raised", bd=1,
                      highlightbackground=CASE_EDGE,
                      font=("Segoe UI", 7, "bold"))
        b.grid(row=i // 3, column=i % 3, padx=3, pady=3)
        return b

    # ---- the bench, in the same window ----
    def _scrollable(self, parent):
        """A tab body that scrolls, so the options stay reachable on a laptop.

        The console face alone is most of a small screen's height, so the
        panel below it has to scroll or half of it is simply unreachable.
        Returns the inner frame to put content in.
        """
        holder = tk.Frame(parent, bg=bench.BG)
        holder.pack(fill="both", expand=True)
        cv = tk.Canvas(holder, bg=bench.BG, highlightthickness=0, height=200)
        bar = ttk.Scrollbar(holder, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=bench.BG)
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=bar.set)
        cv.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        def resize(_e=None):
            cv.configure(scrollregion=cv.bbox("all"))
            cv.itemconfig(win, width=cv.winfo_width())

        inner.bind("<Configure>", resize)
        cv.bind("<Configure>", resize)

        # No wheel binding here: the app routes every wheel event once, in
        # _on_wheel, to whichever tagged canvas is under the pointer. The
        # bind_all-on-Enter dance this replaces broke the moment the pointer
        # crossed onto a child widget, because that fires <Leave> too and
        # the unbind_all took the whole window's wheel with it.
        cv._wheel = "y"
        return inner

    def _on_wheel(self, ev):
        """One wheel for the whole application.

        Find the widget under the pointer and walk up its parents to the
        first canvas that asked for wheel events. A plain wheel always
        scrolls the PAGE (the vertical canvas), even over the tank strip,
        because that is what a wheel does everywhere else; Shift+wheel
        scrolls the strip sideways, which is the convention everywhere
        sideways scrolling exists.
        """
        try:
            w = self.winfo_containing(ev.x_root, ev.y_root)
        except (KeyError, tk.TclError):
            return
        step = -1 * (ev.delta // 120)
        shift = bool(ev.state & 0x1)
        while w is not None:
            kind = getattr(w, "_wheel", None)
            if kind == "x" and shift:
                w.xview_scroll(step, "units")
                return
            if kind == "y" and not shift:
                w.yview_scroll(step, "units")
                return
            w = w.master

    def _build_bench(self, parent):
        """The bench under the console: one view at a time, big switcher.

        The notebook tab ears were small and dim, and each tab opened on
        a wall of settings that scrolled badly on a laptop. Now a segmented
        switcher picks the view, the explanations live behind info dots,
        and the commands that used to crowd the tabs live in the Console
        and Bench menus above.
        """
        holder = tk.Frame(parent, bg=bench.BG)
        holder.pack(fill="both", expand=True)
        self.tab_site = tk.Frame(holder, bg=bench.BG)
        self.tab_mod = tk.Frame(holder, bg=bench.BG)
        self.tab_log = tk.Frame(holder, bg=bench.BG)
        self.tab_paper = tk.Frame(holder, bg=bench.BG)
        self.tab_net = tk.Frame(holder, bg=bench.BG)
        self._views = {"Site": self.tab_site, "Modules": self.tab_mod,
                       "Network": self.tab_net, "Serial log": self.tab_log,
                       "Printer": self.tab_paper}
        self._switch = bench.Segmented(holder, list(self._views),
                                       self._show_view)
        self._switch.pack(fill="x", anchor="w")
        self._view_holder = tk.Frame(holder, bg=bench.BG)
        self._view_holder.pack(fill="both", expand=True)
        # The alarm strip pins to the bottom of the Site view, OUTSIDE the
        # scroll: what the site is doing to the console is the one line
        # that must never need scrolling to see.
        status = tk.Frame(self.tab_site, bg=bench.CARD)
        status.pack(side="bottom", fill="x", padx=10, pady=(2, 8))
        self.alarm_lbl = tk.Label(status, bg=bench.CARD, fg="#7bd88f",
                                  justify="left", anchor="w",
                                  font=("Consolas", 9), padx=10, pady=6)
        self.alarm_lbl.pack(fill="x")
        self._build_site(self._scrollable(self.tab_site))
        self._build_modules(self._scrollable(self.tab_mod))
        self._build_log(self.tab_log)
        self._build_paper_tab(self.tab_paper)
        self._build_network(self._scrollable(self.tab_net))
        # The site the console already knows about, there at first sight.
        # The old bench opened this view empty and waited for a rescan,
        # which read as broken every time.
        self._refresh_site()
        self._switch.select("Site")

    def _show_view(self, name):
        # pack, not place: the holder then takes its height from the view
        # that is showing, the way the old notebook sized itself. The lift
        # matters: the views were created before the holder they pack into,
        # so without it they sit BELOW it in Tk's z-order and the bench is
        # a black rectangle.
        for v in self._views.values():
            v.pack_forget()
        self._views[name].pack(in_=self._view_holder, fill="both",
                               expand=True)
        self._views[name].lift()

    def _build_log(self, tab):
        """Everything said over the wire, newest at the bottom."""
        head = tk.Frame(tab, bg=bench.BG)
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text="EVERYTHING SAID OVER THE WIRE", bg=bench.BG,
                 fg=bench.MUTED, font=("Segoe UI", 8, "bold")
                 ).pack(side="left")
        tk.Button(head, text="Clear", command=lambda:
                  self.logbox.delete("1.0", "end"),
                  bg=bench.CARD, fg=bench.BODY, relief="flat",
                  font=("Segoe UI", 8), padx=10,
                  activebackground=bench.CARD_HI,
                  activeforeground=bench.INK).pack(side="right")
        self.logbox = tk.Text(tab, height=10, bg="#17191d", fg="#9fd0a0",
                              font=("Consolas", 9), borderwidth=0,
                              padx=10, pady=8)
        self.logbox.pack(fill="both", expand=True, padx=10, pady=8)

    def _build_paper_tab(self, tab):
        """The roll: everything the console has ever printed.

        The two paper switches live in the Bench menu; this view is the
        paper itself, kept looking like paper.
        """
        head = tk.Frame(tab, bg=bench.BG)
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text="THE ROLL", bg=bench.BG, fg=bench.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        bench.info_dot(head,
                       "Everything the console has printed, oldest at the "
                       "top. The slip hanging out of the console is the "
                       "same text on its way here; Tear off moves it. The "
                       "paper switches are in the Bench menu."
                       ).pack(side="left", padx=(5, 0))
        tk.Button(head, text="Tear off", command=self.cut_paper,
                  bg=bench.CARD, fg=bench.BODY, relief="flat",
                  font=("Segoe UI", 8), padx=10,
                  activebackground=bench.CARD_HI,
                  activeforeground=bench.INK).pack(side="right")
        # the paper roll behind the left-hand door
        self.paper = tk.Text(tab, height=10, bg="#f4f1e8",
                             fg="#20211f", font=("Consolas", 9),
                             borderwidth=0, padx=14, pady=10)
        self.paper.pack(fill="both", expand=True, padx=10, pady=8)

    # =====================================================================
    # the network view: the Lantronix XPort in the TCP/IP module
    # =====================================================================
    def _build_network(self, tab):
        """The card that puts the console on a network, and how to reach it.

        A real TLS-350 talks RS-232; the TCP/IP Interface Module is a
        Lantronix XPort wired to that port, and this is the emulated one.
        Off by default, because binding tcp/9999 and udp/30718 is a thing
        to do on purpose, not on launch. Once started, the setup menu and
        DeviceInstaller discovery answer exactly as the card does, and the
        console itself is reachable on the tunnel port.
        """
        self.xport = xport.XPortConfig(paths.xport_config_file())
        self.xport.port = self.port
        self._xport_started = False

        bench.section(
            tab, "TCP/IP Interface Module",
            "A TLS-350 speaks RS-232; this card is the Lantronix XPort that "
            "puts it on Ethernet. Start it and three ports answer as the "
            "real card's do: tcp/9999 is the telnet setup menu, udp/30718 "
            "is Lantronix DeviceInstaller discovery, and the console itself "
            "is on the serial-tunnel port. It is off until you start it, "
            "because opening those ports is a deliberate act.")

        box = bench.card(tab)
        box.pack(fill="x", pady=(0, 8))
        inner = box.inner
        self._net_rows = {}
        for key, label in (("mac", "MAC address"), ("ip", "IP address"),
                           ("port", "Serial tunnel port"),
                           ("setup", "Setup menu"),
                           ("discovery", "DeviceInstaller")):
            row = tk.Frame(inner, bg=bench.CARD)
            row.pack(fill="x", padx=10, pady=3)
            tk.Label(row, text=label, bg=bench.CARD, fg=bench.MUTED,
                     width=18, anchor="w", font=bench.FONT).pack(side="left")
            val = tk.Label(row, bg=bench.CARD, fg=bench.INK, anchor="w",
                           font=bench.MONO)
            val.pack(side="left")
            self._net_rows[key] = val

        bar = tk.Frame(tab, bg=bench.BG)
        bar.pack(fill="x", pady=(4, 8))
        self._net_button = tk.Button(bar, text="Start XPort networking",
                                     command=self._start_xport,
                                     bg="#3c5c3c", fg="#e8f0e8", relief="flat",
                                     font=bench.FONT_HEAD, padx=14, pady=4,
                                     activebackground="#4a6d4a",
                                     activeforeground=bench.INK)
        self._net_button.pack(side="left")
        bench.info_dot(
            bar, "Once started, point PuTTY or telnet at this machine on "
            "port 9999 to walk the setup menu, or run Lantronix "
            "DeviceInstaller to discover the card. The IP set in the menu "
            "is the emulated card's own; it is kept between runs the way a "
            "real card keeps it in Flash.").pack(side="left", padx=(8, 0))

        if self.console.supports("ifsf"):
            bench.section(
                tab, "IFSF",
                "An IFSF console answers the International Forecourt "
                "Standards Forum tank-gauge databases instead of the "
                "standard function codes. Turn it on and the same tank the "
                "console gauges is readable as IFSF data elements. The LON "
                "transport the frames ride is in external IFSF specifications "
                "this simulator does not have, so the databases are the "
                "emulation; the framing is not invented.")
            self._ifsf_on = tk.BooleanVar(
                value=bool(self.console.setting("ifsf_platform", 0, True)))
            rowi = tk.Frame(tab, bg=bench.BG)
            rowi.pack(fill="x", pady=(0, 6))
            tk.Checkbutton(
                rowi, text="  IFSF platform (database support)",
                variable=self._ifsf_on, command=self._set_ifsf,
                bg=bench.BG, fg=bench.BODY, selectcolor=bench.CARD,
                activebackground=bench.BG, activeforeground=bench.INK,
                font=bench.FONT).pack(side="left")

        bench.section(
            tab, "Auto-dial",
            "With a SiteFax/modem card fitted and a receiver programmed in "
            "COMMUNICATION SETUP, an alarm the console was told to report "
            "makes it dial out. Whether anyone ANSWERS is this switch, "
            "because no modem here carries real tones. Ignore enough "
            "retries and AUTODIAL FAILURE posts, exactly as the "
            "troubleshooting guide describes; the next successful call "
            "clears it. What the console would say once connected is not "
            "documented in any manual, so this simulator does not invent "
            "it: the call, the schedule and the alarms are the emulation.")
        bench.section(
            tab, "Remote display",
            "The slot-4 dual-port module's RJ-45 half drives a remote "
            "display at the register. Fault the link and system alarm 08, "
            "Remote Display Comm Error, posts until it is back.")
        self._rdu_ok = tk.BooleanVar(value=not self.console.rdu_fault)
        rowr = tk.Frame(tab, bg=bench.BG)
        rowr.pack(fill="x", pady=(0, 6))
        tk.Checkbutton(
            rowr, text="  remote display communicating",
            variable=self._rdu_ok, command=self._set_rdu_link,
            bg=bench.BG, fg=bench.BODY, selectcolor=bench.CARD,
            activebackground=bench.BG, activeforeground=bench.INK,
            font=bench.FONT).pack(side="left")

        self._dial_answers = tk.BooleanVar(
            value=self.console.autodial.answers)
        row = tk.Frame(tab, bg=bench.BG)
        row.pack(fill="x", pady=(0, 6))
        tk.Checkbutton(
            row, text="  the receiver answers the call",
            variable=self._dial_answers,
            command=self._set_dial_answers,
            bg=bench.BG, fg=bench.BODY, selectcolor=bench.CARD,
            activebackground=bench.BG, activeforeground=bench.INK,
            font=bench.FONT).pack(side="left")

        self._net_hint = tk.Label(tab, bg=bench.BG, fg=bench.MUTED,
                                  justify="left", anchor="w",
                                  font=bench.FONT_SM, wraplength=520)
        self._net_hint.pack(fill="x", padx=2)

        # The RS-232 card's security DIP switch. With it on and a security
        # code programmed (COMMUNICATION SETUP), the console will not answer
        # a serial command that does not carry the code -- silently, as the
        # real card does.
        bench.section(
            tab, "RS-232 card",
            "The serial interface card. Its security DIP switch lives in "
            "the Switches menu with the other physical switches: with it on "
            "AND a security code set in COMMUNICATION SETUP, the console "
            "answers only commands that carry the six-digit code and stays "
            "silent to the rest, exactly as 576013-635 describes. Pull "
            "every comm card on the Modules view and the serial port goes "
            "deaf, because then there is no port. The main breaker and the "
            "battery are in the Switches menu too.")

        self._sync_network()

    def _set_breaker(self):
        """The wall breaker. Off is dark everywhere at once; on is a warm
        boot if the battery held RAM through the outage and a cold boot if
        it did not, exactly as the hardware behaves."""
        if not self._sw_breaker.get():
            self.console.breaker_off()
            held = "battery holding RAM" if self.console.battery_backup()                 else "NO battery backup: RAM is already gone"
            self.log(f"-- MAIN BREAKER OFF ({held})")
        else:
            kind = self.console.breaker_on()
            if kind == "cold":
                self.log("-- MAIN BREAKER ON: cold boot, programming lost")
                self._after_console_change()
                self._boot_sequence()
                return
            self.log("-- MAIN BREAKER ON: warm boot, everything kept")
        self._render()

    def _boot_sequence(self):
        """What a cold-started console shows, 576013-637 pp.12-13:
        CLEARING ALL RAM, SYSTEM COLD START, SYSTEM SELF TEST, SYSTEM
        STARTUP COMPLETE, then *** SYSTEM RESET *** on the printer -- and
        then, on a console with an archive in its E2 chip, the restore
        offer comes up on its own; nobody has to go looking for it.
        """
        screens = ["CLEARING ALL RAM", "SYSTEM COLD START",
                   "SYSTEM SELF TEST", "SYSTEM STARTUP COMPLETE"]
        self.console.booting = True
        self.busy_until = time.time() + len(screens) * 1.2

        def show(i):
            if i < len(screens):
                self.msg = screens[i]
                self._render()
                self.after(1200, lambda: show(i + 1))
                return
            self.msg = ""
            self.busy_until = 0.0
            self.console.booting = False
            self.paper_out(["*** SYSTEM RESET ***"])
            self.log("-- PRINT: *** SYSTEM RESET ***")
            if self.console.archive_exists():
                self.boot_restore = "ask"
                self.log("-- archive found: RESTORE SETUP DATA offered")
            self._render()

        show(0)

    def _set_battery_switch(self):
        self.console.battery_switch = self._sw_batt_switch.get()
        self.console.battery_changed()
        state = "ON" if self.console.battery_switch else "OFF"
        tail = ""
        if not self.console.powered and not self.console.battery_backup():
            tail = " -- with the AC off, RAM is gone NOW"
        self.log(f"-- Battery Backup switch (S1) {state}{tail}")

    def _set_battery(self):
        self.console.battery_present = self._sw_battery.get()
        self.console.battery_changed()
        state = "fitted" if self.console.battery_present else "REMOVED"
        tail = ""
        if not self.console.powered and not self.console.battery_backup():
            tail = " -- with the AC off, RAM is gone NOW"
        self.log(f"-- battery {state}{tail}")

    def _set_panel_security(self):
        self.console.panel_security = self._sw_panel_sec.get()
        state = "ON" if self.console.panel_security else "OFF"
        self.log(f"-- DIP SW2-1 front panel security {state}")

    def _set_display_blank(self):
        self.console.display_blanked = self._sw_display.get()
        state = "closed (display off)" if self.console.display_blanked             else "open (display on)"
        self.log(f"-- DIP SW2-3 {state}")
        self._render()

    def _set_cover(self):
        self.console.cover_open = not self._sw_cover.get()
        state = ("REMOVED (Protective Cover Alarm posts)"
                 if self.console.cover_open else "fitted")
        self.log(f"-- power area cover {state}")

    def _set_rs232_security(self):
        self.console.rs232_security = self._rs232_sec.get()
        code = self.console.security_code()
        state = "enabled" if self.console.rs232_security else "disabled"
        note = "" if code else " (no code set, so no effect yet)"
        self.log(f"-- RS-232 security DIP {state}{note}")

    def _set_dim_link(self):
        self.console.dim_fault = not self._dim_ok.get()
        state = ("DOWN (DIM Communication Alarm posts, meter data stops)"
                 if self.console.dim_fault else "up")
        self.log(f"-- DIM link {state}")

    def _set_rdu_link(self):
        self.console.rdu_fault = not self._rdu_ok.get()
        state = ("faulted (Remote Display Comm Error posts)"
                 if self.console.rdu_fault else "communicating")
        self.log(f"-- remote display {state}")

    def _set_ifsf(self):
        self.console.set_setting("ifsf_platform", 1 if self._ifsf_on.get()
                                 else 0, 0)
        state = "on (database support)" if self._ifsf_on.get() else "off"
        self.log(f"-- IFSF platform {state}")

    def _set_dial_answers(self):
        self.console.autodial.answers = self._dial_answers.get()
        state = "answers" if self.console.autodial.answers else             "does NOT answer (retries, then AUTODIAL FAILURE)"
        self.log(f"-- auto-dial receiver {state}")

    def _start_xport(self):
        if self._xport_started:
            return
        import threading
        self.xport.port = self.port
        threading.Thread(target=xport.serve, args=(self.xport, "0.0.0.0",
                                                   self.log),
                         kwargs={"powered":
                                 lambda: self.console.powered},
                         daemon=True).start()
        self._xport_started = True
        self.log("-- XPort networking started")
        self._sync_network()

    def _sync_network(self):
        rows, c = self._net_rows, self.xport
        rows["mac"].config(text=xport.mac_hex(c.mac, "-"))
        rows["ip"].config(text=(c.ip if c.assigned()
                                else f"0.0.0.0  (not set; reachable via "
                                f"AutoIP {c.autoip()})"))
        rows["port"].config(text=str(self.port))
        on = self._xport_started
        rows["setup"].config(
            text="tcp/9999  -- listening" if on else "tcp/9999  -- stopped",
            fg=bench.OK if on else bench.MUTED)
        rows["discovery"].config(
            text="udp/30718  -- listening" if on else "udp/30718  -- stopped",
            fg=bench.OK if on else bench.MUTED)
        if on:
            self._net_button.config(text="XPort networking is running",
                                    state="disabled", bg=bench.CARD_EDGE)
            self._net_hint.config(
                text="Walk the setup menu:  telnet <this machine> 9999  "
                "(press Enter within 5 seconds).  Discover the card with "
                "Lantronix DeviceInstaller.  The console answers on the "
                f"tunnel port {self.port}.")
        else:
            self._net_hint.config(
                text="Stopped. Nothing is bound. Starting it opens tcp/9999 "
                "and udp/30718 on every interface, so your firewall may "
                "ask; that is expected.")

    def _build_modules(self, tab):
        """The card cage: what is fitted, and the board underneath it."""
        bench.section(
            tab, "The card cage",
            "A TLS-350 is a card cage in three compartments, and it only "
            "offers the functions its fitted cards can serve: pull the "
            "sensor card and Sensor Setup leaves FUNCTION, and the tool "
            "gets 9999 over the wire. Fit more than one of a card and it "
            "carries more devices; the bay runs out of slots before the "
            "console runs out of appetite. Under all of it is the CPU "
            "board and the software on it, which is the whole difference "
            "between a 350, a PLUS and an R: a console cannot drive a "
            "card its program has never heard of. Loading an example "
            "site (Console menu) brings its own board with it.")
        self._build_version(tab)
        self.module_vars = {}
        self.module_rows = {}
        self.bay_labels = {}
        for bay in ("is", "power", "comm", "sw"):
            box = tk.LabelFrame(tab, bg=bench.BG, fg=bench.INK,
                                font=("Segoe UI", 8, "bold"),
                                text=BAY_NAME[bay])
            box.pack(anchor="w", fill="x", padx=8, pady=(4, 2))
            head = tk.Label(box, bg=bench.BG, fg=bench.MUTED,
                            font=("Consolas", 8), anchor="w")
            head.pack(anchor="w", padx=6)
            self.bay_labels[bay] = head
            for key, name, part, mbay, wires, most in MODULES:
                if mbay != bay:
                    continue
                self._module_row(box, key, name, part, wires, most)
        box = tk.LabelFrame(tab, bg=bench.BG, fg=bench.INK,
                            font=("Segoe UI", 8, "bold"),
                            text="Software Modules (the S-Module's keys)")
        box.pack(anchor="w", fill="x", padx=8, pady=(4, 6))
        self.software_vars = {}
        self.software_rows = {}
        for key, name, part in SOFTWARE_MODULES:
            row = tk.Frame(box, bg=bench.BG)
            row.pack(anchor="w", fill="x", padx=6, pady=1)
            var = tk.BooleanVar(value=self.console.licensed(key))
            self.software_vars[key] = var
            tick = tk.Checkbutton(row, variable=var, bg=bench.BG, fg="#e2e5de",
                                  selectcolor="#22242a",
                                  activebackground=bench.BG,
                                  command=lambda k=key: self._set_software(k))
            tick.pack(side="left")
            label = tk.Label(row, text=f" {name}", bg=bench.BG, fg="#e2e5de",
                             width=38, anchor="w", font=("Segoe UI", 8))
            label.pack(side="left")
            tk.Label(row, text=part or "--", bg=bench.BG, fg="#8f948b",
                     width=12, anchor="w",
                     font=("Consolas", 8)).pack(side="left")
            note = tk.Label(row, text="", bg=bench.BG, fg="#8f948b", width=22,
                            anchor="w", font=("Segoe UI", 8))
            note.pack(side="left")
            self.software_rows[key] = (tick, label, note)
        self._sync_bays()
        self._sync_version()

    def _build_version(self, tab):
        """The CPU board and the software on it, which is the gate under the
        card cage.

        A TLS-350, a PLUS and an R are the same box: these two controls are
        the difference between them. Between them they can take a whole
        chapter off FUNCTION, take the cards that chapter drives with it, and
        decide whether there is a fourth mode at all.
        """
        box = tk.LabelFrame(tab, bg=bench.BG, fg=bench.INK,
                            font=("Segoe UI", 8, "bold"),
                            text="CPU board and software (what makes it a "
                                 "350, a PLUS or an R)")
        box.pack(anchor="w", fill="x", padx=8, pady=(4, 2))
        row = tk.Frame(box, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=(2, 0))

        tk.Label(row, text="version", bg=bench.BG, fg="#b9bdb4",
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 4))
        self.version_var = tk.StringVar(value=self._version_label())
        picker = ttk.Combobox(row, textvariable=self.version_var, width=13,
                              state="readonly",
                              values=[self._version_label(n)
                                      for n in versions.NUMBERS])
        picker.pack(side="left")
        picker.bind("<<ComboboxSelected>>", lambda _e: self._set_version())

        tk.Label(row, text="   board", bg=bench.BG, fg="#b9bdb4",
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 4))
        self.board_var = tk.StringVar(value=self._board_label())
        boards = ttk.Combobox(row, textvariable=self.board_var, width=30,
                              state="readonly",
                              values=[self._board_label(c)
                                      for c in versions.BOARD_CODES])
        boards.pack(side="left")
        boards.bind("<<ComboboxSelected>>", lambda _e: self._set_board())

        self.version_head = tk.Label(box, bg=bench.BG, fg=bench.MUTED,
                                     anchor="w", font=("Consolas", 8))
        self.version_head.pack(anchor="w", fill="x", padx=6, pady=(3, 0))
        self.version_note = tk.Label(box, bg=bench.BG, fg="#b9bdb4",
                                     justify="left", wraplength=740,
                                     anchor="w", font=("Segoe UI", 8))
        self.version_note.pack(anchor="w", fill="x", padx=6, pady=(0, 4))

    def _version_label(self, n=None):
        n = self.console.version if n is None else n
        return f"V{n}  ({versions.RELEASED.get(n, '')})"

    def _board_label(self, code=None):
        code = self.console.board if code is None else code
        return f"{code}  {versions.board_name(code)}"

    def _set_version(self):
        """Change the software, and let everything it never knew fall away."""
        try:
            wanted = int(self.version_var.get().split()[0].lstrip("V"))
        except ValueError:
            wanted = None
        if wanted is None or not self.console.set_version(wanted):
            self.version_var.set(self._version_label())
            return
        self._after_console_change()
        info = self.console.software_info()
        self.log(f"-- software V{self.console.version} ({info['number']})")
        self._flash(f"VERSION {info['version']}"[:COLS] + chr(10)
                    + f"SOFTWARE# {info['number']}"[:COLS])

    def _set_board(self):
        """Change the CPU board, which is the other half of the same question."""
        wanted = self.board_var.get().split()[0]
        if not self.console.set_board(wanted):
            self.board_var.set(self._board_label())
            return
        self._after_console_change()
        self.log(f"-- CPU board {wanted} ({versions.board_name(wanted)})")
        self._flash(versions.board_name(wanted)[:COLS] + chr(10)
                    + f"SOFTWARE# {self.console.software_info()['number']}"[:COLS])

    def _sync_version(self):
        """Say what this console is, and grey what it cannot drive."""
        info = self.console.software_info()
        # the name on the box follows the board and the software, so the
        # window says which console this one is at the moment
        self.title(f"{APP_NAME}  --  VERSION {info['version']}"
                   f"  --  serial on 127.0.0.1:{self.port}")
        self.version_var.set(self._version_label())
        self.board_var.set(self._board_label())
        self.version_head.config(
            text=f"  VERSION {info['version']}   SOFTWARE# {info['number']}"
                 f"   CREATED {info['created']}   S-MODULE {info['smodule']}")
        missing = [versions.FEATURES[f] for f in versions.FEATURE_ROW
                   if not self.console.supports(f)]
        note = (f"Released {info['released']}, on an "
                f"{versions.board_name(self.console.board)}. "
                f"{self.console.family()} software, so "
                + ("Reconciliation Mode is on this console"
                   if self.console.family() == "3XX"
                   else "there is no Reconciliation Mode") + ".")
        if missing:
            note += "  Not on it: " + ", ".join(sorted(missing)) + "."
        self.version_note.config(text=note)
        for key, (spin, label, note_label, wires) in self.module_rows.items():
            known = self.console.knows_module(key)
            spin.config(state="normal" if known else "disabled",
                        to=self.console.most(key))
            label.config(fg="#e2e5de" if known else "#6f746c")
            note_label.config(
                text=wires if known else
                f"not until V{versions.arrived_in(versions.MODULE_FEATURE[key])}")
        for key, (tick, label, note_label) in self.software_rows.items():
            known = self.console.knows_option(key)
            tick.config(state="normal" if known else "disabled")
            label.config(fg="#e2e5de" if known else "#6f746c")
            note_label.config(
                text="" if known else
                f"not until V{versions.arrived_in(versions.SOFTWARE_FEATURE[key])}")

    def _set_clock_speed(self):
        self.console.clock_speed = self._speeds.get(self.speed.get(), 1.0)
        self.log(f"-- console clock {self.speed.get()}")

    def _reset_console_asked(self):
        """Reset is the one menu item that destroys work, so it asks."""
        from tkinter import messagebox
        if messagebox.askokcancel(
                "Reset console",
                "Everything out of the cage, all programming gone: a "
                "console out of its box.\n\nThis cannot be undone.",
                parent=self):
            self._reset_console()

    def _load_preset_named(self, name):
        self.preset.set(name)
        self._load_preset()

    def _reset_console(self):
        """Everything out, everything blank, a console out of its box."""
        self.console.reset(keep_clock=True)
        self._after_console_change()
        self.log("-- console reset")
        self._flash("CONSOLE RESET" + chr(10) + "NOTHING PROGRAMMED")

    def _load_preset(self):
        """Drop a whole example site in, cards and programming and fuel."""
        name = self.preset.get()
        if not presets.load(self.console, name):
            self._flash("NO SUCH PRESET")
            return
        self._after_console_change()
        self.log(f"-- loaded preset: {name}")
        self._flash("SITE LOADED" + chr(10) + name.upper()[:COLS])

    def _after_console_change(self):
        """Put the panel and the bench back in step with the console."""
        self.func, self.step, self.device = 0, HEADER, 1
        self.mode = MODES.index("NORMAL")
        self._entered = False
        self.editing, self.buf, self.confirm = False, "", None
        self.chart_open = self.locked = False
        self.dlv = 0
        for key, var in self.module_vars.items():
            var.set(str(self.console.fitted(key)))
        for key, var in self.software_vars.items():
            var.set(self.console.licensed(key))
        self._sync_bays()
        self._sync_version()
        self._refresh_site()

    def _set_software(self, key):
        """A feature the S-Module does not carry is not on the menu."""
        if not self.console.knows_option(key):
            self.software_vars[key].set(False)
            self._flash("NOT IN THIS CONSOLE" + chr(10)
                        + f"VERSION {self.console.software_info()['version']}"[:COLS])
            return
        self.console.software[key] = bool(self.software_vars[key].get())
        self.console.save()
        self.func, self.step, self.device = 0, HEADER, 1
        self._refresh_site()
        state = "ENABLED" if self.console.licensed(key) else "NOT INSTALLED"
        self._flash(SOFTWARE_NAME[key][:COLS] + chr(10) + state)

    def _module_row(self, parent, key, name, part, wires, most):
        row = tk.Frame(parent, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=1)
        var = tk.StringVar(value=str(self.console.count(key)))
        self.module_vars[key] = var
        spin = tk.Spinbox(row, from_=0, to=most, width=2, textvariable=var,
                          bg="#2a2d31", fg="#e8eae4", font=("Consolas", 8),
                          buttonbackground="#4a4d53", justify="center",
                          command=lambda k=key: self._set_module(k))
        spin.pack(side="left")
        spin.bind("<KeyRelease>", lambda _e, k=key: self._set_module(k))
        label = tk.Label(row, text=f" {name}", bg=bench.BG, fg="#e2e5de",
                         width=38, anchor="w", font=("Segoe UI", 8))
        label.pack(side="left")
        tk.Label(row, text=part or "--", bg=bench.BG, fg="#8f948b", width=12,
                 anchor="w", font=("Consolas", 8)).pack(side="left")
        note = tk.Label(row, text=(f"{wires} per card" if wires else ""),
                        bg=bench.BG, fg="#8f948b", width=22, anchor="w",
                        font=("Segoe UI", 8))
        note.pack(side="left")
        self.module_rows[key] = (spin, label, note,
                                 f"{wires} per card" if wires else "")

    def _set_module(self, key):
        """Fit or pull cards, within what the bay and the card allow."""
        try:
            want = int(self.module_vars[key].get() or 0)
        except ValueError:
            return
        if want and not self.console.knows_module(key):
            self.module_vars[key].set(str(self.console.fitted(key)))
            self._flash("NOT IN THIS CONSOLE" + chr(10)
                        + f"VERSION {self.console.software_info()['version']}"[:COLS])
            return
        if not self.console.set_module(key, want):
            self.module_vars[key].set(str(self.console.count(key)))
            bay = BAY_NAME[MODULE_BAY[key]]
            self._flash("WILL NOT FIT" + chr(10) + bay[:COLS])
            self._sync_bays()
            return
        self.func = 0
        self.step = HEADER
        self.device = 1
        self._sync_bays()
        self._refresh_site()
        n = self.console.fitted(key)
        self._flash(f"{n} x {MODULE_PART.get(key) or 'MODULE'}"[:COLS]
                    + chr(10) + MODULE_LABEL[key][:COLS])

    def _sync_bays(self):
        for bay, label in self.bay_labels.items():
            used, slots = self.console.bay_used(bay), BAY_SLOTS[bay]
            label.config(text=f"  {used} of {slots} slots used")

    def _build_site(self, tab):
        """The site: tanks with their probes, then everything wired in."""
        self.site_body = tk.Frame(tab, bg=bench.BG)
        self.site_body.pack(fill="both", expand=True, padx=12, pady=(0, 4))

    def _refresh_site(self):
        for w in self.site_body.winfo_children():
            w.destroy()
        self._site_sync = []
        c = self.console
        body = self.site_body

        # ---- the tanks, side by side, each with its probe ----
        tanks = c.programmed_tanks()
        bench.section(
            body, f"Tanks ({len(tanks)})",
            "Each tank is the buried cylinder seen end-on, with the probe "
            "down its riser. Drag the pale float and you set how much fuel "
            "is in the tank; drag the blue float and you set the water "
            "under it. The water float cannot pass the product float, "
            "because on the hardware it rides the bottom of the fuel. "
            "Alarms are DERIVED from these levels against the limits you "
            "programmed on the console: drag a tank below its low-product "
            "limit and the alarm appears on the display, on the LED, and "
            "over the wire.")
        if not c.has("probe"):
            tk.Label(body, text="No probe module fitted -- fit one on the "
                     "Modules view.", bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        elif not tanks:
            tk.Label(body, text="None configured yet: switch a position on "
                     "at TANK CONFIG in IN-TANK SETUP, or load an example "
                     "site from the Console menu.", bg=bench.BG,
                     fg=bench.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        else:
            strip = bench.HStrip(body, height=336)
            strip.pack(fill="x")
            for n, (label, full) in tanks.items():
                card = bench.TankCard(strip.body, self, n, label, full)
                card.pack(side="left", padx=(0, 8), pady=(2, 2))
                self._site_sync.append(card.sync)

        # ---- the sensors, as tiles that wrap to the window ----
        sensors = c.programmed_sensors()
        bench.section(
            body, f"Sensors ({len(sensors)})",
            "Every sensor the console has been told about. The state list "
            "offers only what that sensor's own type can report: a "
            "single-float sump sensor has FUEL and OUT, and no amount of "
            "water in the sump will make it say WATER.")
        if not sensors:
            tk.Label(body, text="No sensor module fitted."
                     if not any(c.has(k) for k in c.SENSOR_CODES)
                     else "None configured yet: switch a position on at "
                     "SENSOR CONFIG.", bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        else:
            grid = bench.FlowGrid(body, bench.SensorTile.W)
            grid.pack(fill="x")
            for mod, num, label in sensors:
                grid.add(bench.SensorTile(grid, self, mod, num, label))

        # ---- the dispensers, which BIR reconciles the probe against ----
        bench.section(
            body, "Dispensers",
            "Assign a meter to a tank and give it a flow, and the tank "
            "goes down, the meter total goes up, and the shift "
            "reconciliation has something to reconcile. The metered "
            "transactions reach the console through a Dispenser Interface "
            "Module; pull the DIM, or fault its link below, and the fuel "
            "still flows at the site but this console cannot see it.")
        if not c.licensed("bir"):
            tk.Label(body, text="BIR is not installed, so the console has "
                     "no meter data.", bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        elif not (c.has("edim") or c.has("mdim")):
            tk.Label(body, text="No DIM fitted, so no meter data reaches "
                     "the console. Fit an EDIM or MDIM on the Modules view.",
                     bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        else:
            self._dim_ok = tk.BooleanVar(value=not c.dim_fault)
            row = tk.Frame(body, bg=bench.BG)
            row.pack(fill="x", pady=(0, 4))
            tk.Checkbutton(
                row, text="  DIM link to the POS/dispensers up",
                variable=self._dim_ok, command=self._set_dim_link,
                bg=bench.BG, fg=bench.BODY, selectcolor=bench.CARD,
                activebackground=bench.BG, activeforeground=bench.INK,
                font=bench.FONT).pack(side="left")
            grid = bench.FlowGrid(body, bench.MeterCard.W)
            grid.pack(fill="x")
            for meter in range(1, 7):
                card = bench.MeterCard(grid, self, meter)
                grid.add(card)
                self._site_sync.append(card.sync)

        # ---- the ISD monitoring tests, on a vapor recovery site ----
        if c.licensed("isd") and c.has("smart"):
            bench.section(
                body, "Vapor recovery (ISD)",
                "The ISD monitoring tests. A simulator measures no vapour, "
                "so the bench sets a test's outcome the way it sets a "
                "sensor's state, and the console does the rest: the alarm "
                "posts, a FAIL shuts dispensing down, the reports carry it. "
                "From a shutdown alarm, ALARM/TEST three times on the panel "
                "reaches the override, exactly as 577013-800 walks it.")
            grid = bench.FlowGrid(body, bench.SensorTile.W)
            grid.pack(fill="x")
            ISD_TESTS = [("leakage", "VAPOR LEAKAGE"),
                         ("gross", "GROSS PRESSURE"),
                         ("degrade", "DEGRD PRESSURE"),
                         ("collect_gross", "GROSS COLLECT"),
                         ("collect_degrade", "DEGRD COLLECT"),
                         ("sensor", "SENSOR OUT"),
                         ("setup", "ISD SETUP")]
            for key, label in ISD_TESTS:
                grid.add(bench.IsdTile(grid, self, key, label))

        # ---- the lines the console is watching ----
        lines = c.programmed_lines()
        bench.section(
            body, f"Lines ({len(lines)})",
            "Only the lines the console has been told about. A card in the "
            "cage is wires; a LINE is a position switched on at LINE "
            "CONFIG, and putting a leak on one the console is not watching "
            "proves nothing.")
        if not lines:
            fitted = any(c.has(k) for k in ("plld", "wplld", "vlld"))
            tk.Label(body, text=("None configured yet: switch a position "
                                 "on at LINE CONFIG." if fitted
                                 else "No line leak module fitted."),
                     bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        else:
            grid = bench.FlowGrid(body, bench.LineCard.W)
            grid.pack(fill="x")
            for kind, n, label in lines:
                card = bench.LineCard(grid, self, kind, n, label)
                grid.add(card)
                self._site_sync.append(card.sync)

    def _sensor_type_note(self, mod, num):
        """What the bench says the sensor is, so the short list makes sense."""
        from .console import FIELDS
        code = self.console.SENSOR_TYPE_CODE.get(mod)
        if not code:
            return ""
        value = self.console.sensor_type(mod, num) or "1"
        for choice in (FIELDS.get(f"S{code}01", {}).get("choices") or []):
            if str(choice[0]) == value:
                return f"  {choice[1].lower()}"
        return ""

    # =====================================================================
    # the menu the panel walks
    # =====================================================================
    def functions(self):
        m = MODES[self.mode]
        if m == "DIAGNOSTIC":
            return [{"function": f["function"],
                     "steps": [{"text": sc["l1"], "l2": sc.get("l2", ""),
                                "live": sc.get("live"), "code": None,
                                "diagprint": sc.get("diagprint"),
                                "act": sc.get("act"),
                                "depth": sc.get("depth", 0),
                                "vp": sc.get("vp"),
                                "expand": sc.get("expand")}
                               for sc in f["screens"]]}
                    for f in self.console.available_diagnostics()]
        if m in ("NORMAL", "RECONCILIATION"):
            menu = (self.console.available_operating() if m == "NORMAL"
                    else self.console.available_reconciliation())
            return [{"function": f["function"], "print": f.get("print"),
                     "scope": f.get("scope"),
                     "steps": [dict(st, code=None) for st in f["steps"]]}
                    for f in menu]
        return self.console.available_functions()

    def cur_function(self):
        fns = self.functions()
        return fns[self.func % len(fns)] if fns else None

    def steps(self):
        """The steps this console is showing, which is not all of them.

        A screen the manual gates on a setting, END VALUE only when the end
        factor is OTHER, STUCK DELAY only when a relay is assigned, is not
        there to step onto until the setting says so.
        """
        fn = self.cur_function()
        if fn is None:
            return []
        if MODES[self.mode] == "DIAGNOSTIC":
            return self._diag_screens(fn)
        steps = self.console.visible_steps(fn, self.device)
        return [st for st in steps if self._selection_allows(st)]

    def _selection_allows(self, step):
        """A screen a SELECTION gates rather than a setting.

        THRESHOLD is on the Periodic Reconciliation Report and not on the
        shift one, and which of those you are looking at is a choice made on
        the panel, not something programmed.
        """
        want = (step.get("when") or {}).get("sel")
        if not want:
            return True
        return str(self.sel.get(want, "")) in (step["when"].get("is") or [])

    def _diag_screens(self, fn):
        """The diagnostic screens on offer, which depends on where you are.

        The figures put a screen you reach with ENTER in a column of its own,
        under the screen that says PRESS <ENTER>. So those are not steps of
        the function: they are a level down, and BACKUP is the way out.
        """
        # some diagnostic screens belong to one vapor processor only:
        # the polisher has LOAD/EFFLUENT/VALVE/TEMP, the membrane has VP
        # STATE/HC SENSOR. A screen's "vp" says which processor it is for.
        vp = (self.console.values.get("SV4000") or "00")
        polisher = vp in ("05", "06")
        def offered(sc):
            want = sc.get("vp")
            if not want:
                return True
            return (want == "polisher") == polisher
        screens = [sc for sc in fn["steps"] if offered(sc)]
        if self.sub is None:
            return [sc for sc in screens if not sc.get("depth")]
        out = [screens[self.sub]]
        for sc in screens[self.sub + 1:]:
            if not sc.get("depth"):
                break
            if sc.get("expand") == "slots":
                # one screen per slot in the cage, which is what the console
                # has to show rather than one example slot
                out += [{"text": l1, "l2": l2, "code": None, "depth": 1}
                        for l1, l2 in self.console.slot_report()]
                continue
            out.append(sc)
        return out

    def _diag_children(self):
        """The full-list index of the screen the panel is on, if it has any."""
        fn = self.cur_function()
        if fn is None or MODES[self.mode] != "DIAGNOSTIC" or self.sub is not None:
            return None
        screens = fn["steps"]
        tops = [i for i, sc in enumerate(screens) if not sc.get("depth")]
        if not tops or self.step == HEADER:
            return None
        i = tops[self.step % len(tops)]
        if i + 1 < len(screens) and screens[i + 1].get("depth"):
            return i
        return None

    def cur_step(self):
        st = self.steps()
        if not st or self.step < 0:
            return None
        return st[self.step % len(st)]

    def cur_code(self):
        """The function this step writes, which the profile can decide.

        FULL VOL is one function on a one-point tank, another on a four-point
        one and another again on a linear one, because the profile IS which
        function holds the tank's volumes.
        """
        e = self.cur_step()
        if not e or not e.get("code"):
            return None
        c = self._profile_code(e) or e["code"]
        if c[4:6] == "00":
            return c
        if self._console_step(e) and not e.get("repeat"):
            # a console-wide screen that carries its own number, AUTO SHIFT
            # #2 CLOSING is S79402 wherever the panel is pointed
            return c
        return f"{c[:4]}{self.device:02d}"

    def _profile_code(self, step):
        return screens.profile_code(self.console, step, self.device)

    # ---- delivery maintenance ----------------------------------------------
    def _delivery(self):
        """The delivery the function is looking at, newest first."""
        records = self.console.deliveries.records.get(self.device) or []
        return records[self.dlv % len(records)] if records else None

    def _delivery_lines(self, step, text):
        """"T 1: MMM DD, YYYY HH:MM / TICKET VOLUME: XXX"."""
        what = step["dlv"]
        if what in ("date", "time", "insert", "insertbol"):
            held = self._insert.get(what, "")
            if self.editing:
                return [text[:COLS], self._edit_text()[:COLS]]
            if what == "date":
                return [text[:COLS], "DATE: " + (held or time.strftime(
                    "%m/%d/%Y", self.console.now()))]
            if what == "time":
                return [text[:COLS], "TIME: " + (held or time.strftime(
                    "%I:%M %p", self.console.now()))]
            return [text[:COLS], f"TICKET VOLUME: {held or 0}"[:COLS]]
        record = self._delivery()
        if record is None:
            return [f"T {self.device}: NO DELIVERIES"[:COLS], text[:COLS]]
        when = clock_words(record.end["at"])
        head = f"T {self.device}: {when}"
        if what == "prior":
            return [head[:COLS], "PRESS <ENTER> FOR PRIOR"]
        if self.editing:
            return [head[:COLS], f"{text}: {self._edit_text()}"[:COLS]]
        if what == "bol":
            return [head[:COLS], f"BOL: {record.bol}"[:COLS]]
        ticket = "" if record.ticket is None else f"{record.ticket:.0f}"
        return [head[:COLS], f"TICKET VOLUME: {ticket}"[:COLS]]

    def _choice(self, step):
        """What a selection screen is showing.

        A selection shared between functions can hold a value this one does
        not offer, the line rate is 3.0 GPH on a PLLD and 0.20 GAL/HR on a
        VLLD, and a console only ever shows a choice it has.
        """
        names = [c[0] if isinstance(c, list) else c
                 for c in (step.get("choices") or [])]
        value = str(self.sel.get(step["sel"], ""))
        if names and value not in names:
            value = names[0]
            self.sel[step["sel"]] = value
        return value

    def _scope_word(self, kind):
        """"ALL TANKS", or the one device the panel is pointed at."""
        word = "TANK" if kind == "tank" else "LINE"
        scope = str(self.sel.get("scope" if kind == "tank" else "line_scope",
                                 ""))
        if scope.startswith("SINGLE"):
            return f"{word} {self.device}"
        return f"ALL {word}S"

    def _named_head(self, head):
        """A head the screen names for itself, with this console in it.

        `%d` is the device the panel is on, and `(PRODUCT LABEL)` is what
        that device was programmed with -- the manuals draw both, and a
        console draws neither.
        """
        head = head.replace("%d", str(self.device))
        if "(" in head:
            letter = self._device_code()
            code = {"T": "602", "L": "702", "V": "707", "G": "712",
                    "C": "742", "H": "747", "s": "722", "Q": "782",
                    "W": "7A2", "P": "760", "R": "807", "I": "802",
                    "r": "7C5"}.get(letter, "602")
            label = (self.console.text(code, self.device)
                     or f"{DEVICE_WORD.get(letter, 'DEVICE')} {self.device}")
            for placeholder in ("(PRODUCT LABEL)", "(Product Label)",
                                "(LABEL)", "(Label)"):
                head = head.replace(placeholder, label)
            # 577013-800 Rev P p.1059 draws the air flow meter's screen as
            # "LABEL: (AFM label)": the parenthetical is the label the site
            # programmes, and a console shows it rather than the word.
            for placeholder, which in (("(AFM label)", "evr_afm_label"),
                                       ("(PS label)", "evr_ps_label")):
                if placeholder in head:
                    head = head.replace(
                        placeholder,
                        self.console.setting(which, 0, "")).rstrip()
        return head

    def _scope_head(self, step, text):
        """"TEST CONTROL: ALL TANKS": the screens that name what they will
        run the test on."""
        prefix = step.get("head_scope")
        if not prefix:
            return text
        fn = self.cur_function() or {}
        return f"{prefix}: {self._scope_word(fn.get('scope') or 'tank')}"

    def _setup_scope_head(self, step):
        """The In-Tank Leak Test Setup screens, which name their own scope.

        576013-623 Rev AN p.119: "If you choose SINGLE TANK, the tank number
        (for example 'TANK 1') replaces the phrase 'ALL TANK' on each
        screen." The all-tanks wording is not consistent between screens --
        p.124 draws `TEST RATE: ALL TANK` and p.125 draws
        `TST EARLY STOP: ALL TANKS` -- so each screen carries its own.
        """
        prefix = step.get("setup_scope")
        if not prefix:
            return None
        which = step.get("scope_setting", "tank_test_method")
        if which == "line_test_method":
            # 576013-623 Rev AN ch.13 is the same function for lines, and
            # says LINE where ch.8 says TANK
            single = self.console.setting(
                which, 0, "ALL LINES") == "SINGLE LINE"
            word = f"LINE {self.device}" if single else "ALL LINES"
            if prefix == "TEST":
                # p.154 draws "ALL LINES:" bare for all, p.158
                # "TEST SINGLE LINE: LINE 1" for one
                return (f"TEST SINGLE LINE: LINE {self.device}" if single
                        else "ALL LINES:")
            return f"{prefix}: {word}"
        single = self.console.setting(
            which, 0, "ALL TANK") == "SINGLE TANK"
        if single:
            word = f"TANK {self.device}"
        else:
            word = "ALL TANKS" if step.get("scope_plural") else "ALL TANK"
        if prefix == "TEST":
            # the frequency screen, p.118: "TEST ALL TANK:" all-tanks, and
            # "TEST SINGLE TANK: TANK 1" for one
            return (f"TEST SINGLE TANK: TANK {self.device}" if single
                    else "TEST ALL TANK:")
        return f"{prefix}: {word}"

    def _load_lines(self, step):
        """The two Tanker Load Report screens the manual draws.

        "T #: UNLEADED GASOLINE / PRESS <PRINT> FOR REPORT" for the tank, and
        "T #: DATE #(LOAD NO.) / TOTAL = XXXX GALS" for one load of it.
        """
        console = self.console
        if step["load"] == "tank":
            label = console.text("602", self.device) or f"TANK {self.device}"
            return [f"T {self.device}: {label}"[:COLS],
                    "PRESS <PRINT> FOR REPORT"]
        head, total = console.loads.screen(self.device, self.load)
        return [head[:COLS], total[:COLS]]

    def _enter_delivery(self, step):
        """CHANGE then ENTER on a delivery screen: the ticket, or the BOL."""
        what = step["dlv"]
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        console = self.console
        if what == "prior":
            records = console.deliveries.records.get(self.device) or []
            if not records:
                self._flash("NO DELIVERIES")
                return
            self.dlv = (self.dlv + 1) % len(records)
            self._flash("PRIOR DELIVERY" + chr(10) + f"{self.dlv + 1} BACK")
            return
        if what in ("date", "time"):
            self._insert[what] = text
            self.confirm = [text[:COLS], CONT_STEP]
            self._render()
            return
        if what == "insert":
            when = self._insert_when()
            try:
                volume = float(text)
            except ValueError:
                self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
                return
            record = console.deliveries.insert(self.device, when, volume)
            if record is None:
                self._flash("INVALID INSERT")
                return
            self._insert.clear()
            # the manual's insert branch asks for a BOL after the ticket
            # volume, so the record stays to hand for that next step
            self._insert["record"] = record
            self.confirm = [f"TICKET VOLUME: {volume:.0f}"[:COLS],
                            CONT_STEP]
            self._render()
            return
        if what == "insertbol":
            record = self._insert.get("record")
            if record is None:
                self._flash("INSERT A DELIVERY" + chr(10) + "FIRST")
                return
            record.bol = text[:20]
            console.save()
            self.confirm = [f"BOL: {record.bol}"[:COLS],
                            CONT_STEP]
            self._render()
            return
        record = self._delivery()
        if record is None:
            self._flash("NO DELIVERIES")
            return
        if what == "bol":
            record.bol = text[:20]
            self.confirm = [f"BOL: {record.bol}"[:COLS],
                            CONT_STEP]
        else:
            try:
                record.ticket = float(text)
            except ValueError:
                self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
                return
            self.confirm = [f"TICKET VOLUME: {record.ticket:.0f}"[:COLS],
                            CONT_STEP]
        console.save()
        self._render()

    def _insert_when(self):
        """The date and time typed for an inserted delivery, or now."""
        now = self.console.now()
        date = self._insert.get("date") or time.strftime("%m/%d/%Y", now)
        clock = self._insert.get("time") or time.strftime("%H%M", now)
        digits = "".join(c for c in date if c.isdigit())
        hhmm = "".join(c for c in clock if c.isdigit())[:4] or "0000"
        try:
            return time.mktime((int(digits[4:8]), int(digits[:2]),
                                int(digits[2:4]), int(hhmm[:2]),
                                int(hhmm[2:]), 0, 0, 1, -1))
        except (ValueError, OverflowError):
            return time.mktime(now)

    def _device_code(self):
        """Table 29-1's letter for whatever this function is looking at."""
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        if "MAG SUMP" in name:
            return "s"
        if "IN-TANK" in name or "TANK" in name.split()[0:1]:
            return "T"
        for word, letter in (("COMMUNICATION", "D"), ("LIQUID", "L"),
                             ("EXTERNAL INPUT", "I"),
                             ("VAPOR", "V"),
                             ("GROUNDWATER", "G"), ("2-WIRE", "C"),
                             ("3-WIRE", "H"), ("SMART", "s"),
                             # the mag sump functions are a smart sensor's,
                             # and 576013-610 Rev AC p.82 heads them "s 1:"
                             ("MAG SUMP", "s"),
                             ("PUMP RELAY", "r"), ("OUTPUT RELAY", "R"),
                             ("WPLLD", "W"), ("PRESSURE LINE", "Q"),
                             ("LINE LEAK DETECT", "P")):
            if word in name:
                return letter
        return "T"

    def cur_field(self):
        """The field this step edits.

        Usually one function, one field, so the field is filed under the
        function's own code. Where the console asks several questions about
        one function, end shape then end factor, baud rate then parity,
        the step names the field it wants and that field claims its part.
        """
        e = self.cur_step()
        from .console import FIELDS
        if not e or not e.get("code"):
            return None
        return FIELDS.get(self._profile_code(e) or e.get("field")
                          or e["code"])

    # =====================================================================
    # display
    # =====================================================================
    def _alarms(self):
        return describe_alarms(self.console.compute_alarms())

    def _stored(self):
        code, f = self.cur_code(), self.cur_field()
        if not code:
            return ""
        raw = self.console.values.get(code.upper())
        if raw is None:
            return ""
        return fieldio.decode(f, code, raw) if f else raw.strip()

    # ---- typing into a field, the way the console does it -------------------
    def _begin_edit(self, value=""):
        """CHANGE: hold the value that is there, and put the cursor on it."""
        self.editing = True
        self.buf = "" if value is None else str(value)
        self.cur = 0
        self._tap = None

    def _edit_text(self):
        """`buf` with the console's cursor flashing in it.

        "The Arrow keys are used to move the cursor left and right WITHOUT
        CHANGING the displayed character", Operator's Quick Help 576013-939:
        which settles it. There is a cursor, there is a displayed character
        under it, and CHANGE therefore cannot have blanked the field. What
        you type replaces one character where the cursor is, and the ones you
        do not type over keep what they had.
        """
        if self._time_field() and self.meridiem:
            return self._edit_time()
        if (self.cur_field() or {}).get("kind") == "date":
            return self._edit_date()
        text = self.buf
        i = max(0, min(self.cur, len(text)))
        if i >= len(text):
            text = text + " "
        if self._blink:
            return text[:i] + "_" + text[i + 1:]
        return text

    def _edit_date(self):
        """"DATE: --/--/----", the template a date blanks to."""
        digits = self.buf
        cells = [digits[i] if i < len(digits) else "-" for i in range(8)]
        i = max(0, min(self.cur, 7))
        if self._blink:
            cells[i] = "_"
        return "".join(cells[:2]) + "/" + "".join(cells[2:4]) + "/" \
            + "".join(cells[4:])

    def _edit_time(self):
        """"TIME: 12:45 PM", with the cursor over one of the four digits.

        The colon and the half of the day are not typed, so the cursor skips
        them: four positions, and the arrows move between AM and PM instead.
        """
        digits = (self.buf + "____")[:4]
        i = max(0, min(self.cur, 3))
        if self._blink:
            digits = digits[:i] + "_" + digits[i + 1:]
        # Both halves of the day stay on the screen while you are editing,
        # which is what the Setup Manual draws ("TIME: XX:XX AM PM") and what
        # the console in the video shows; the arrows pick between them. How a
        # real one marks WHICH is picked is not recorded anywhere, so the
        # chosen half is shown in place and the other after it.
        other = "PM" if self.meridiem == "AM" else "AM"
        return f"{digits[:2]}:{digits[2:]} {self.meridiem} {other}"

    def _put(self, ch):
        """Type a character where the cursor is, and move past it."""
        text = self.buf.ljust(self.cur)
        self.buf = text[:self.cur] + ch + text[self.cur + 1:]
        self.cur += 1

    def _retap(self, ch):
        """Multi-tap: the same key again changes the character just typed.

        "Push the key again to change the character to a B, again to enter a
        C": the cursor does not move while one key is being cycled.
        """
        i = max(0, self.cur - 1)
        text = self.buf.ljust(i)
        self.buf = text[:i] + ch + text[i + 1:]

    def _toggle_sign(self):
        """The +/- key. "press the +/- key so that a minus (-) sign appears"."""
        if self.buf.startswith("-"):
            self.buf = self.buf[1:]
            self.cur = max(0, self.cur - 1)
        else:
            self.buf = "-" + self.buf
            self.cur += 1

    def _lines(self):
        if self.msg:
            return self.msg.split(chr(10))[:ROWS]
        if self.locked:
            return ["SYSTEM SECURITY", "CODE: " + "*" * len(self.buf) + "_"]
        if MODES[self.mode] == "NORMAL" and not self._entered:
            # the status display a console sits on, cycling any active alarms
            # The console's resting screen, as photographed: the date and time
            # across the top line and the status underneath.
            clock = self.console.clock_text()
            al = self._alarms()
            if not al:
                return [clock, "ALL FUNCTIONS NORMAL"]
            # "If more than one condition exists, the display will alternately
            # flash all messages": and a single unacknowledged one flashes on
            # its own, which is what the manual's example screen is showing.
            a = al[self._cycle % len(al)]
            key = a["aa"] + a["nn"] + a["tt"]
            if key not in self.console.acked and self._blink:
                return [clock, ""]
            return [clock, a["screen"]]
        if self.confirm:
            return list(self.confirm)
        if self.step == MODE_SCREEN:
            return [f"{MODES[self.mode]} MODE".replace("NORMAL MODE", "MODE"),
                    CONT_FUNCTION]
        fn = self.cur_function()
        if fn is None:
            return ["NO FUNCTIONS", "CHECK MODULES"]
        e = self.cur_step()
        if e is None:
            # the function's own screen, which is where FUNCTION lands
            return [fn["function"][:COLS], CONT_STEP]
        text = e["text"].split("(")[0].strip().upper()
        f = self.cur_field() or {}
        if f.get("kind") == "slots":
            # a config screen is per MODULE: which positions are connected
            wires = f.get("slots") or 4
            base = ((self.device - 1) // wires) * wires
            cells = (self.buf if self.editing else
                     self.console.slot_text(f["code"][1:4], wires,
                                            base)).split()
            if self.editing and self._blink:
                cells = list(cells)
                cells[self.slot % max(len(cells), 1)] = " "
            return [self._module_head(text, base // wires + 1)[:COLS],
                    ("SLOT #: " + " ".join(cells))[:COLS]]
        if self.editing:
            if MODES[self.mode] != "SETUP":
                return [text[:COLS], self._edit_text()[:COLS]]
            if self._is_label_step(e):
                return [self._enter(text)[:COLS],
                        f"{self._device_code()}{self.device}: "
                        f"{self._edit_text()}"[:COLS]]
            head, label = self._setup_context(e, text)
            return [head[:COLS],
                    self._second(label, self._edit_text())[:COLS]]
        if self._live_mode():
            if MODES[self.mode] == "RECONCILIATION":
                return self._recon_lines(e, text)
            if e.get("dlv"):
                return self._delivery_lines(e, text)
            if e.get("load"):
                return self._load_lines(e)
            if e.get("vmc"):
                # "x #: (S/N) / PRESS <ENTER>", then a value a side at a time
                console, side = self.console, e.get("side")
                if e["vmc"] == "head":
                    return [console.vmc_head(self.device)[:COLS],
                            "PRESS <ENTER>"]
                return [console.vmc_head(self.device, side)[:COLS],
                        console.vmc_reading(self.device, side,
                                            e["vmc"])[:COLS]]
            if e.get("history"):
                # "Q #: PLLD NUMBER # / PRESS PRINT FOR HISTORY"
                letter = self._device_code()
                label = self.console.text(
                    {"Q": "782", "W": "7A2", "s": "722"}.get(letter, "782"),
                    self.device)
                default = ("SUMP" if letter == "s" else "LINE")
                head = (f"{letter} {self.device}: {label}" if label
                        else f"{letter} {self.device}: {default} {self.device}")
                return [head[:COLS], "PRESS PRINT FOR HISTORY"]
            head = self._scope_head(e, text)
            if e.get("sel"):
                return [head[:COLS], self._choice(e)[:COLS]]
            if e.get("entry"):
                # "TEST DURATION: ALL TANKS / DURATION: XX"
                return [head[:COLS],
                        f"DURATION: {self.sel.get(e['entry'], '')}"[:COLS]]
            if e.get("action"):
                return [head[:COLS], "PRESS <ENTER>"]
            if e.get("livehead"):
                # a header the console dates itself: "REPORT DATE: AUG 23
                # 2026 / PRESS <ENTER>" on the ISD report functions
                one = self.console.live_reading(e["livehead"], self.device)
                return [one[:COLS], (e.get("body") or "PRESS <ENTER>")[:COLS]]
            if e.get("body"):
                # a header the manual descends into with ENTER rather than a
                # screen with a reading on it
                return [head[:COLS], e["body"][:COLS]]
            live = self.console.live_reading(e.get("live"), self.device)
            head_spec = e.get("head")
            if head_spec and "%d" in head_spec:
                # "Q 1: PLLD #1": a screen that names its own head, with the
                # device number in it
                return [self._named_head(head_spec)[:COLS],
                        (live or "")[:COLS]]
            if head_spec == "device":
                # "s 1: SUMP 1 / 0.000 IN     74.8 F": the mag sump screens
                # are headed by the sensor and its label rather than by what
                # the step is called (576013-610 Rev AC p.82, p.86)
                letter = self._device_code()
                code = {"T": "602", "L": "702", "V": "707", "G": "712",
                        "C": "742", "H": "747", "s": "722", "Q": "782",
                        "W": "7A2", "P": "760", "R": "807", "I": "802",
                        "r": "7C5"}.get(letter, "602")
                label = (self.console.text(code, self.device)
                         or f"{DEVICE_WORD.get(letter, 'DEVICE')} "
                            f"{self.device}")
                head = f"{letter} {self.device}: {label}"
                if live and chr(10) in live:
                    one, two = live.split(chr(10), 1)
                    return [one[:COLS], two[:COLS]]
                return [head[:COLS], (live or "")[:COLS]]
            if head_spec == "plain":
                # a site-wide reading: no device prefix, the ISD status
                # screens are about the site, not a tank
                return [text[:COLS], (live or "")[:COLS]]
            head = f"{self._device_code()} {self.device}:{text}" if live else text
            return [head[:COLS], live[:COLS] if live else "PRESS <STEP>"]
        if MODES[self.mode] == "DIAGNOSTIC" and e.get("act"):
            # "RESET ACCUCHART NO ... Selecting YES clears the AccuChart Tank
            # Profile": CHANGE walks the answer, ENTER does it.
            head = self.console.diag_line(e["text"], self.device)
            body = (e.get("l2") or "")
            body = body[:body.rfind(":") + 1] + (" YES" if self.armed
                                                 else " NO")                 if ":" in body else body
            return [head[:COLS], body[:COLS]]
        if MODES[self.mode] == "DIAGNOSTIC":
            # Two lines straight from the manual, but pointed at THIS console:
            # the device the panel is on, its programmed label, and the values
            # the console can actually answer for.
            head = self.console.diag_line(e["text"], self.device)
            body = (self.console.diag_value(e["live"], self.device,
                                            self._diag_kind())
                    if e.get("live") else (e.get("l2") or ""))
            if body and chr(10) in body:
                # the line leak diagnostics read on BOTH lines: a pressure and
                # a pair of switch states, not a label over a value
                head, body = body.split(chr(10), 1)
            return [head[:COLS], body[:COLS]]
        if self._chart_locked(e):
            # "TANK PROFILE : 50 PTS / ENTER PASSCODE->______<", with the
            # passcode going in, which is the one setup screen that reads
            # off the panel rather than off the console
            return ["TANK PROFILE : 50 PTS",
                    "ENTER PASSCODE->" + "*" * len(self.buf) + "_"]
        if e.get("profile") and self._profile_pending:
            # "CLEAR EXISTING PROFILE / ARE YOU SURE? : NO"
            return ["CLEAR EXISTING PROFILE",
                    f"ARE YOU SURE? : {'YES' if self.armed else 'NO'}"]
        if e.get("archive"):
            text = e["text"].split("(")[0].strip().upper()
            if self.sure:
                # the choice is made; the console asks it again
                return [f"{text}: YES"[:COLS],
                        f"ARE YOU SURE?: {'YES' if self.armed else 'NO'}"]
            return ["ARCHIVE UTILITY",
                    f"{text}: {'YES' if self.armed else 'NO'}"[:COLS]]
        if e.get("point") and e["point"] in self._point:
            # a point the panel is holding, part-typed or just entered
            head, _p = self._setup_context(e, e["text"].split("(")[0]
                                           .strip().upper())
            held = self._point.get(e["point"], "")
            if e["point"] == "height":
                return [head[:COLS],
                        f"HEIGHT : {masks.apply('000000', held or '0')}"[:COLS]]
            at = self._point.get("height", "")
            try:
                at = f"{float(at):.2f}"
            except (TypeError, ValueError):
                at = "0.00"
            return [head[:COLS],
                    f"{at} INCH VOL: "
                    f"{masks.apply('000000', held or '0')}"[:COLS]]
        # everything else a setup step draws is the console at rest, and
        # that screen belongs to `screens`, because the printer and the
        # serial port draw the same one.
        return screens.setup_lines(self.console, fn, e, self.device,
                                   chart_open=True)

    LABEL_CODES = screens.LABEL_CODES

    def _is_label_step(self, step):
        return screens.is_label_step(step)

    def _diag_kind(self):
        """"plld" or "wplld", from the diagnostic the panel is in."""
        fn = self.cur_function() or {}
        return "wplld" if "WPLLD" in fn.get("function", "") else "plld"

    _enter = staticmethod(screens.enter)

    def _module_head(self, text, module=1):
        """"TANK CONFIG - MODULE 1": a config screen is per module.

        With two probe modules fitted, tanks 5 to 8 are module 2's four
        positions, and this is the screen that says so.
        """
        head = text.split("(")[0].strip()
        for word, name in (("TANK CONFIG", "TANK CONFIG"),
                           ("SENSOR CONFIG", "SENSOR CONFIG"),
                           ("SS CONFIG", "SS CONFIG"),
                           ("LINE CONFIG", "LINE CONFIG"),
                           ("INPUT CONFIG", "INPUT CONFIG"),
                           ("RELAY CONFIG", "RELAY CONFIG")):
            if head.startswith(word):
                return f"{name} - MODULE {module}"
        # the two pump ones are punctuated differently, and it is not a
        # typesetting accident: 576013-623 Rev AN draws
        # "PUMPSENS CONFIG: MODULE1" on p.162 and "PUMP MON CONFIG: MODULE1"
        # on p.165, against "TANK CONFIG - MODULE 1" on p.92.
        for word, name in (("PUMP SENSE CONFIG", "PUMPSENS CONFIG"),
                           ("PUMP RELAY CONFIG", "PUMP MON CONFIG")):
            if head.startswith(word):
                return f"{name}: MODULE{module}"
        return head

    # ---- settings the console keeps outside the wire format -----------------
    def _console_value(self, step, editing=False):
        """Tank chart security's own fields, which no S-function holds."""
        from .console import FIELDS
        f = FIELDS.get(step["console"], {})
        if editing:
            return self._edit_text()
        kind, which = f.get("kind"), f.get("which")
        if kind == "chartcode":
            return "******" if self.console.chart_secured() else "000000"
        if kind == "view":
            return self.console.probe_serial(self.device)
        if kind == "consolefloat":
            value = getattr(self.console, which, {}).get(self.device)
            return f"{value:g}" if value else "0"
        if kind == "consoletextdev":
            return getattr(self.console, which, {}).get(self.device, "")
        if kind == "setting":
            device = self.device if f.get("scope") == "device" else 0
            value = self.console.setting(which, device, f.get("default", ""))
            return f"{value}{f.get('unit', '')}" if value else value
        if kind == "pmc_threshold":
            # the field's own prompt is "IWC"; return just the value, so
            # the second line reads "IWC +0.200" once, not twice
            off, on = self.console.pmc_thresholds()
            v = off if which == "off" else on
            return f"{v:+06.3f}"
        return getattr(self.console, which, "") or ""

    def _chart_locked(self, step):
        """A secured chart screen nobody has given the passcode for yet.

        "If you selected 50 points for Tank Profile AND Tank Chart Security
        has been enabled, press STEP and the system displays: TANK PROFILE :
        50 PTS / ENTER PASSCODE->______<"
        """
        if self.chart_open or MODES[self.mode] != "SETUP" or not step:
            return False
        protected = bool(step.get("point")) or bool(
            (step.get("when") or {}).get("chart_secured"))
        return (protected and self.console.chart_secured()
                and self.console.tank_profile(self.device) == "04")

    def _console_step(self, step):
        """Is this screen the console's own, or some device's?"""
        return screens.console_step(step)

    _second = staticmethod(screens.second)

    def _shown(self):
        """What line two reads, defaulted the way a console out of the box is."""
        return screens.shown(self.console, self.cur_field(), self._stored())

    def _masked(self, value):
        """`value` drawn in the field's own fixed-width mask."""
        return screens.masked(self.cur_field(), value)

    def _setup_context(self, step, text):
        """The two halves of a setup screen: whose it is, and what it asks.

        The manual's own screens: a tank step reads "T1: (Product Label)" over
        "PRODUCT CODE: 1", so the device and its label take the top line and
        the prompt carries the value. A console-wide step has no device to
        name, so the PROMPT takes the top line and the value goes underneath
        on its own, "SYSTEM UNITS" over "U.S.", behind a short label only
        where the manual shows one: "SET TIME" over "TIME: 1:32 PM".
        """
        code = step.get("code") or ""
        # a screen can name itself: "T1: SIPHON MANIFOLDED" rather than the
        # product label, "AUTO SHIFT #2 CLOSING" rather than the step's words
        head = step.get("head")
        if head == "product":
            # 576013-623 Rev AN p.128 heads the average-sales screens with
            # the product label on its own -- these are per PRODUCT, and the
            # manual says so: "press TANK/SENSOR to select a different
            # product"
            return (self.console.text("602", self.device)
                    or f"TANK {self.device}"), (step.get("l2") or "")
        if head:
            # a head can name the device more than once: the manual's PLLD
            # screen is "Q 1: PLLD NUMBER 1"
            head = self._named_head(head)
        label = (step.get("l2") or "").replace("%d", str(self.device))
        if self._console_step(step):
            return head or text, label
        if head:
            # a screen that names itself carries the value bare underneath:
            # "T1: ANNUAL TEST FAIL / ALARM DISABLED"
            return head, label
        fn = self.cur_function()
        if fn and fn["function"].startswith("COMMUNICATION"):
            # the manual heads a port screen "COMM BOARD: 1" and a receiver
            # screen "D1:", and this function walks both
            if code[1:3] in ("52", "5B"):
                named = self.console.text("522", self.device)
                return (f"D{self.device}: {named}".rstrip(),
                        label or text + ":")
            return f"COMM BOARD: {self.device}", label or text + ":"
        letter = self._device_code()
        named = self.console.text(
            {"T": "602", "L": "702", "V": "707", "G": "712", "C": "742",
             "H": "747", "s": "722", "Q": "782", "W": "7A2", "P": "760",
             "R": "807", "I": "802", "r": "7C5"}.get(letter, "602"),
            self.device)
        return f"{letter}{self.device}: {named}".rstrip(), label or text + ":"

    def _guard(self, fn):
        """Wrap a key so a busy console ignores it, the way a real one does.

        A console with no power ignores everything: the keys are scanned by
        the same board the breaker just turned off.
        """
        def press():
            if self.console.powered and not self._busy():
                fn()
        return press

    def _busy(self):
        """Is the console in the middle of something it cannot be interrupted
        in? An archive save or restore is the only one."""
        return time.time() < getattr(self, "busy_until", 0.0)

    def _keyed(self):
        self._last_key = time.time()

    def _sync_device(self):
        """Keep TANK/SENSOR pointed at a device this function actually has.

        Walk to tank 5, then step to a function whose module carries three
        sensors, and the panel would still say 5. A console points at the
        first device it has.
        """
        devices = self._devices()
        if devices and self.device not in devices:
            self.device = devices[0]

    def _render(self):
        if self.console.display_blanked and self.console.powered:
            # SW2-3 closed: the display is off, the console is not. Keys,
            # serial and printer all still work; you just cannot see.
            for _rid in self._text_ids:
                self.lcd.itemconfig(_rid, text="")
            self.hint.config(text="DISPLAY OFF   |   DIP SW2-3 is closed "
                                  "(Switches menu)")
            return
        if self.isdflow:
            for _i, _rid in enumerate(self._text_ids):
                self.lcd.itemconfig(
                    _rid, text=self._isdflow_lines()[_i].ljust(COLS)[:COLS])
            self.hint.config(text="grade-hose mapping: TANK/SENSOR scrolls, "
                                  "ENTER selects, STEP cancels or continues")
            return
        if self.isd_override:
            if self.isd_override == "enter":
                rows = ["ISD SHUTDOWN OVERRIDE", "PRESS <ENTER>"]
            else:
                word = "YES" if self.isd_override == "yes" else "NO"
                rows = ["OVERRIDE SHUTDOWN & LOG", f"ARE YOU SURE?: {word}"]
            for _i, _rid in enumerate(self._text_ids):
                self.lcd.itemconfig(_rid, text=rows[_i].ljust(COLS)[:COLS])
            self.hint.config(text="ISD shutdown override: ENTER, CHANGE "
                                  "picks YES, ENTER overrides and logs it")
            return
        if self.boot_restore:
            # the cold-start restore offer, drawn the way 576013-637 shows
            # it: the clock over the question, then the step prompt
            if self.boot_restore == "step":
                rows = ["RESTORE SETUP DATA: YES", "PRESS <STEP> TO CONTINUE"]
            else:
                word = "YES" if self.boot_restore == "yes" else "NO"
                rows = [self.console.clock_text()[:COLS],
                        f"RESTORE SETUP DATA: {word}"]
            for _i, _rid in enumerate(self._text_ids):
                self.lcd.itemconfig(_rid, text=rows[_i].ljust(COLS)[:COLS])
            self.hint.config(text="cold start with an archive: CHANGE picks "
                                  "YES, ENTER, then STEP restores; STEP on "
                                  "NO carries on without restoring")
            return
        if not self.console.powered:
            # dark glass: not even the ghosts of the unlit segments change,
            # but the text is gone and so is the status line under the face
            for _rid in self._text_ids:
                self.lcd.itemconfig(_rid, text="")
            self.hint.config(text="NO AC POWER   |   the breaker is off "
                                  "(Switches menu)")
            return
        self._sync_device()
        rows = self._lines()
        rows += [""] * (ROWS - len(rows))
        for _i, _rid in enumerate(self._text_ids):
            self.lcd.itemconfig(_rid, text=rows[_i].ljust(COLS)[:COLS])
        bits = [f"MODE: {MODES[self.mode]}"]
        if MODES[self.mode] != "NORMAL":
            fns = self.functions()
            fn = self.cur_function()
            if fn:
                bits.append(f"FUNCTION {self.func % len(fns) + 1}/{len(fns)}: "
                            + fn["function"])
                bits.append(f"STEP {self.step % max(len(self.steps()), 1) + 1}"
                            f"/{len(self.steps())}")
            e = self.cur_step()
            if e and e.get("code"):
                bits.append(f"{self.cur_code()}   TANK/SENSOR {self.device}")
            elif e:
                bits.append("navigable, not stored")
            if self.editing:
                bits.append("ENTER saves, STEP/FUNCTION/MODE discards")
        self.hint.config(text="   |   ".join(bits))

    def _flash(self, text):
        self.msg = text
        self._render()
        self._flash_id = self.after(1500, self._clear)

    def _clear(self):
        self.msg = ""
        self._render()

    def _poll(self):
        if not self.console.powered:
            # the site outside carries on; the console does not
            for key in ("power", "warn", "alarm"):
                self._set_led(key, False)
            for sync in getattr(self, "_site_sync", []):
                try:
                    sync()
                except Exception:
                    pass
            self._poll_id = self.after(700, self._poll)
            return
        self.console.tick()
        self.console.in_setup = MODES[self.mode] == "SETUP"
        if self.isdflow:
            self._isdflow_poll()
        # "The system will automatically return to the Operating Mode status
        # display in 15 minutes if no activity takes place while the system is
        # in the Setup Mode."
        if MODES[self.mode] == "SETUP" and time.time() - self._last_key > 900:
            self.mode = MODES.index("NORMAL")
            self.func, self.step = 0, HEADER
            self._entered = False
            self.editing, self.buf, self.confirm = False, "", None
            self.console.in_setup = False
            self.log("-- 15 minutes idle: returned to Operating Mode")
        for sync in getattr(self, "_site_sync", []):
            try:
                sync()
            except Exception:
                pass
        al = self._alarms()
        self._blink = not self._blink
        # The lights follow the CONDITION, not the message: "you cannot turn
        # off warning and alarm lights until you correct the cause". So an
        # acknowledged alarm whose cause is still there keeps its light, and a
        # corrected one that nobody has acknowledged yet has gone dark while
        # its message is still on the display.
        cond = [a for a in describe_alarms(self.console.conditions())
                if not a["description"].endswith("Active")]
        warn = any("Warning" in a["description"] for a in cond)
        alarm = any("Warning" not in a["description"] for a in cond)
        self._set_led("power", True)
        self._set_led("warn", warn and self._blink)   # the manual: it flashes
        self._set_led("alarm", alarm)
        self._cycle += 1
        # "If your system has a printer, it will print an alarm or warning
        # report when it detects a warning or alarm condition."
        for tank, record in self.console.printed_deliveries:
            # "when the system recognizes that a delivery occurred, an
            # adjusted delivery report is automatically printed"
            self.paper_out(printer.delivery(self.console, tank, record))
            if self.console.licensed("bir"):
                # a reconciling console prints the adjusted report too,
                # which "takes into consideration all dispensing that
                # occurred during the delivery"
                self.paper_out(printer.adjusted_delivery(
                    self.console, tank, record))
            self.log(f"-- PRINT: delivery on tank {tank}, "
                     f"{record.amount:.0f} gallons")
        self.console.printed_deliveries.clear()
        # "Confirmation Report": prints after a successful auto-dial call
        for receiver in self.console.autodial.confirm_pending:
            self.paper_out(["CONFIRMATION REPORT",
                            f"RECEIVER {receiver}: CONNECTED",
                            self.console.clock_text()])
            self.log(f"-- PRINT: confirmation report, receiver {receiver}")
        self.console.autodial.confirm_pending.clear()
        # "Each time an AccuChart calibration is updated, a user notification
        # message is sent to the local printer."
        for tank, when in self.console.accuchart_log:
            self.paper_out(printer.accuchart_update(self.console, tank, when))
            self.log(f"-- PRINT: accuchart update on tank {tank}")
        self.console.accuchart_log.clear()
        keys = {a["aa"] + a["nn"] + a["tt"] for a in al}
        if keys - self._posted:
            self.paper_out(printer.alarms(self.console))
            self.log("-- PRINT: alarm posted")
        self._posted = keys
        # On the bench, say WHY each message is still there: an alarm whose
        # cause is gone is only waiting to be acknowledged.
        livekeys = {a["aa"] + a["nn"] + a["tt"] for a in cond}
        rows = []
        for a in al:
            if (a["aa"] + a["nn"] + a["tt"]) in livekeys:
                tail = " (silenced)" if self.console.silenced else ""
            else:
                tail = ": cause corrected, ALARM/TEST clears it"
            rows.append("  " + a["text"] + tail)
        txt = "  ALL FUNCTIONS NORMAL" if not rows else "\n".join(rows)
        self.alarm_lbl.config(text=txt, fg="#7bd88f" if not al else "#ff9b9b")
        if not self.msg and (MODES[self.mode] == "NORMAL" or self.editing):
            self._render()
        self._poll_id = self.after(700, self._poll)

    def destroy(self):
        """Stop the timers before the window goes, or they fire into nothing."""
        for pending in (getattr(self, "_poll_id", None),
                        getattr(self, "_flash_id", None)):
            if pending:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
        super().destroy()

    def _set_led(self, key, on):
        cv, oval, colour = self.led[key]
        cv.itemconfig(oval, fill=colour if on else LED_OFF)

    def log(self, line):
        try:
            self.logbox.insert("end", line + "\n")
            self.logbox.see("end")
        except Exception:
            pass

    # =====================================================================
    # keys
    # =====================================================================
    def _abandon(self, what):
        """Leave an entry the way the console leaves one: silently.

        576013-623: "If you press the STEP, FUNCTION, or MODE key without
        pressing ENTER, the data will not be saved." The manual says the
        data is lost; it does not say the console announces it, and no
        screen diagram in any manual shows a message for it. An earlier
        version flashed NOT SAVED here, which was this simulator inventing
        a screen the hardware does not have. The bench hint under the
        console still warns while an entry is open, because the bench may
        say what the console must not.
        """
        if self.editing:
            self.editing = False
            self.buf = ""
            return True
        return False

    def _mode_offered(self, mode):
        """"You must have the BIR software module key installed to access
        this mode": so a console without it has three, not four."""
        if MODES[mode] != "RECONCILIATION":
            return True
        return bool(self.console.available_reconciliation())

    def _step_mode(self, by):
        mode = self.mode
        for _ in range(len(MODES)):
            mode = (mode + by) % len(MODES)
            if self._mode_offered(mode):
                return mode
        return self.mode

    def k_mode(self):
        self._keyed()
        self.chart_open = False
        self._abandon("MODE")
        self.sub = None
        self.confirm = None
        self.mode = self._step_mode(1)
        self.func = 0
        # "Press the MODE key to display the Setup Mode main screen": every
        # mode has one, and FUNCTION is what leaves it
        self.step = MODE_SCREEN
        self._entered = False
        # "you will be required to enter this code before you can access any
        # setup or diagnostic function"
        # "you will be required to enter this code before you can access any
        # setup or diagnostic function": the reporting modes are not guarded
        self.locked = (self.console.panel_security
                       and bool(self.console.security_code())
                       and MODES[self.mode] in ("SETUP", "DIAGNOSTIC"))
        self.buf = ""
        self._render()

    def k_function(self):
        """FUNCTION scrolls the functions, and lands on the function screen.

        "IN-TANK SETUP / PRESS <STEP> TO CONTINUE" is a screen in its own
        right, which every chapter of the setup manual starts at.
        """
        self._keyed()
        self.chart_open = False
        if self._abandon("FUNCTION"):
            return
        self.sub = None
        self.confirm = None
        if MODES[self.mode] == "NORMAL" and not self._entered:
            self._entered = True
            self.func = 0
        elif self.step == MODE_SCREEN:
            self.func = 0                    # the mode screen: in at the top
        else:
            self.func = (self.func + 1) % max(len(self.functions()), 1)
        self.step = HEADER
        self._render()

    def k_step(self):
        """"Use the STEP key to move from one procedure to the next."""
        self._keyed()
        if self.isdflow:
            state = self.isdflow["state"]
            if state == "countdown":
                self.log("-- auto map cancelled")
            self.isdflow = None
            self._render()
            return
        if self.boot_restore:
            if self.boot_restore == "step":
                # the restore runs; 637: the printer carries the report
                self.boot_restore = None
                self._run_archive("restore")
                return
            # STEP past NO: carry on without restoring
            self.log("-- restore declined; running unprogrammed")
            self.boot_restore = None
            self._render()
            return
        if self.confirm:
            self.confirm = None
            pending = getattr(self, "_archive_pending", None)
            if pending:
                # "When you press STEP in response to this message, the system
                # starts saving your setup data to the EEPROM."
                self._archive_pending = None
                self.armed = self.sure = False
                self._run_archive(pending)
                return
            if (self.cur_step() or {}).get("archive"):
                # The archive's own confirmations are stages of ONE step:
                # SAVE SETUP DATA: YES, then ARE YOU SURE?, then the save.
                # Everywhere else the confirmation is the tail of the step you
                # just finished, and STEP leaves it.
                self._render()
                return
            # "DAYS = XX / PRESS <STEP> TO CONTINUE ... Press STEP. The system
            # displays the message:" and what it displays is the NEXT step.
            # One press, not two.
        if self._abandon("STEP"):
            return
        if self.step == MODE_SCREEN:
            self.step = HEADER
            self._render()
            return
        steps = self.steps()
        if not steps:
            return
        self.step = 0 if self.step == HEADER else (self.step + 1) % len(steps)
        self.armed = self.sure = False
        self._render()

    def k_backup(self):
        """The reverse of STEP, through the hierarchy the manual describes.

        "BACKUP will move through the hierarchy of commands as follows:
        through Steps within a Function to that Function; then back through
        Functions to Mode; then back through Modes." So the first step backs
        up to the function screen, not into the previous function's tail.
        """
        self._keyed()
        if self._abandon("BACKUP"):
            return
        self.confirm = None
        if self.sub is not None and self.step <= 0:
            # back out of the screens ENTER went down into, onto the one that
            # offered them
            fn = self.cur_function()
            tops = [i for i, sc in enumerate(fn["steps"])
                    if not sc.get("depth")] if fn else []
            self.step = max((n for n, i in enumerate(tops) if i <= self.sub),
                            default=0)
            self.sub = None
            self._render()
            return
        if self.step > 0:
            self.step -= 1
        elif self.step == 0:
            self.step = HEADER
        elif self.step == MODE_SCREEN:
            self.mode = self._step_mode(-1)
            self._entered = False
        else:
            fns = self.functions()
            if MODES[self.mode] == "NORMAL" and not self._entered:
                # The operating status display is the top of the tree, and
                # BACKUP did nothing at all here. No manual says what the key
                # does on that screen (see UNKNOWNS.md), but the one sentence
                # that describes the key finishes "then back through Modes",
                # and that is the only move left from the top.
                self.mode = self._step_mode(-1)
                self.step = MODE_SCREEN
                self._entered = False
                self._render()
                return
            if self.func == 0:
                # "back through Functions to Mode": the mode's own screen
                self.step = MODE_SCREEN
            elif fns:
                self.func = (self.func - 1) % len(fns)
        self._render()

    def k_tank(self):
        """TANK/SENSOR, which does not discard an entry.

        The rule is quoted exactly: "If you press the STEP, FUNCTION, or MODE
        key without pressing ENTER, the data will not be saved." TANK/SENSOR
        is not in that list, it "is used to advance by tank or sensor through
        setup procedures or displayed data", and it does that on the first
        press, whatever is on the screen.
        """
        if self.isdflow and self.isdflow["state"] in ("select", "product",
                                                      "assignhose"):
            self.isdflow["idx"] += 1
            self._render()
            return
        self._keyed()
        self.chart_open = False
        self.editing, self.buf = False, ""
        self.confirm = None
        devices = self._devices()
        if self.device in devices:
            self.device = devices[(devices.index(self.device) + 1)
                                  % len(devices)]
        else:
            self.device = devices[0]
        self._render()

    # which config screen decides the devices a function can point at
    CONFIG_OF = {
        "IN-TANK SETUP": "601", "IN-TANK LEAK TEST SETUP": "601",
        "FUEL MANAGEMENT SETUP": "601", "IN-TANK INVENTORY": "601",
        "IN-TANK TEST RESULTS": "601", "IN-TANK DIAGNOSTIC": "601",
        "LIQUID SENSOR SETUP": "701", "LIQUID STATUS": "701",
        "LIQUID DIAGNOSTIC": "701",
        "VAPOR SENSOR SETUP": "706", "VAPOR STATUS": "706",
        "VAPOR DIAGNOSTIC": "706",
        "GROUNDWATER SENSOR SETUP": "711", "GROUNDWATER STATUS": "711",
        "GROUNDWATER DIAGNOSTIC": "711",
        "2-WIRE CL SENSOR SETUP": "741", "2 WIRE CL STATUS": "741",
        "3-WIRE CL SENSOR SETUP": "746", "3 WIRE CL STATUS": "746",
        "SMART SENSOR SETUP": "721", "SMART SENSOR STATUS": "721",
        "PRESSURE LINE LEAK SETUP": "781", "PLLD LINE DISABLE SETUP": "781",
        "PRESSURE LINE RESULTS": "781",
        "WPLLD LINE LEAK SETUP": "7A1", "WPLLD LINE DISABLE SETUP": "7A1",
        "WPLLD LINE RESULTS": "7A1",
        "LINE LEAK DETECTOR SETUP": "751",
        "VLLD LINE DISABLE SETUP": "751",
        "LINE LEAK DETECT RESULTS": "751",
        "PUMP SENSOR SETUP": "771", "PUMP RELAY MONITOR SETUP": "7C4",
        "PUMP RELAY MON STATUS": "7C4",
        "EXTERNAL INPUT SETUP": "801",
        "OUTPUT RELAY SETUP": "806", "TEST OUTPUT RELAYS": "806",
    }

    # The input module carries two, and it shares a cage key with the relays.
    DEVICE_LIMIT = {"EXTERNAL INPUT SETUP": 2}

    def _live_mode(self):
        """The two modes that read the site rather than programme it."""
        return MODES[self.mode] in ("NORMAL", "RECONCILIATION")

    def _devices(self):
        """Every device this function's module carries, in order.

        TANK/SENSOR walks all of them whether or not they have been
        configured or programmed: on a real console, programming tank 1 and
        pressing TANK/SENSOR takes you to tank 2 on any screen, and round all
        the positions the module has wires for. Which of them are switched on
        at the config screen decides what the site HAS, not what the panel can
        be pointed at.
        """
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        code = self.CONFIG_OF.get(name)
        if name in self.DEVICE_LIMIT:
            n = self.DEVICE_LIMIT[name]
        else:
            n = max(SLOT_POSITIONS.get(code, 0), self._device_count())                 if code else self._device_count()
        n = max(n, 1)
        if code and MODES[self.mode] != "SETUP":
            # Reporting on a position nobody wired up is reporting on
            # something that is not there: LIQUID STATUS walked eight sensors
            # and called five of them NORMAL on a site with three.
            live = self.console.configured_devices(code, n)
            if live:
                return live
        return list(range(1, n + 1))

    def _device_count(self):
        """How many devices this function has, rather than a flat sixteen.

        A console steps through the devices its cards can carry: four tanks
        to a probe module, eight sensors to a sensor module. The four
        console-wide ones (header lines, shift times) go round four.
        """
        fn = self.cur_function()
        if fn is None:
            return 1
        name = fn["function"]
        need = FUNCTION_REQUIRES.get(name)
        if MODES[self.mode] != "SETUP":
            requires = {f["function"]: f.get("requires")
                        for f in (self.console.available_operating()
                                  + self.console.available_diagnostics()
                                  + self.console.available_reconciliation())}
            module = requires.get(name)
            need = (module,) if module else None
        if need:
            # as many devices as the cards fitted carry between them. A
            # requirement can name a family of cards rather than one, and
            # COMMUNICATION SETUP names four.
            cards = []
            for entry in ([need] if isinstance(need, str) else need):
                cards += [entry] if isinstance(entry, str) else list(entry)
            return max(max(self.console.capacity(m) for m in cards), 1)
        if name.startswith("COMMUNICATION"):
            return 6                    # six comm ports, six receivers
        return 4

    def k_change(self):
        self._keyed()
        if self.isd_override in ("no", "yes"):
            self.isd_override = "yes" if self.isd_override == "no" else "no"
            self._render()
            return
        if self.boot_restore in ("ask", "yes"):
            self.boot_restore = "yes" if self.boot_restore == "ask" else "ask"
            self._render()
            return
        self.confirm = None
        e = self.cur_step()
        if e is None:
            return
        if e.get("act"):
            self.armed = not self.armed
            self._render()
            return
        if self._live_mode():
            # Operating mode has choices too: which tanks to test, at which
            # rate, for how long. CHANGE walks them, ENTER accepts.
            if e.get("dlv"):
                if e["dlv"] == "prior":
                    self._flash("PRESS <ENTER>" + chr(10) + "FOR THE PRIOR ONE")
                    return
                self._change_into(self._delivery_value(e))
                self._render()
                return
            if e.get("adjust"):
                self._change_into("")
                self._render()
                return
            if e.get("action") and MODES[self.mode] == "RECONCILIATION":
                # "press CHANGE. The system displays SHIFT CLOSE NOW: YES"
                self.armed = not self.armed
                self._render()
                return
            if e.get("sel"):
                names = [c[0] if isinstance(c, list) else c
                         for c in e["choices"]]
                cur = self._choice(e)
                nxt = (names.index(cur) + 1) % len(names) if cur in names else 0
                self.sel[e["sel"]] = names[nxt]
                if MODES[self.mode] == "RECONCILIATION":
                    self._recon_sync()
                self._render()
            elif e.get("entry"):
                self._change_into(str(self.sel.get(e["entry"], "")))
                self._render()
            return
        if self._chart_locked(e):
            self._flash("ENTER PASSCODE" + chr(10) + "THEN <ENTER>")
            return
        if e.get("console"):
            from .console import FIELDS
            f = FIELDS.get(e["console"], {})
            if f.get("kind") == "view":
                self._flash("VIEW ONLY")
                return
            if f.get("kind") == "setting" and f.get("choices"):
                # a fixed list of words is walked with CHANGE, not typed
                device = self.device if f.get("scope") == "device" else 0
                names = f["choices"]
                now = self.console.setting(f["which"], device,
                                           f.get("default", names[0]))
                self.console.set_setting(
                    f["which"],
                    names[(names.index(now) + 1) % len(names)]
                    if now in names else names[0], device)
                self.console.save()
                self._render()
                return
            self._change_into(self._console_value(e))
            self._render()
            return
        if e.get("point") in ("height", "volume"):
            self._change_into(self._point.get(e["point"], ""))
            self._render()
            return
        if e.get("point") == "count":
            self._flash("PRESS <STEP>" + chr(10) + "TO ADD A POINT")
            return
        if e.get("profile"):
            from .console import Console as _C
            if self._profile_pending:
                self.armed = not self.armed
                self._render()
                return
            names = [_C.PROFILE_NAME[k] for k in self.console.profiles()]
            cur = (self.buf if self.editing else
                   _C.PROFILE_NAME[self.console.tank_profile(self.device)])
            i = (names.index(cur) + 1) % len(names) if cur in names else 0
            self.buf, self.editing = names[i], True
            self._render()
            return
        if e.get("archive"):
            self.armed = not self.armed
            self._render()
            return
        if not e.get("code"):
            self._flash("NOT SIMULATED" + chr(10) + "navigable only")
            return
        f = self.cur_field()
        if f is not None and f.get("kind") == "slots":
            # "To activate a PLLD, you replace the X with a number by pressing
            # the CHANGE key ... You move between PLLDs by pressing the right
            # or left arrow key."
            if not self.editing:
                wires = f.get("slots") or 4
                base = ((self.device - 1) // wires) * wires
                self.editing = True
                self.buf = self.console.slot_text(f["code"][1:4], wires, base)
                self.slot = 0
            cells = self.buf.split()
            n = self.slot % max(len(cells), 1)
            cells[n] = "X" if cells[n] != "X" else str(n + 1)
            self.buf = " ".join(cells)
            self._render()
            return
        kind = (f or {}).get("kind")
        if kind in ("enum", "flag"):
            # "Press CHANGE until the desired ... appears. Press ENTER to
            # confirm your choice." So CHANGE walks the list, from whatever is
            # programmed the first time and from the buffer after that. A flag
            # is that list with two entries on it: "To enable Tank Annual Test
            # Needed Warnings, press CHANGE. The system now displays ...
            # ENABLED."
            if kind == "flag":
                names = list(f.get("words") or ("DISABLED", "ENABLED"))
            else:
                names = [lab for _v, lab in fieldio.choices_of(f, self.console)]
            cur = self.buf if self.editing else str(self._shown() or "")
            nxt = (names.index(cur) + 1) % len(names) if cur in names else 0
            self.buf = names[nxt] if names else ""
            self.editing = True
            self.cur = 0
        else:
            self._change_into(self._edit_seed())
        self._render()

    # Field kinds the keypad types as numbers.
    NUMERIC_KINDS = ("int", "flag", "float", "time", "date", "digits")

    def _signed_entry(self, field=None):
        """Does this field take a minus sign?

        "If the value is negative, press the +/- key so that a minus (-) sign
        appears on the display" is Tank Tilt, and the pressure offset screens
        say the same. A clock does not have a negative half.
        """
        f = self.cur_field() if field is None else field
        if (f or {}).get("kind") not in ("int", "float"):
            return False
        low = f.get("min")
        return low is None or low < 0

    def _numeric_entry(self, field=None):
        """Is this screen asking for a number?

        "When a numeric value is required, the keys provide only a numeric
        function." So no multi-tap on a limit, a time, a packed field, a
        volume adjustment or a serial number.
        """
        f = self.cur_field() if field is None else field
        if (f or {}).get("kind") in self.NUMERIC_KINDS:
            return True
        step = self.cur_step() or {}
        if step.get("adjust") or step.get("point") in ("height", "volume"):
            return True
        if step.get("dlv") in ("insert", "date", "time"):
            return True
        console = step.get("console")
        if console:
            from .console import FIELDS
            spec = FIELDS.get(console, {})
            if spec.get("number") or spec.get("digits"):
                return True
            if spec.get("kind") == "consoletextdev":
                # "VMC SERIAL NUMBER / S/N : 005830"
                return True
        return False

    def _edit_seed(self):
        """What CHANGE puts in the buffer: the value, in the form you type it.

        A clock reads "2:00 AM" and a date reads "12/25/2024", but neither is
        what the keypad sends: those screens take digits. So the seed for a
        packed field is the digits behind it, and everything else seeds with
        exactly what the screen was already showing.
        """
        f = self.cur_field() or {}
        kind = f.get("kind")
        if kind not in ("time", "date", "digits"):
            return self._shown()
        code = self.cur_code() or ""
        raw = self.console.values.get(code.upper())
        if raw is not None:
            body = fieldio.body_of(code, raw)
            part = f.get("part")
            if part:
                body = body[part[0]:part[0] + part[1]]
            body = body.strip()
            if body[:1].isdigit():
                return body
        if kind == "date":
            # A date is the one field that does NOT keep its value: on real
            # hardware CHANGE blanks it to a template and you fill the
            # template in. A time, recorded fifteen seconds later on the same
            # console, keeps every digit you do not type over, so this is a
            # property of the field and not of the key.
            return ""
        if f.get("code") == "S50100":
            # the clock is never blank: it is whatever the console is keeping
            return time.strftime("%I%M", self.console.now())
        return ""

    def _time_field(self):
        """Is the screen in front of you asking for a time of day?"""
        return (self.cur_field() or {}).get("kind") == "time"

    def _seed_meridiem(self):
        """AM or PM, off whatever the field holds now.

        The stored value is twenty-four hour; the screen and the keypad are
        both twelve hour with a half-of-day beside them, so entering a time
        starts from the half the console is already showing.
        """
        code = self.cur_code() or ""
        raw = self.console.values.get(code.upper())
        f = self.cur_field() or {}
        body = ""
        if raw is not None:
            body = fieldio.body_of(code, raw)
            part = f.get("part")
            if part:
                body = body[part[0]:part[0] + part[1]]
        body = body.strip()
        if len(body) >= 2 and body[:2].isdigit():
            hour = int(body[:2])
        else:
            hour = self.console.now().tm_hour
        return "PM" if hour >= 12 else "AM"

    def _change_into(self, value):
        """CHANGE on a field that holds a value.

        First press: the value stays and the cursor goes on its first
        character, which is what the manual's correction procedure needs,
        "use the arrow keys to move the cursor to the incorrect character,
        press CHANGE and enter the correct character". Pressing CHANGE again
        rubs the field out: "(To erase a label press CHANGE again.)"
        """
        if self.editing:
            self.buf, self.cur, self._tap = "", 0, None
            return
        if self._time_field():
            self.meridiem = self._seed_meridiem()
        self._begin_edit("" if value is None else str(value))

    def _delivery_value(self, step):
        """What a Delivery Maintenance screen has in it already."""
        what = step.get("dlv")
        if what in ("date", "time", "insert", "insertbol"):
            return self._insert.get(what, "")
        record = self._delivery()
        if record is None:
            return ""
        if what == "bol":
            return record.bol or ""
        return "" if record.ticket is None else f"{record.ticket:.0f}"

    def k_enter(self):
        self._keyed()
        if self.isdflow:
            st = self.isdflow
            c = self.console
            if st["state"] == "insufficient":
                # Retry? ENTER re-arms the window
                self._isdflow_start("automap")
            elif st["state"] == "select":
                hoses = c.isd_hoses() + [(0, "", "NON VAPOR RECOVERY HOSE")]
                d, _fp, _label = hoses[st["idx"] % len(hoses)]
                if d:
                    c.isd_hose_map[d] = st["meter"]
                    grade = c.setting("evr_hose_label", d, "UNASSIGNED")
                    self.isdflow = {"state": "result", "meter": st["meter"],
                                    "fp": c.meters.get(st["meter"], 0),
                                    "hose": d, "grade": grade[:6]}
                    self.log(f"-- auto map: meter {st['meter']} -> hose {d}")
                else:
                    self.isdflow = None
                    self.log("-- auto map: non vapor recovery hose")
            elif st["state"] == "product":
                meters = sorted(c.meters) or [0]
                st.update(state="assignhose",
                          meter=meters[st["idx"] % len(meters)], idx=0)
            elif st["state"] == "assignhose":
                hoses = c.isd_hoses() or [(0, "", "")]
                d, _fp, _label = hoses[st["idx"] % len(hoses)]
                if d:
                    c.isd_hose_map[d] = st["meter"]
                    self.log(f"-- manual map: meter {st['meter']} "
                             f"-> hose {d}")
                self.isdflow = None
            elif st["state"] == "clear_confirm":
                c.isd_clear_hose(st["hose"])
                st.update(state="cleared")
                self.log(f"-- hose {st['hose']} setup cleared")
            self._render()
            return
        e_flow = self.cur_step() if MODES[self.mode] == "SETUP" else None
        if e_flow is not None and e_flow.get("isdflow"):
            self._isdflow_start(e_flow["isdflow"])
            return
        if self.isd_override:
            if self.isd_override == "enter":
                self.isd_override = "no"
            elif self.isd_override == "yes":
                # "beeper shuts off, dispensing resumes"; the alarm light
                # and message stay until the alarm clears
                self.console.isd_do_override()
                self.console.silenced = True
                self.isd_override = None
                self.log("-- ISD shutdown OVERRIDDEN and logged; "
                         "dispensing resumes, the alarm stands")
            self._render()
            return
        if self.boot_restore:
            if self.boot_restore == "yes":
                self.boot_restore = "step"
                self._render()
            return
        if self.locked:
            if self.buf == self.console.security_code():
                self.locked = False
                self.buf = ""
                self._flash("CODE ACCEPTED")
            else:
                self.buf = ""
                self.mode = MODES.index("NORMAL")
                self.locked = False
                self._entered = False
                self._flash("INVALID SECURITY" + chr(10) + "CODE")
            return
        e0 = self.cur_step()
        if (MODES[self.mode] == "DIAGNOSTIC" and e0 is not None
                and e0.get("live") == "line_pressure"):
            # ENTER on the pressure screen starts the Gross test on the line
            # that screen is showing. It flashes what it did and then goes
            # straight back to the screen rather than holding a confirmation
            # box over it, because the whole point of starting a test from
            # here is watching this screen while it runs: the pressure on it
            # IS the test.
            kind = self._diag_kind()
            said = self.console.start_line_test(kind, self.device)
            self.log(f"-- diag: 3.0 gph test on {kind} line "
                     f"{self.device}: {said.lower()}")
            # No flash. A console does not announce this: the screen already
            # says what happened, it goes to RUNNING PUMP and the pressure
            # climbs to the pump, and a message over the top of that is a
            # message over the top of the only thing worth looking at.
            self._render()
            return
        if e0 is not None and e0.get("act"):
            if not self.armed:
                self._flash("NOTHING TO DO")
                return
            self.armed = False
            said = self.console.diag_action(e0["act"], self.device)
            self.log(f"-- diag {e0['act']}: {said.lower()}")
            self.confirm = [said[:COLS], CONT_STEP]
            self._render()
            return
        parent = self._diag_children()
        if parent is not None:
            # "PRESS <ENTER>": and down a level you go
            self.sub = parent
            self.step = 0
            self.confirm = None
            self._render()
            return
        e = self.cur_step()
        if self._chart_locked(e):
            if self.buf == self.console.chart_code:
                self.chart_open = True
                self.buf = ""
                self._flash("PASSCODE ACCEPTED")
            else:
                self.buf = ""
                self._flash("INVALID PASSCODE")
            return
        if e is not None and e.get("console"):
            self._enter_console(e)
            return
        if e is not None and e.get("point") in ("height", "volume"):
            self._enter_point(e["point"])
            return
        if e is not None and e.get("profile"):
            self._enter_profile()
            return
        if e is not None and e.get("archive"):
            # "press CHANGE, then ENTER. The system confirms your choice ...
            # The system asks you to reconfirm ... ARE YOU SURE?: NO"
            if not self.armed:
                self._flash("NOTHING TO DO")
                return
            label = e["text"].upper()
            if not self.sure:
                self.confirm = [f"{label}: YES"[:COLS], CONT_STEP]
                self.sure = True
                self.armed = False
            else:
                self.confirm = ["ARE YOU SURE?: YES", CONT_STEP]
                self._archive_pending = e["archive"]
            self._render()
            return
        if self._live_mode() and e is not None:
            if e.get("runtest"):
                # A results screen is a per-line screen, and ENTER on the line
                # you are looking at starts the test whose result it shows.
                # The Operator's Manual routes a manual test through START
                # LINE PRESSURE TEST instead; this is the same engine, reached
                # from the line rather than from the function.
                kind, rate_key = e["runtest"].split(":")
                said = self.console.leaks.start(kind, self.device, rate_key)
                self.log(f"-- {rate_key} test on {kind} {self.device}: {said}")
                self._flash(f"{self.console.lines.code(kind)} {self.device}: "
                            + rate_key.upper() + chr(10) + said)
                return
            if e.get("dlv"):
                self._enter_delivery(e)
                return
            if e.get("adjust"):
                self._enter_adjustment(e)
                return
            if e.get("vmc") == "head":
                # "Press ENTER: x #: (S/N) SIDE A / STATUS: IDLE"
                self.step = 1
                self._render()
                return
            if e.get("action"):
                if MODES[self.mode] == "RECONCILIATION" and not self.armed:
                    self._flash("NOTHING TO DO")
                    return
                self.armed = False
                self._flash(self._run_action(e["action"]))
                return
            if e.get("entry") and self.editing:
                self._accept_entry(e)
                return
            return
        code, f = self.cur_code(), self.cur_field()
        if not self.editing or not code:
            return
        if (f or {}).get("kind") == "slots":
            wires = f.get("slots") or 4
            base = ((self.device - 1) // wires) * wires
            self.console.set_slots(f["code"][1:4], self.buf, base)
            self.confirm = [f"SLOT #: {self.buf}"[:COLS],
                            CONT_STEP]
            self.editing, self.buf = False, ""
            self._refresh_site()
            self._render()
            return
        typed = self.buf
        if self._time_field() and self.meridiem:
            # the keypad enters a twelve hour clock and the arrows say which
            # half of the day, so both go to the encoder
            typed = f"{typed} {self.meridiem}"
        try:
            data = fieldio.encode(f or {}, code, typed,
                                  self.console.values.get(code.upper()))
        except ValueError as e:
            self._flash("INVALID ENTRY" + chr(10) + str(e)[:COLS])
            self.editing = False
            self.buf = ""
            return
        if data is None:
            self._flash("NOTHING ENTERED")
        else:
            self.console.values[code.upper()] = data
            if code.upper().startswith("S501"):
                # SET TIME is the console's clock, not a stored number
                self.console.set_clock()
            self.console.save()
            self._refresh_site()
            # "PRODUCT CODE: X / PRESS <STEP> TO CONTINUE": the console
            # holds the confirmation until you step off it.
            step = self.cur_step()
            shown = fieldio.decode(f, code, data) if f else self.buf
            if self._is_label_step(step):
                head = f"{self._device_code()}{self.device}: {shown}"
            else:
                # the confirmation is the screen's own second line, held: the
                # manual shows "TIME: XX:XX XM" over CONT_STEP
                words = step["text"].split("(")[0].strip().upper()
                _head, label = self._setup_context(step, words)
                head = self._second(label, shown)
            self.confirm = [head[:COLS], CONT_STEP]
        self.editing = False
        self.buf = ""

    # ---- starting and stopping tests from the panel ------------------------
    def _accept_entry(self, step):
        """A number typed at a step that wants one, in its own range."""
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        if not text.isdigit():
            self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
            return
        value = int(text)
        lo, hi = step.get("min"), step.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            self._flash("INVALID ENTRY" + chr(10) + f"{lo} TO {hi} HOURS")
            return
        self.sel[step["entry"]] = str(value)
        self._flash("ENTERED" + chr(10) + f"{value} HOURS")

    def _enter_adjustment(self, step):
        """"Enter the total positive or negative adjustment volume in gallons".

        The console confirms it the way it confirms everything else, and the
        volume lands in the period the operator was looking at, and in the
        day and the period that contain it.
        """
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        try:
            gallons = float(text)
        except ValueError:
            self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
            return
        kind, _previous = self._recon_period()
        self.console.bir.adjust(self.device, gallons, kind)
        self.console.save()
        word = self._recon_word(step)
        self.confirm = [f"{word}: {gallons:.0f}"[:COLS],
                        CONT_STEP]
        self.log(f"-- {kind} adjustment {gallons:+.0f} gallons on tank "
                 f"{self.device}")
        self._render()

    def _test_devices(self, kind):
        """Which devices the SELECT ALL/SINGLE step is pointing at."""
        scope = self.sel["scope" if kind == "tank" else "line_scope"]
        if scope.startswith("SINGLE"):
            return [self.device]
        if kind == "tank":
            return sorted(self.console.tank_level) or [self.device]
        return list(range(1, max(self.console.capacity(kind), 1) + 1))

    def _rate_key(self, kind, step):
        """The engine's name for the rate the panel is showing."""
        label = self.sel["rate" if kind == "tank" else "line_rate"]
        for choice in step.get("choices") or []:
            if isinstance(choice, list) and choice[0] == label:
                return choice[1]
        return {"3.0 GPH": "gross", "0.2 GPH": "periodic",
                "0.1 GPH": "annual"}.get(label, "periodic")

    def _run_action(self, action):
        verb, kind = action.split(":")
        if verb == "close":
            # "Manual Shift lets you close the shift and generate a Shift
            # Reconciliation Report."
            if not self.console.licensed("bir"):
                return "BIR NOT INSTALLED"
            rows = self.console.bir.close(kind)
            self.paper_out(printer.reconcile(self.console, kind=kind,
                                             previous=True))
            self.log(f"-- {kind} closed on {len(rows)} tank(s)")
            return f"{kind.upper()} CLOSED" + chr(10) + f"{len(rows)} TANK(S)"
        steps = self.steps()
        rate_step = next((s for s in steps if s.get("sel", "").endswith("rate")),
                         {})
        if verb == "sump":
            # 576013-610 Rev AC p.87 and p.89: ENTER on each of these picks a
            # sensor and then runs it. The controls behind them are function
            # codes 099, 09A and 09B, which this console already answers.
            what = {"start": "sump_start", "height": "sump_height",
                    "stop": "sump_stop"}[kind]
            self.console.control_device(what, self.device)
            self.console.save()
            said = {"start": "FILL SUMP", "height": "MEASURING HEIGHT",
                    "stop": "TEST ABORTED"}[kind]
            gap = "" if kind in ("start", "height") else " "
            self.confirm = [f"s {self.device}:{gap}{said}"[:COLS], CONT_STEP]
            return None
        if verb == "stop":
            done = [self.console.leaks.stop(kind, d)
                    for d in self._test_devices(kind)]
            stopped = sum(1 for d in done if d != "NO TEST RUNNING")
            return (f"{stopped} TEST(S)" + chr(10) + "STOPPED") if stopped                 else ("NO TEST" + chr(10) + "RUNNING")
        rate_key = self._rate_key(kind, rate_step)
        if rate_key == "purge":
            # "Air Purge purges air from the VLLD Controller by performing six
            # consecutive VLLD Controller 3.0 gph selftests"
            for dev in self._test_devices(kind):
                self.console.leaks.air_purge(dev)
            self.log(f"-- air purge on {len(self._test_devices(kind))} line(s)")
            return "AIR PURGE DONE" + chr(10) + "6 SELFTESTS"
        hours = float(self.sel["hours"]) if kind == "tank" else None
        manual = kind == "tank" and self.sel["stop_mode"].startswith("MANUAL")
        started = 0
        for dev in self._test_devices(kind):
            if self.console.leaks.start(kind, dev, rate_key, hours,
                                        manual) == "TEST STARTED":
                started += 1
        self.log(f"-- {rate_key} test started on {started} {kind}(s)")
        return (f"{started} TEST(S) STARTED" + chr(10)
                + (f"{hours:.0f} HOURS" if hours else rate_key.upper()))             if started else ("NO TEST STARTED" + chr(10) + "CHECK DEVICE")

    def _enter_console(self, step):
        """Store one of the settings the console keeps for itself."""
        from .console import FIELDS
        f = FIELDS.get(step["console"], {})
        text, kind = self.buf.strip(), f.get("kind")
        self.editing, self.buf = False, ""
        console = self.console
        if kind == "chartcode":
            digits = "".join(c for c in text if c.isdigit())
            if len(digits) != 6:
                self._flash("INVALID ENTRY" + chr(10) + "ENTER 6 DIGITS")
                return
            on = console.set_chart_code(digits)
            self.chart_open = on         # whoever set it knows it
            self.confirm = ["CODE: ******" if on else "CODE: 000000",
                            CONT_STEP]
        elif kind == "consolefloat":
            try:
                getattr(console, f["which"])[self.device] = float(text)
            except ValueError:
                self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
                return
            console.record_chart_change(self.device)
            console.save()
            self.confirm = [f"{step['text'].upper()}: {text}"[:COLS],
                            CONT_STEP]
        elif kind == "pmc_threshold":
            try:
                value = float(text.replace("IWC", "").strip())
            except ValueError:
                self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
                return
            if not console.set_pmc_threshold(f["which"], value):
                # "-8 < off < on < +3"
                self._flash("OUT OF RANGE" + chr(10) + "-8 < OFF < ON < +3")
                return
            off, on = console.pmc_thresholds()
            shown = off if f["which"] == "off" else on
            self.confirm = [f"{step['head']}"[:COLS],
                            f"IWC {shown:+06.3f}"[:COLS]]
        elif kind == "setting":
            device = self.device if f.get("scope") == "device" else 0
            allowed = f.get("choices")
            number = f.get("number")
            if number:
                if not text.isdigit() or not (number[0] <= int(text)
                                              <= number[1]):
                    self._flash("INVALID ENTRY" + chr(10)
                                + f"{number[0]} TO {number[1]}")
                    return
                text = text.rjust(int(f.get("width") or 0), "0")
            elif allowed and text.upper() not in allowed:
                self._flash("INVALID ENTRY" + chr(10)
                            + "/".join(allowed)[:COLS])
                return
            elif f.get("maxlen") and len(text) > int(f["maxlen"]):
                self._flash("INVALID ENTRY" + chr(10)
                            + f"MAX {f['maxlen']} CHARS")
                return
            console.set_setting(f["which"], text.upper(), device)
            console.save()
            prompt = f.get("prompt") or ""
            self.confirm = [f"{prompt} {text.upper()}".strip()[:COLS],
                            CONT_STEP]
        elif kind == "consoletextdev":
            store = getattr(console, f["which"])
            if text:
                store[self.device] = text[:int(f.get("maxlen") or 20)]
            else:
                store.pop(self.device, None)     # entering nothing removes it
            console.save()
            self.confirm = [f"S/N : {text}"[:COLS], CONT_STEP]
        else:
            setattr(console, f["which"], text[:int(f.get("maxlen") or 20)])
            console.save()
            self.confirm = [f"{step['text'].upper()}: {text}"[:COLS],
                            CONT_STEP]
        self._render()

    def _enter_point(self, which):
        """A strapped height, then the volume at it: one chart point.

        "Press CHANGE, enter the height ... Press STEP ... enter the volume at
        this height", and the pair joins the tank's 50 point chart.
        """
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        try:
            value = float(text)
        except ValueError:
            self._flash("INVALID ENTRY" + chr(10) + "NUMBERS ONLY")
            return
        self._point[which] = value
        if which == "height":
            self.confirm = [f"HEIGHT : {value:g}"[:COLS],
                            CONT_STEP]
            self._render()
            return
        height = self._point.get("height")
        if height is None:
            self._flash("ENTER A HEIGHT" + chr(10) + "FIRST")
            return
        n = self.console.add_chart_point(self.device, height, value)
        self._point.clear()
        self.confirm = [f"{height:g} INCH VOL : {value:g}"[:COLS],
                        f"{n} OF 50 POINTS"]
        self._render()

    def _enter_profile(self):
        """Choose a tank profile, and say goodbye to the old one's volumes.

        "Changing profile selection will erase the previously entered 50 point
        profile!": so the console asks before it does that.
        """
        from .console import Console as _C
        tank = self.device
        wanted = self._profile_pending
        if wanted:
            self._profile_pending = None
            self.editing, self.buf = False, ""
            if not self.armed:
                self._flash("PROFILE UNCHANGED")
                return
            self.armed = False
            erased = self.console.set_tank_profile(tank, wanted)
            self._refresh_site()
            self.confirm = [f"TANK PROFILE: {_C.PROFILE_NAME[wanted]}"[:COLS],
                            CONT_STEP]
            self.log(f"-- tank {tank} profile {_C.PROFILE_NAME[wanted]}, "
                     f"{erased} value(s) erased")
            self._render()
            return
        chosen = next((k for k, v in _C.PROFILE_NAME.items()
                       if v == self.buf), None)
        self.editing, self.buf = False, ""
        if chosen is None or chosen == self.console.tank_profile(tank):
            self._flash("PROFILE UNCHANGED")
            return
        if self.console.profile_erasable(tank, chosen):
            self._profile_pending = chosen
            self.armed = False
            self._render()
            return
        self.console.set_tank_profile(tank, chosen)
        self._refresh_site()
        self.confirm = [f"TANK PROFILE: {_C.PROFILE_NAME[chosen]}"[:COLS],
                        CONT_STEP]
        self._render()

    # "This process may take a minute or so." A minute is a long time to sit
    # in front of a bench, so the simulator takes seconds; what matters is
    # that the console is BUSY and does not answer the keypad while it is.
    ARCHIVE_SECONDS = 3.0

    def _run_archive(self, what):
        """Save, restore or clear. The console goes away while it works."""
        self.log(f"-- archive {what}: working")
        if self.ARCHIVE_SECONDS <= 0:
            self._finish_archive(what)
            return
        self.busy_until = time.time() + self.ARCHIVE_SECONDS
        self._render()
        self.after(int(self.ARCHIVE_SECONDS * 1000),
                   lambda: self._finish_archive(what))

    def _finish_archive(self, what):
        console = self.console
        self.busy_until = 0.0
        if what == "save":
            n = console.archive_save()
            msg = f"{n} VALUE(S) SAVED" if n >= 0 else "SAVE FAILED"
        elif what == "restore":
            n = console.archive_restore()
            msg = f"{n} VALUE(S) RESTORED" if n >= 0 else "NO ARCHIVE SAVED"
            if n >= 0:
                # "The system also prints a complete listing of all restored
                # setup data."
                self.paper_out(printer.setup(console))
                self.log("-- PRINT: restored setup data")
        else:
            n = console.archive_clear()
            msg = (f"{n} VALUE(S) CLEARED" if n >= 0 else "CLEAR FAILED")
        self.log(f"-- archive {what}: {msg.lower()}")
        # "When the save is completed, the system returns the original
        # message: ARCHIVE UTILITY / PRESS <STEP> TO CONTINUE."
        self.step = HEADER
        self.armed = self.sure = False
        self._refresh_site()
        self._flash("ARCHIVE UTILITY" + chr(10) + msg[:COLS])

    def k_alarm(self):
        """ALARM/TEST, which does less than people expect.

        "ALARM/TEST silences the alarm. It does not clear the alarm message
        from the display or disable the alarm." A condition that is still true
        stays on the display and keeps its light; only an alarm whose cause has
        already been corrected leaves the screen when you acknowledge it.
        """
        self._keyed()
        # "From an ISD shutdown alarm display, press ALARM/TEST 3 times"
        # (577013-800 p.35 Fig 23): the third press opens the override.
        if self.console.isd_shutdown_active() and self.isd_override is None:
            self._alarm_presses += 1
            if self._alarm_presses >= 3:
                self._alarm_presses = 0
                self.isd_override = "enter"
                self._render()
                return
        else:
            self._alarm_presses = 0
        fn = self.cur_function()
        if MODES[self.mode] == "NORMAL" and self._entered and fn                 and fn["function"] == "TEST OUTPUT RELAYS":
            # "This key also activates and deactivates output relays when
            # using the Output Relay Test function."
            n = self.device
            self.console.relays[n] = not self.console.relays.get(n)
            state = "ON" if self.console.relays[n] else "OFF"
            self.log(f"-- relay {n} switched {state} by ALARM/TEST")
            self._flash(f"RELAY {n} {state}"
                        + chr(10) + "PRESS ANY KEY FOR SETUP")
            return
        # "If your system has a printer, it will print an alarm or warning
        # report when this button is pressed."
        self.paper_out(printer.alarms(self.console))
        shown, still, cleared = self.console.acknowledge(self.mt_key)
        if not shown:
            self._flash("ALARM TEST" + chr(10) + "ALL FUNCTIONS NORMAL")
            return
        if self.console.has("mt") and not self.mt_key and shown != cleared:
            self._flash(f"{shown} PROTECTED ALARM(S)"
                        + chr(10) + "INSERT KEY IN PORT")
        elif still and cleared:
            self._flash(f"{cleared} CLEARED, {still} STILL"
                        + chr(10) + "ACTIVE - CORRECT CAUSE")
        elif still:
            self._flash(f"{still} ALARM(S) SILENCED"
                        + chr(10) + "STILL ACTIVE")
        else:
            self._flash(f"{cleared} ALARM(S)" + chr(10) + "ACKNOWLEDGED")

    # =====================================================================
    # the printer
    # =====================================================================
    def _set_live_paper(self):
        """Whether paper hangs out of the console, or only onto the roll.

        Everything printed is on the roll below either way. The slip coming
        out of the slot is the thing worth watching and also a large thing to
        have across the bench, so it can be turned off.
        """
        if not self.live_paper.get():
            self.cut_paper()
        self.log("-- console paper: "
                 + ("shown" if self.live_paper.get() else "hidden"))

    def _set_paper(self):
        """Take the roll out, or put a new one in.

        With no paper the console does not print, and it does not pretend
        to either: "Printer out of Paper" is a system alarm, so the light
        comes on, the display carries it, and it is in the status report a
        tool reads until somebody loads a roll.
        """
        self.console.out_of_paper = bool(self.no_paper.get())
        if self.console.out_of_paper:
            self.cut_paper()
            self._flash("PRINTER OUT OF PAPER" + chr(10) + "LOAD PAPER")
        else:
            self._flash("PAPER LOADED")
        self.log("-- printer: "
                 + ("out of paper" if self.console.out_of_paper else "loaded"))
        self._draw_printer()
        self._render()

    def paper_out(self, lines):
        """Feed a report out of the printer.

        Onto the roll in the bench, which is the whole history, and onto the
        slip hanging out of the slot, which is what has not been torn off yet.
        A second report while the first is still hanging does not start a new
        slip, a roll has no idea where one report ends and the next begins,
        which is the reason the CUT button exists.

        With no paper in it nothing comes out at all, which is the point of
        the alarm that says so.
        """
        if self.console.out_of_paper:
            self.log("-- nothing printed: out of paper")
            return
        folded = printer.fit(lines)
        try:
            self.paper.insert("end", "\n".join(folded) + "\n\n")
            self.paper.see("end")
        except Exception:
            pass
        if self.live_paper.get():
            self._slip_feed(folded + [""])

    def _slip_feed(self, lines):
        """Put those lines on the paper hanging out of the slot.

        Wound so the TOP of what has just been printed is at the slot, which
        is where somebody standing at the console would start reading it.
        """
        if self.slip is None:
            self.slip = self._build_slip()
        if not self.slip_out:
            self.slip_text.delete("1.0", "end")
            self._slip_lines = 0
            self.slip_out = True
        started = self._slip_lines + 1
        self.slip_text.insert("end", "\n".join(lines) + "\n")
        self._slip_lines += len(lines) + 1
        self._place_slip()
        self.slip_text.see(f"{self._slip_lines}.0")
        self.slip_text.see(f"{started}.0")
        self._draw_tear()

    # ---- reconciliation ----------------------------------------------------
    RECON_KIND = {"SHIFT": "shift", "DAILY": "daily", "WEEKLY": "weekly",
                  "PERIODIC": "periodic"}

    def _recon_period(self):
        """Which period the mode is looking at, and which end of it.

        The panel's own choices ARE the report: REPORT TYPE picks shift,
        daily or periodic; SELECT picks daily, weekly or periodic on the
        variance reports; CURRENT or PREVIOUS picks the running one or the
        last one closed.
        """
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        if name in ("DISPLAY AND PRINT", "MANUAL SHIFT CLOSE"):
            word = self.sel.get("report_type", "SHIFT")
        elif name == "MANUAL ADJUSTMENTS":
            word = self.sel.get("adjust_type", "SHIFT")
        else:
            word = self.sel.get("variance_period", "DAILY")
        kind = self.RECON_KIND.get(str(word), "shift")
        previous = str(self.sel.get("which", "CURRENT")) == "PREVIOUS"
        return kind, previous

    def _recon_sync(self):
        """The console reads its screens off whatever the panel is pointed at."""
        kind, previous = self._recon_period()
        self.console.recon_kind = kind
        self.console.recon_previous = previous

    def _recon_word(self, step):
        """The screen's own wording, which follows the period chosen.

        "SELECT SHIFT: CURRENT" on a shift report is "SELECT DAY: (Date)" on
        a daily one, and the manual draws both.
        """
        text = step["text"].split("(")[0].strip().upper()
        by = step.get("text_by") or {}
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        if name in ("DISPLAY AND PRINT",):
            word = str(self.sel.get("report_type", "SHIFT"))
        elif name == "MANUAL ADJUSTMENTS":
            word = str(self.sel.get("adjust_type", "SHIFT"))
        else:
            word = str(self.sel.get("variance_period", "DAILY"))
        said = by.get(word, text).upper()
        if step.get("adjust_prefix") and "%S" in said:
            # "(Selected) SHFT ADJ VOL" and "(Date) ADJ VOL": the manual's
            # two placeholders are the shift you chose and the day you chose
            # (576013-610 Rev AC p.28-19 and p.28-20; function codes 79B and
            # 79C answer "CURRENT SHFT ADJ" and "MAR 26  ADJ VOL")
            if word == "DAILY":
                fill = time.strftime("%b %d", self.console.now()).upper()
            else:
                fill = str(self.sel.get("which", "CURRENT"))
            said = said.replace("%S", fill)
        return said

    def _recon_head(self, step):
        """Line one, which in this chapter is the choice you made last.

        The manual walks it that way all the way down: "DISPLAY AND PRINT /
        REPORT TYPE: SHIFT", then "REPORT TYPE: SHIFT / PROD 1: (Product)",
        then "PROD 1: (Product) / SELECT SHIFT: CURRENT".
        """
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        which = step.get("head")
        label = self.console.text("602", self.device) or ""
        if which == "prod":
            return f"PROD {self.device}:{label}".rstrip()
        if which == "tank":
            return f"T {self.device}: {label}".rstrip()
        if which == "type":
            return f"REPORT TYPE: {self.sel.get('report_type', 'SHIFT')}"
        if which == "select":
            return f"SELECT: {self.sel.get('variance_period', 'DAILY')}"
        return name

    def _recon_lines(self, step, _text):
        """One Reconciliation Mode screen."""
        self._recon_sync()
        head = self._recon_head(step)
        word = self._recon_word(step)
        if step.get("product"):
            label = self.console.text("602", self.device) or ""
            return [head[:COLS],
                    f"PROD {self.device}:{label}".rstrip()[:COLS]]
        if step.get("adjust"):
            if self.editing:
                return [head[:COLS], f"{word}: {self._edit_text()}"[:COLS]]
            kind, _previous = self._recon_period()
            row = self.console.bir.row(self.device, kind, False)
            held = row["adjust"] if row else 0.0
            return [head[:COLS], f"{word}: {held:.0f}"[:COLS]]
        if step.get("action"):
            return [head[:COLS],
                    f"{word}: {'YES' if self.armed else 'NO'}"[:COLS]]
        if step.get("sel"):
            value = str(self.sel.get(step["sel"], ""))
            if word.startswith("SELECT DAY"):
                # "SELECT DAY: (Date)": the day itself, not the word
                value = self.console.recon_reading("close_date", self.device)
            return [head[:COLS], f"{word}: {value}"[:COLS]]
        live = self.console.recon_reading(step["live"][6:], self.device)             if step.get("live") else ""
        return [head[:COLS], f"{word}: {live.strip()}"[:COLS]]

    def _recon_report(self, name, step):
        """PRINT in Reconciliation Mode, which is the report you are standing
        in, for every product or for the one selected."""
        console = self.console
        self._recon_sync()
        kind, previous = self._recon_period()
        device = self.device if (step or {}).get("print_scope") == "device"             else None
        tanks = [device] if device else None
        fn = self.cur_function()
        which = (fn or {}).get("print")
        report = {"reconcile": printer.reconcile,
                  "delivery_variance": printer.delivery_variance,
                  "book_variance": printer.book_variance,
                  "variance_analysis": printer.variance_analysis}.get(which)
        if report is None:
            return name, printer.status(console)
        return name, report(console, tanks, kind, previous)

    def _report(self):
        """What PRINT gives you here, which the manuals say screen by screen.

        The operating mode table annotates its functions and steps with what
        PRINT does at each, "(PRINT - Inventory for all tanks)", "(PRINT -
        Results for selected tank)": so a function prints all its devices
        and a step marked for one prints that one. In Setup, "to print a Setup
        Data Report, press the MODE key to display the Setup Mode main screen
        ... then press the PRINT key", and inside a function it is that
        function's own programming.
        """
        mode = MODES[self.mode]
        fn = self.cur_function()
        name = fn["function"] if fn else ""
        step = self.cur_step()
        console = self.console

        if mode == "SETUP":
            if self.step == MODE_SCREEN:
                return "SETUP DATA REPORT", printer.setup(console)
            return name, printer.setup(console, name)

        if mode == "DIAGNOSTIC":
            if self.step == MODE_SCREEN:
                # the console's own diagnostic is what the mode prints
                return "SYSTEM DIAGNOSTIC", printer.revision(console)
            return self._diagnostic_report(name, step)

        if mode == "RECONCILIATION":
            if self.step == MODE_SCREEN or not fn:
                # the mode's own screen prints the shift the site is running
                return "SHIFT RECONCILIATION", printer.reconcile(console)
            return self._recon_report(name, step)

        if not self._entered:
            # "press PRINT while the monitor is displaying the status message"
            return "INVENTORY REPORT", printer.inventory(console)
        kind = (fn or {}).get("print")
        device = self.device if (step or {}).get("print_scope") == "device"             else None
        return name, self._operating_report(kind, device, step)

    def _operating_report(self, kind, device=None, step=None):
        """One of the reports the operating mode table names."""
        console = self.console
        tanks = [device] if device else None
        step = step or {}
        if step.get("history"):
            # "press STEP to display Q #: PRESS PRINT FOR HISTORY"
            return printer.leak_history(console, step["history"], device)
        # "PRINT - Last load report for selected tank" on the load screen,
        # "Last 40 load reports" on the ones above it
        index = self.load if step.get("load") == "number" else None
        if kind is None:
            return printer.status(console)
        if kind.startswith("sensors:"):
            return printer.sensors(console, kind.split(":")[1], device)
        return {
            "inventory": lambda: printer.inventory(console, tanks),
            "deliveries": lambda: printer.deliveries(console, tanks),
            "loads": lambda: printer.loads(console, tanks, index),
            "fuel": lambda: printer.fuel(console, tanks),
            "shift": lambda: printer.shift(console, tanks),
            "csld": lambda: printer.csld(console, tanks),
            "tank_tests": lambda: printer.leak_tests(console, "tank", device),
            "plld_tests": lambda: printer.leak_tests(console, "plld", device),
            "wplld_tests": lambda: printer.leak_tests(console, "wplld", device),
            "vlld_tests": lambda: printer.leak_tests(console, "vlld", device),
            "isd:status": lambda: self._isd_panel_report("status"),
            "isd:daily": lambda: self._isd_panel_report("daily"),
            "isd:monthly": lambda: self._isd_panel_report("monthly"),
            "relay_status": lambda: printer.relays(console, "pumpmon"),
            "relay_setup": lambda: printer.relays(console, "relay"),
            "vmc": lambda: printer.vmc(console, device),
        }.get(kind, lambda: printer.status(console))()

    def _isdflow_lines(self):
        """The two display lines of the mapping flow, per 577013-800 Fig 8."""
        st = self.isdflow
        c = self.console
        kind = st["state"]
        if kind == "countdown":
            left = max(0, int(st["until"] - time.mktime(c.now())))
            return [f"DISPENSE NOW {left // 60:02d}:{left % 60:02d}",
                    "PRESS <STEP> TO CANCEL"]
        if kind == "insufficient":
            return ["Insufficient Data. Retry?", "PRESS <ENTER>"]
        if kind == "assigned":
            return [f"FP: {st['fp']}  M: {st['meter']}  "
                    f"Assigned H: {st['hose']}", "PRESS <STEP> TO CONTINUE"]
        if kind == "select":
            hoses = c.isd_hoses() + [(0, "", "NON VAPOR RECOVERY HOSE")]
            d, fp, label = hoses[st["idx"] % len(hoses)]
            if d:
                return [f"H: {d}  FP: {fp}  Label {label}"[:COLS],
                        "PRESS <ENTER>"]
            return ["NON VAPOR RECOVERY HOSE", "PRESS <ENTER>"]
        if kind == "result":
            return [f"FP: {st['fp']}  GRD: {st['grade']}  "
                    f"M: {st['meter']}  H: {st['hose']}"[:COLS],
                    "PRESS <STEP> TO CONTINUE"]
        if kind == "product":
            meters = sorted(c.meters) or [0]
            m = meters[st["idx"] % len(meters)]
            fp = c.meters.get(m, 0)
            return ["SELECT PRODUCT",
                    f"L{st['idx'] % len(meters) + 1:02d} B03 S01 "
                    f"FP{fp:02d} M{m:02d} P{fp:02d}"[:COLS]]
        if kind == "assignhose":
            hoses = c.isd_hoses() or [(0, "", "UNASSIGNED")]
            d, _fp, label = hoses[st["idx"] % len(hoses)]
            return ["ASSIGN HOSE", f"{d:02d}: {label}"[:COLS]]
        if kind == "clear_confirm":
            return [f"CLEAR HOSE {st['hose']} SETUP?",
                    "PRESS <ENTER> TO CONFIRM"]
        if kind == "cleared":
            return [f"HOSE {st['hose']} SETUP CLEARED",
                    "PRESS <STEP> TO CONTINUE"]
        return ["", ""]

    def _isdflow_start(self, which):
        """ENTER on one of the mapping steps opens its flow."""
        c = self.console
        if which == "addhose":
            # the new hose drops you onto its own FUEL POS LABEL screen
            n = c.isd_add_hose()
            self.device = n
            steps = self.steps()
            for i, st in enumerate(steps):
                if st.get("console") == "set.evr_fuel_pos":
                    self.step = i
                    break
            self.log(f"-- hose {n} added; enter its fuel position label")
            self._render()
            return
        if which == "clearhose":
            self.isdflow = {"state": "clear_confirm", "hose": self.device}
        elif which == "automap":
            # ten minutes of console time to dispense; the meters selling
            # on the bench ARE the dispense
            self.isdflow = {
                "state": "countdown",
                "until": time.mktime(c.now()) + 600,
                "start": dict(c.bir.totals)}
        elif which == "manualmap":
            self.isdflow = {"state": "product", "idx": 0}
        self._render()

    def _isdflow_poll(self):
        """The countdown runs on console time, and a finished dispense
        moves the flow on: enough fuel picks the hose list, less than half
        a gallon is the manual's Insufficient Data screen."""
        st = self.isdflow
        if not st or st["state"] != "countdown":
            return
        c = self.console
        now = time.mktime(c.now())
        sold = {m: c.bir.totals.get(m, 0.0) - st["start"].get(m, 0.0)
                for m in c.bir.totals}
        active = any(c.meter_flow.get(m, 0.0) > 0 for m in c.meters)
        best = max(sold, key=lambda m: sold[m], default=None)
        if best is not None and sold.get(best, 0) > 0 and not active:
            # the nozzle hung up: judge the dispense
            if sold[best] < 0.5:
                st.update(state="insufficient")
            elif best in c.isd_hose_map.values():
                hose = [h for h, m in c.isd_hose_map.items()
                        if m == best][0]
                st.update(state="assigned", meter=best,
                          fp=c.meters.get(best, 0), hose=hose)
            else:
                st.update(state="select", meter=best, idx=0)
            self._render()
            return
        if now >= st["until"]:
            self.isdflow = None
            self.log("-- auto map: no dispense inside the window")
        self._render()

    def _isd_panel_report(self, kind):
        """The three CP-201 panel reports, from the same machinery the
        serial V01/V02/V03 reports answer with -- one source, two ways out,
        which is what the hardware does too."""
        from . import wire as _wire
        import time as _t
        h = _wire.Handler(self.console, verbose=False)
        now = self.console.now()
        if kind == "status":
            since, monthly, head = None, False, "ISD STATUS"
        elif kind == "daily":
            since = _t.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                               0, 0, 0, 0, 1, -1))
            monthly, head = False, "ISD DAILY REPORT"
        else:
            since = _t.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 1, -1))
            monthly, head = True, "ISD MONTHLY REPORT"
        rows = h._isd_status_lines(since, monthly, head)
        if kind != "status":
            rows += h._isd_carb_lines()
        rows += h._isd_alarm_lines()
        return rows

    def _diagnostic_report(self, name, step):
        """Diagnostic prints what the screen in front of you says it does."""
        console = self.console
        line = (step or {}).get("text", "")
        if (step or {}).get("diagprint"):
            # "press STEP until the 3.0 Diag screen appears, and press Print"
            return name, printer.line_diag(console, self._diag_kind(),
                                           self.device, step["diagprint"])
        if name == "SYSTEM DIAGNOSTIC":
            # "press the PRINT key and the printer prints: SOFTWARE REVISION
            # LEVEL ..."
            return name, printer.revision(console)
        if name == "SERVICE REPORT":
            return name, printer.service_codes(console)
        if name == "ALARM HISTORY REPORT":
            # "SYSTEM ALARM HISTORY" is the console's own; every other screen
            # is headed with its device code, "T X:", "L X:", "Q X:"
            if line.startswith("SYSTEM"):
                return name, printer.alarm_history(console, system=True)
            return name, printer.alarm_history(console, line[:1])
        if name == "BIR DIAGNOSTICS":
            return name, printer.meters(console)
        if name.startswith("CSLD"):
            return name, printer.csld(console)
        if name.startswith("IN-TANK LEAK RESULT"):
            return name, printer.leak_tests(console, "tank", self.device)
        if name.startswith("ACCUCHART"):
            # "13. <Control-A> IB9400 AccuChart Calibration History" and
            # "17. <Control-A> I@B600 AccuChart Diagnostics - Calibration
            # Status" are what tech support asks for, so PRINT gives both.
            return name, (printer.accuchart(console, [self.device], "status")
                          + [""]
                          + printer.accuchart(console, [self.device],
                                              "history")[3:])
        if name.startswith("SMART SENSOR"):
            return name, printer.sensors(console, "smart")
        return name, printer.status(console)

    def k_print(self):
        self._keyed()
        title, lines = self._report()
        self.paper_out(lines)
        if self.console.out_of_paper:
            self._flash("PRINTER OUT OF PAPER" + chr(10) + "LOAD PAPER")
            return
        self.log(f"-- PRINT: {title}")
        self._flash("PRINTING" + chr(10) + title[:COLS])

    def k_paper(self):
        """PAPER FEED: blank paper, out of the slot, one line a press.

        Neither the Setup Manual nor the Operator's Manual describes this key
        in its key-function section, but the Operator's Quick Help, 576013-939
        Rev D, does: "PAPER FEED - Press to advance the paper through the
        printer." How far one press advances it is still nobody's statement;
        a line is the reading that makes the key useful.
        """
        self._keyed()
        if self.console.out_of_paper:
            self._flash("PRINTER OUT OF PAPER" + chr(10) + "LOAD PAPER")
            return
        if not self.live_paper.get():
            # the paper is being fed whether or not the bench is showing it
            self.live_paper.set(True)
        self._slip_feed([""])
        self._flash("PAPER FEED")

    def k_blue(self):
        """The blue Maintenance Tracker key.

        The manual's own sequence: the key reads DISABLED the first time and
        ENABLED after, then asks for the Contractor's ID key in the MT Comm
        card. Without the board there is nothing to log in to.
        """
        self._keyed()
        if not self.console.has("mt"):
            self._flash("MAINTENANCE TRACKER" + chr(10) + "NO MT COMM")
            return
        self.mt_key = not self.mt_key
        if self.mt_key:
            self._flash("MAINTENANCE TRACKER" + chr(10) + "LOGGED IN 004217")
        else:
            self._flash("MAINTENANCE TRACKER" + chr(10) + "KEY REMOVED")

    def k_white(self):
        self._keyed()
        self._flash("MAINT REPORT" + chr(10) + "NO HISTORY")

    def k_alnum(self, key):
        self._keyed()
        if self._chart_locked(self.cur_step()):
            if key.isdigit() and len(self.buf) < 6:
                self.buf += key
            elif key == "+":
                self.buf = self.buf[:-1]
            self._render()
            return
        if self.locked:
            if key.isdigit() and len(self.buf) < 6:
                self.buf += key
            elif key == "+":
                self.buf = self.buf[:-1]
            self._render()
            return
        if not self.editing:
            step = self.cur_step() or {}
            if step.get("load") == "number" and key in (",", "+"):
                # "Press the arrow keys to view the Tanker Load Report
                # for the previous or next load number". The right arrow
                # arrives as "," (same key as the decimal), not ".".
                records = self.console.loads.all(self.device)
                if records:
                    self.load = (self.load + (1 if key == "," else -1))                         % len(records)
                self._render()
            return
        f = self.cur_field() or {}
        if f.get("kind") == "slots":
            # The right-arrow key arrives as "," -- one key, arrow and
            # decimal both, and k_alnum is handed the "," face. This
            # branch used to test for "." and so the right arrow moved
            # nothing on TANK CONFIG while the left arrow worked.
            cells = max(len(self.buf.split()), 1)
            if key == ",":
                self.slot = (self.slot + 1) % cells
            elif key == "+":
                self.slot = (self.slot - 1) % cells
            self._render()
            return
        numeric = self._numeric_entry(f)
        if key in ("+", ",") and self._time_field():
            # "To change the time press CHANGE and enter the correct time.
            # Select either AM or PM by using the arrow keys." Both arrows,
            # and there are only two halves to a day, so either one swaps it.
            self.meridiem = "PM" if self.meridiem == "AM" else "AM"
            self._tap = None
            self._render()
            return
        if key == "+":
            # One key, two jobs. "The Left-Arrow key lets you move the cursor
            # to the left. The +/- is used to identify a positive or negative
            # value": so it is the sign key on a field that HAS a sign, and
            # the cursor key everywhere else. A date and a packed digit field
            # have no sign, so on those it walks the cursor back the way it
            # does on a label.
            if self._signed_entry(f):
                self._toggle_sign()
            else:
                self.cur = max(0, self.cur - 1)
                self._tap = None
        elif key == ",":
            # "The Right-Arrow key lets you advance the cursor to the right
            # when making alphanumeric entries ... The . (decimal) is used in
            # numeric entries as required."
            if f.get("kind") == "float":
                self._put(".")
            else:
                self.cur = min(len(self.buf), self.cur + 1)
                self._tap = None
        elif numeric or key == "0":
            self._put(key)
            self._tap = None
        else:
            seq = LETTERS.get(key, key)
            if (f.get("code") or "")[1:4] == MODEM_STRING_CODE:
                seq += MODEM_EXTRA.get(key, "")
            if self._tap and self._tap[0] == key and self._tap[2] == self.cur:
                i = (self._tap[1] + 1) % len(seq)
                self._retap(seq[i])
                self._tap = (key, i, self.cur)
            else:
                # "If the next character is on another key, you can press the
                # new key instead of the right-arrow key": a different key
                # takes the next position by itself.
                self._put(seq[0])
                self._tap = (key, 0, self.cur)
        self._render()

    # Bench widgets you can type into. A keystroke aimed at one of these is
    # the bench being operated, not the console's keypad being pressed.
    TYPING_WIDGETS = ("entry", "spinbox", "text", "combobox", "listbox")

    def _typing_on_the_bench(self):
        """Is the keyboard focus in a bench field rather than on the panel?

        Tk sends a key event to the focused widget and then up its bindtags to
        this window, so without this the leak-rate box shares every keystroke
        with the keypad: typing 60 walks the menu, and BACKSPACE, which is the
        BACKUP key, walks it backwards. The console's own keys are the buttons
        and the shortcuts, never a character typed into a bench box.
        """
        try:
            widget = self.focus_get()
        except (KeyError, tk.TclError):
            return False
        if widget is None or widget is self:
            return False
        name = widget.winfo_class().lower()
        return any(kind in name for kind in self.TYPING_WIDGETS)

    def _on_key(self, ev):
        if not self.console.powered:
            return
        if self._typing_on_the_bench() or self._busy():
            return
        m = {"m": self.k_mode, "f": self.k_function, "s": self.k_step,
             "b": self.k_backup, "c": self.k_change, "Return": self.k_enter,
             "t": self.k_tank, "p": self.k_print, "BackSpace": self.k_backup}
        if self.editing and (ev.char.isalnum() or ev.char in "-. /:"):
            kind = (self.cur_field() or {}).get("kind")
            if kind in ("int", "float", "time", "date", "digits")                     and not (ev.char.isdigit() or ev.char in "-./:"):
                return
            self._put(ev.char.upper())
            self._tap = None
            self._render()
            return
        if self.editing and ev.keysym in ("Left", "Right", "Home", "End"):
            if (self.cur_field() or {}).get("kind") == "slots":
                # a slots screen has positions, not a text cursor: the
                # keyboard arrows do what the console's arrow keys do
                if ev.keysym in ("Left", "Home"):
                    self.k_alnum("+")
                else:
                    self.k_alnum(",")
                return
            if ev.keysym == "Left":
                self.cur = max(0, self.cur - 1)
            elif ev.keysym == "Right":
                self.cur = min(len(self.buf), self.cur + 1)
            elif ev.keysym == "Home":
                self.cur = 0
            else:
                self.cur = len(self.buf)
            self._tap = None
            self._render()
            return
        fn = m.get(ev.keysym) or m.get(ev.keysym.lower())
        if fn:
            fn()
