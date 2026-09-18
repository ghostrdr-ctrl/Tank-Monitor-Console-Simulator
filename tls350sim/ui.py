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
"""One window: the console on top, the bench underneath.

LOOK. The display is a monochrome LCD, not a colour screen, pale grey-green
glass, near-black characters, a fixed 20x2 character cell, and the faint
"ghost" of every unlit segment behind the text, which is what makes an old
calculator or gauge display recognisable. The keys are the console's two
12-key pads: operating keys left, alphanumeric right, dark faces with light
legends, laid out in the manual's order.

The LAYOUT follows the manual's Figure 2-1 and its key descriptions. The
finish and the proportions are measured off two photographs of a real
console, a running one and the maker's own product shot, which agree with
each other: see the notes over the colours below. Tell me what is off and
it is a few constants at the top of this file.
"""
import os
import sys
import time
import weakref

import tkinter as tk
from tkinter import font as tkfont
from .facepanel import CURSOR, DotMatrixLCD, KeyCap, blend
from tkinter import ttk

from . import APP_NAME, DISCLAIMER, PUBLISHER, __version__
from . import fieldio, masks
from . import bench, exposed, paths, update, updateui, xport
from .clock import clock_date
from . import presets
from . import printer
from . import screens
from . import traffic as _traffic
from . import versions
from . import wirelists
from .console import (BAY_NAME, DAY_NAMES, DEVICE_WORD, FUNCTION_REQUIRES,
                      MODULE_BAY, MODULE_LABEL, MODULE_PART,
                      MODULES, MODULE_SHORT, SLOT_POSITIONS,
                      COMM_PORTS as CONSOLE_COMM_PORTS, SOFTWARE_MODULES,
                      SOFTWARE_NAME, describe_alarms,
                      front_panel_indicator)

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

# MODIFY TANK/METER MAP's row, which the paper draws as well as the glass.
# See `screens.MAP_CELLS`.
MAP_CELLS = screens.MAP_CELLS
MAP_BLANK = screens.MAP_BLANK

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
#
# The display is a monochrome character LCD, two lines of twenty-four, each
# character a 5 x 7 dot matrix. It is the reflective STN kind: a pale
# grey-green glass, dark indigo characters, and every cell a faint block
# standing off the glass, with the unlit dots only just there inside it.
# It is not a VFD, it is not light characters on dark glass, and it is not
# the yellow-green of a backlit module; passes before this one drew both.
# The colours are sampled from a photograph of a running console.
CASE = "#ececea"          # off-white enclosure
CASE_EDGE = "#c9c9c5"
BLUE = "#1c3f91"          # base pinstripes
LABEL = "#1a1a1a"         # the black legends beside the indicators
LCD_LIT = {"glass": "#a7af9f",   # the glass between the cells
           "cell": "#9ca59b",    # the block each character sits in
           "ghost": "#949e94",   # the dots that are not on
           "ink": "#1f2456"}     # the dots that are
LCD_DEAD = {"glass": "#a3a99b", "cell": "#999f96", "ghost": "#929991",
            "ink": "#929991"}    # no power: the same glass, nothing on it

# The strip across the top of the panel where a real console carries its
# maker's branding. Left bare here, but it keeps its height so that every
# dimension below it is unchanged.
BRAND_PLATE_H = 34
LCD_RIM = "#8f918a"       # the edge of the cut-out the glass sits in
LCD_SHADE = "#4c4e48"     # and the shadow its lip casts along the top
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

# What each mode calls itself on the glass, which is not its name here
# with " MODE" after it. `DIAG MODE` is fifteen drawings across five
# documents -- 576013-818 Rev AA p.1-3 and its Figure 6-2, 576013-610 Rev
# AC p.27-1 and ch.33, 576013-818 Rev AB, 577013-819 Rev F and 577013-937
# Rev J -- against a single `DIAGNOSTIC MODE`, which is a prose section
# heading in 576013-610 p.2-6 and not a screen. The other three are their
# full names, so deriving the word from the list got two right by accident
# and this one wrong for as long as it was derived.
#
# It matters more than a word: step 1 of every troubleshooting procedure in
# those guides is literally "press the MODE key until the front panel
# display reads DIAG MODE". See FIDELITY U10.
MODE_SCREEN_NAME = {"NORMAL": "MODE", "SETUP": "SETUP MODE",
                    "DIAGNOSTIC": "DIAG MODE",
                    "RECONCILIATION": "RECONCILIATION MODE"}
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

def enable_dpi_awareness():
    """Ask Windows to stop pretending the screen is 96 DPI.

    Without this the process is DPI-unaware: Windows hands it a 96 DPI
    screen, draws the window at that size and then stretches the result to
    fit the real one. Everything is soft, and -- worse -- the window is
    sized against a screen that reports 1280 when it is really 1920, so a
    console face that fits perfectly well gets clamped and the right-hand
    keys are cut off.

    Must be called before the first window exists. Per-monitor v2 is what
    we want (the scale follows the window between monitors); the two older
    calls are the fallbacks on Windows 8.1 and 7. Every one of them fails
    harmlessly if the awareness is already set, which is what happens when
    a test builds a second SimApp in the same process.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
    except ImportError:                                # pragma: no cover
        return
    try:
        # -4 is DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return
    except (AttributeError, OSError):                  # pragma: no cover
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return
    except (AttributeError, OSError):                  # pragma: no cover
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()       # system-wide
    except (AttributeError, OSError):                  # pragma: no cover
        pass


def claim_taskbar_identity():
    """Tell Windows this process is its own application.

    A process that does not set an Application User Model ID inherits the
    one belonging to whatever launched it, and the taskbar groups and
    ICONS by that ID. Run from a source tree that host is `python.exe`, so
    the taskbar button showed Python's logo however good the window's own
    icon was -- and pinning the button pinned Python.

    Must be called before the first window exists, like the DPI call above,
    and it is harmless to call twice.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
    except ImportError:                                # pragma: no cover
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{PUBLISHER}.{APP_NAME}".replace(" ", ""))
    except (AttributeError, OSError):                  # pragma: no cover
        pass


def line_pick(kind, number):
    """One line on a PLLD or WPLLD SELECT LINE screen.

    576013-610 Rev AC p.11-5: "When you press CHANGE, the system displays
    the message: SELECT LINE / Q#: PLLD #X", and p.12-5 draws `W#:WPLLD #X`
    for the wireless card -- the head the results screens on p.11-1 and
    p.12-1 already give the line.
    """
    letter, word = {"plld": ("Q", "PLLD"), "wplld": ("W", "WPLLD")}[kind]
    return f"{letter} {number}: {word} #{number}"


class SimApp(tk.Tk):
    def __init__(self, console, port=10001, policy=None, capture=None):
        enable_dpi_awareness()
        claim_taskbar_identity()
        super().__init__()
        self._set_icon()
        # What one of the pixel dimensions in this file is worth on this
        # screen. They were all measured at 96 DPI, which is what Windows
        # reports to a program that has not asked otherwise; now that the
        # window is drawn at the screen's real resolution, they have to be
        # multiplied up or the whole face comes out a third of the size.
        # Type is not scaled here: it is given in points, and Tk's own
        # scaling below turns those into the right number of pixels.
        self.px = self.winfo_fpixels("1i") / 96.0
        self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        # The bench's own dimensions were measured at 96 DPI too, and the
        # bench had no way to know it was not on one. It has to be told
        # before a single tile is built, because a tile takes its size in
        # its constructor. See `bench.set_scale`.
        bench.set_scale(self.px)
        self.console = console
        self.port = port
        # The exposure the process was started with, and the capture it is
        # writing. Handed neither, the bench serves as it always has and the
        # Capture view is not built. See `exposed.py`.
        self.policy = policy or exposed.Policy(exposed.BENCH)
        self.capture = capture
        if self.capture is not None:
            self.capture.enable(self.policy.capturing)
        self._cap_shown = 0          # rows of the capture already on screen
        self._cap_follow = True
        self._cap_after = None
        self.title(f"{APP_NAME}  --  serial on 127.0.0.1:{port}")
        self.configure(bg=PANEL_BG)
        self.reset_panel()
        self._build()
        # The printer door's height is set by _fit_door, not by its
        # children, so it must be sized BEFORE the window asks itself how
        # tall to be -- measured first, the door is a 1px sliver and the
        # window opens too short to show the console's own face.
        self.update_idletasks()
        # what the console face needs, before the door is given anything
        self._panel_req = self._panel.winfo_reqwidth()
        self._fit_door()
        self.update_idletasks()
        want_h = min(self.winfo_reqheight(),
                     self.winfo_screenheight() - self.S(90))
        # Wide enough that the panel keeps every pixel it asked for AND
        # the printer door still gets its share of the window: the door is
        # a fraction of the width, so the panel is what is left of the
        # rest, and solving for the width is what stops the door opening
        # at its minimum on a screen with room to spare.
        chrome = self.winfo_reqwidth() - self._doors.winfo_reqwidth()
        roomy = int((self._panel_req + chrome) / (1.0 - DOOR_SHARE))
        want_w = min(max(self.winfo_reqwidth(), roomy, self.S(900)),
                     self.winfo_screenwidth() - self.S(60))
        self.geometry(f"{want_w}x{want_h}+{self.S(40)}+{self.S(30)}")
        self.minsize(self.S(880), self.S(460))
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
        self.subs = []            # the screens ENTER descended into, a path
        self.locked = False       # waiting for the System Security Code
        self.armed = False        # an archive answer toggled to YES
        self._profile_pending = None   # a tank profile waiting to be confirmed
        self._point = {}          # a 50 point pair being strapped in
        self.dlv = 0              # which delivery Delivery Maintenance is on
        self.shift_ix = 0         # which shift Last-Shift Inventory is on
        self._insert = {}         # a delivery being entered by hand
        self.chart_open = False   # the tank chart passcode, once given
        self.sure = False         # and the ARE YOU SURE? screen after it
        self.boot_restore = None  # the cold-start RESTORE SETUP DATA prompt
        # The `ARE YOU SURE?` a disabled beeper is asked, and the line it
        # is asked under. None until a step with `sure` is answered its own
        # way. See SU11 and `k_step`.
        self._sure_head = None
        # Where the REMOVE VMC SERIAL NUMBER walk has got to: 576013-623
        # Rev AN p.27-2 draws six screens for it and the last of them is
        # the parent again. One of "no", "yes", "sure_no", "sure_yes".
        self.vmc_remove = "no"
        self.isd_override = None  # the shutdown-override confirmation walk
        self.isdflow = None       # the grade-hose mapping flows of 577013-800
        self.sumpflow = None      # SELECT MAG SENSOR, 576013-610 p.24-4
        # (the confirmation on the glass, the one STEP shows next): p.20-2
        # confirms a started tank test in two screens
        self._confirm_next = None
        self._clear_stages()
        self._alarm_presses = 0   # ALARM/TEST x3 reaches the override
        self._lamp_test_until = 0.0   # the held lamp test owns the lamps
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
        # Which side of a clock field the cursor is on. A real console walks
        # one cursor along the digits and then onto AM and onto PM; the arrows
        # moving it between the two halves IS the selection. See _clock_text.
        self.on_meridiem = False
        self.msg = ""
        self._tap = None
        self._cycle = 0
        # 576013-610 ch.33's own sequence, which the blue key starts:
        # None, "prompt" (MAINTENANCE TRACKER / DISABLED or ENABLED),
        # "insert" (INSERT KEY IN PORT / PRESS <ENTER>), or the second line
        # of the answer -- LOGGED IN XXXXXX, or one of the four refusals.
        self.mt_login = None
        self.mt_asked = None      # console time the insert prompt went up
        self.mt_ever = False      # DISABLED the first time, ENABLED after
        self.maint_report = False  # the white key's MAINTENANCE REPORT screen
        self.relay_setup_due = False   # `ON - PRESS ANY KEY`, owed a printout
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
                    "which": "CURRENT", "adjust_type": "SHIFT",
                    # the day typed on SELECT DAY, where a date can be typed
                    # rather than toggled -- None is the day in progress
                    "day": None}
        self._blink = False
        # whether STEP has been pressed off the Operating Mode status display
        self._entered = False

    def _set_icon(self, window=None):
        """A window's own icon, which is also its taskbar button's.

        Nothing set one. `assets/icon.ico` reached the EXE's resource
        and the installer's `SetupIconFile` and never the window, so the
        program ran under Tk's default feather -- in the title bar, in
        Alt-Tab and on the taskbar. `wm iconbitmap` came back empty on a
        live window, which is the whole diagnosis.

        Both forms, and the order matters. Measured with `WM_GETICON`
        on the real toplevel, on this Tk and this Windows:

            bare                     small=None  big=None
            iconbitmap(default=...)  small=None  big=None    <- no effect
            iconbitmap(...)          small=HICON big=HICON
            iconphoto(True, png)     small=None  big=None    <- no effect

        `default=` is documented as the one that covers the whole
        application, and on Windows it only reaches toplevels created
        AFTER it -- the root window already exists by the time `__init__`
        can call anything, so on its own it leaves the very window the
        taskbar is showing without an icon. The plain call is what sends
        `WM_SETICON`. So: the plain form for this window, `default=` for
        the bench window and the dialogs that come later.

        The `.ico` carries six sizes and Windows picks the one it wants;
        `iconphoto` with the PNG sets nothing at all here and is kept only
        as the path for a platform where `iconbitmap` wants an XBM.
        """
        window = self if window is None else window
        try:
            ico = paths.asset("icon.ico")
            window.iconbitmap(ico)                     # this window
            window.iconbitmap(default=ico)             # and, in theory, the
            return                                     # ones after it
        except Exception:
            pass
        try:
            # kept on the instance: Tk does not own the image, and a
            # PhotoImage that goes out of scope takes the icon with it
            self._icon = tk.PhotoImage(file=paths.asset("icon.png"))
            window.iconphoto(True, self._icon)
        except Exception:                              # pragma: no cover
            # An icon is not worth failing to start over.
            pass

    @property
    def sub(self):
        """The screen ENTER descended into, or None at the top level.

        A branch can hold a branch. 576013-623 Rev AN p.6-5 descends from
        `AUTO TRANSMIT SETUP` / `PRESS <ENTER>` to `TRANSMIT MESSAGE SETUP`
        / `PRESS <ENTER>` and from there again to the twelve
        `AUTO ... LIMIT` screens, so where the panel is standing is a PATH
        and not one index. `self.subs` is that path, deepest last; `sub` is
        its last element, which is what a caller that knows only one level
        -- every caller in Diagnostic Mode, where no figure nests -- means.
        """
        return self.subs[-1] if self.subs else None

    @sub.setter
    def sub(self, value):
        self.subs = [] if value is None else [value]

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
        # Its own, explicitly: `iconbitmap(default=...)` is documented as
        # covering every later toplevel and this one is later and does not
        # get it. Measured, like the rest of `_set_icon`.
        self._set_icon(win)
        win.title(f"Site Bench  --  {APP_NAME}")
        win.configure(bg=bench.BG)
        win.minsize(self.S(560), self.S(380))
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
        # drawn at 96 DPI, so through S() like the rest of the bench
        want_h, want_w, least = self.S(470), self.S(640), self.S(560)
        if y + h + 40 + want_h <= sh:
            self.bench_win.geometry(f"{w}x{want_h}+{x}+{y + h + 40}")
        elif x + w + 20 + want_w <= sw:
            self.bench_win.geometry(
                f"{want_w}x{min(h, sh - 80)}+{x + w + 12}+{y}")
        else:
            self.bench_win.geometry(f"{max(w - 80, least)}x{want_h}"
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
        conm.add_command(label="Seed programming from file...",
                         command=self._seed_asked)
        conm.add_command(label="Save programming to file...",
                         command=self._archive_asked)
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
        self.lever_down = tk.BooleanVar(
            value=self.console.printer_lever_open)
        benm.add_checkbutton(label="Printer feed release lever down",
                             variable=self.lever_down,
                             command=self._set_lever)
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
        self._sw_fiscal = tk.BooleanVar(
            value=self.console.fiscal_height_switch)
        swm.add_checkbutton(label="DIP SW2-4: fiscal height security",
                            variable=self._sw_fiscal,
                            command=self._set_fiscal_height)
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

        Off-white box, a bare brand plate, a backlit monochrome LCD,
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
        self._doors = doors
        bay = tk.Frame(doors, bg=CASE, width=self.S(DOOR_W))
        bay.pack(side="left", fill="y")
        bay.pack_propagate(False)
        self._build_printer(bay)
        panel = tk.Frame(doors, bg=CASE)
        panel.pack(side="left", fill="both", expand=True)
        self._panel = panel

        # ---- the brand plate, left bare ----
        # A real console carries its maker's wordmark and a model flash across
        # this strip. Both are trademarks, and neither one teaches anything
        # about operating the console, so this simulator draws neither. The
        # plate keeps its full height, so the face below it is unchanged.
        head = tk.Frame(panel, bg=CASE, height=self.S(BRAND_PLATE_H))
        head.pack(fill="x", padx=self.S(18), pady=(self.S(10), self.S(2)))
        head.pack_propagate(False)

        # ---- the display over the indicator column and the two keypads ----
        # In the photograph the display sits ABOVE the keypads and its right
        # edge finishes level with the right edge of the alphanumeric pad,
        # it does not run out past the keys on either side. So the two are
        # stacked in one block, the display anchored to that block's right,
        # and the type is sized afterwards from how wide the keypads came
        # out: 24 characters across the width of the keys, and no wider.
        stack = tk.Frame(panel, bg=CASE)
        stack.pack(padx=self.S(18), pady=(self.S(2), self.S(4)), expand=True)
        row = tk.Frame(stack, bg=CASE)
        row.pack(side="bottom", anchor="e")

        # Everything on the row is laid out in key widths, measured off two
        # photographs of the console (a running one, and the maker's own
        # product shot) that agree with each other:
        #   the keys are square, and the two pads stand a fifth of a pad
        #   apart; the indicator lamps are domes half a key across, a fifth
        #   of a pad to the left of the operating pad, their centres a key
        #   pitch apart and the first one just over a pitch below the top
        #   of the pad; their legends are plain capitals about a key wide,
        #   nearly half a key clear of the lamps.
        kw, kh = self._key_size()
        pitch = kh + self.S(6)
        opsw = 3 * (kw + self.S(6))
        leds = tk.Frame(row, bg=CASE)
        leds.grid(row=0, column=0, padx=(0, int(opsw * 0.20)), sticky="n")
        leds.grid_rowconfigure(0, minsize=int(pitch * 0.575))
        lamp = int(kw * 0.5)
        legend = tkfont.Font(family="Segoe UI", size=8)
        for size in range(14, 6, -1):
            legend.configure(size=size)
            if legend.measure("ALARM") <= kw * 1.05:
                break
        self.led, self._glint = {}, {}
        for i, (key, text, colour) in enumerate((("alarm", "ALARM", "#e01b1b"),
                                                 ("warn", "WARNING", "#f2d029"),
                                                 ("power", "POWER", "#3fbf55"))):
            leds.grid_rowconfigure(i + 1, minsize=int(pitch * 1.05))
            h = tk.Frame(leds, bg=CASE)
            h.grid(row=i + 1, column=0, sticky="e")
            tk.Label(h, text=text, bg=CASE, fg=LABEL,
                     font=legend).pack(side="left", padx=(0, int(kw * 0.4)))
            cv = tk.Canvas(h, width=lamp, height=lamp, bg=CASE,
                           highlightthickness=0)
            oval = cv.create_oval(1, 1, lamp - 2, lamp - 2, fill=LED_OFF,
                                  outline="#3a3a38")
            # the glint on the dome, up and to the left: a small spot a
            # little lighter than whatever the lamp is showing
            g = max(1, lamp // 9)
            self._glint[key] = cv.create_oval(
                lamp * 0.32 - g, lamp * 0.32 - g, lamp * 0.32 + g,
                lamp * 0.32 + g, fill=blend(LED_OFF, "#ffffff", 0.45),
                outline="")
            cv.pack(side="left")
            self.led[key] = (cv, oval, colour)

        NL = chr(10)
        ops = tk.Frame(row, bg=KEYPAD_BG)
        ops.grid(row=0, column=1, padx=(0, int(opsw * 0.20)), sticky="n")
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
            key = self._key(ops, label, cmd, i, face, txt)
            if label.startswith("ALARM"):
                # 577013-814's TLS-3xx Audible and Visual Test Procedure is a
                # HOLD, not a press: "Press and Hold Alarm Test button for a
                # minimum 3 seconds." So the button needs press and release,
                # not just its command.
                key.bind("<ButtonPress-1>", self._alarm_held_start, add="+")
                key.bind("<ButtonRelease-1>", self._alarm_held_stop, add="+")

        alpha = tk.Frame(row, bg=KEYPAD_BG)
        alpha.grid(row=0, column=2, sticky="n")
        # the third of each pair is the line set in the larger type: the
        # digit, which is under its letters on the number keys and over
        # its shifted characters on the three at the bottom
        keys = [("QZ." + NL + "1", "1", 1), ("ABC" + NL + "2", "2", 1),
                ("DEF" + NL + "3", "3", 1), ("GHI" + NL + "4", "4", 1),
                ("JKL" + NL + "5", "5", 1), ("MNO" + NL + "6", "6", 1),
                ("PRS" + NL + "7", "7", 1), ("TUV" + NL + "8", "8", 1),
                ("WXY" + NL + "9", "9", 1),
                (chr(0x2190) + NL + "+/-", "+", 0),
                # the 0 key carries its three shifted characters over the
                # digit, the space drawn as an open box, the way the key
                # itself is printed: space, hyphen, comma, then 0
                (chr(0x25a1) + "-," + NL + "0", "0", 1),
                (chr(0x2192) + NL + chr(0x2022), ",", 0)]
        for i, (label, k, big) in enumerate(keys):
            self._key(alpha, label, lambda kk=k: self.k_alnum(kk), i,
                      KEY_FACE, KEY_TEXT, big=big)

        # Now the display, measured off a straight-on photograph of the
        # whole face, 1200 pixels wide:
        #
        #   glass          x 640..977   (337 wide, 45 high)
        #   operating pad  x 697..824   (127)
        #   number pad     x 849..976   (127)
        #
        # So the glass is 1.21 times the span of the two pads, 697..976;
        # its RIGHT edge finishes LEVEL with the right edge of the number
        # pad, one pixel in three hundred, not past it; and all of the
        # overhang is on the LEFT, where it reaches back over the indicator
        # lamps at x 649..672 and stops short of the operating pad. So the
        # keys are hung off the right of the block, the glass is sized from
        # their span, and the dots are whatever fills the width.
        wrap = tk.Frame(stack, bg=CASE)
        wrap.pack(side="top", anchor="e", pady=(0, int(pitch * 0.7)))
        # the glass sits back in a cut-out of the case: no bezel, just the
        # thin dark edge of the opening, and the shadow the glass draws
        # itself along its top and left
        rim = tk.Frame(wrap, bg=LCD_RIM)
        rim.pack()
        # the shadow lies OUTSIDE the glass, along its top and left: drawn
        # on the glass it fell across the top row of dots and clipped them
        shade = tk.Frame(rim, bg=LCD_SHADE)
        shade.pack(padx=self.S(1), pady=self.S(1))
        # Tk works out what a frame of gridded keys asks for lazily, and an
        # unmeasured keypad reports one pixel, so make it settle first.
        self.update_idletasks()
        span = ops.winfo_reqwidth() + alpha.winfo_reqwidth() + int(opsw * 0.20)
        room = int(span * 1.21)
        # (the rim is two pixels, and the glass is packed two in from it)
        edge = self.S(2)
        self.lcd = DotMatrixLCD(shade, COLS, ROWS, room - edge * 2,
                                lit=LCD_LIT, dead=LCD_DEAD)
        self.lcd.pack(padx=(edge, 0), pady=(edge, 0))
        # the rows are addressed by index; kept as a list so the render path
        # reads as it did when these were canvas text items
        self._text_ids = list(range(ROWS))

        # ---- navy pinstripes across the base ----
        stripes = tk.Canvas(panel, height=self.S(52), bg=CASE,
                            highlightthickness=0)
        stripes.pack(fill="x", padx=self.S(18), pady=(self.S(6), self.S(8)))

        def draw_stripes(_e=None):
            stripes.delete("all")
            w = stripes.winfo_width() or self.S(900)
            for i in range(9):
                y = self.S(2) + i * self.S(5)
                stripes.create_line(0, y, w, y, fill=BLUE,
                                    width=self.S(1 + (i // 4)))

        stripes.bind("<Configure>", draw_stripes)

        self.hint = tk.Label(panel, text="", bg=CASE, fg="#4a4a48",
                             font=("Segoe UI", 8), justify="left", wraplength=820)
        self.hint.pack(anchor="w", padx=self.S(18), pady=(0, self.S(8)))

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
            # the base's own pitch and weight, through S(), so the two
            # doors' stripes run on into the base's at any scaling
            y = base + i * self.S(5)
            if y < h - 1:
                cv.create_line(0, y, w, y, fill=BLUE,
                               width=self.S(1 + (i // 4)))

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
                                 padx=self.S(SLIP_PAD), pady=self.S(5),
                                 cursor="arrow",
                                 highlightthickness=0)
        self.slip_text.configure(yscrollcommand=self._slip_scrolled)
        self.slip_text.pack(fill="both", expand=True)
        # No scrollbar beside it: the paper is the width of the cutout and
        # nothing else may be. A thumb is drawn ON the paper's right margin
        # instead, and the wheel and a drag both wind it.
        self.slip_thumb = tk.Canvas(body, width=self.S(5), bg=PAPER, bd=0,
                                    highlightthickness=0)
        for widget in (self.slip_text, self.slip_thumb):
            widget.bind("<MouseWheel>", self._slip_wheel)
        self.slip_thumb.bind("<B1-Motion>", self._slip_drag)
        self.slip_thumb.bind("<Button-1>", self._slip_drag)
        # the torn bottom edge, and the cut button sitting on the paper
        self.slip_tear = tk.Canvas(slip, height=self.S(SLIP_TEAR),
                                   width=self.S(10),
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

        The search starts lower than it once did. A character's advance is
        a whole number of pixels, and rounding it bites harder at small
        sizes: the same six-point type that measured four pixels a
        character on a screen the program thought was 96 DPI measures
        seven on the real one, which is 1.75 times the width for 1.5 times
        the screen. Six points stopped fitting the slot, and a floor that
        does not fit is not a floor, it is a clipped report.
        """
        best = None
        for size in range(4, 15):
            self.slip_font.configure(size=size)
            if (self.slip_font.measure("0") * PAPER_COLS
                    > width - self.S(SLIP_PAD) * 2):
                break
            best = size
        if best is None:
            best = 4
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
        h = self.S(SLIP_TEAR)
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
            trim = self.S(SLIP_TEAR) + self.S(12)   # torn edge and borders
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
        # The panel beside it has a width it cannot go under -- the keys
        # and the glass -- and the door is what is left over. Without this
        # the door grows on the first <Configure>, after the window has
        # already been sized to the face, and pushes the right-hand column
        # of keys off the edge of the window.
        # measured on the frame that actually holds the two doors, not on
        # the window: between them stand a border and a margin, and sharing
        # out the window's width instead leaves the panel some twenty
        # pixels short -- exactly enough to cut the last column of keys off.
        held = getattr(self, "_doors", None)
        room = held.winfo_width() if held else self.winfo_width()
        spare = room - getattr(self, "_panel_req", 0)
        want = max(self.S(300), min(self.S(520),
                                    int(self.winfo_width() * DOOR_SHARE),
                                    max(self.S(300), spare)))
        # ...and it is taller than it is wide, which on the real console is
        # what gives the printer door its shape. The panel beside it does not
        # need the height, so the door sets it and the panel spreads into it.
        tall = max(int(want / DOOR_RATIO), self.S(430))
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

    def S(self, n):
        """One of this file's measured pixel dimensions, on this screen."""
        return max(1, round(n * self.px))

    # The legends on a real console are set in a neo-grotesque of the
    # Helvetica school -- circular O, horizontal terminals, an M with
    # upright stems -- in a regular weight, not bold. Arial is metrically
    # Helvetica and is on every Windows machine; the rest of the list is
    # for everywhere else, ending at whatever Tk would have picked anyway.
    KEY_FAMILIES = ("Arial", "Helvetica", "Liberation Sans", "Nimbus Sans",
                    "DejaVu Sans", "TkDefaultFont")
    # A number key's digit stands about twice as tall as the three letters
    # over it, and is set heavier than they are. Measured down the DEF/3
    # key of the photographed console: the letters are five pixels of cap
    # height, the digit eleven.
    DIGIT_SCALE = 2.1
    DIGIT_WEIGHT = "bold"

    def _key_family(self):
        if not getattr(self, "_keyfamily", None):
            have = set(tkfont.families())
            self._keyfamily = next(
                (f for f in self.KEY_FAMILIES if f in have), "TkDefaultFont")
        return self._keyfamily

    def _key_font(self):
        if not getattr(self, "_keyfont", None):
            self._keyfont = tkfont.Font(family=self._key_family(), size=7)
        return self._keyfont

    def _key_big_font(self):
        """The larger of the two sizes: a number key's digit."""
        if not getattr(self, "_keybigfont", None):
            small = self._key_font()
            self._keybigfont = tkfont.Font(
                family=self._key_family(),
                size=max(8, round(abs(small.cget("size")) * self.DIGIT_SCALE)),
                weight=self.DIGIT_WEIGHT)
        return self._keybigfont

    # the longest legend any key carries; every key is cut to clear it
    WIDEST_LEGEND = "SENSOR"
    # ...with room to spare, because a real key is a good deal bigger than
    # its legend: measured off the photograph, the word CHANGE runs 22 of
    # the 38 pixels its keycap is wide, so the legend takes a shade under
    # three fifths of the face and the rest is blank moulding.
    LEGEND_SHARE = 0.58

    def _key_size(self):
        """What one key measures. Every key on the console is the same
        square -- the operating keys and the number keys alike, as
        photographed -- so there is one size, taken from the longest legend
        any of them carries. Sized from the legend's own type, so the keys
        scale with the screen the way the Tk buttons before them did."""
        font = self._key_font()
        wide = font.measure(self.WIDEST_LEGEND)
        side = max(round(wide / self.LEGEND_SHARE),
                   wide + self.S(KeyCap.BEVEL) * 2 + self.S(8))
        return side, side

    def _key(self, parent, label, cmd, i, face, txt, w=None, big=None):
        """One moulded keycap on the black grid: a flat top on four sloped
        sides, that sinks when pressed. Every key is the same square, so
        `w` is accepted and ignored. `big` is the line of the legend that
        is set in the larger type, if any."""
        cmd = self._guard(cmd)
        width, height = self._key_size()
        # the moulded edge is a share of the key, not a fixed few pixels
        b = KeyCap(parent, label, cmd, face, txt, width=width, height=height,
                   grid=KEYPAD_BG, font=self._key_font(),
                   bevel=max(2, round(width * 0.13)),
                   big_font=self._key_big_font(), big_line=big)
        b.grid(row=i // 3, column=i % 3, padx=self.S(3), pady=self.S(3))
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
        self.tab_traffic = tk.Frame(holder, bg=bench.BG)
        self.tab_mod = tk.Frame(holder, bg=bench.BG)
        self.tab_log = tk.Frame(holder, bg=bench.BG)
        self.tab_paper = tk.Frame(holder, bg=bench.BG)
        self.tab_net = tk.Frame(holder, bg=bench.BG)
        self.tab_capture = tk.Frame(holder, bg=bench.BG)
        self._views = {"Site": self.tab_site, "Traffic": self.tab_traffic,
                       "Modules": self.tab_mod,
                       "Network": self.tab_net, "Serial log": self.tab_log,
                       "Capture": self.tab_capture,
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
        self._build_traffic(self._scrollable(self.tab_traffic))
        self._build_modules(self._scrollable(self.tab_mod))
        self._build_log(self.tab_log)
        self._build_paper_tab(self.tab_paper)
        self._build_network(self._scrollable(self.tab_net))
        self._build_capture(self.tab_capture)
        # Hidden until something is being captured. A view that is always
        # there and always empty teaches people to ignore it, and this is
        # the one view that must be noticed the moment it has something in
        # it. `_apply_exposure` shows it.
        self._switch.set_visible("Capture", self.policy.capturing)
        if self.capture is not None:
            self._poll_capture()
        # The site the console already knows about, there at first sight.
        # The old bench opened this view empty and waited for a rescan,
        # which read as broken every time.
        self._refresh_site()
        self.refresh_traffic()
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

    # =====================================================================
    # the capture: everything a stranger said, and what the card said back
    # =====================================================================
    DIRS = {
        "in": ("-->", "#e0b64f"),        # what arrived
        "out": ("<--", "#7fb0e8"),       # what the card answered
        "open": ("(+)", "#5f7a5f"),
        "close": ("(-)", "#5f7a5f"),
        "drop": ("!!!", "#c08a4a"),
        "refused": ("XXX", "#e06c6c"),   # the gate turned it away
        "deflected": ("###", "#e06c6c"),  # it thinks it reprogrammed the card
    }

    def _build_capture(self, tab):
        """The wire itself, as it arrives.

        Separate from the Serial log on purpose. That view is the bench's
        own narrative -- what the simulator decided, in sentences, for a
        trainee. This one is evidence: a source address, a direction, the
        command code and the bytes, with nothing interpreted. They answer
        different questions and merging them would spoil both.
        """
        head = tk.Frame(tab, bg=bench.BG)
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text="EVERY EXCHANGE, AND WHO IT WAS WITH",
                 bg=bench.BG, fg=bench.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        bench.info_dot(
            head,
            "One line per direction. --> is what arrived, <-- is what the "
            "card answered, XXX is a connection the gate refused, and ### "
            "is a stranger who thinks they have just reprogrammed the card "
            "and has not. The same rows are appended to the capture file as "
            "JSON lines, which is what a SIEM should read; this view is for "
            "watching it happen."
        ).pack(side="left", padx=(5, 0))

        self._cap_stats = tk.Label(head, bg=bench.BG, fg=bench.MUTED,
                                   font=bench.MONO_SM)
        self._cap_stats.pack(side="left", padx=(12, 0))

        tk.Button(head, text="Clear", command=self._clear_capture,
                  bg=bench.CARD, fg=bench.BODY, relief="flat",
                  font=("Segoe UI", 8), padx=10,
                  activebackground=bench.CARD_HI,
                  activeforeground=bench.INK).pack(side="right")
        self._cap_follow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            head, text="follow", variable=self._cap_follow_var,
            command=self._set_follow, bg=bench.BG, fg=bench.MUTED,
            selectcolor=bench.CARD, activebackground=bench.BG,
            activeforeground=bench.INK, font=bench.FONT_SM
        ).pack(side="right", padx=(0, 8))

        self._cap_where = tk.Label(tab, bg=bench.BG, fg=bench.MUTED,
                                   anchor="w", justify="left",
                                   font=bench.FONT_SM, wraplength=560)
        self._cap_where.pack(fill="x", padx=12, pady=(4, 0))

        self.capbox = tk.Text(tab, height=12, bg="#17191d", fg="#c8ccd2",
                              font=bench.MONO_SM, borderwidth=0,
                              padx=10, pady=8, wrap="none")
        self.capbox.pack(fill="both", expand=True, padx=10, pady=8)
        for name, (_arrow, colour) in self.DIRS.items():
            self.capbox.tag_configure(name, foreground=colour)
        self.capbox.tag_configure("peer", foreground="#9aa0a8")
        self.capbox.tag_configure("body", foreground="#c8ccd2")

    def _set_follow(self):
        self._cap_follow = self._cap_follow_var.get()

    def _clear_capture(self):
        """Clears the VIEW. The capture file is evidence and is appended to.

        Deliberately not wired to the file: a button on a bench must not be
        able to destroy what a honeypot has collected, and someone reaching
        for Clear wants a readable screen, not a shredder.
        """
        self.capbox.delete("1.0", "end")
        if self.capture is not None:
            self.capture.clear()
        self._cap_shown = 0

    def _poll_capture(self):
        """Drain what the listeners have recorded onto the screen.

        Polled rather than pushed. The capture's `on_record` fires on the
        listener threads, and Tk may only be touched from the thread that
        made it -- a widget written from a socket thread is the crash that
        takes the window with it. So the threads write to the ring and this
        reads the ring, on Tk's own clock.
        """
        self._cap_after = None
        cap = self.capture
        if cap is None:
            return
        try:
            rows = cap.rows(self._cap_shown)
            if rows:
                self._cap_shown += len(rows)
                for row in rows:
                    self._append_capture(row)
                if self._cap_follow:
                    self.capbox.see("end")
            st = cap.stats()
            self._cap_stats.config(
                text="%d exchanges  %d sources%s%s"
                     % (st["total"],
                        st["peers"],
                        "+" if st.get("peers_untallied") else "",
                        "  (%d dropped from view)" % st["dropped"]
                        if st["dropped"] else ""))
        except tk.TclError:
            return
        self._cap_after = self.after(400, self._poll_capture)

    def _append_capture(self, row):
        stamp = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
        arrow, _colour = self.DIRS.get(row["dir"], ("...", bench.MUTED))
        kind = row["dir"] if row["dir"] in self.DIRS else "body"
        src = row.get("src") or "--"
        proto = row.get("proto") or ""
        self.capbox.insert("end", "%s.%03d " % (stamp, int(row["ts"] * 1000)
                                                % 1000), "peer")
        self.capbox.insert("end", "%-15s " % src, "peer")
        self.capbox.insert("end", "%-9s " % proto, "peer")
        self.capbox.insert("end", "%s " % arrow, kind)
        if row.get("cmd"):
            self.capbox.insert("end", "%-8s " % row["cmd"], kind)
        body = row.get("note") or exposed.printable(row.get("raw", ""))
        if len(body) > 220:
            body = body[:220] + " ... (%d bytes)" % row.get("bytes", 0)
        self.capbox.insert("end", body + "\n", "body")

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

        self._build_exposure(tab)

        box = bench.card(tab)
        box.pack(fill="x", pady=(0, 8))
        inner = box.inner
        self._net_rows = {}
        for key, label in (("mac", "MAC address"), ("ip", "IP address"),
                           ("port", "Serial tunnel port"),
                           ("setup", "Setup menu"),
                           ("web", "Web manager"),
                           ("discovery", "DeviceInstaller"),
                           ("reach", "Reachable at")):
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
        tk.Button(bar, text="Reset card", command=self._reset_card,
                  bg="#5c3c3c", fg="#f0e8e8", relief="flat",
                  font=bench.FONT_HEAD, padx=14, pady=4,
                  activebackground="#6d4a4a",
                  activeforeground=bench.INK).pack(side="left", padx=(8, 0))
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
                value=bool(self.console.setting("ifsf_platform", 0, False)))
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

        self._build_comm_errors(tab)

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

    # -- who is allowed to reach this ------------------------------------

    LEVEL_COLOUR = {
        exposed.BENCH: bench.OK,
        exposed.LAN: bench.WARN,
        exposed.EXPOSED: bench.BAD,
    }

    def _build_exposure(self, tab):
        """The one control that decides who may talk to this console.

        A pill with a menu, like the sensor and ISD tiles, rather than a
        checkbox: this is a choice between three states and not an on/off,
        and the pill can carry the colour that says which one is in force
        at a glance. Red is not decoration -- an exposed console is a
        different thing from a bench one and the bench should look
        different while it is.
        """
        bench.section(
            tab, "Exposure",
            "Who is allowed to reach the ports this program opens. The "
            "console was written for a bench, where the other end means "
            "well: it keeps what it is told and it answers everything. On "
            "a public address neither is safe, so Exposed changes both -- "
            "nothing the network says is written to disk, the card's "
            "identity stops carrying this machine's name, connections are "
            "capped and rated per source, replies are delayed so an "
            "instant answer does not give the emulation away, and every "
            "byte is captured to the Capture view and to a file. It is not "
            "a sandbox: run it as an unprivileged user on a host that does "
            "not matter.")

        card = bench.card(tab)
        card.pack(fill="x", pady=(0, 6))
        row = tk.Frame(card.inner, bg=bench.CARD)
        row.pack(fill="x", padx=10, pady=8)

        tk.Label(row, text="Reachable by", bg=bench.CARD, fg=bench.MUTED,
                 width=18, anchor="w", font=bench.FONT).pack(side="left")
        self._exp_pill = tk.Label(
            row, bg="#2c2f35", fg=bench.OK, font=bench.FONT_HEAD,
            padx=12, pady=3, cursor="hand2")
        self._exp_pill.pack(side="left")
        self._exp_pill.bind("<Button-1>", self._exposure_menu)

        self._exp_note = tk.Label(card.inner, bg=bench.CARD, fg=bench.MUTED,
                                  anchor="w", justify="left",
                                  font=bench.FONT_SM, wraplength=520)
        self._exp_note.pack(fill="x", padx=10, pady=(0, 8))
        self._paint_exposure()

    def _exposure_menu(self, _e=None):
        m = tk.Menu(self, tearoff=0, bg="#2c2f35", fg=bench.INK,
                    activebackground=bench.ACCENT,
                    activeforeground="#101216", font=bench.FONT)
        for level in exposed.LEVELS:
            m.add_command(label=exposed.LABELS[level],
                          foreground=self.LEVEL_COLOUR[level],
                          command=lambda v=level: self._pick_exposure(v))
        m.tk_popup(self._exp_pill.winfo_rootx(),
                   self._exp_pill.winfo_rooty()
                   + self._exp_pill.winfo_height())

    # The warning the Exposed choice puts up. Not a "are you sure?" -- that
    # teaches people to click through -- but the four things that are
    # actually true and are not obvious, each of which has bitten somebody.
    EXPOSED_WARNING = "\n\n".join((
        "This console is about to answer strangers.",

        "\u2022  It is not a sandbox. This mode closes the holes a "
        "honeypot must not have -- nothing the network says is written "
        "to disk, no source can hold more than its share of the "
        "listeners, and the card stops publishing a hash of this "
        "machine's name. It does not make the program safe to run as "
        "an administrator on a machine that matters. Run it as an "
        "unprivileged user, in a container, on a host you can throw "
        "away.",

        "\u2022  You will be indexed as exposed critical "
        "infrastructure, and you may get abuse reports from your ISP. "
        "Do not do this on a home connection or on any network you do "
        "not own.",

        "\u2022  The demo site is a fingerprint. This program ships "
        "with it, so a scanner that has seen one of these recognises "
        "the next one. Load or seed a site of your own. GasPot's own "
        "default station names became the published signature that "
        "identified it.",

        "\u2022  Collecting other people's traffic may be regulated "
        "where you are. Check before you leave it running.",

        "Nothing already programmed into this console will be written "
        "to disk again until the exposure is set back.",
    ))

    def _pick_exposure(self, level):
        if level == self.policy.level:
            return
        if level == exposed.EXPOSED:
            from tkinter import messagebox
            if not messagebox.askokcancel(
                    "Expose this console to the network?",
                    self.EXPOSED_WARNING, icon="warning", default="cancel",
                    parent=self):
                return
        self._apply_exposure(level)

    def _apply_exposure(self, level):
        """Put a new exposure in force, here and in every listener.

        The listeners hold the policy OBJECT, not a copy of its numbers, so
        mutating it in place is what reaches them -- a fresh `Policy` would
        change the bench's mind and leave the sockets serving the old one.
        That is also why the card is restarted below: its gate was built
        from the limits at the time, and a gate cannot widen itself.
        """
        was = self.policy.level
        self.policy.level = level
        total, per_ip, rate, idle = exposed.Policy.LIMITS[level]
        self.policy.max_total = total
        self.policy.max_per_ip = per_ip
        self.policy.rate_per_min = rate
        self.policy.idle_timeout = idle
        self.policy._delay = exposed.Policy.DELAY[level]
        exposed.freeze_writes(self.policy.readonly)

        # Arm the capture the listeners are ALREADY holding. Making a new
        # one here is what the first version did, and it did not work: the
        # card's listeners were handed their capture when the card came up
        # and keep it for the card's life, so a fresh object never reached
        # them and switching to Exposed at runtime recorded nothing from
        # the card while the view sat there looking armed.
        if self.capture is None:
            self.capture = exposed.Capture(enabled=False)
            self._poll_capture()
        self.capture.enable(self.policy.capturing,
                            paths.default_capture_file()
                            if self.policy.capturing and not self.capture.path
                            else None)
        self._switch.set_visible("Capture", self.policy.capturing)

        self.log("-- exposure: %s" % exposed.LABELS[level])
        if self.policy.readonly:
            self.log("-- writes frozen: nothing the network says will be "
                     "saved until this is set back")
        elif was == exposed.EXPOSED:
            self.log("-- writes allowed again")
        self._paint_exposure()
        # The listeners hold this policy object and this capture object,
        # and the gate reads the policy's limits on every admission, so a
        # level changed here reaches a card that is already running. What
        # it does NOT change is where the card is bound: that was decided
        # when it started, and `--exposed` is what moves it off loopback.
        if getattr(self, "_xport_started", False) and level != exposed.BENCH:
            self.log("-- the card is still bound where it started; this "
                     "changed what it will do, not where it answers")

    def _paint_exposure(self):
        level = self.policy.level
        colour = self.LEVEL_COLOUR[level]
        self._exp_pill.config(text=exposed.LABELS[level].upper(), fg=colour)
        note = exposed.BLURB[level]
        if self.capture is not None and self.policy.capturing:
            note += "  Capture: %s" % (self.capture.path or "in this window "
                                       "only")
        self._exp_note.config(text=note)
        if hasattr(self, "_cap_where"):
            self._cap_where.config(
                text=("Appending to %s" % self.capture.path)
                if self.capture is not None and self.capture.path
                else "Held in this window only -- no capture file was named. "
                     "Start with --capture FILE.jsonl to keep one.")

    def _set_breaker(self):
        """The wall breaker. Off is dark everywhere at once; on is a warm
        boot if the battery held RAM through the outage and a cold boot if
        it did not, exactly as the hardware behaves."""
        if not self._sw_breaker.get():
            self._cancel_boot()
            self.console.breaker_off()
            held = ("battery holding RAM" if self.console.battery_backup()
                    else "NO battery backup: RAM is already gone")
            self.log(f"-- MAIN BREAKER OFF ({held})")
        else:
            kind = self.console.breaker_on()
            if kind == "cold":
                self.log("-- MAIN BREAKER ON: cold boot, programming lost")
                self._after_console_change()
                self._boot_sequence("cold")
                return
            self.log("-- MAIN BREAKER ON: warm boot, everything kept")
            # A warm boot has screens of its own, and this showed none: it
            # wrote one log line and re-rendered, so the technician who has
            # just swapped a board saw nothing where the manual promises
            # three screens. FIDELITY M11.
            self._boot_sequence("warm")
            return
        self._render()

    # The two power-up sequences, 576013-637 and 576013-623.
    #
    # A COLD boot, 637 pp.12-13: CLEARING ALL RAM, SYSTEM COLD START, SYSTEM
    # SELF TEST, SYSTEM STARTUP COMPLETE, then *** SYSTEM RESET *** on the
    # printer -- and then, on a console with an archive in its E2 chip, the
    # restore offer comes up on its own; nobody has to go looking for it.
    #
    # A WARM boot, 637 p.16 verbatim, after replacing the setup storage
    # device with the battery left on: "The display will cycle through the
    # following warm boot screens: SYSTEM WARM START / SYSTEM SELF TEST /
    # SYSTEM STARTUP COMPLETE. At this point front panel display reads: MMM
    # DD, YYYY HH:MM:SS XM / ALL FUNCTIONS NORMAL."
    #
    # No CLEARING ALL RAM, no SYSTEM RESET printout, no restore offer and no
    # BATTERY IS OFF alarm: nothing was cleared, so there is nothing to
    # announce and nothing to offer to put back. 528 p.11 step 10 says the
    # same from the other end -- after a same-part-number swap the console
    # simply "has warm booted" and is ready.
    BOOT_SCREENS = {
        "cold": ["CLEARING ALL RAM", "SYSTEM COLD START", "SYSTEM SELF TEST",
                 "SYSTEM STARTUP COMPLETE"],
        "warm": ["SYSTEM WARM START", "SYSTEM SELF TEST",
                 "SYSTEM STARTUP COMPLETE"],
    }

    # 623 p.33 draws a fifth cold screen between the self test and the
    # startup, captioned "The display below appears only if you have one of
    # the modem modules or WPLLD installed"; 577013-528 p.8 draws the same
    # screen and says "This messsage only appears if a SiteFax or SiteLink
    # module is installed" (the manual's own spelling). It is the modem
    # initialising, so the two captions name the same set of cards between
    # them. The manual's own indentation of these figures is the FIGURE's --
    # its four screens are indented 7, 6, 7 and 6 -- so the two lines are
    # centred in the console's twenty four columns rather than counted off
    # the page.
    MODEM_CARDS = ("modem", "slink", "wplld", "wplldcom")
    WORKING_SCREEN = ("WORKING".center(COLS).rstrip() + chr(10)
                      + ("*" * 20).center(COLS).rstrip())

    def _boot_sequence(self, kind="cold"):
        """The power-up screens, and what follows them on a cold start."""
        screens = list(self.BOOT_SCREENS[kind])
        if kind == "cold" and any(self.console.has(card)
                                  for card in self.MODEM_CARDS):
            screens.insert(-1, self.WORKING_SCREEN)
        self._cancel_boot()
        # A boot starts the panel from the status display. The warm boot
        # returned to the mode, function, step and EDIT it was in when the
        # breaker opened, where 576013-637 p.16 says the display reads the
        # date over ALL FUNCTIONS NORMAL. FIDELITY O21.
        self._to_status_display()
        self.console.booting = True
        self.busy_until = time.time() + len(screens) * 1.2

        def show(i):
            self._boot_id = None
            if not self.console.powered:
                return
            if i < len(screens):
                self.msg = screens[i]
                self._render()
                self._boot_id = self.after(1200, lambda: show(i + 1))
                return
            self.msg = ""
            self.busy_until = 0.0
            self.console.booting = False
            if kind == "cold":
                self.paper_out(["*** SYSTEM RESET ***"])
                self.log("-- PRINT: *** SYSTEM RESET ***")
                if self.console.archive_exists():
                    self.boot_restore = "ask"
                    self.log("-- archive found: RESTORE SETUP DATA offered")
            self._render()

        show(0)

    def _cancel_boot(self):
        """Stop a boot under way, because the breaker has opened on it.

        The screens were a chain of `after` callbacks nothing cancelled:
        open the breaker half a second into a cold boot and `*** SYSTEM
        RESET ***` still printed on the dead console, with the restore
        offer armed on it, and closing it twice ran two chains side by
        side. FIDELITY O21.
        """
        pending, self._boot_id = getattr(self, "_boot_id", None), None
        if pending:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        if self.console.booting:
            self.msg = ""
        self.busy_until = 0.0
        self.console.booting = False

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
        state = ("closed (display off)" if self.console.display_blanked
                 else "open (display on)")
        self.log(f"-- DIP SW2-3 {state}")
        self._render()

    def _set_cover(self):
        self.console.cover_open = not self._sw_cover.get()
        state = ("REMOVED (Protective Cover Alarm posts)"
                 if self.console.cover_open else "fitted")
        self.log(f"-- power area cover {state}")

    def _set_fiscal_height(self):
        """Position 4: what the hardware allows, against the flag somebody
        programmed. I132 prints both, and until now only one could move.
        See FIDELITY M13 and BENCH.md P5."""
        self.console.fiscal_height_switch = self._sw_fiscal.get()
        state = "ON" if self.console.fiscal_height_switch else "OFF"
        self.log(f"-- DIP SW2-4 fiscal height security {state}")

    def _set_rs232_security(self):
        self.console.rs232_security = self._rs232_sec.get()
        code = self.console.security_code()
        state = "enabled" if self.console.rs232_security else "disabled"
        note = "" if code else " (no code set, so no effect yet)"
        self.log(f"-- RS-232 security DIP {state}{note}")

    def _dim_boards(self):
        """[(name, bus, slot)] for every DIM board in the cage: the EDIMs
        on the comm bus, then the MDIM on the power bus."""
        c = self.console
        out = [(f"EDIM {i}", 3, i) for i in range(1, c.count("edim") + 1)]
        if c.has("mdim"):
            out.append(("MDIM", 2, 1))
        if not out:
            out.append(("EDIM 1", 3, 1))
        return out

    def _pick_dim(self, name):
        for board, bus, slot in self._dim_boards():
            if board == name:
                self._dim_choice = (bus, slot)
                self.log(f"-- dispensers: showing the meters on {name} "
                         f"(bus {bus}, slot {slot})")
                self._refresh_site()
                self.refresh_traffic()
                return

    def _set_dim_port(self, port):
        down = not self._dim_ok[port].get()
        self.console.set_dim_port(port, down)
        state = ("DOWN (DIM Communication Alarm posts, its meters stop "
                 "reporting)" if down else "up")
        self.log(f"-- DIM port {port} link {state}")

    def _build_comm_errors(self, tab):
        """A UART or modem error on a comm port, which 888 reports and
        nothing here could suffer by itself. BENCH.md P9."""
        from . import wire
        bench.section(
            tab, "Comm port errors",
            "888's COMM STATUS report lists, per position, the last error "
            "the port had and when: a UART settings error, a modem that "
            "timed out, a lost carrier. No port on this bench can suffer one "
            "on its own, so this puts one on the record, with the port's own "
            "UART settings under it.")
        row = tk.Frame(tab, bg=bench.BG)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="port", bg=bench.BG, fg=bench.FAINT,
                 font=bench.FONT_SM).pack(side="left")
        ports = sorted(self.console.comm_positions()) or [1]
        self._err_port = tk.StringVar(value=str(ports[0]))
        pm = tk.OptionMenu(row, self._err_port, *[str(p) for p in ports])
        pm.config(bg="#24262b", fg=bench.INK, font=bench.MONO_SM,
                  relief="flat", highlightthickness=0,
                  activebackground=bench.CARD_EDGE, anchor="w", padx=4,
                  pady=0)
        pm["menu"].config(bg="#24262b", fg=bench.INK, font=bench.MONO_SM)
        pm.pack(side="left", padx=(4, 10))
        tk.Label(row, text="error", bg=bench.BG, fg=bench.FAINT,
                 font=bench.FONT_SM).pack(side="left")
        words = [f"{n:2d} {name}" for n, name in
                 sorted(wire.Handler.COMM_ERROR.items())]
        self._err_word = tk.StringVar(value=words[0])
        em = tk.OptionMenu(row, self._err_word, *words)
        em.config(bg="#24262b", fg=bench.INK, font=bench.MONO_SM,
                  relief="flat", highlightthickness=0,
                  activebackground=bench.CARD_EDGE, anchor="w", padx=4,
                  pady=0, width=26)
        em["menu"].config(bg="#24262b", fg=bench.INK, font=bench.MONO_SM)
        em.pack(side="left", padx=(4, 10))
        log = tk.Label(row, text="LOG ERROR", bg="#24262b", fg=bench.ACCENT,
                       font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                       cursor="hand2")
        log.pack(side="left")
        log.bind("<Button-1>", lambda _e: self._log_comm_error())
        clear = tk.Label(row, text="CLEAR", bg="#24262b", fg=bench.FAINT,
                         font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                         cursor="hand2")
        clear.pack(side="left", padx=(6, 0))
        clear.bind("<Button-1>", lambda _e: self._clear_comm_errors())
        bench.Tip(log, "Put this error on the port's record, stamped now. "
                       "I888 prints it under the port with its UART "
                       "settings, and TIME OF LAST COMM ERROR moves.")

    def _log_comm_error(self):
        port = int(self._err_port.get())
        number = int(self._err_word.get().split()[0])
        self.console.fault_comm(port, number)
        self.log(f"-- comm {port}: {self._err_word.get().strip()[3:]} "
                 "logged (888 reports it)")

    def _clear_comm_errors(self):
        port = int(self._err_port.get())
        self.console.clear_comm_errors(port)
        self.log(f"-- comm {port}: error record cleared")

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
        state = ("answers" if self.console.autodial.answers
                 else "does NOT answer (retries, then AUTODIAL FAILURE)")
        self.log(f"-- auto-dial receiver {state}")

    def attach_card(self, cfg, net):
        """Adopt a card the launcher has already brought up."""
        self.xport = cfg
        self._card_net = net
        self._xport_started = True
        self._sync_network()
        self._watch_card()

    def _watch_card(self):
        """Follow the card's address, because programming it moves it."""
        try:
            self._sync_network()
        except tk.TclError:
            return
        self.after(1000, self._watch_card)

    def _start_xport(self):
        if self._xport_started:
            return
        from . import xportnet
        if not self.xport.assigned():
            self.xport.reset_card()
        net = xportnet.CardNetwork(self.console, self.xport, log=self.log,
                                   powered=lambda: self.console.powered)
        net.start()
        self._card_net = net
        self._xport_started = True
        self.log("-- XPort networking started")
        self._sync_network()
        self._watch_card()

    def _reset_card(self):
        """Put the card back where it can be found again.

        A real card's own "7 Defaults" keeps the address it was given, which
        is right on a site and unhelpful on a bench: a trainee who has
        programmed an address they cannot reach has no way back. This clears
        the address too, so there is always one place to look.
        """
        self.xport.reset_card()
        self.log("-- card reset: address %s, tunnel port %d, 9600 8N1"
                 % (self.xport.ip, self.xport.port))
        self._sync_network()

    def _sync_network(self):
        rows, c = self._net_rows, self.xport
        net = getattr(self, "_card_net", None)
        rows["mac"].config(text=xport.mac_hex(c.mac, "-"))
        rows["ip"].config(text=(c.ip if c.assigned()
                                else f"0.0.0.0  (not set; reachable via "
                                f"AutoIP {c.autoip()})"))
        rows["port"].config(text=str(c.port))
        on = self._xport_started
        rows["web"].config(
            text=("tcp/%d  -- listening" % c.http_port) if on
            else ("tcp/%d  -- stopped" % c.http_port),
            fg=bench.OK if on else bench.MUTED)
        if on and net:
            host, note = net.bind_host()
            if note:
                rows["reach"].config(text=note, fg=bench.MUTED)
            else:
                rows["reach"].config(
                    text="%s:%d  -- inventory, 9999 setup, %d web"
                         % (host, c.port, c.http_port), fg=bench.OK)
        else:
            rows["reach"].config(text="not started", fg=bench.MUTED)
        if on:
            # The address in the title bar is the one to connect to, so it
            # is never a guess which of several windows is which card.
            self.title(f"{APP_NAME}  --  card at "
                       f"{c.effective_ip()}:{c.port}")
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
        self._build_identity(tab)
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
                if key == "smart":
                    self._build_smart_press(box)
                if key == "probe":
                    self._build_probe_gt(box)
            if bay == "comm":
                self._build_comm_slots(box)
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

    def _build_identity(self, tab):
        """The console's identity plate, which the Weights and Measures
        chart and audit reports print and an archive restore alone could
        fill. BENCH.md P11."""
        box = tk.LabelFrame(tab, bg=bench.BG, fg=bench.INK,
                            font=("Segoe UI", 8, "bold"),
                            text="Identity plate (Weights and Measures)")
        box.pack(anchor="w", fill="x", padx=8, pady=(4, 2))
        row = tk.Frame(box, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=(2, 4))
        self.serial_var = tk.StringVar(value=self.console.serial_number)
        self.wm_var = tk.StringVar(value=self.console.wm_office)
        for text, var, width in (("serial number", self.serial_var, 14),
                                 ("W&M office", self.wm_var, 24)):
            tk.Label(row, text=f"{text} ", bg=bench.BG, fg="#8f948b",
                     font=("Segoe UI", 8)).pack(side="left")
            e = tk.Entry(row, textvariable=var, width=width, bg="#2a2d31",
                         fg="#e8eae4", insertbackground="#e8eae4",
                         font=("Consolas", 8))
            e.pack(side="left", padx=(0, 10))
            e.bind("<KeyRelease>", lambda _e: self._set_identity())
        bench.Tip(row, "What the secured tank chart and its audit trail "
                       "print: CONSOLE SERIAL NUMBER and WEIGHTS AND "
                       "MEASURES, off the console's own label. IFSF's device "
                       "id reads the serial too.")

    def _set_identity(self):
        self.console.serial_number = self.serial_var.get().strip()[:20]
        self.console.wm_office = self.wm_var.get().strip()[:40]
        self.console.save()

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
        self._bench_say(f"software V{self.console.version}, version "
                        f"{info['version']}, SOFTWARE# {info['number']}")

    def _set_board(self):
        """Change the CPU board, which is the other half of the same question."""
        wanted = self.board_var.get().split()[0]
        if not self.console.set_board(wanted):
            self.board_var.set(self._board_label())
            return
        self._after_console_change()
        self._bench_say(
            f"CPU board {wanted} ({versions.board_name(wanted)}), "
            f"SOFTWARE# {self.console.software_info()['number']}")

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
            note_label.config(text=wires if known else self._not_yet(key))
        for key, (tick, label, note_label) in self.software_rows.items():
            known = self.console.knows_option(key)
            tick.config(state="normal" if known else "disabled")
            label.config(fg="#e2e5de" if known else "#6f746c")
            note_label.config(
                text="" if known else
                f"not until V{versions.arrived_in(versions.SOFTWARE_FEATURE[key])}")

    def _not_yet(self, key):
        """Why the bench will not let this card be fitted on this software.

        Two kinds of gate, because two documents state them: a feature row
        in Tables 3-1 to 3-5, and a card whose own installation manual has
        one those tables carry no row for -- the slot-4 multiport wants "an
        ECPU2 board, a NVMEM203 memory module and software version 24 or
        higher". See FIDELITY M5.
        """
        feature = versions.MODULE_FEATURE.get(key)
        if feature:
            gone = versions.withdrawn_in(feature)
            if gone is not None and self.console.version >= gone:
                return f"discontinued at V{gone}"
            return f"not until V{versions.arrived_in(feature)}"
        least, _boards = versions.MODULE_MINIMUM[key]
        return f"not until V{least}, on an ECPU2 with an NVMEM203"

    # How long a line test really takes, in console minutes. "Fifteen
    # minutes after a Gross test has completed the Periodic test starts with
    # the measurement of leak rate LR1. After another 15 minute waiting
    # period LR2 is measured", and the Annual needs a third.
    TEST_MINUTES = {"gross": 1, "periodic": 32, "annual": 47}

    def _say_how_long(self, kind, rate_key, said):
        """Warn the bench when a test is going to outlast its patience.

        A 0.20 line test is two fifteen-minute cycles at least, so on a
        console clock running at x1 the panel sits on TEST 0.20 for over
        half an hour. That is exactly what a real console does and it reads
        as hung -- the user reported it as one. The console's own screens
        cannot say more than the state they are documented to show, so the
        BENCH says it, and points at the control that fixes it.
        See FIDELITY U3.
        """
        minutes = self.TEST_MINUTES.get(rate_key, 0)
        if said != "TEST STARTED" or minutes < 5:
            return
        speed = self.console.clock_speed or 1.0
        wall = minutes * 60.0 / speed
        if wall < 120.0:
            return
        self.log(f"-- that is a {minutes} minute test on the console's "
                 f"clock, which at x{speed:g} is {wall / 60.0:.0f} minutes "
                 f"of sitting here. Bench > Console clock runs it faster.")

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

    # The archive format the console writes from SAVE SETUP DATA and reads
    # from RESTORE, which is also what `--seed` takes. One extension, so a
    # file saved here is offered back by the file dialog without being
    # hunted for.
    SETUP_FILES = [("Console setup data", "*.vrset"), ("All files", "*.*")]

    def _seed_asked(self):
        """Pour a saved site into this console, as `--seed` does on launch.

        The same door the command line uses, because the command line is no
        use to somebody who has already got the window open -- and because
        an exposed console is told, in the warning it puts up, to load a
        site of its own rather than answer strangers with the demo one.
        Until now there was no way to do that without restarting.
        """
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            parent=self, title="Seed programming from a saved setup",
            filetypes=self.SETUP_FILES,
            initialdir=os.path.dirname(self.console.archive_path() or "")
            or None)
        if not path:
            return
        try:
            n = self.console.seed(path)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            # A file somebody picked by hand is the likeliest place for a
            # wrong one, so this says which file and what was wrong with it
            # rather than dropping a traceback behind the window.
            messagebox.showerror("Could not read that file",
                                 "%s\n\n%s" % (os.path.basename(path), e),
                                 parent=self)
            return
        if not n:
            messagebox.showwarning(
                "Nothing to seed",
                "%s carries no programming this console recognises.\n\n"
                "A setup file is the one written by SAVE SETUP DATA, or by "
                "Save programming to file."
                % os.path.basename(path), parent=self)
            return
        self._after_console_change()
        self._sync_network()
        self.log("-- seeded %d value(s) from %s" % (n,
                                                    os.path.basename(path)))
        self._bench_say("seeded %d value(s) from %s"
                        % (n, os.path.basename(path)))

    def _archive_asked(self):
        """Write this console's programming out, for seeding another one.

        The console's own SAVE SETUP DATA writes to the E2 chip, which is a
        fixed path nobody chose. This is the same bytes to a file somebody
        picked, so a site worked up on the bench can be carried to another
        machine -- or to an exposed instance that must not be running the
        demo site.
        """
        from tkinter import filedialog, messagebox
        if exposed.writes_frozen():
            messagebox.showwarning(
                "Writes are frozen",
                "This console is exposed, so it is not writing to disk. Set "
                "the exposure back to Bench on the Network view to save its "
                "programming.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save this console's programming",
            defaultextension=".vrset", filetypes=self.SETUP_FILES)
        if not path:
            return
        try:
            self.console.archive_save(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Could not write that file",
                                 "%s\n\n%s" % (os.path.basename(path), e),
                                 parent=self)
            return
        self.log("-- programming saved to %s" % os.path.basename(path))
        self._bench_say("programming saved to %s" % os.path.basename(path))

    def _reset_console(self):
        """Everything out, everything blank, a console out of its box."""
        self.console.reset(keep_clock=True)
        self._after_console_change()
        self.log("-- console reset")
        self._bench_say("console reset: nothing programmed")

    def _load_preset(self):
        """Drop a whole example site in, cards and programming and fuel."""
        name = self.preset.get()
        # The card goes with the site: each example is programmed with its
        # own address, so switching site means going to find the new card,
        # as it would mean driving to the next forecourt.
        if not presets.load(self.console, name, card=self.xport):
            self._bench_say("no such preset")
            return
        self._after_console_change()
        self.log(f"-- loaded preset: {name}")
        card = presets.card_of(name)
        if card:
            self.log("-- this site's card is at %s, tunnel port %s"
                     % (card.get("ip"), card.get("port")))
        self._sync_network()
        self._bench_say(f"site loaded: {name}")

    def _after_console_change(self):
        """Put the panel and the bench back in step with the console."""
        self.func, self.step, self.device = 0, HEADER, 1
        self.subs = []
        self.mode = MODES.index("NORMAL")
        self._entered = False
        self.editing, self.buf, self.confirm = False, "", None
        self.chart_open = self.locked = False
        self.dlv = 0
        self.shift_ix = 0
        for key, var in self.module_vars.items():
            var.set(str(self.console.fitted(key)))
        for key, var in self.software_vars.items():
            var.set(self.console.licensed(key))
        self._sync_bays()
        self._sync_version()
        self._refresh_site()
        self.refresh_traffic()

    def _set_software(self, key):
        """A feature the S-Module does not carry is not on the menu."""
        if not self.console.knows_option(key):
            self.software_vars[key].set(False)
            self._bench_say(
                "not in this console at version "
                + self.console.software_info()["version"])
            return
        self.console.software[key] = bool(self.software_vars[key].get())
        self.console.save()
        self.func, self.step, self.device = 0, HEADER, 1
        self.subs = []
        self._refresh_site()
        self.refresh_traffic()
        state = "ENABLED" if self.console.licensed(key) else "NOT INSTALLED"
        self._bench_say(f"{SOFTWARE_NAME[key]} {state}")

    def _module_row(self, parent, key, name, part, wires, most):
        row = tk.Frame(parent, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=1)
        # the CARDS in the bay, which for a comm card is not the number of
        # positions it answers on: a dual-port module is one card and two.
        var = tk.StringVar(value=str(self.console.fitted(key)))
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

    def _build_smart_press(self, box):
        """Which of the smart sensor family's two cards is in the cage.

        "When Smart Sensor Interface Modules (8 inputs) or Smart Sensor /
        Press Modules (7 inputs) are installed" -- one console fits one kind,
        and the seven-input one is seven because its eighth channel is the
        atmospheric pressure sensor a Vac Sensor site needs. See FIDELITY M2.
        """
        self.smart_press_var = tk.BooleanVar(value=self.console.smart_press)
        tick = tk.Checkbutton(
            box, variable=self.smart_press_var,
            text=("  the seven-input Smart Sensor/Press Module instead "
                  "(332250-001, on-board ATMP sensor)"),
            bg=bench.BG, fg="#8f948b", selectcolor="#22242a",
            activebackground=bench.BG, activeforeground="#e2e5de",
            font=("Segoe UI", 8), anchor="w",
            command=self._set_smart_press)
        tick.pack(anchor="w", padx=22)

    def _set_smart_press(self):
        self.console.smart_press = bool(self.smart_press_var.get())
        self.console.save()
        label, part, _ohms = self.console.card("smart")
        row = self.module_rows.get("smart")
        if row:
            _spin, name, note, _text = row
            name.config(text=f" {label}")
            note.config(text=f"{self.console.wires('smart')} per card")
        self.func, self.step, self.device = 0, HEADER, 1
        self.subs = []
        self._refresh_site()
        self.refresh_traffic()
        self._bench_say(f"{part} {label}")

    def _build_probe_gt(self, box):
        """Which of the probe family's two cards is in the cage.

        The Probe/Thermistor Interface Module carries four thermistor
        positions beside its four probes, and a ground temperature thermistor
        has nowhere else to go: "the thermistor must be wired to thermistor
        position number 1 (positions 2 - 4 are not used)", 576013-879 Rev W
        p.60. It is the card the captured console has. See FIDELITY M4.
        """
        self.probe_gt_var = tk.BooleanVar(value=self.console.probe_gt)
        tick = tk.Checkbutton(
            box, variable=self.probe_gt_var,
            text=("  the Probe/Thermistor Interface Module instead "
                  "(847490-104, one ground temperature thermistor for VLLD)"),
            bg=bench.BG, fg="#8f948b", selectcolor="#22242a",
            activebackground=bench.BG, activeforeground="#e2e5de",
            font=("Segoe UI", 8), anchor="w",
            command=self._set_probe_gt)
        tick.pack(anchor="w", padx=22)

    def _set_probe_gt(self):
        self.console.probe_gt = bool(self.probe_gt_var.get())
        self.console.save()
        label, part, _ohms = self.console.card("probe")
        row = self.module_rows.get("probe")
        if row:
            _spin, name, note, _text = row
            name.config(text=f" {label}")
        self.func, self.step, self.device = 0, HEADER, 1
        self.subs = []
        self._refresh_site()
        self.refresh_traffic()
        self._bench_say(f"{part} {label}".strip())

    def _build_comm_slots(self, box):
        """Which of the four slots each comm card is in.

        The bay is four SLOTS and six POSITIONS: 577013-528 Rev G puts
        single-port modules in slots 1, 2 or 3 and dual-port ones in slot 4,
        and a dual-port module answers on two positions. Where a card sits
        is a fault of its own -- 576013-818 Table 7-2, "System will not
        communicate via RS-232 Module -- RS-232 Module in slot 4 of Comm Bay
        card cage -- Move Module to Comm Cage slots 1, 2, or 3" -- so the
        bench has to be able to put one in the wrong slot and move it back.
        See FIDELITY M7.
        """
        tk.Label(box, text="  Which slot each card is in. Slots 1 to 3 take "
                           "single-port modules and slot 4 takes a dual-port "
                           "one, which answers on two positions.",
                 bg=bench.BG, fg=bench.MUTED, font=("Segoe UI", 8),
                 anchor="w", justify="left").pack(anchor="w", padx=6,
                                                  pady=(6, 0))
        row = tk.Frame(box, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=(2, 0))
        self.comm_slot_vars = {}
        self.comm_slot_menus = {}
        for slot in sorted(CONSOLE_COMM_PORTS):
            cell = tk.Frame(row, bg=bench.BG)
            cell.pack(side="left", padx=(0, 10))
            tk.Label(cell, text=f"slot {slot}", bg=bench.BG, fg="#8f948b",
                     font=("Consolas", 8)).pack(anchor="w")
            var = tk.StringVar(value=self.EMPTY_SLOT)
            self.comm_slot_vars[slot] = var
            menu = tk.OptionMenu(cell, var, self.EMPTY_SLOT,
                                 command=lambda _v, s=slot: self._place_comm(s))
            menu.config(bg="#2a2d31", fg="#e8eae4", font=("Segoe UI", 8),
                        highlightthickness=0, activebackground="#3a3d43",
                        width=16, anchor="w")
            menu["menu"].config(bg="#2a2d31", fg="#e8eae4",
                                font=("Segoe UI", 8))
            menu.pack(anchor="w")
            self.comm_slot_menus[slot] = menu
        harness = tk.Frame(box, bg=bench.BG)
        harness.pack(anchor="w", fill="x", padx=6, pady=(2, 4))
        self.harness_var = tk.BooleanVar(value=self.console.dual_harness)
        self.double_harness_var = tk.BooleanVar(
            value=self.console.double_harness)
        for var, text, command in (
                (self.harness_var,
                 "Dual-port harness connected (4-pin to J4, 8-pin to J6)",
                 self._set_harness),
                (self.double_harness_var,
                 "Double dual-port harness 332609-001 (a second dual-port "
                 "module, in slot 3)", self._set_double_harness)):
            tick = tk.Checkbutton(harness, variable=var, text=" " + text,
                                  bg=bench.BG, fg="#e2e5de",
                                  selectcolor="#22242a",
                                  activebackground=bench.BG,
                                  activeforeground="#e2e5de",
                                  font=("Segoe UI", 8), anchor="w",
                                  command=command)
            tick.pack(anchor="w")
        self.comm_slot_note = tk.Label(box, text="", bg=bench.BG,
                                       fg="#8f948b", font=("Consolas", 8),
                                       anchor="w", justify="left")
        self.comm_slot_note.pack(anchor="w", padx=6, pady=(0, 4))
        self._build_mt_key(box)

    def _build_mt_key(self, box):
        """The Contractor's ID key in your hand.

        "You should have your ID key ready to plug into the MT Comm card in
        the Comm Bay of the TLS-350", 576013-610 ch.33 -- so this is where it
        goes. The blue key on the panel runs the log-in: STEP asks for the
        key, ENTER reads whatever is in the port, and what the console makes
        of it is the manual's own four answers. A key that is accepted is on
        8A3's active list afterwards, which is where that list comes from.
        See FIDELITY D13.
        """
        tk.Label(box, text="  The Contractor's ID key in the MT Comm card. "
                           "The blue key on the panel logs it in.",
                 bg=bench.BG, fg=bench.MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(anchor="w", padx=6, pady=(4, 0))
        row = tk.Frame(box, bg=bench.BG)
        row.pack(anchor="w", fill="x", padx=6, pady=(0, 6))
        self.key_id = tk.StringVar(value="A12345")
        self.key_label = tk.StringVar(value="J SMYTHE")
        self.key_expired = tk.BooleanVar(value=False)
        for text, var, width in (("ID", self.key_id, 8),
                                 ("label", self.key_label, 20)):
            tk.Label(row, text=f"{text} ", bg=bench.BG, fg="#8f948b",
                     font=("Segoe UI", 8)).pack(side="left")
            tk.Entry(row, textvariable=var, width=width, bg="#2a2d31",
                     fg="#e8eae4", insertbackground="#e8eae4",
                     font=("Consolas", 8)).pack(side="left", padx=(0, 10))
        tk.Checkbutton(row, variable=self.key_expired,
                       text=" certification expired", bg=bench.BG,
                       fg="#e2e5de", selectcolor="#22242a",
                       activebackground=bench.BG,
                       activeforeground="#e2e5de",
                       font=("Segoe UI", 8)).pack(side="left")
        # and what the technician came to do: a service code from
        # 577013-874's list, logged against the key in the box. 116 and 11A
        # are the record of these, and nothing wrote one. BENCH.md P10.
        visit = tk.Frame(box, bg=bench.BG)
        visit.pack(anchor="w", fill="x", padx=6, pady=(0, 6))
        tk.Label(visit, text="service code ", bg=bench.BG, fg="#8f948b",
                 font=("Segoe UI", 8)).pack(side="left")
        codes = [f"{code} {name}" for code, name in self.console.service_codes()]
        self.service_var = tk.StringVar(value=codes[0] if codes else "")
        sm = tk.OptionMenu(visit, self.service_var, *(codes or ["--"]))
        sm.config(bg="#2a2d31", fg="#e8eae4", font=("Segoe UI", 8),
                  highlightthickness=0, activebackground="#3a3d43",
                  width=30, anchor="w")
        sm["menu"].config(bg="#2a2d31", fg="#e8eae4", font=("Segoe UI", 8))
        sm.pack(side="left", padx=(0, 10))
        visit_btn = tk.Label(visit, text="LOG VISIT", bg="#24262b",
                             fg=bench.ACCENT, font=("Segoe UI", 8, "bold"),
                             padx=8, pady=1, cursor="hand2")
        visit_btn.pack(side="left")
        visit_btn.bind("<Button-1>", lambda _e: self._log_visit())
        bench.Tip(visit_btn, "Log a service entry under the key's ID: what "
                             "the technician did, from the Maintenance "
                             "Service Codes list (577013-874). The Service "
                             "Report History, 116 and 11A, is the record of "
                             "these.")

    EMPTY_SLOT = "-- empty --"

    def _comm_card_names(self):
        """{label: key} for the comm cards actually in the cage."""
        out = {}
        for key, name, part, bay, _w, _m in MODULES:
            if bay == "comm" and self.console.fitted(key):
                out[f"{MODULE_SHORT.get(key, name)}  {part or ''}".strip()] = key
        return out

    def _place_comm(self, slot):
        """Move a card into that slot, or empty it."""
        want = self.comm_slot_vars[slot].get()
        key = self._comm_card_names().get(want)
        self.console.place_comm(key, slot)
        self._sync_bays()
        self._refresh_site()
        self.refresh_traffic()
        self._bench_say(f"comm slot {slot}: "
                        + (MODULE_SHORT.get(key, "") if key else "empty"))

    def _set_harness(self):
        self.console.dual_harness = self.harness_var.get()
        self.console.save()
        self._sync_bays()

    def _set_double_harness(self):
        self.console.double_harness = self.double_harness_var.get()
        self.console.save()
        self._sync_bays()

    def _sync_comm_slots(self):
        """The four menus, what is in them, and what is not communicating."""
        if not getattr(self, "comm_slot_vars", None):
            return
        names = self._comm_card_names()
        layout = self.console.comm_layout()
        for slot, var in self.comm_slot_vars.items():
            menu = self.comm_slot_menus[slot]["menu"]
            menu.delete(0, "end")
            for label in [self.EMPTY_SLOT] + sorted(names):
                menu.add_command(
                    label=label,
                    command=lambda l=label, s=slot, v=var: (
                        v.set(l), self._place_comm(s)))
            here = layout.get(slot)
            var.set(next((l for l, k in names.items() if k == here),
                         self.EMPTY_SLOT))
        ports = self.console.comm_positions()
        reads = ", ".join(f"{p}:{self.console.comm_board_name(p)}"
                          for p in sorted(ports)) or "nothing fitted"
        dead = [str(s) for s in sorted(layout)
                if not self.console.comm_slot_works(s)]
        note = "  positions  " + reads
        if dead:
            note += "    not communicating: slot " + ", ".join(dead)
        self.comm_slot_note.config(text=note)

    def _set_module(self, key):
        """Fit or pull cards, within what the bay and the card allow."""
        try:
            want = int(self.module_vars[key].get() or 0)
        except ValueError:
            return
        if want and not self.console.knows_module(key):
            self.module_vars[key].set(str(self.console.fitted(key)))
            self._bench_say(
                "not in this console at version "
                + self.console.software_info()["version"])
            return
        if not self.console.set_module(key, want):
            self.module_vars[key].set(str(self.console.fitted(key)))
            bay = BAY_NAME[MODULE_BAY[key]]
            self._bench_say(f"will not fit: {bay}")
            self._sync_bays()
            return
        self.func = 0
        self.step = HEADER
        self.device = 1
        # and the branch ENTER went down into, which may have gone with the
        # card: left, STEP stood on a path with no screens under it
        self.subs = []
        self._sync_bays()
        self._refresh_site()
        self.refresh_traffic()
        n = self.console.fitted(key)
        self._bench_say(f"{n} x {MODULE_PART.get(key) or 'MODULE'} "
                        + MODULE_LABEL[key])

    def _sync_bays(self):
        for bay, label in self.bay_labels.items():
            used = self.console.bay_used(bay)
            slots = self.console.bay_slots(bay)
            label.config(text=f"  {used} of {slots} slots used")
        self._sync_comm_slots()

    def _build_site(self, tab):
        """The site: tanks with their probes, then everything wired in."""
        self.site_body = tk.Frame(tab, bg=bench.BG)
        self.site_body.pack(fill="both", expand=True, padx=12, pady=(0, 4))

    # ---- the forecourt's own day -------------------------------------------
    def _build_traffic(self, tab):
        self.traffic_body = tk.Frame(tab, bg=bench.BG)
        self.traffic_body.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._traffic_sync = []

    def refresh_traffic(self):
        """Rebuild the Traffic view. Public: the blend cards call it."""
        if not hasattr(self, "traffic_body"):
            return
        for w in self.traffic_body.winfo_children():
            w.destroy()
        self._traffic_sync = []
        c = self.console
        traffic = c.traffic
        body = self.traffic_body

        # ---- the master switch, and what the day is doing ----
        bench.section(
            body, "Site traffic",
            "Cars arriving, nozzles lifting, tanks going down -- the site "
            "doing what a site does while nobody is looking at it. Nothing "
            "here is new physics: a car is a nozzle lifted through the same "
            "door the LIFT NOZZLE pill uses, so BIR books it, the meter "
            "events table records it, CSLD's idle time is the gaps between "
            "the cars, and the line's handle goes up while the fuel moves. "
            "OFF is not the same as CLOSED: off means the generator is not "
            "there and a meter sells whatever gal/h somebody typed into it; "
            "CLOSED is a modelled site with its lights out.")
        head = bench.card(body)
        head.pack(fill="x")
        row = tk.Frame(head.inner, bg=bench.CARD)
        row.pack(fill="x", padx=10, pady=8)
        self._traffic_pill = tk.Label(
            row, text="RUNNING" if traffic.on else "OFF", bg="#24262b",
            fg=bench.OK if traffic.on else bench.FAINT,
            font=("Segoe UI", 9, "bold"), padx=14, pady=3, cursor="hand2")
        self._traffic_pill.pack(side="left")
        self._traffic_pill.bind("<Button-1>", lambda _e: self._traffic_off())
        bench.Tip(self._traffic_pill,
                  "Turn the whole generator on or off. Off leaves every "
                  "meter exactly where the bench left it.")
        self._traffic_now = tk.Label(row, bg=bench.CARD, fg=bench.MUTED,
                                     font=bench.MONO_SM, anchor="e")
        self._traffic_now.pack(side="right", fill="x", expand=True,
                               padx=(12, 0))
        # The day's exceptions go on their own wrapped line. They used to
        # be appended to the line above, which is one right-anchored label
        # on a row that also holds the RUNNING pill: with every tally
        # populated it wanted 1,563px and was given 1,198 on a 1400px
        # window, so the last counters on it -- the dry tanks, and
        # yesterday's closing figure -- could not be read at any window
        # size. A counter nobody can see is the thing this view keeps
        # being fixed for.
        self._traffic_tally = tk.Label(head.inner, bg=bench.CARD,
                                       fg=bench.FAINT, font=bench.MONO_SM,
                                       anchor="w", justify="left")
        self._traffic_tally.pack(fill="x", padx=10, pady=(0, 2))
        self._traffic_tally.bind("<Configure>", self._rewrap)

        # ---- why nothing is happening, when nothing is ----
        # The generator's one failure mode looks exactly like success: the
        # pill says RUNNING in green, the curve draws, the hour lights up,
        # and not one car ever arrives, because the console has no DIM in
        # it and `selling()` is empty. Everything that fixes that is on
        # another view, so the reason says which one and offers to go
        # there. See `Traffic.blockers()`.
        self._traffic_why = tk.Frame(head.inner, bg=bench.CARD)
        self._traffic_why.pack(fill="x", padx=10, pady=(0, 2))
        self._why_shown = None
        self._sync_traffic_why()

        # ---- volume and shape ----
        picks = tk.Frame(head.inner, bg=bench.CARD)
        picks.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(picks, text="volume", bg=bench.CARD, fg=bench.FAINT,
                 font=bench.FONT_SM, width=7, anchor="w").pack(side="left")
        self._level_pills = {}
        for name in _traffic.LEVEL_ORDER:
            pill = tk.Label(picks, text=name, bg="#24262b", fg=bench.MUTED,
                            font=("Segoe UI", 8, "bold"), padx=9, pady=2,
                            cursor="hand2")
            pill.pack(side="left", padx=(0, 3))
            pill.bind("<Button-1>", lambda _e, n=name: self._traffic_level(n))
            self._level_pills[name] = pill
        tk.Label(picks, text="cars/day", bg=bench.CARD, fg=bench.FAINT,
                 font=bench.FONT_SM).pack(side="left", padx=(10, 3))
        self._cars_var = tk.StringVar(value=f"{traffic.cars_per_day:g}")
        cars = tk.Entry(picks, textvariable=self._cars_var, width=6,
                        bg="#24262b", fg=bench.INK, font=bench.MONO_SM,
                        insertbackground=bench.INK, relief="flat",
                        justify="right")
        cars.pack(side="left")
        cars.bind("<KeyRelease>", self._traffic_cars)
        bench.Tip(cars, "The four words set this; type over it for a site "
                        "between two of them. 576013-818 Table 11-1 caps a "
                        "12,000 gallon gasoline tank at 200,000 gallons a "
                        "month -- about 6,600 a day -- and BUSY is a "
                        "three-tank forecourt at roughly that, which is "
                        "where p.11-10's 'very high activity' cause for NO "
                        "CSLD IDLE TIME lives.")

        shapes = tk.Frame(head.inner, bg=bench.CARD)
        shapes.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(shapes, text="shape", bg=bench.CARD, fg=bench.FAINT,
                 font=bench.FONT_SM, width=7, anchor="w").pack(side="left")
        self._shape_pills = {}
        for name in _traffic.SHAPE_ORDER:
            pill = tk.Label(shapes, text=_traffic.SHAPE_WORDS[name],
                            bg="#24262b", fg=bench.MUTED,
                            font=("Segoe UI", 8, "bold"), padx=9, pady=2,
                            cursor="hand2")
            pill.pack(side="left", padx=(0, 3))
            pill.bind("<Button-1>", lambda _e, n=name: self._traffic_shape(n))
            self._shape_pills[name] = pill

        curve = bench.TrafficCurve(head.inner, self)
        curve.pack(fill="x", padx=10, pady=(0, 8))
        self._traffic_sync.append(curve.sync)

        # ---- the grades ----
        selling = traffic.selling()
        bench.section(
            body, f"Grades ({len(selling)})",
            "What the site sells, read off the tanks' own PRODUCT LABELs -- "
            "the console has no idea what a grade is, and neither does "
            "this: a tank labelled PREMIUM UNLEADED is premium because a "
            "technician programmed it so. The share is relative, and it is "
            "shared out over the grades this site actually has.")
        if not selling:
            tk.Label(body, text="No meters mapped to tanks yet -- map them "
                     "on the Site view, under Dispensers.", bg=bench.BG,
                     fg=bench.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        else:
            grades = bench.card(body)
            grades.pack(fill="x")
            for fam in sorted(selling):
                grade = bench.GradeRow(grades.inner, self, fam, selling[fam])
                grade.pack(fill="x", pady=3)
                self._traffic_sync.append(grade.sync)

        # ---- the blends ----
        bench.section(
            body, f"Blended grades ({len(c.blends)})",
            "A blender mixes at the nozzle, and the TLS-350 never learns "
            "the ratio: 576013-818 p.12-7 says a tank maps to only one "
            "meter per fueling position, and the only blender support in "
            "the manuals is a DIM parameter telling the console how to read "
            "the POS. So a blend here is what it is on a site -- a grade "
            "button that runs two meters at a ratio -- and the console sees "
            "two ordinary transactions against two ordinary tanks. Set up "
            "regular and E-85 and you can blend E15 out of them.")
        add = tk.Label(body, text="+ ADD A BLEND", bg="#24262b",
                       fg=bench.ACCENT, font=("Segoe UI", 8, "bold"),
                       padx=10, pady=2, cursor="hand2")
        add.pack(anchor="w", pady=(0, 6))
        add.bind("<Button-1>", lambda _e: self._add_blend())
        if c.blends:
            grid = bench.FlowGrid(body, bench.BlendCard.W)
            grid.pack(fill="x")
            for meter in sorted(c.blends):
                card = bench.BlendCard(grid, self, meter)
                grid.add(card)
                self._traffic_sync.append(card.sync)

        # ---- the truck that comes on its own ----
        bench.section(
            body, "Deliveries",
            "A site does not wait until the tank is dry and the tanker does "
            "not arrive the moment it is called, so this is an order with a "
            "lead time and then a drop. What lands is a multi-compartment "
            "load down the same fill riser the truck dialog uses, and the "
            "console is never told it happened -- it watches the level rise "
            "and works it out. Mind that a delivery during an in-tank leak "
            "test invalidates the test: 576013-610 Table 29-4, RECENT "
            "DELIVERY.")
        deliver = bench.card(body)
        deliver.pack(fill="x")
        drow = tk.Frame(deliver.inner, bg=bench.CARD)
        drow.pack(fill="x", padx=10, pady=8)
        self._auto_pill = tk.Label(
            drow, text="ON" if traffic.auto_deliver else "OFF", bg="#24262b",
            fg=bench.OK if traffic.auto_deliver else bench.FAINT,
            font=("Segoe UI", 8, "bold"), padx=10, pady=2, cursor="hand2")
        self._auto_pill.pack(side="left")
        self._auto_pill.bind("<Button-1>", lambda _e: self._traffic_auto())
        tk.Label(drow, text="call a truck below", bg=bench.CARD,
                 fg=bench.FAINT, font=bench.FONT_SM).pack(side="left",
                                                          padx=(8, 3))
        self._low_var = tk.StringVar(value=f"{traffic.low_pct:g}")
        self._fill_var = tk.StringVar(value=f"{traffic.fill_pct:g}")
        self._lead_var = tk.StringVar(value=f"{traffic.lead_hours:g}")
        for var, tail in ((self._low_var, "% full, fill to"),
                          (self._fill_var, "%, tanker takes"),
                          (self._lead_var, "h")):
            entry = tk.Entry(drow, textvariable=var, width=4, bg="#24262b",
                             fg=bench.INK, font=bench.MONO_SM,
                             insertbackground=bench.INK, relief="flat",
                             justify="right")
            entry.pack(side="left")
            entry.bind("<KeyRelease>", self._traffic_delivery)
            entry.bind("<FocusOut>", self._traffic_delivery_done)
            tk.Label(drow, text=tail, bg=bench.CARD, fg=bench.FAINT,
                     font=bench.FONT_SM).pack(side="left", padx=(3, 6))
        self._orders_lbl = tk.Label(deliver.inner, bg=bench.CARD,
                                    fg=bench.MUTED, font=bench.MONO_SM,
                                    anchor="w", justify="left")
        self._orders_lbl.pack(fill="x", padx=10, pady=(0, 8))

        # ---- what is stopping fuel right now ----
        bench.section(
            body, "Shutdowns",
            "Everything currently stopping product moving, and why. A line "
            "the console shut down after a failed test; a line whose "
            "programmed DISABLE ALARM assignments (787, 7A7, 75B) are "
            "active; and a relay wired to a tank whose alarm has dropped "
            "the contactor -- 576013-623 p.7-24, 'allowing the operator to "
            "set a relay to shut down the submersible'. All three take the "
            "pump out, so the handle gets no pressure and no fuel moves.")
        shut = bench.card(body)
        shut.pack(fill="x")
        self._shutdown_lbl = tk.Label(shut.inner, bg=bench.CARD,
                                      fg=bench.MUTED, font=bench.MONO_SM,
                                      anchor="w", justify="left")
        self._shutdown_lbl.pack(fill="x", padx=10, pady=8)

        self._traffic_sync.append(self._sync_traffic_head)
        self._sync_traffic_head()

    @staticmethod
    def _rewrap(event):
        """Wrap a label to the room it has, not to a number in the source.

        Only when the number CHANGES: setting `wraplength` re-lays the
        label out, which is another <Configure>, and a handler that writes
        unconditionally is a handler that never stops.
        """
        want = max(200, event.width - 8)
        if int(event.widget.cget("wraplength")) != want:
            event.widget.config(wraplength=want)

    def _sync_traffic_why(self):
        """Rebuild the reason banner, but only when the reason changed.

        This runs off the same tick as the rest of the header, so it must
        cost nothing on the overwhelming majority of ticks where the answer
        is the same as it was. The cached key is the whole of what would be
        drawn, which also catches a meter mapped on the Site view -- that
        does not rebuild this tab, and the banner has to clear itself when
        the thing it was complaining about is fixed.
        """
        holder = getattr(self, "_traffic_why", None)
        if holder is None or not holder.winfo_exists():
            return
        traffic = self.console.traffic
        reason = traffic.idle_reason()
        rows = traffic.blockers() if traffic.on else []
        # the view we are ON is part of what gets drawn: a link to it is a
        # dead click, so it is rendered differently, so a change of view
        # has to invalidate this the same way a change of reason does
        key = (reason, tuple(rows), self._switch.current)
        if key == getattr(self, "_why_shown", None):
            return
        self._why_shown = key
        for w in holder.winfo_children():
            w.destroy()
        # Emptied, never unpacked: `pack_forget` then `pack` would put the
        # banner back at the END of the card, under the curve, instead of
        # between the RUNNING pill and the volume pills where it belongs.
        # An empty frame is a one-pixel line nobody sees.
        if reason is None and not rows:
            holder.pack_configure(pady=(0, 0))
            return
        holder.pack_configure(pady=(0, 2))
        # A shut site and a switched-off generator are STATES, not faults:
        # CLOSED is the one setting that gives CSLD all the idle time it
        # wants, and saying it in alarm red would be a lie about the bench.
        bad = traffic.on and reason is not None
        edge = tk.Frame(holder, bg=bench.BAD if bad else bench.CARD_EDGE,
                        padx=1, pady=1)
        edge.pack(fill="x")
        box = tk.Frame(edge, bg="#24262b")
        box.pack(fill="both", expand=True)
        if not traffic.on:
            head = "THE GENERATOR IS OFF"
        elif traffic.cars_per_day <= 0:
            head = "THE SITE IS CLOSED"
        elif reason is not None:
            head = "RUNNING, AND NOTHING IS BEING SOLD"
        else:
            head = "RUNNING, WITH SOMETHING TO FIX"
        tk.Label(box, text=head, bg="#24262b",
                 fg=bench.BAD if bad else bench.MUTED,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(
                     fill="x", padx=8, pady=(6, 0))
        if reason is not None and not rows:
            lone = tk.Label(box, text=reason, bg="#24262b", fg=bench.MUTED,
                            font=bench.FONT_SM, anchor="w", justify="left",
                            wraplength=620)
            lone.pack(fill="x", padx=8, pady=(1, 7))
            lone.bind("<Configure>", self._rewrap)
        for why, where in rows:
            line = tk.Frame(box, bg="#24262b")
            line.pack(fill="x", padx=8, pady=1)
            here = where == self._switch.current
            go = tk.Label(line,
                          text="BELOW ↓" if here else f"{where.upper()} →",
                          bg="#24262b",
                          fg=bench.FAINT if here else bench.ACCENT,
                          font=("Segoe UI", 8, "bold"),
                          cursor="arrow" if here else "hand2", padx=6)
            go.pack(side="right")
            if here:
                # a link to the view somebody is already looking at is a
                # dead click, and dead clicks are the whole reason this
                # banner exists
                bench.Tip(go, "Further down this view.")
            else:
                go.bind("<Button-1>",
                        lambda _e, n=where: self._switch.select(n))
                bench.Tip(go, f"Go to the {where} view, where this is fixed.")
            text = tk.Label(line, text=why, bg="#24262b", fg=bench.BODY,
                            font=bench.FONT_SM, anchor="w", justify="left",
                            wraplength=520)
            text.pack(side="left", fill="x", expand=True)
            text.bind("<Configure>", self._rewrap)
        if rows:
            tk.Frame(box, bg="#24262b", height=5).pack(fill="x")

    def _sync_traffic_head(self):
        c = self.console
        traffic = c.traffic
        self._sync_traffic_why()
        for name, pill in getattr(self, "_level_pills", {}).items():
            on = name == traffic.level_word()
            pill.config(fg=bench.INK if on else bench.MUTED,
                        bg=bench.CARD_EDGE if on else "#24262b")
        for name, pill in getattr(self, "_shape_pills", {}).items():
            on = name == traffic.shape
            pill.config(fg=bench.INK if on else bench.MUTED,
                        bg=bench.CARD_EDGE if on else "#24262b")
        self._traffic_pill.config(
            text="RUNNING" if traffic.on else "OFF",
            fg=bench.OK if traffic.on else bench.FAINT)
        up = traffic.nozzles_up()
        was = traffic.yesterday()
        self._traffic_now.config(
            text=f"{up} nozzle{'' if up == 1 else 's'} up   "
                 f"{traffic.cars:,} cars   {traffic.gallons:,.0f} gal today")
        rest = []
        if traffic.deliveries:
            rest.append(f"{traffic.deliveries} delivered")
        if traffic.balked:
            rest.append(f"{traffic.balked} queued away")
        if traffic.blocked:
            rest.append(f"{traffic.blocked} turned away by a shutdown")
        if traffic.dry:
            rest.append(f"{traffic.dry} found the tank empty")
        # yesterday's closing figure, which a seven hour tick across
        # midnight used to destroy before anyone could read it
        if was and was.cars:
            rest.append(f"yesterday: {was.cars:,} cars, "
                        f"{was.gallons:,.0f} gal")
        self._traffic_tally.config(text="   ".join(rest))
        # the tanker board
        rows = []
        for tank, order in sorted(traffic.orders.items()):
            when = time.strftime("%H:%M", time.localtime(order.due))
            rows.append(f"tank {tank}: {order.gallons:,.0f} gal ordered, "
                        f"due {when}")
        for tank in sorted(c.drops.running):
            rows.append(f"tank {tank}: {c.drops.describe(tank)}")
        for tank in sorted(c.tank_level):
            left = traffic.days_left(tank)
            if left is not None and left < 7:
                rows.append(f"tank {tank}: {left:.1f} days of stock left")
        self._orders_lbl.config(
            text="\n".join(rows) or "nothing ordered, nothing on the ground")
        # and what is dead
        dead = []
        for kind, number, label in c.programmed_lines():
            if not c.lines.disabled(kind, number):
                continue
            why = []
            if (kind, number) in c.leaks.disabled:
                why.append("failed test")
            for aa, nn, _tt in c.lines.shutdown_alarms(kind, number):
                why.append(c.alarm_name(aa, nn))
            tank = c.lines.tank_of(kind, number)
            if tank and c.outputs.pump_cut(tank):
                why.append(f"relay on tank {tank}")
            dead.append(f"{kind.upper()} {number} {label}: "
                        + ", ".join(why or ["shut down"]))
        for number, tank in c.outputs.cutting():
            dead.append(f"relay {number} ({c.outputs.label(number)}): "
                        f"tank {tank} pumps off")
        self._shutdown_lbl.config(
            text="\n".join(dead) or "nothing shut down; every pump answers "
                                    "its handle",
            fg=bench.BAD if dead else bench.MUTED)

    def _traffic_off(self):
        traffic = self.console.traffic
        traffic.on = not traffic.on
        if not traffic.on:
            # Hang up every nozzle THIS GENERATOR is holding, so turning it
            # off leaves the forecourt quiet rather than mid-fill -- and
            # nothing else. It used to stop every sale on the site, which
            # meant a technician's own fill, lifted by hand from the meter
            # card before the switch was touched, was hung up at zero
            # gallons by a switch that promises the bench "behaves exactly
            # as it did before it existed". See `Sale.by`.
            sales = self.console.sales
            for meter, blend_sale in list(sales.blends.items()):
                if getattr(blend_sale, "by", None) == "traffic":
                    sales.stop(meter)
            for meter, sale in list(sales.running.items()):
                if getattr(sale, "by", None) == "traffic":
                    sales.stop(meter)
        self.console.save()
        self.log(f"-- site traffic {'running' if traffic.on else 'off'}")
        self._sync_traffic_head()

    def _traffic_level(self, name):
        self.console.traffic.set_level(name)
        self._cars_var.set(f"{self.console.traffic.cars_per_day:g}")
        self.console.save()
        self.log(f"-- site traffic: {name}")
        self._sync_traffic_head()

    def _traffic_shape(self, name):
        self.console.traffic.shape = name
        self.console.save()
        self.log(f"-- site traffic shape: {_traffic.SHAPE_WORDS[name]}")
        self._sync_traffic_head()

    def _traffic_cars(self, _e=None):
        try:
            self.console.traffic.cars_per_day = float(self._cars_var.get()
                                                      or 0)
        except ValueError:
            return
        self.console.save()
        # the word over the box has to follow the number in it, or the view
        # goes on lighting CLOSED over a forecourt that is selling
        self._sync_traffic_head()

    def _traffic_auto(self):
        traffic = self.console.traffic
        traffic.auto_deliver = not traffic.auto_deliver
        self._auto_pill.config(
            text="ON" if traffic.auto_deliver else "OFF",
            fg=bench.OK if traffic.auto_deliver else bench.FAINT)
        self.console.save()

    # what each delivery box will accept: (attribute, lowest, highest)
    _DELIVERY_LIMITS = (("low_pct", 1.0, 95.0),
                        ("fill_pct", 5.0, 100.0),
                        ("lead_hours", 0.0, 168.0))

    def _delivery_boxes(self):
        return zip((self._low_var, self._fill_var, self._lead_var),
                   self._DELIVERY_LIMITS)

    def _traffic_delivery(self, _e=None):
        """Take the delivery settings, but not values that mean "never".

        Every box took `float(text or 0)` and nothing checked the range.
        Clearing the low% box -- which is how anyone retypes it -- wrote
        0, and "order when the tank falls below 0%" is never: a tank at 5%
        of capacity sat there for twenty console hours and no truck was
        called, with the board still reading "nothing ordered, nothing on
        the ground". In the other direction low=500/fill=400 was accepted
        and re-ordered forever, 16 loads in 44 hours, every tank pinned at
        capacity with its overfill alarm posted.
        """
        traffic = self.console.traffic
        for var, (name, low, high) in self._delivery_boxes():
            text = var.get().strip()
            if not text:
                # a box being retyped is not a setting of zero
                continue
            try:
                value = float(text)
            except ValueError:
                continue
            setattr(traffic, name, min(high, max(low, value)))
        self.console.save()

    def _traffic_delivery_done(self, _e=None):
        """Put back what was actually stored, once the box loses focus.

        Otherwise a box that was clamped goes on showing the number that
        was refused -- which is the same kind of lie as the setting it was
        clamped for.
        """
        traffic = self.console.traffic
        for var, (name, _low, _high) in self._delivery_boxes():
            var.set(f"{getattr(traffic, name):g}")

    def _add_blend(self):
        meter = bench.free_meter(self.console)
        if meter is None:
            self.log("-- no free meter for another blend")
            return
        self.console.blends[meter] = {"label": "MID", "parts": []}
        self.console.save()
        self.refresh_traffic()

    def _refresh_site(self):
        for w in self.site_body.winfo_children():
            w.destroy()
        self._site_sync = []
        c = self.console
        body = self.site_body
        if not hasattr(self, "_dim_choice"):
            self._dim_choice = (bench.DEFAULT_BUS, bench.DEFAULT_SLOT)
        if self._dim_choice not in {(b, s) for _n, b, s in self._dim_boards()}:
            self._dim_choice = (bench.DEFAULT_BUS, bench.DEFAULT_SLOT)

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
            # no width: a sensor tile is as wide as the longest thing
            # written on it, which is the only width that survives a
            # screen that is not at 96 DPI. See `SensorTile`.
            grid = bench.FlowGrid(body)
            grid.pack(fill="x")
            for mod, num, label in sensors:
                grid.add(bench.SensorTile(grid, self, mod, num, label))

        # ---- the external inputs: dry contacts, five kinds of meaning ----
        contacts = c.inputs.configured()
        bench.section(
            body, f"External inputs ({len(contacts)})",
            "A dry contact per programmed input on the I/O module. What the "
            "console does when one closes is the TYPE it was given at "
            "EXTERNAL INPUT SETUP: an alarm to report, a generator whose "
            "tanks are tested while it is off, a pump that says the "
            "forecourt is busy, a remote ALARM/TEST button, or a vapour "
            "processor's run signal.")
        if not contacts:
            tk.Label(body, text="No I/O module fitted."
                     if not c.has("io")
                     else "None configured yet: switch a position on at "
                     "INPUT CONFIG in EXTERNAL INPUT SETUP.", bg=bench.BG,
                     fg=bench.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        else:
            grid = bench.FlowGrid(body, bench.InputTile.W)
            grid.pack(fill="x")
            for number in contacts:
                tile = bench.InputTile(grid, self, number)
                grid.add(tile)
                self._site_sync.append(tile.sync)

        # ---- the output relays: what the console is driving ----
        coils = c.outputs.configured()
        bench.section(
            body, f"Output relays ({len(coils)})",
            "Every relay programmed at OUTPUT RELAY SETUP, and whether the "
            "console has it pulled in: a STANDARD relay follows the alarms "
            "it is assigned to, a MOMENTARY one lets go when the alarm is "
            "acknowledged, a PUMP CONTROL OUTPUT follows the pump request on "
            "its tank. The lamp is the coil; the tile says what a meter "
            "across the contacts would read.")
        if not coils:
            tk.Label(body, text="No relay module fitted."
                     if not (c.has("relay") or c.has("io"))
                     else "None configured yet: switch a position on at "
                     "RELAY CONFIG in OUTPUT RELAY SETUP.", bg=bench.BG,
                     fg=bench.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        else:
            grid = bench.FlowGrid(body, bench.RelayTile.W)
            grid.pack(fill="x")
            for number in coils:
                tile = bench.RelayTile(grid, self, number)
                grid.add(tile)
                self._site_sync.append(tile.sync)

        # ---- the dispensers, which BIR reconciles the probe against ----
        bench.section(
            body, "Dispensers",
            "Assign a meter to a tank and give it a flow, and the tank "
            "goes down, the meter total goes up, and the shift "
            "reconciliation has something to reconcile. The metered "
            "transactions reach the console through a Dispenser Interface "
            "Module; pull the DIM, or fault its link below, and the fuel "
            "still flows at the site but this console cannot see it.")
        # The cards are drawn on every site. The fuel leaves the tank on the
        # handle alone (`Sales.draw`); what the key and the DIM decide is
        # whether the console gets to book it, and that is a note, not a
        # reason to hide the dispensers.
        if not c.licensed("bir"):
            tk.Label(body, text="BIR is not installed: the tanks still go "
                     "down as these meters sell, but nothing reconciles "
                     "it.", bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        has_dim = c.has("edim") or c.has("mdim")
        if not has_dim:
            tk.Label(body, text="No DIM fitted: the fuel still leaves the "
                     "tanks, but no meter data reaches the console. Fit an "
                     "EDIM or MDIM on the Modules view for that.",
                     bg=bench.BG, fg=bench.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
        row = tk.Frame(body, bg=bench.BG)
        row.pack(fill="x", pady=(0, 4))
        if has_dim:
            # one link per DIM port, because BA1 keeps a fault history per
            # port and the alarm is per port too. BENCH.md D1.
            self._dim_ok = {}
            for port in range(1, c.DIM_PORTS + 1):
                var = tk.BooleanVar(value=port not in c.dim_down)
                self._dim_ok[port] = var
                box = tk.Checkbutton(
                    row, text=f"  DIM port {port} link up",
                    variable=var,
                    command=lambda p=port: self._set_dim_port(p),
                    bg=bench.BG, fg=bench.BODY, selectcolor=bench.CARD,
                    activebackground=bench.BG, activeforeground=bench.INK,
                    font=bench.FONT)
                box.pack(side="left", padx=(0, 14))
                bench.Tip(box, "The RS-232 link from this DIM port to the "
                               "POS or dispenser controller. Take it down "
                               "and the DIM Communication Alarm posts for "
                               "the port, the meters on it stop reporting, "
                               "and BA1's fault history gets a row with "
                               "its post time, clear time and duration."
                               + (" The bench's meters are all on port 1."
                                  if port == 1 else ""))
        # Which DIM board the cards below belong to. 7B1 keys a meter
        # by bus and slot -- "Bus 3: 01-06 / 3=Comm Bus, Bus 2: 09-16 /
        # 2=Power Bus (MDIM)" -- and a second board's meter 1 is not the
        # first board's, which is what lets two POS terminals report the
        # same FP and M. The bench sat on the first EDIM alone; a site
        # with an MDIM, or a second EDIM, could not be staged. BENCH D4.
        boards = self._dim_boards()
        if len(boards) > 1:
            pick = tk.Frame(row, bg=bench.BG)
            pick.pack(side="right")
            tk.Label(pick, text="board", bg=bench.BG, fg=bench.FAINT,
                     font=bench.FONT_SM).pack(side="left")
            names = [name for name, _bus, _slot in boards]
            current = [name for name, bus, slot in boards
                       if (bus, slot) == self._dim_choice]
            self._dim_var = tk.StringVar(
                value=current[0] if current else names[0])
            menu = tk.OptionMenu(pick, self._dim_var, *names,
                                 command=self._pick_dim)
            menu.config(bg="#24262b", fg=bench.INK, font=bench.MONO_SM,
                        relief="flat", highlightthickness=0,
                        activebackground=bench.CARD_EDGE, anchor="w",
                        padx=4, pady=0)
            menu["menu"].config(bg="#24262b", fg=bench.INK,
                                font=bench.MONO_SM)
            menu.pack(side="left", padx=(4, 0))
            bench.Tip(menu, "Which DIM board these meters are on. A "
                            "meter is bus, slot, position and meter to "
                            "the console, so meter 1 on a second board "
                            "is a different meter from meter 1 on the "
                            "first; the MDIM on the power bus carries "
                            "meters 9 to 16.")
        bus, slot = self._dim_choice
        first = 9 if bus == 2 else 1
        grid = bench.FlowGrid(body, bench.MeterCard.W)
        grid.pack(fill="x")
        for number in range(first, first + 8):
            ident = bench.MeterId(bus, slot, (number + 1) // 2, number)
            card = bench.MeterCard(grid, self, ident)
            grid.add(card)
            self._site_sync.append(card.sync)

        # ---- the vapour monitor controllers, on a VMCI site ----
        if c.has("vmc"):
            controllers = sorted(c.vmc_serials) or []
            bench.section(
                body, f"Vapour monitor controllers ({len(controllers)})",
                "A VMC reports a status per fuelling side and the console "
                "does not decide it -- 576013-610 Table 29-23's four alarm "
                "rows ARE four of the eight statuses a controller sends. So "
                "the bench sets the status the way it sets a sensor's "
                "state, and the console does the rest: the alarm posts in "
                "category 36, both alarm histories carry it, and the VMC "
                "Alarm History Report has rows in it. A controller exists "
                "once it has a serial number, which is VMC SETUP on the "
                "panel.")
            if not controllers:
                tk.Label(body, text="None yet: add a serial number at VMC "
                         "SETUP in Setup Mode.", bg=bench.BG,
                         fg=bench.MUTED, font=("Segoe UI", 9)).pack(
                             anchor="w")
            else:
                grid = bench.FlowGrid(body, bench.VmcTile.W)
                grid.pack(fill="x")
                for number in controllers:
                    for side in c.VMC_SIDES:
                        grid.add(bench.VmcTile(grid, self, number, side))

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
            # IsdTile's own width, not the sensor tile's: these are ISD
            # tests, they are laid out by `IsdTile`, and a grid told to
            # column on somebody else's tile width leaves a ragged gap or
            # overlaps -- it went unnoticed only while the two were equal.
            grid = bench.FlowGrid(body, bench.IsdTile.W)
            grid.pack(fill="x")
            # The collection test a site HAS depends on which system it
            # is: a Balance site's is FLOW COLLECT and an Assist site's is
            # GROSS COLLECT and DEGRD COLLECT. The bench offered the assist
            # pair on every console, including the default one, which is a
            # Balance site. See FIDELITY I10.
            from . import isd as _isd
            ISD_TESTS = [("leakage", "VAPOR LEAKAGE"),
                         ("gross", "GROSS PRESSURE"),
                         ("degrade", "DEGRD PRESSURE")]
            ISD_TESTS += [(key, _isd.COLLECTION_LABEL[key])
                          for key in _isd.COLLECTION_TESTS[c.evr_site()]]
            ISD_TESTS += [("sensor", "SENSOR OUT"),
                          ("setup", "ISD SETUP")]
            for key, label in ISD_TESTS:
                grid.add(bench.IsdTile(grid, self, key, label))
            # and the processor's own switch, where there is a processor
            if c.vapor_processor_type() != "NONE":
                tile = bench.ProcessorTile(grid, self)
                grid.add(tile)
                self._site_sync.append(tile.sync)

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
                                "buf": sc.get("buf"),
                                "prompt": sc.get("prompt"),
                                "capture": sc.get("capture"),
                                "console": sc.get("console"),
                                "toggle": sc.get("toggle"),
                                "sel": sc.get("sel"),
                                "choices": sc.get("choices"),
                                "then": sc.get("then"),
                                "leave": sc.get("leave"),
                                "choices_from": sc.get("choices_from"),
                                "pick": sc.get("pick"),
                                "run": sc.get("run"),
                                "depth": sc.get("depth", 0),
                                "vp": sc.get("vp"),
                                "when": sc.get("when"),
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
        steps = [st for st in steps if self._selection_allows(st)]
        if MODES[self.mode] == "SETUP":
            # Setup Mode has the same levels Diagnostic Mode has: a screen
            # that says PRESS <ENTER> holds the screens below it, and they
            # are not steps of the function. See `_level_screens`.
            return self._setup_screens(steps)
        return steps

    def _setup_screens(self, steps):
        """This level's screens, with the ones that repeat spread out."""
        out = []
        for st in self._level_screens(steps):
            if st.get("expand") == "meters":
                # "Press STEP to continue to the next meter", p.17-6: one
                # screen per meter on the map, which is what the console
                # has to show rather than one example row. The manual's own
                # figure IS the empty template, so a console with nothing
                # mapped still draws one.
                keys = sorted(self.console.meter_map)
                out += [dict(st, meter=k) for k in keys] or [dict(st)]
                continue
            out.append(st)
        return out

    def _level_screens(self, screens, path=None):
        """The screens at the level the panel is standing on.

        A screen's `depth` is how far down it sits: absent or 0 at the top,
        1 inside the branch that offered it, 2 inside a branch of that. The
        path says which branch was entered at each level, so the screens on
        offer are the run that follows the deepest one -- stopping at the
        first screen that climbs back OUT of it, and skipping the ones
        deeper in, which belong to a branch that has not been entered.
        """
        path = self.subs if path is None else path
        lvl = len(path)
        out = []
        for sc in screens[path[-1] + 1 if path else 0:]:
            d = sc.get("depth") or 0
            if d < lvl:
                break
            if d == lvl:
                out.append(sc)
        return out

    def _level_index(self, screens, n, path=None):
        """Where the n-th screen of this level sits in the offered list."""
        path = self.subs if path is None else path
        lvl = len(path)
        start = path[-1] + 1 if path else 0
        seen = 0
        for i in range(start, len(screens)):
            d = screens[i].get("depth") or 0
            if d < lvl:
                break
            if d != lvl:
                continue
            if seen == n:
                return i
            seen += 1
        return None

    def _map_row(self, cells):
        """The five cells at the columns p.17-5 draws them at."""
        return screens.map_row_text(cells)

    def _map_choices(self, n, cells):
        """What CHANGE offers in the n-th cell of the map row.

        p.17-5 lists them: the bus is "2: Power Bay (MDIMs)" or "3:
        Communication Bay (BDIMs, EDIMs, or CDIMs)", the slot is whatever
        that bus has, the fueling position and the meter are 00-99, and the
        tank is "99: Tank with no probe", "00: Remove meter from tank/meter
        map" or a tank number. `X` is on the page too, as the cell's own
        unset state, and is where the cycle starts and returns to.
        """
        blank = "X" * MAP_CELLS[n][1]
        if n == 0:
            return [blank] + sorted(wirelists.BUS_SLOTS)
        if n == 1:
            # a slot belongs to its own bus, and until a bus is picked there
            # is no list to walk
            return [blank] + [f"{s:02d}"
                              for s in wirelists.BUS_SLOTS.get(cells[0], ())]
        return [blank] + [f"{i:02d}" for i in range(100)]

    def _ascend(self):
        """Come back out of a branch, onto the screen that offered it."""
        child = self.subs[-1]
        self.subs = self.subs[:-1]
        fn = self.cur_function()
        screens = self._offered(fn) if fn else []
        lvl = len(self.subs)
        self.step = 0
        n = 0
        for i in range(self.subs[-1] + 1 if self.subs else 0, len(screens)):
            d = screens[i].get("depth") or 0
            if d < lvl:
                break
            if d != lvl:
                continue
            if i == child:
                self.step = n
                return
            n += 1

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
        screens = self._diag_offered(fn)
        # The branch is its children, and does not open with a copy of the
        # screen that offered it. In every chapter-6 figure the `E` arrow
        # runs from the `PRESS <ENTER>` screen TO THE FIRST SCREEN OF THE
        # BRANCH: Figure 6-2 draws `SYSTEM CONFIGURATION / PRESS <ENTER>`
        # -E-> `SLOT 1 4 PROBE / POR= XXXXXX C= XXXXXX`, Figure 6-25 draws
        # `BIR METER MAP / PRESS <ENTER>` -E-> `BIR METER MAP / STATUS:
        # COMPLETE`, and the same at COMM BOARD RE-INITIALIZE, PC and DIM
        # and WPLLD DIAGNOSTIC DATA, the two CSLD reports, both key-block
        # branches and the Mag Sensor diagnostics.
        #
        # The parent was the first member of the list, so ENTER redrew the
        # screen you were already on and every branch in Diagnostic Mode
        # cost one keypress more than the manual's diagram -- with "nothing
        # happened" as the feedback for a correct press, which teaches a
        # technician to press ENTER twice or to give the branch up as dead.
        # It also made BACKUP's way out invisible: the screen on each side
        # of the boundary was the same two lines. See the diagnostic-mode
        # audit, DG3, and the key-behaviour audit, K11.
        out = []
        for sc in self._level_screens(screens):
            if sc.get("expand") == "slots":
                # one screen per slot in the cage, which is what the console
                # has to show rather than one example slot
                out += [{"text": l1, "l2": l2, "code": None, "depth": 1}
                        for l1, l2 in self.console.slot_report()]
                continue
            if sc.get("expand") == "mt_keys":
                # "Continue to press STEP to scroll through a list of active
                # keys": one screen per key the console will accept, from the
                # console's own list rather than the three names in Figure
                # 6-4. CHANGE on a row arms BLOCK: YES and ENTER selects that
                # key, which is how the figure blocks J DOE. See FIDELITY
                # D13.
                #
                # The label runs left in seventeen and the ID is six, which
                # is 8A3's own two fields and comes to 24 with the space
                # between them -- exactly the display.
                out += [{"text": f"{name:<17.17s} {ident}",
                         "l2": "BLOCK: NO", "code": None, "depth": 1,
                         "act": "mt_select", "ident": ident,
                         "verbatim": True}
                        for ident, name in self.console.tracker_keys()]
                continue
            out.append(sc)
        return out

    def _diag_offered(self, fn):
        """The screens of one diagnostic function this console can show.

        Both the list a walk STEPS through and the index ENTER descends on
        have to be built from this same list. `_diag_children` used to count
        over `fn["steps"]` raw and `_diag_screens` filtered it, so on any
        console where a screen was hidden the two disagreed and ENTER
        descended into a different branch from the one the panel was
        standing on. Nothing hid a top-level screen until D10's four gates,
        which is why it had never shown. See FIDELITY D10 and D15.
        """
        # some diagnostic screens belong to one vapor processor only:
        # the polisher has LOAD/EFFLUENT/VALVE/TEMP, the membrane has VP
        # STATE/HC SENSOR. A screen's "vp" says which processor it is for.
        vp = (self.console.values.get("SV4000") or "00")
        polisher = vp in ("05", "06")
        out = []
        for sc in fn["steps"]:
            if sc.get("when") and not self.console.visible(sc, self.device):
                # a diagnostic screen can be conditional too: Fig 6-3 gives
                # SERVICE REPORT two branches, and which one a console walks
                # is whether it has a Maintenance Tracker. See FIDELITY D3.
                continue
            if not self._selection_allows(sc):
                # a choice made on the panel rather than programmed: CSLD
                # MONTHLY REPORT's CURRENT or PREVIOUS decides which tank
                # screen follows it, 576013-610 Rev AC p.27-3. FIDELITY D23.
                continue
            want = sc.get("vp")
            if want and (want == "polisher") != polisher:
                continue
            out.append(sc)
        return out

    def _offered(self, fn):
        """Every screen of this function this console can show, all levels.

        The list `self.subs` indexes. Diagnostic Mode gates screens on the
        vapor processor and on `when`; Setup Mode gates them on `when` and
        on the panel's own selection.
        """
        if MODES[self.mode] == "DIAGNOSTIC":
            return self._diag_offered(fn)
        return [st for st in self.console.visible_steps(fn, self.device)
                if self._selection_allows(st)]

    def _branch_at(self):
        """Where, in the offered list, the branch under the panel starts.

        None unless the screen the panel is standing on is one that says
        PRESS <ENTER> and has screens a level down behind it.
        """
        fn = self.cur_function()
        if fn is None or MODES[self.mode] not in ("DIAGNOSTIC", "SETUP"):
            return None
        if self.step == HEADER:
            return None
        screens = self._offered(fn)
        here = self._level_screens(screens)
        if not here:
            return None
        i = self._level_index(screens, self.step % len(here))
        if i is None:
            return None
        if (i + 1 < len(screens)
                and (screens[i + 1].get("depth") or 0) == len(self.subs) + 1):
            return i
        return None

    def _diag_children(self):
        """`_branch_at` under the name the register cites.

        FIDELITY D15 and CLOSED U37 both name this method, and a citation
        in a comment outlives the entry it names -- so the name stays and
        the one-mode guard with it.
        """
        if MODES[self.mode] != "DIAGNOSTIC":
            return None
        return self._branch_at()

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

    def _shift_no(self):
        """The programmed shift Last-Shift Inventory is standing on."""
        order = self.console.shifts.programmed() or [1]
        return order[self.shift_ix % len(order)]

    def _shift_head(self):
        """`T 1: SHIFT TIME 1`, p.8-1's head for every one of its readings."""
        return f"T {self.device}: SHIFT TIME {self._shift_no()}"

    def _enter_shift_adjustment(self):
        """ENTER on DLVY ADJUSTMENT: the total off the tickets, for the
        shift and tank the panel is standing on."""
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        try:
            gallons = float(text)
        except ValueError:
            self._refuse("numbers only")
            return
        if not self.console.shifts.set_adjustment(self._shift_no(),
                                                  self.device, gallons):
            # a shift that has neither run nor closed on this console has
            # no record for the amount to go into
            self.log("-- no shift record to adjust")
            return
        self.confirm = [f"DLVY ADJUSTMENT: {gallons:.0f}"[:COLS], CONT_STEP]
        self._render()

    def _increase_report(self, device):
        """INVENTORY INCREASE for the tank shown: the slip a delivery prints
        by itself, which is p.4-4's report and was measured off real paper
        (W11, W16). A tank with no finished delivery has no increase to
        print, and nothing comes out."""
        record = self.console.deliveries.last(device) if device else None
        if record is None or not record.end:
            self.log("-- no delivery on this tank to report")
            return []
        return printer.delivery(self.console, device, record)

    def _delivery_day(self, step):
        """The day PRINT on a delivery's own screen reports, or None.

        576013-610 Rev AC p.5-3, on `T 1:XXX XX,XXXX XX:XXXX` / `TICKET
        VOLUME: XXX`: "Press PRINT to print a Delivery Report for all
        deliveries for the day and tank shown." FIDELITY O7.
        """
        record = self._delivery()
        if (step or {}).get("dlv") != "ticket" or record is None:
            return None
        return record.end["at"]

    def _delivery_lines(self, step, text):
        """"T 1:MMM DD, YYYY HH:MMxM / TICKET VOLUME: XXX"."""
        what = step["dlv"]
        if what == "tank":
            # 576013-610 Rev AC p.5-2: "Press STEP to verify that you want
            # to insert ticketed deliveries. The system displays this
            # message:" `SELECT: INSERT` / `T 1: UNLEADED GASOLINE`, "To
            # select other tanks in the system, press TANK/SENSOR" -- and
            # p.5-1 and p.5-3 draw the EDIT/VIEW twin. Neither branch had
            # it, so a ticket was inserted against a tank no screen named.
            # FIDELITY U7.
            label = self.console.text("602", self.device) or ""
            mode = self.sel.get("dlv_mode") or "EDIT/VIEW"
            return [f"SELECT: {mode}"[:COLS],
                    f"T {self.device}: {label}".rstrip()[:COLS]]
        if what in ("date", "time", "insert", "insertbol"):
            held = self._insert.get(what, "")
            if self.editing:
                # The label stays while you type, which is what
                # 576013-610 Rev AC p.5-2 draws for all four of these
                # screens: `ENTER DELIVERY DATE` / `DATE: XX/XX/XXXX`,
                # `ENTER DELIVERY TIME` / `TIME: XX:XX XX`, `TICKET
                # VOLUME: XXX`, and p.5-3's `BOL: 23223`. This arm dropped
                # every label the four arms below it build, so the glass
                # read a bare `12252026` with nothing to say what it was.
                #
                # The EDIT/VIEW branch thirty lines down has kept its label
                # all along -- `f"{text}: {self._edit_text()}"` -- which is
                # what makes this an oversight in one renderer rather than
                # a decision. See the refusals audit, R5.
                return [self._insert_head(what, text)[:COLS],
                        (self._insert_prefix(what)
                         + self._edit_text())[:COLS]]
            if what in ("date", "time"):
                # a value already typed is shown in the screen's own shape,
                # not as the digit run the keypad sent
                now = self.console.now()
                clock = time.strftime("%m/%d/%Y" if what == "date"
                                      else "%I:%M %p", now)
                shown = self._insert_shown(what, held) if held else clock
                return [text[:COLS],
                        (self._insert_prefix(what) + str(shown))[:COLS]]
            if what == "insertbol":
                # 576013-610 Rev AC p.5-3: the inserted delivery's Bill of
                # Lading screen reads `BOL:`, and this drew the TICKET
                # VOLUME line belonging to the screen before it, so the
                # panel showed a volume where a number was being typed.
                # See FIDELITY O7.
                return [self._insert_head(what, text)[:COLS],
                        f"BOL: {held}"[:COLS]]
            return [self._insert_head(what, text)[:COLS],
                    f"TICKET VOLUME: {held or 0}"[:COLS]]
        record = self._delivery()
        if record is None:
            return [f"T {self.device}: NO DELIVERIES"[:COLS], text[:COLS]]
        head = f"T {self.device}:{self._delivery_stamp(record.end['at'])}"
        if self.editing:
            return [head[:COLS], f"{text}: {self._edit_text()}"[:COLS]]
        if what == "bol":
            return [head[:COLS], f"BOL: {record.bol}"[:COLS]]
        ticket = "" if record.ticket is None else f"{record.ticket:.0f}"
        return [head[:COLS], f"TICKET VOLUME: {ticket}"[:COLS]]

    def _choices(self, step):
        """The choices this step actually offers.

        Usually all of them. A variance report's SELECT is the exception:
        `S532`, `S533` and `S534` are one enable flag PER PERIOD -- the
        console prints them as `PERIODIC DISABLED / WEEKLY DISABLED / DAILY
        ENABLED` -- and the function itself already disappears when all
        three are off. A console cannot honour one third of a setting and
        ignore the other two thirds, so a period the site has disabled is
        not on the panel either. See FIDELITY G10.
        """
        if step.get("choices_from") == "vac_sensors":
            # SELECT VAC SENSOR: ALL, or one of the site's own vac sensors,
            # Figures 6-29 and 6-30. FIDELITY D28.
            return ["ALL"] + [str(n) for n in self.console.vac_sensors()]
        if step.get("choices_from") == "lines":
            # SELECT LINE offers the lines, not the word SINGLE LINE.
            # 576013-610 Rev AC p.11-5: "To stop PLLD tests on a single line,
            # press CHANGE in response to the message SELECT LINE / ALL
            # LINES. When you press CHANGE, the system displays the message:
            # SELECT LINE / Q#: PLLD #X. Press ENTER. To select a different
            # line, press CHANGE until you display the line you want". p.12-5
            # draws the WPLLD walk the same way. This offered ALL LINES and
            # SINGLE LINE, which is chapter 13's VLLD walk, and then took the
            # line from TANK/SENSOR -- which walked on past the site's last
            # line to the card's sixth. The lines are the ones the console
            # has. The operating-mode audit's OP13; FIDELITY O24.
            kind = (self.cur_function() or {}).get("scope")
            return ["ALL LINES"] + [line_pick(kind, n) for k, n, _l
                                    in self.console.programmed_lines()
                                    if k == kind]
        names = [c[0] if isinstance(c, list) else c
                 for c in (step.get("choices") or [])]
        if step.get("rate_gate"):
            # "If your system does not have 0.2 or 0.1 gph test options, you
            # will not see these selections", and a rate its schedule has
            # Disabled cannot be started by hand, 576013-610 Rev AC p.11-3
            # and p.12-3. A rate is offered when a line the walk is pointed
            # at allows it. FIDELITY O24.
            kind = (self.cur_function() or {}).get("scope")
            keys = {c[0]: c[1] for c in step.get("choices") or []
                    if isinstance(c, list)}
            devices = self._test_devices(kind)
            names = [name for name in names
                     if any(self.console.line_rate_allowed(
                         kind, d, keys.get(name, "gross")) for d in devices)
                     ] or names[:1]
        gate = step.get("choices_when")
        if not gate:
            return names
        stored = (self.console.values.get(gate["code"])
                  or gate.get("default", "")).strip()
        on = {name for digit, name in zip(stored, gate["flags"])
              if digit == "1"}
        # a stored value of the wrong length is not a reason to leave the
        # panel with nothing to step onto
        return [name for name in names if name in on] or names

    def _choice(self, step):
        """What a selection screen is showing.

        A selection shared between functions can hold a value this one does
        not offer, the line rate is 3.0 GPH on a PLLD and 0.20 GAL/HR on a
        VLLD, and a console only ever shows a choice it has.
        """
        names = self._choices(step)
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
        picked = self._picked_line(kind)
        if picked:
            # p.11-5's `STOP LINE TEST: LINE (#)`, the line SELECT LINE is on
            return f"{word} {picked}"
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
        if step.get("head") == "device":
            # a screen headed by the device rather than by the step's own
            # name: "R 1: OVERFILL ALARM" over "PUSH ALARM/TEST KEY"
            return self._device_head()
        if step.get("head") == "function":
            # a screen headed by the function: "TEST OUTPUT RELAYS" over
            # "ENTER RELAY NUMBER #", 576013-610 Rev AC p.22-1
            return (self.cur_function() or {}).get("function", text)
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
        if what == "tank":
            # nothing to enter: the tank is chosen with TANK/SENSOR
            return
        if what in ("date", "time"):
            if what == "date" and not self._date_in_range(text):
                # 576013-610 Rev AC p.5-2, and the console's own words:
                # "If you enter a date that is out of the range of the
                # current or previous reconciliation periods, the error
                # message, 'DATE IS OUT OF RANGE,' will appear."
                #
                # It is one of exactly TWO error messages the manuals draw
                # anywhere -- `INVALID INSERT` on the same page is the
                # other -- and this console had the other one and not this.
                # A date thirty-six years before the console's own clock
                # was taken in silence and the walk carried on and inserted
                # the delivery. `_refuse`'s rule is that the console says
                # nothing about a keystroke it cannot use, and the whole
                # point of these two is that they are the exceptions.
                self._flash("DATE IS OUT OF RANGE")
                return
            self._insert[what] = text
            # the line the screen was already showing, which is what every
            # other confirmation on this console is
            self.confirm = [(self._insert_prefix(what)
                             + self._insert_shown(what, text))[:COLS],
                            CONT_STEP]
            self._render()
            return
        if what == "insert":
            when = self._insert_when()
            try:
                volume = float(text)
            except ValueError:
                self._refuse("numbers only")
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
                self.log("-- nothing to edit: no delivery inserted")
                return
            record.bol = text[:20]
            console.save()
            self.confirm = [f"BOL: {record.bol}"[:COLS],
                            CONT_STEP]
            self._render()
            return
        record = self._delivery()
        if record is None:
            self.log("-- no deliveries on this tank")
            return
        if what == "bol":
            record.bol = text[:20]
            self.confirm = [f"BOL: {record.bol}"[:COLS],
                            CONT_STEP]
        else:
            try:
                entered = float(text)
            except ValueError:
                self._refuse("numbers only")
                return
            # The ticket is what the reconciliation's TICKETED column is
            # made of, and this wrote the record and never told BIR: an
            # entered ticket sat on the delivery report and the period's
            # ticketed total stayed at zero. The difference goes in, so a
            # corrected ticket corrects rather than doubles. BENCH.md T3.
            self.console.bir.ticket(record.tank,
                                    entered - (record.ticket or 0.0))
            record.ticket = entered
            self.confirm = [f"TICKET VOLUME: {record.ticket:.0f}"[:COLS],
                            CONT_STEP]
        console.save()
        self._render()

    @staticmethod
    def _insert_prefix(what):
        """The label each delivery-insert screen carries, off p.5-2."""
        return {"date": "DATE: ", "time": "TIME: ",
                "insertbol": "BOL: "}.get(what, "TICKET VOLUME: ")

    def _insert_shown(self, what, typed):
        """A typed date or time in the shape its own screen draws it.

        p.5-2 draws `DATE: XX/XX/XXXX` and `TIME: XX:XX XX`, and the
        keypad sends eight digits and four. The confirmation used to echo
        the digit run -- `12252026` on a line of its own with no label --
        where every other confirmation on this console is the line the
        screen was already showing. See R1 for the same rule on the setup
        fields, and the refusals audit R5.
        """
        digits = "".join(c for c in str(typed) if c.isdigit())
        if what == "date" and len(digits) >= 8:
            return f"{digits[:2]}/{digits[2:4]}/{digits[4:8]}"
        if what == "time" and len(digits) >= 3:
            hhmm = digits[:4].rjust(4, "0")
            try:
                hour, minute = int(hhmm[:2]), int(hhmm[2:])
            except ValueError:                       # pragma: no cover
                return typed
            if hour > 23 or minute > 59:
                return typed
            # the screen's own twelve hour clock. What the ARROWS do here
            # -- "Select AM or PM", p.5-2 -- is a keypad question and not
            # this one; the console reads the half of the day off the
            # twenty-four hour value it was given. See UNKNOWNS B14.
            half = "AM" if hour < 12 else "PM"
            shown = hour % 12 or 12
            return f"{shown:02d}:{minute:02d} {half}"
        return typed

    def _date_in_range(self, text):
        """Is this typed date inside a reconciliation period the console has?

        "the range of the current or previous reconciliation periods",
        576013-610 Rev AC p.5-2. The console keeps four kinds of period at
        once -- shift, daily, weekly and periodic -- and a delivery is
        counted into every one of them, so the range this takes is the
        WIDEST of the four: no earlier than the oldest period still on the
        books, and no later than now, because the current period ends at
        now.

        The widest is a reading and a deliberately generous one. The page
        says "periods" without naming a kind, and refusing a date the
        console would have taken is the worse error of the two: this
        message exists to catch a date that belongs to no period at all,
        which is the case it was found on -- 1990 on a console whose clock
        says 2026.

        A console with no reconciliation period to compare against takes
        any date, because the sentence has nothing to measure.
        """
        digits = "".join(c for c in str(text) if c.isdigit())
        if len(digits) < 8:
            return True
        try:
            opens = time.mktime((int(digits[4:8]), int(digits[:2]),
                                 int(digits[2:4]), 0, 0, 0, 0, 1, -1))
        except (ValueError, OverflowError):
            # a date this console cannot even build is `_refuse`'s business
            return True
        bir, floors = self.console.bir, []
        for kind in ("shift", "daily", "weekly", "periodic"):
            row = bir.last(self.device, kind) or bir.current(self.device, kind)
            opened = (row or {}).get("opened")
            if opened:
                floors.append(float(opened))
        # And no later than the month before this one, which is what
        # keeps this usable. Every period on this console opens at first
        # touch -- `BIR._open` -- so on a console loaded a minute ago all
        # four of them opened a minute ago, and a floor taken from those
        # alone would refuse yesterday's ticket on every bench there is.
        # The widest of the four kinds is the periodic one and its default
        # is MONTHLY, so "the current or previous reconciliation period" of
        # the widest kind is this month and last month. A console with real
        # history keeps whichever floor is older.
        now = self.console.now()
        month = (now.tm_year, now.tm_mon - 1) if now.tm_mon > 1 else (
            now.tm_year - 1, 12)
        floors.append(time.mktime((month[0], month[1], 1, 0, 0, 0, 0, 1, -1)))
        # A DAY overlaps the window, not an instant in it. The screen
        # asks for a date and nothing else, so the day it names runs from
        # midnight to midnight: today is in range on a console whose clock
        # says a quarter to one in the morning, and it would not be if this
        # compared a time of day against `now`.
        closes = opens + 86399.0
        return closes >= min(floors) and opens <= time.mktime(now)

    @staticmethod
    def _delivery_stamp(when):
        """`XXX XX, XXXX XX:XXXX`: the date and time a delivery screen heads
        itself with, in twenty characters.

        576013-610 Rev AC p.5-2 and p.5-3 draw it after `T 1:`, and the two
        together are twenty-four -- p.5-3's grid sets `T 1:` hard against
        the month, the way the real tape sets `L 6:FUEL ALARM`, and the time
        carries its meridiem without a space, `XX:XXXX`. The head was
        `T 1: ` over `clock_words`, which is twenty-six and lost the minutes
        off the end of the glass. FIDELITY U7.
        """
        t = time.localtime(when)
        hour = t.tm_hour % 12 or 12
        meridiem = "AM" if t.tm_hour < 12 else "PM"
        return f"{clock_date(t)} {hour:2d}:{t.tm_min:02d}{meridiem}"

    def _insert_head(self, what, text):
        """The head line of an inserted delivery's screens.

        The date and time screens are headed by what they ask for, `ENTER
        DELIVERY DATE`; the TICKET VOLUME and BOL screens after them are
        headed `T1: XXX XX, XXXX XX:XXXX` on p.5-2 and p.5-3 -- the tank and
        the date and time being inserted, the same head the EDIT/VIEW chain
        puts over a delivery that is there. They were headed `ENTER TICKET
        VOLUME` and `BOL`, which named neither. FIDELITY U7.
        """
        if what not in ("insert", "insertbol"):
            return text
        record = self._insert.get("record")
        when = (record.end["at"] if what == "insertbol" and record is not None
                else self._insert_when())
        return f"T {self.device}:{self._delivery_stamp(when)}"

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

    def _device_head(self):
        """"T 1: UNLEADED": the device, its number and what it is called.

        576013-610 Rev AC p.4-1 heads every reading this way and puts the
        reading underneath, and the mag sump screens on p.82 and p.86 do the
        same. The label is whatever the site programmed for that device.
        """
        letter = self._device_code()
        code = {"T": "602", "L": "702", "V": "707", "G": "712",
                "C": "742", "H": "747", "s": "722", "Q": "782",
                "W": "7A2", "P": "760", "R": "807", "I": "802",
                "r": "7C5"}.get(letter, "602")
        # An unlabelled smart sensor is SMART SENSOR n everywhere except in
        # the mag sump walk, where p.23-1 and p.24-3 both call it SUMP 1.
        # `Console.sump_screen` already falls back that way, so step 0 read
        # `s 1: SMART SENSOR 1` and step 2 read `s 1: SUMP 1` on the same
        # function -- one walk disagreeing with itself. FIDELITY O8.
        word = DEVICE_WORD.get(letter, "DEVICE")
        fn = self.cur_function()
        if letter == "s" and "MAG SUMP" in ((fn or {}).get("function") or ""):
            word = "SUMP"
        label = (self.console.text(code, self.device)
                 or f"{word} {self.device}")
        return f"{letter} {self.device}: {label}"

    def _device_code(self):
        """Table 29-1's letter for whatever this function is looking at.

        One table, in `screens`, because there were two of these and they
        were the same twenty lines: the panel read one and the printer and
        the wire read the other, so a letter added to either was a letter
        the other kept getting wrong.
        """
        fn = self.cur_function()
        return screens.device_code(fn["function"] if fn else "")

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
        raw = self.console.stored(code)
        if raw is None:
            return ""
        return (fieldio.decode(f, code, raw, self.console) if f
                else raw.strip())

    # ---- typing into a field, the way the console does it -------------------
    def _begin_edit(self, value=""):
        """CHANGE: hold the value that is there, and put the cursor on it."""
        self.editing = True
        self.buf = "" if value is None else str(value)
        self.cur = 0
        self.on_meridiem = False
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
        if self._date_entry():
            return self._edit_date()
        text = self.buf
        i = max(0, min(self.cur, len(text)))
        if i >= len(text):
            text = text + " "
        if self._blink:
            return text[:i] + CURSOR + text[i + 1:]
        return text

    def _edit_date(self):
        """"DATE: --/--/----", the template a date blanks to."""
        digits = self.buf
        cells = [digits[i] if i < len(digits) else "-" for i in range(8)]
        i = max(0, min(self.cur, 7))
        if self._blink:
            cells[i] = CURSOR
        return "".join(cells[:2]) + "/" + "".join(cells[2:4]) + "/" \
            + "".join(cells[4:])

    def _edit_time(self):
        """"TIME: 12:45 PM", with the cursor over one of the four digits.

        The colon and the half of the day are not typed, so the cursor skips
        them: four positions, and the arrows move between AM and PM instead.
        """
        digits = (self.buf + "____")[:4]
        i = max(0, min(self.cur, 3))
        if self._blink and not self.on_meridiem:
            digits = digits[:i] + CURSOR + digits[i + 1:]
        # Both halves of the day stay on the screen while you are editing,
        # which is what the Setup Manual draws ("TIME: XX:XX AM PM"). HOW a
        # real console marks which half is picked was unrecorded until a video
        # of one settled it: the halves keep a fixed AM PM order and the same
        # blinking cursor that walks the digits moves onto the FIRST LETTER of
        # the chosen one. Read frame by frame the panel goes
        #   TIME: 03:02 _M PM   (AM chosen)
        #   TIME: 03:02 AM _M   (PM chosen)
        # and on ENTER the line collapses to the chosen half alone. See
        # UNKNOWNS A4b for the video and the timestamps.
        am, pm = "AM", "PM"
        if self._blink and self.on_meridiem:
            if self.meridiem == "AM":
                am = "_M"
            else:
                pm = "_M"
        return f"{digits[:2]}:{digits[2:]} {am} {pm}"

    def _entry_cells(self):
        """How many cells this field's template has, or None if it is free.

        A date is eight, `--/--/----`, and a time is four. Everything else
        -- a label, a number, a float -- is as long as it is.
        """
        if self._date_entry():
            return 8
        if self._time_field():
            return 4
        return None

    def _put(self, ch):
        """Type a character where the cursor is, and move past it."""
        cells = self._entry_cells()
        if cells is not None and self.cur >= cells:
            # The cursor cannot walk off the end of a fixed template. Without
            # this a ninth digit on a date grew the buffer while the display
            # went on showing the first eight, so the screen read as a
            # complete entry and ENTER refused it.
            return
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
            return ["SYSTEM SECURITY",
                    "CODE: " + "*" * len(self.buf) + CURSOR]
        if self.maint_report:
            # Chapter 32's screen, reached by a key rather than by a
            # function, so it is drawn wherever the console was standing --
            # including the resting screen, whose branch below returns
            # before `self.confirm` is ever looked at.
            return list(self.confirm or ["MAINTENANCE REPORT",
                                         "PRESS <PRINT>"])
        if MODES[self.mode] == "NORMAL" and not self._entered:
            # the status display a console sits on, cycling any active alarms
            # The console's resting screen, as photographed: the date and time
            # across the top line and the status underneath.
            clock = self.console.clock_text()
            al = self._alarms()
            if not al:
                # centred on the glass, as photographed: two cells in
                return [clock, "ALL FUNCTIONS NORMAL".center(COLS)]
            # "If more than one condition exists, the display will alternately
            # flash all messages": and a single unacknowledged one flashes on
            # its own, which is what the manual's example screen is showing.
            #
            # A message holds for a whole flash, which is why this counts in
            # PAIRS of polls. `_blink` and `_cycle` both advance once a poll,
            # so stepping the message once a poll locked them together: with
            # an even number of alarms every message landed on the same half
            # of the flash every time, and half of them were blanked on every
            # pass. Two alarms meant one of them was never drawn at all.
            # See FIDELITY U2.
            a = al[(self._cycle // 2) % len(al)]
            key = a["aa"] + a["nn"] + a["tt"]
            if key not in self.console.acked and self._blink:
                return [clock, ""]
            return [clock, a["screen"]]
        if self.sumpflow and MODES[self.mode] == "NORMAL":
            return self._sumpflow_lines()
        if self.confirm:
            return list(self.confirm)
        if self.step == MODE_SCREEN:
            # Centred, as photographed on a real console. `SETUP MODE`
            # and `DIAG MODE` both sit in the middle of the top line with
            # seven blank cells before them, where a FUNCTION screen's name
            # -- `SYSTEM DIAGNOSTIC` in the same set of photographs -- is
            # hard against the left. So it is the mode screen that centres
            # and not the console, which is why left-aligning both looked
            # right until the glass was seen. The resting screen's own
            # `ALL FUNCTIONS NORMAL` was already centred here for the same
            # reason and off the same kind of evidence.
            return [MODE_SCREEN_NAME[MODES[self.mode]].center(COLS),
                    CONT_FUNCTION]
        fn = self.cur_function()
        if fn is None:
            return ["NO FUNCTIONS", "CHECK MODULES"]
        e = self.cur_step()
        if e is None:
            # the function's own screen, which is where FUNCTION lands --
            # and on a console with nothing programmed for it, the reason
            # there is nothing behind it (CLOSED U18)
            return [fn["function"][:COLS],
                    self._unconfigured(fn) or CONT_STEP]
        text = e["text"].split("(")[0].strip().upper()
        if e.get("map"):
            # 576013-623 Rev AN p.17-5's own two lines, and the row's cells
            # sit under the words they belong to rather than evenly.
            cells = (self.buf or MAP_BLANK).split()
            if self.editing and self._blink:
                cells = list(cells)
                n = self.slot % len(MAP_CELLS)
                cells[n] = " " * len(cells[n])
            return [e["head"][:COLS], self._map_row(cells)]
        if e.get("vmc") == "remove":
            # 576013-623 Rev AN p.27-2. The screen that removes a VMC asks
            # twice, and its two questions are drawn differently: the first
            # is a value on the controller's own line, the second is a
            # question over the serial being removed. Neither has a space
            # before its colon and the second has no colon at all.
            held = self.console.vmc_serial(self.device) or ""
            if self.vmc_remove.startswith("sure"):
                return [f"REMOVE VMC: {held}"[:COLS],
                        f"ARE YOU SURE? "
                        f"{'YES' if self.vmc_remove == 'sure_yes' else 'NO'}"]
            return [f"x {self.device}: {held}".rstrip()[:COLS],
                    f"REMOVE VMC: "
                    f"{'YES' if self.vmc_remove == 'yes' else 'NO'}"]
        if e.get("offset"):
            # p.17-6: three cells naming the meter and one the panel sets.
            cells = screens.offset_cells(self.console, e.get("meter"))
            if self.editing:
                cells[-1] = self.buf or "+0.00"
                if self._blink:
                    cells[-1] = " " * len(str(cells[-1]))
            return [e["head"][:COLS], screens.offset_row_text(cells)]
        f = self.cur_field() or {}
        if f.get("kind") == "slots":
            # a config screen is per MODULE: which positions are connected
            wires = (self.console.positions(f["code"][1:4])
                     or f.get("slots") or 4)
            base = ((self.device - 1) // wires) * wires
            cells = (self.buf if self.editing else
                     self.console.slot_text(f["code"][1:4], wires,
                                            base)).split()
            if self.editing and self._blink:
                cells = list(cells)
                cells[self.slot % max(len(cells), 1)] = " "
            return [self._module_head(text, base // wires + 1)[:COLS],
                    ("SLOT #: " + " ".join(cells))[:COLS]]
        if MODES[self.mode] == "DIAGNOSTIC" and e.get("buf") and self.editing:
            # "ENTER ID TO BLOCK / ID: XXXXXX", with the ID going in -- and
            # the Service Notice duration is the same shape with its own
            # prompt, "DURATION : 1". The prompt comes off the field where
            # the screen names one.
            from .console import FIELDS
            prompt = (FIELDS.get(e.get("console") or "", {}).get("prompt")
                      or e.get("prompt") or "ID:")
            return [self.console.diag_line(e["text"], self.device)[:COLS],
                    f"{prompt} {self.buf}"[:COLS]]
        if self.editing:
            if self._live_mode():
                # The live modes draw their own screens while a value is
                # going in, and three branches written for exactly that were
                # unreachable because this returned first: what the panel
                # showed was the step's NAME over the digits, where the
                # manual keeps the head and the label and puts the digits
                # after the label. Worst of the three was Manual
                # Adjustments, whose "head" was the raw `%s` format string
                # out of recondata.json, uppercased. See FIDELITY O11.
                if MODES[self.mode] == "RECONCILIATION":
                    return self._recon_lines(e, text)
                if e.get("dlv"):
                    return self._delivery_lines(e, text)
                if e.get("shift_adjust"):
                    # the head and the label stay while the amount goes in
                    return [self._shift_head()[:COLS],
                            f"DLVY ADJUSTMENT: {self._edit_text()}"[:COLS]]
                if e.get("entry"):
                    # "TEST DURATION: ALL TANKS" over "DURATION: XX",
                    # 576013-610 Rev AC p.20-2
                    return [self._scope_head(e, text)[:COLS],
                            f"DURATION: {self._edit_text()}"[:COLS]]
                if e.get("density"):
                    # "T 1: XXXX DELIVERY" over "DENSITY         :5.9972",
                    # "where XXXX = NEXT or LAST" -- p.4-5, and the same
                    # label-and-gap shape the setup manual gives the tank's
                    # own density step
                    return [self._named_head(e["head"])[:COLS],
                            f"DENSITY         :{self._edit_text()}"[:COLS]]
                if e.get("pick") == "device":
                    # `TEST OUTPUT RELAYS` over `ENTER RELAY NUMBER #`,
                    # with the number going in where the `#` is. The head
                    # is the function's own name on p.22-1 and stays there
                    # while you type; the fallback below would have put the
                    # step's name over the bare digits.
                    return [self._scope_head(e, text)[:COLS],
                            e["body"].replace("#", self._edit_text())[:COLS]]
                return [text[:COLS], self._edit_text()[:COLS]]
            if MODES[self.mode] != "SETUP":
                return [text[:COLS], self._edit_text()[:COLS]]
            if self._is_label_step(e):
                return [self._enter(text)[:COLS],
                        f"{self._device_code()}{self.device}: "
                        f"{self._edit_text()}"[:COLS]]
            scoped = self._setup_scope_head(e)
            if scoped is not None:
                # A screen that names its own scope keeps that name while
                # you change it. `screens.setup_lines` draws these as
                # `TST EARLY STOP: ALL TANKS` over the value, and the edit
                # went through the generic path below instead -- so CHANGE
                # on that screen replaced BOTH lines, heading it with the
                # tank's product label and putting the step's own words,
                # `LEAK TEST EARLY STOP:`, in front of the value. Thirty
                # columns on a twenty-four column display, and a screen
                # that changed identity at the first press of a key. The
                # same argument as the console-setting prompt below: the
                # console must not contradict itself about which screen
                # you are on. See UNKNOWNS B9.
                return [scoped[:COLS],
                        self._second(e.get("l2", ""),
                                     self._edit_text())[:COLS]]
            if e.get("console"):
                # A console setting keeps its prompt while you type in
                # it. `screens.py` draws the resting screen as the
                # field's own prompt and its value -- `BDIM TRANS ALARM
                # DELAY` over `HOURS: 024`, `ISO 3166 COUNTRY` over
                # `CODE:` -- and this path dropped the prompt on the first
                # press of CHANGE, so the only word telling a technician
                # what unit they were typing went away at the moment they
                # started typing. The two screens either side of them, on
                # the same function, keep theirs, because those are S-code
                # fields and go through `_second` below.
                #
                # The Diagnostic Mode branch two hundred lines up has read
                # the prompt off the same field all along, for the Service
                # Notice duration: "the prompt comes off the field where
                # the screen names one". No manual page draws the mid-edit
                # state of any of them -- see UNKNOWNS B9 -- so what
                # settles it is that the console must not contradict
                # itself three screens apart.
                from .console import FIELDS
                prompt = FIELDS.get(e["console"], {}).get("prompt")
                if prompt is None:
                    prompt = text + ":"
                prompt = prompt.replace("%d", str(self.device))
                head, _label = self._setup_context(e, text)
                return [head[:COLS],
                        self._second(prompt, self._edit_text())[:COLS]]
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
                if letter in ("Q", "W"):
                    # 576013-610 Rev AC p.11-2 and p.12-2 draw the history
                    # prompt under `Q #: PLLD NUMBER #` and `W #: WPLLD NUMBER
                    # #`, the head of the two result screens above it, and
                    # never the line's label -- so one walk named the same
                    # line three ways. The sump's head is p.23-2's own and
                    # keeps the label. The operating-mode audit's OP4;
                    # FIDELITY O23.
                    word = "PLLD" if letter == "Q" else "WPLLD"
                    head = f"{letter} {self.device}: {word} NUMBER {self.device}"
                return [head[:COLS], "PRESS PRINT FOR HISTORY"]
            head = self._scope_head(e, text)
            if e.get("sel"):
                # A choice the manual draws behind a word of its own:
                # 576013-610 Rev AC p.5-1 puts `SELECT: EDIT/VIEW` under
                # EDIT/VIEW OR INSERT, where this drew the bare choice.
                # See FIDELITY O7.
                pick = self._choice(e)
                label = e.get("sel_label")
                return [head[:COLS],
                        (f"{label}: {pick}" if label else pick)[:COLS]]
            if e.get("entry"):
                # "TEST DURATION: ALL TANKS / DURATION: XX"
                return [head[:COLS],
                        f"DURATION: {self.sel.get(e['entry'], '')}"[:COLS]]
            if e.get("action"):
                # An action the manual asks twice for draws its own answer:
                # 576013-610 Rev AC p.8-2 puts `CLOSE NOW: NO` under CLOSE
                # CURRENT SHIFT and says "Press CHANGE, ENTER, then STEP to
                # close the current shift", which is the same dance
                # Reconciliation Mode's own close already does. An action
                # with no such line is still a plain PRESS <ENTER>.
                body = e.get("body")
                if body and ":" in body:
                    body = (body[:body.rfind(":") + 1]
                            + (" YES" if self.armed else " NO"))
                return [head[:COLS], (body or "PRESS <ENTER>")[:COLS]]
            if e.get("livehead"):
                # a header the console dates itself: "REPORT DATE: AUG 23
                # 2026 / PRESS <ENTER>" on the ISD report functions
                one = self.console.live_reading(e["livehead"], self.device)
                return [one[:COLS], (e.get("body") or "PRESS <ENTER>")[:COLS]]
            if e.get("body"):
                # a header the manual descends into with ENTER rather than a
                # screen with a reading on it
                return [head[:COLS], e["body"][:COLS]]
            if e.get("head") == "shift":
                # 576013-610 Rev AC p.8-1 and p.8-2 head all four of
                # Last-Shift Inventory's readings `T #: SHIFT TIME #` -- the
                # tank AND the shift -- and STEP walks the shifts. They were
                # headed with the tank and its product, over BIR's one shift.
                # FIDELITY O2 and O22.
                value = self.console.shifts.shown(e["live"][6:],
                                                  self._shift_no(),
                                                  self.device)
                return [self._shift_head()[:COLS],
                        e["reading"].format(value)[:COLS]]
            live = self.console.live_reading(e.get("live"), self.device)
            head_spec = e.get("head")
            if head_spec and "%d" in head_spec:
                # "Q 1: PLLD #1": a screen that names its own head, with the
                # device number in it -- and, where the manual words the line
                # under it, that shape too: "T 1: NEXT DELIVERY" over
                # "DENSITY = X.XXXX" (576013-610 Rev AC p.4-5).
                shape = e.get("reading")
                body = (shape.format(live.strip()) if shape and live
                        else (live or ""))
                return [self._named_head(head_spec)[:COLS], body[:COLS]]
            if head_spec == "device":
                # "s 1: SUMP 1 / 0.000 IN     74.8 F": the mag sump screens
                # are headed by the sensor and its label rather than by what
                # the step is called (576013-610 Rev AC p.82, p.86)
                head = self._device_head()
                if live and chr(10) in live:
                    one, two = live.split(chr(10), 1)
                    return [one[:COLS], two[:COLS]]
                shape = e.get("reading")
                if shape and live:
                    # a device-headed screen whose second line is the
                    # reading inside a phrase: "ON - PRESS ANY KEY"
                    return [head[:COLS], shape.format(live.strip())[:COLS]]
                return [head[:COLS], (live or "")[:COLS]]
            if head_spec == "product":
                # "The product name displayed is of the lowest tank number
                # containing the product", and the manual heads every one of
                # FUEL MANAGEMENT's screens with that name ALONE --
                # `REGULAR UNLEADED` -- where this console drew
                # `T 1: DIESEL`. FIDELITY O4.
                return [self.console.fuel_label(self.device)[:COLS],
                        (live or "")[:COLS]]
            if head_spec == "plain":
                # a site-wide reading: no device prefix, the ISD status
                # screens are about the site, not a tank
                return [text[:COLS], (live or "")[:COLS]]
            if live:
                # 576013-610 Rev AC p.4-1 draws a reading as the device
                # and its label over "LABEL = value UNITS", with its own
                # worked example: "T 1: UNLEADED" over
                # "VOLUME = 10,000 GAL". This used to head the screen
                # with the step's NAME jammed against the colon and put a
                # bare right-justified number underneath, which no manual
                # draws -- grepping all 28 extractions for "T 1:VOLUME"
                # returns nothing. screens.setup_context has done it the
                # manual's way for Setup Mode all along; this is the
                # sibling it never got.
                # A screen the manual draws its own way says so, and says
                # it the way the manual reads: the sensor status screens
                # are "L1: (Location)" over "SENSOR NORMAL" (p.16-1), the
                # test results are "GRS: (Date) (Results)" (p.9-1), and
                # the last-shift readings are "BEGIN INVENTORY: XXXXXX"
                # (p.8-1). The rule above is for the readings the manual
                # draws with an "=".
                shape = e.get("reading")
                value = live.strip()
                # A reading with nothing behind it is the label alone. The
                # manual never draws a dangling "=", and a screen that has
                # no value yet is not a screen with an empty one.
                if shape:
                    body = shape.format(value)
                elif value and len(text) + 3 + len(value) <= COLS:
                    body = f"{text} = {value}"
                else:
                    # No room for the value beside the label, or no value to
                    # put there. LAST DELIVERY DENSITY is 21 characters, so
                    # "LABEL = value" fills the display before the value
                    # starts -- and the manual names that step in its flow
                    # chart without ever drawing its screen, so what a real
                    # console shortens it to is not recorded. See UNKNOWNS
                    # A20. The label alone is at least a line the manual has.
                    body = text

                return [self._device_head()[:COLS], body[:COLS]]
            return [text[:COLS], "PRESS <STEP>"]
        if MODES[self.mode] == "DIAGNOSTIC" and e.get("act"):
            # "RESET ACCUCHART NO ... Selecting YES clears the AccuChart Tank
            # Profile": CHANGE walks the answer, ENTER does it.
            #
            # A screen carrying BOTH a reader and an action reads: this
            # branch used to take the head from the manual's literal and
            # leave the `live` unreachable, so `BLOCK: XXXXXX` never became
            # `BLOCK: A54321`. See FIDELITY D13.
            body = e.get("l2") or ""
            if e.get("verbatim"):
                head = e["text"]
            elif e.get("live"):
                read = self.console.diag_value(e["live"], self.device,
                                               self._diag_kind()) or ""
                # a reader that answers on BOTH lines answers both of them:
                # `COMM 1 (RS-232)` over `REINIT COMM BD: NO` is one token,
                # and taking only its first line ran the two together
                head, _sep, second = read.partition(chr(10))
                head = head or e["text"]
                body = second or body
            else:
                head = self.console.diag_line(e["text"], self.device)
            # The answer is the last word, not whatever follows a colon.
            #
            # This keyed the YES/NO swap on finding a colon in the line, and
            # `RESET ACCUCHART NO` is the manual's own wording for Figure
            # 6-10's last screen and has none -- so CHANGE armed the reset,
            # the line went on reading NO, and ENTER then cleared the tank
            # profile and restarted the 56-day calibration with nothing on
            # the glass having said it was about to. Arming a destructive
            # action invisibly is the exact failure the console's
            # CHANGE-then-ENTER design exists to prevent. FIDELITY D18.
            word = " YES" if self.armed else " NO"
            for tail in ("YES", "NO"):
                if body.endswith(tail):
                    body = body[:-len(tail)].rstrip() + word
                    break
            return [head[:COLS], body[:COLS]]
        if MODES[self.mode] == "DIAGNOSTIC":
            # Two lines straight from the manual, but pointed at THIS console:
            # the device the panel is on, its programmed label, and the values
            # the console can actually answer for.
            if e.get("sel") and e.get("choices_from") == "vac_sensors":
                # `SELECT VAC SENSOR / ALL VAC SENSORS`, and CHANGE draws
                # `sX (Vac Sensor Label)`. FIDELITY D28.
                pick = self._choice(e)
                if pick == "ALL":
                    return [e["text"][:COLS], "ALL VAC SENSORS"]
                named = self.console.diag_line("s 1: (Vac Sensor Label)",
                                               int(pick))
                return [e["text"][:COLS],
                        f"s{pick} {named.split(': ', 1)[-1]}"[:COLS]]
            if e.get("sel"):
                # `CSLD MONTHLY REPORT / SELECT: CURRENT MONTH`, and CHANGE
                # makes it PREVIOUS MONTH: one screen, p.27-3 and 576013-818
                # Rev AB Figure 6-11's `C` between the two. FIDELITY D23.
                return [self.console.diag_line(e["text"], self.device)[:COLS],
                        f"SELECT: {self._choice(e)}"[:COLS]]
            head = self.console.diag_line(e["text"], self.device)
            if e.get("pick") and str(self.sel.get(e["pick"]) or "ALL") != "ALL":
                # `START MANUAL TEST: sX`, for the sensor SELECT VAC SENSOR
                # named. FIDELITY D28.
                head = head.replace(": ALL", f": s{self.sel[e['pick']]}")
            body = (self.console.diag_value(e["live"], self.device,
                                            self._diag_kind())
                    if e.get("live") else "")
            if not body:
                # A reading this console cannot take falls back to the line
                # the manual draws rather than to nothing: ORIG and CURR REF
                # DISTANCE answer for a Mag probe and not for a capacitance
                # one -- "Probe types 01=CAP0 and 02=CAP1 are not supported
                # by this command" -- and two of the three shipped presets
                # fit CAP probes. A console never draws a blank line.
                # FIDELITY D2.
                body = e.get("l2") or ""
            if body and chr(10) in body:
                # the line leak diagnostics read on BOTH lines: a pressure and
                # a pair of switch states, not a label over a value
                head, body = body.split(chr(10), 1)
            return [head[:COLS], body[:COLS]]
        if self._chart_locked(e):
            # "TANK PROFILE : 50 PTS / ENTER PASSCODE->______<", with the
            # passcode going in, which is the one setup screen that reads
            # off the panel rather than off the console.
            #
            # The field is SIX cells between the arrow and the closing `<`,
            # and it keeps its width as the digits go in -- 576013-623 Rev AN
            # p.5-27, and the same construction `_change_into` already used
            # two screens down. This drew `ENTER PASSCODE->***` with no field
            # and no closing bracket, which the citation audit only accepted
            # because a prefix of the manual's line is still a prefix.
            typed = "".join(ch for ch in self.buf if ch.isdigit())[:6]
            field = "*" * len(typed) + "_" * (6 - len(typed))
            if self._blink and len(typed) < 6:
                field = field[:len(typed)] + CURSOR + field[len(typed) + 1:]
            return ["TANK PROFILE : 50 PTS", f"ENTER PASSCODE->{field}<"]
        if e.get("profile") and self._profile_pending:
            # "CLEAR EXISTING PROFILE / ARE YOU SURE? : NO"
            return ["CLEAR EXISTING PROFILE",
                    f"ARE YOU SURE? : {'YES' if self.armed else 'NO'}"]
        if e.get("archive"):
            text = e["text"].split("(")[0].strip().upper()
            if self.sure:
                # the choice is made; the console asks it again.
                #
                # A SPACE before the colon, which the manual does not print.
                # Photographed on a real console mid-restore: `RESTORE SETUP
                # DATA: YES / ARE YOU SURE? : YES`. The mark measures to the
                # centre of cell 14 within 0.05 of a cell, where a flush
                # colon would sit in cell 13 -- a full cell away. The other
                # ARE YOU SURE screens keep the manual's flush colon until a
                # photograph says otherwise; see UNKNOWNS B4.
                return [f"{text}: YES"[:COLS],
                        f"ARE YOU SURE? : {'YES' if self.armed else 'NO'}"]
            return ["ARCHIVE UTILITY",
                    f"{text}: {'YES' if self.armed else 'NO'}"[:COLS]]
        if e.get("point") in ("height", "volume") and (
                e["point"] in self._point or "height" in self._point):
            # a point the panel is holding, part-typed or just entered. The
            # volume screen counts as held the moment the HEIGHT beside it is,
            # because that height is the only thing on the screen that says
            # which of the fifty points is being strapped. FIDELITY F15.
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
                    f"{at} INCH VOL : "
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
        """What line two reads, defaulted the way a console out of the box is.

        Through `screen_word`, because this is the PANEL's view: a field
        can be rendered one way on the glass and another on the paper and
        the wire, and ULLAGE is (p.5-14 draws `90 PERCENT`, the site tape
        prints `ULLAGE: 90%`). Both uses of this are panel-side -- the
        value CHANGE walks from, and the seed CHANGE puts in the buffer.
        """
        f = self.cur_field()
        return screens.screen_word(
            f, screens.shown(self.console, f, self._stored()))

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
            # "COMM BOARD: 1 (Type)" -- 576013-623 Rev AN p.6-2 draws the
            # card in the slot beside its number, and draws it on the
            # screen you are CHOOSING on: its `PARITY: ODD` figure is the
            # screen after CHANGE has moved parity off NONE and it still
            # reads `(Type)`. `screens.py` was fixed and this path was not,
            # so the board type vanished from the head on the first press
            # of CHANGE and came back when you stepped away.
            return (f"COMM BOARD: {self.device} "
                    f"({self.console.comm_board_name(self.device)})",
                    label or text + ":")
        letter = self._device_code()
        named = self.console.text(
            {"T": "602", "L": "702", "V": "707", "G": "712", "C": "742",
             "H": "747", "s": "722", "Q": "782", "W": "7A2", "P": "760",
             "R": "807", "I": "802", "r": "7C5"}.get(letter, "602"),
            self.device)
        if step.get("bare"):
            # the value stands on its own under the device's own head, with
            # no prompt in front of it. See `screens.setup_context`.
            return f"{letter}{self.device}: {named}".rstrip(), ""
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
        # The white key's MAINTENANCE REPORT screen is not a step of any
        # function, so nothing else would take it back off the glass. Any
        # key leaves it; `k_white` and `k_print` put it back, because those
        # are the two the screen itself names.
        if self.maint_report:
            # and the screen's two lines, which are held in `confirm`: left,
            # the glass went on saying PRESS <PRINT> for the maintenance
            # history while PRINT printed SYSTEM SETUP under it
            self.confirm = None
        self.maint_report = False
        # `ON - PRESS ANY KEY`, and 576013-610 Rev AC p.2-5 says what the
        # any key is for: "Press any key to printout Relay Setup". This
        # runs before every handler's own work, so the key that cashes it
        # still does whatever else it does. See FIDELITY U8.
        if getattr(self, "relay_setup_due", False):
            self.relay_setup_due = False
            self.paper_out(printer.relays(self.console))
            self.log("-- PRINT: output relay setup")

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
        # the backlight is on whenever the console is, unless SW2-3 has
        # blanked the display
        self.lcd.backlight(self.console.powered
                           and not self.console.display_blanked)
        if self.console.display_blanked and self.console.powered:
            # SW2-3 closed: the display is off, the console is not. Keys,
            # serial and printer all still work; you just cannot see.
            for _rid in self._text_ids:
                self.lcd.itemconfig(_rid, text="")
            self.hint.config(text="DISPLAY OFF   |   DIP SW2-3 is closed "
                                  "(Switches menu)")
            return
        if self._busy() and getattr(self, "busy_line", None):
            for _i, _rid in enumerate(self._text_ids):
                self.lcd.itemconfig(
                    _rid,
                    text=["ARCHIVE UTILITY",
                          self.busy_line][_i].ljust(COLS)[:COLS])
            self.hint.config(text="saving setup data; the console does not "
                                  "answer the keypad while it works")
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
        if self.mt_login:
            self._mt_timed_out()
        if self.mt_login:
            rows = self._mt_lines()
            for _i, _rid in enumerate(self._text_ids):
                self.lcd.itemconfig(_rid, text=rows[_i].ljust(COLS)[:COLS])
            self.hint.config(text="Maintenance Tracker log-in: STEP asks for "
                                  "the key, ENTER reads the one in the port "
                                  "(card cage tab), any key leaves")
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
            # dead glass: the backlight is off, the text is gone and so is
            # the status line under the face
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
        # Cancel the timer the LAST flash left running, or it clears this
        # one early: two messages inside a second and a half is ordinary on
        # a panel -- an INVALID ENTRY and then another -- and the first
        # one's timer was wiping the second one's message. It showed up as a
        # test that failed only in a full run, which is the shape V0 warns
        # about.
        pending = getattr(self, "_flash_id", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:                        # pragma: no cover
                pass
        self.msg = text
        self._render()
        self._flash_id = self.after(1500, self._clear)

    def _clear(self):
        self._flash_id = None
        self.msg = ""
        self._render()

    def _run_syncs(self):
        """Update the bench faces without letting one stop the UI heartbeat."""
        reported = getattr(self, "_sync_failed", None)
        if reported is None:
            # weak, so a tile that goes away takes its entry with it
            reported = self._sync_failed = weakref.WeakSet()
        for sync in (list(getattr(self, "_site_sync", []))
                     + list(getattr(self, "_traffic_sync", []))):
            try:
                sync()
            except tk.TclError:
                # a widget going out from under a poll already in flight
                pass
            except Exception as e:
                # said once, and again only after the callback has worked
                if sync not in reported:
                    reported.add(sync)
                    self.log(f"-- bench sync failed: {type(e).__name__}: {e}")
            else:
                reported.discard(sync)

    def _poll(self):
        """The console's heartbeat: 700ms, and it must never miss one.

        The reschedule used to be the LAST statement of this method, so
        anything that raised on the way there -- any defect anywhere in
        `tick()`, the alarm computation, or a bench sync -- took the poll
        loop with it. Tk prints the traceback and drops the callback, and
        nothing schedules another: the console's clock stops advancing,
        `compute_alarms()` never runs again, and the lamps, the alarm
        history and the alarm-driven shutdown relays all freeze while the
        process sits there looking perfectly healthy. Silent, permanent,
        and indistinguishable from a quiet site.

        That is too much to hang on every line below being correct,
        especially now that the same `tick()` is reachable from the network
        on a card a stranger can talk to. So the body is wrapped and the
        reschedule is in a `finally`: a defect costs one logged tick, not
        the console.
        """
        try:
            self._poll_once()
        except tk.TclError:
            # the window going away underneath a poll already in flight;
            # there is nothing to reschedule onto
            return
        except Exception as e:                          # noqa: BLE001
            if getattr(self, "_poll_said", None) != type(e).__name__:
                self._poll_said = type(e).__name__
                self.log("-- tick failed: %s: %s (the console kept running; "
                         "this is said once per kind)" % (type(e).__name__, e))
        finally:
            # `winfo_exists` before scheduling, not just a try around it.
            # `after` on a window that is going away SUCCEEDS -- it returns
            # an id quite happily -- and the callback then fires into a
            # dead interpreter, where Tk prints "invalid command name
            # <id>_poll" from somewhere with no traceback to trace. The old
            # code could not hit this because its reschedule was the last
            # statement of the body, so a window torn down mid-poll simply
            # never reached it; putting the reschedule in a `finally` is
            # what made the check necessary.
            try:
                alive = self.console is not None and self.winfo_exists()
            except tk.TclError:
                alive = False
            if alive:
                try:
                    self._poll_id = self.after(700, self._poll)
                except tk.TclError:
                    pass

    def _poll_once(self):
        if not self.console.powered:
            # the site outside carries on; the console does not
            for key in ("power", "warn", "alarm"):
                self._set_led(key, False)
            self._run_syncs()
            return
        self.console.tick()
        self.console.in_setup = MODES[self.mode] == "SETUP"
        if self.isdflow:
            self._isdflow_poll()
        # "The system will automatically return to the Operating Mode status
        # display in 15 minutes if no activity takes place while the system is
        # in the Setup Mode."
        if MODES[self.mode] == "SETUP" and time.time() - self._last_key > 900:
            # To the STATUS display, lock and all. This reset the mode and
            # the edit and not `locked`, so Operating Mode then asked for a
            # security code over a console that was not in Setup. O20.
            self._to_status_display()
            self.log("-- 15 minutes idle: returned to Operating Mode")
        self._run_syncs()
        al = self._alarms()
        self._blink = not self._blink
        # The lights follow the CONDITION, not the message: "you cannot turn
        # off warning and alarm lights until you correct the cause". So an
        # acknowledged alarm whose cause is still there keeps its light, and a
        # corrected one that nobody has acknowledged yet has gone dark while
        # its message is still on the display.
        # BOTH of these tests used to be case-sensitive, and the console's
        # own descriptions are not written in one case: fifteen are title
        # case and thirty-one are capitals, so `"Warning" in description`
        # saw six categories and sent the other thirty-one conditions to the
        # RED lamp. `SETUP DATA WARNING` was yellow under a liquid sensor and
        # red under a tank, a line and a pressure line -- one message, three
        # devices, two different lights.
        #
        # Photographed on a real console, which is what settles it: the glass
        # reads `Q 1:SETUP DATA WARNING` -- category 21, capitals, the
        # thirty-one -- with the YELLOW lamp lit and the red one dark. And
        # 576013-623 Rev AN p.5-1 names it, "the yellow warning light will
        # flash".
        #
        # `endswith("Active")` was dead for the same reason: the only two
        # descriptions that end in the word, `TANK TEST ACTIVE` and `RELAY
        # ACTIVE`, are capitals, so neither was ever exempted.
        #
        # The lamp comes off the manual's own column now, which is the
        # half of this that was left open: 576013-610 Rev AC chapter 29
        # heads every one of its message tables `Display Message | Front
        # Panel Indicator | Cause | Action`, and the second column is the
        # lamp, one row at a time. `front_panel_indicator` reads the
        # 68 messages the tables name, plus five whose lamp differs between
        # sensor families.
        #
        # It cannot be worked out from the message, which is what the
        # string match was trying to do: `HIGH PRODUCT ALARM` is a Warning
        # with the word ALARM in it, `PAPER OUT` and `TANK SIPHON BREAK`
        # carry neither word, and `LIQUID WARNING` is an Alarm on a Type B
        # sensor and a Warning on a liquid one. Eighteen conditions were on
        # the wrong lamp, every one of them red where the page says yellow.
        #
        # The substring test stays as the fallback for the 159 rows no
        # table names -- most of them families whose chapter is in another
        # manual -- and so does the `ACTIVE` exemption, for the same rows.
        # Where the page speaks it wins: `TANK TEST ACTIVE` is one of its
        # Warnings, and that exemption was this project's own.
        # And it lights for what the console is SHOWING, not for what is
        # merely true. 576013-610 Rev AC p.29-1 keeps the two together:
        # "When no warning or alarm conditions exist, the system displays
        # the ALL FUNCTIONS NORMAL message. If an alarm or warning
        # condition does exist, the system displays the type and location
        # of the condition."
        #
        # The lamps read `conditions()`, the RAW list, where the glass is
        # drawn from `compute_alarms()` -- and Alarm Reduction sits between
        # the two. A water alarm has a three minute filter, "the Water
        # Alarm Filter allows the user to select from several filters that
        # will delay the POSTING of a water alarm" (623 p.7-15), so the red
        # lamp stood over `ALL FUNCTIONS NORMAL` for three minutes with
        # nothing posted and nothing for the technician to read. Probe Out
        # has a two minute delay and the DIM comm alarms six. The console
        # keeps the two lists apart carefully; only the lamp read the wrong
        # one. See the alarm-lifecycle audit, A2.
        #
        # The intersection with `cond` is the other half of p.29-1 and was
        # already right: "you cannot turn off warning and alarm lights
        # until you correct the cause". A latched message whose cause has
        # gone keeps its message -- it is still on `al` -- and drops its
        # lamp, because it is no longer a condition.
        live_now = {a["aa"] + a["nn"] + a["tt"]
                    for a in describe_alarms(self.console.conditions())}
        lit = [(a, self.lamp_for(a)) for a in al
               if a["aa"] + a["nn"] + a["tt"] in live_now]
        warn = any(which == "warning" for _a, which in lit)
        alarm = any(which == "alarm" for _a, which in lit)
        # Not while the held ALARM/TEST test has all three lit. This ran
        # within 700 ms of the test starting and put them back, so its
        # 1.2 seconds lasted 0.7 at most.
        if time.time() >= self._lamp_test_until:
            self._set_led("power", True)
            self._set_led("warn", warn and self._blink)   # it flashes
            self._set_led("alarm", alarm)
        # And the console makes a noise about it. 576013-610 Rev AC
        # p.29-1 gives it a heading of its own -- "AUDIBLE ALARM / Press
        # ALARM/TEST to silence the alarm" -- 576013-623 p.2-2 says the key
        # "shuts off audible alarm", and 577013-814's operability
        # procedures start a dozen numbered steps with "press the
        # Alarm/Test key to silence the beeper".
        #
        # Nothing called `_beep` except the lamp test, so `silenced` was
        # tracked exactly right and drove nothing audible: there was no
        # sound for ALARM/TEST to stop, and a trainee was never taught
        # the noise that makes somebody walk to the panel. A missing
        # caller, not a missing feature.
        #
        # It sounds while a condition is standing and stops when the key is
        # pressed, and a NEW condition clears `silenced` and starts it
        # again -- `compute_alarms` does that already, on the manual's rule
        # that an alarm which comes back is a new alarm.
        #
        # One exception is documented and cannot be reached yet. 623
        # p.5-27 lists a Service Notice warning as posting a message and
        # blinking the yellow lamp with the "console beeper silent", which
        # only reads as an exception if the others sound. This console has
        # no alarm number for Service Notice at all -- UNKNOWNS A26 -- so
        # there is nothing here to exempt.
        if (warn or alarm) and not self.console.silenced:
            self._beep()
        self._cycle += 1
        # Everything the console owes the paper, in the order it owes it.
        #
        # This was four queue drains and an alarm watermark written out here,
        # which is why `run.py --headless` printed nothing at all and why the
        # four automatic printouts the manuals promise had nowhere to live:
        # the renderer for them belongs beside the other reports and the
        # panel is not the only thing that should be able to ask. See
        # FIDELITY P2. `printer.automatic` drains the queues, so whatever
        # asks has to put the answer on the roll -- asking twice gets an
        # empty list the second time.
        for note, lines in printer.automatic(self.console):
            self.paper_out(lines)
            self.log(note)
        # Auto-Transmit has no paper and no frame to show, so the serial log
        # is where it is visible at all. See FIDELITY P3.
        for note in self.console.autotx.notices:
            self.log(note)
        self.console.autotx.notices.clear()
        # On the bench, say WHY each message is still there: an alarm whose
        # cause is gone is only waiting to be acknowledged.
        # `live_now` is the same set the lamps were chosen from: a message
        # is still standing if its condition is still true. This built its
        # own set from a list that had the `ACTIVE` descriptions filtered
        # out of it, so a running tank test read as "cause corrected" in
        # the bench log while the test was still running.
        rows = []
        for a in al:
            if (a["aa"] + a["nn"] + a["tt"]) in live_now:
                tail = " (silenced)" if self.console.silenced else ""
            else:
                tail = ": cause corrected, ALARM/TEST clears it"
            rows.append("  " + a["text"] + tail)
        txt = "  ALL FUNCTIONS NORMAL" if not rows else "\n".join(rows)
        self.alarm_lbl.config(text=txt, fg="#7bd88f" if not al else "#ff9b9b")
        if not self.msg and (MODES[self.mode] == "NORMAL" or self.editing
                             or self._live_screen()):
            self._render()

    def _live_screen(self):
        """Is the screen in front of you a READING rather than a setting?

        The poll redrew the display in Operating Mode alone, so a screen
        anywhere else that shows a live value was painted once when you
        stepped onto it and then never again. The console went on running
        underneath it and the panel stopped saying so, which is
        indistinguishable from a console that has hung.

        The Pressure Line Leak diagnostic is where it shows worst, because
        that screen is the one you START a test from: ENTER runs the pump,
        the line climbs to about thirty psi and ripples the way a submersible
        does, ten seconds later the check valve seats it back to the relief
        valve's setpoint and it bleeds down from there -- and every bit of
        that was already modelled and none of it reached the glass. The
        screen sat on RUNNING PUMP and one frozen pressure for the whole
        half hour a 0.20 gph test takes. See FIDELITY U3, which measured
        that test's length and did not notice the panel was not drawing it.

        A screen carrying a `live` token is the whole of the rule: it is
        what the Diagnostic and Reconciliation screens use for a value the
        console reads rather than one it stores, and a stored value cannot
        change without a keypress that repaints anyway.
        """
        return bool((self.cur_step() or {}).get("live"))

    def destroy(self):
        """Stop the timers before the window goes, or they fire into nothing.

        `_cap_after` joined the list with the Capture view: it is a second
        700ms-class loop, armed the same way, and a callback left in Tk's
        queue at teardown fires into a dead interpreter and prints
        `invalid command name` on stderr from outside any traceback.
        """
        for pending in (getattr(self, "_poll_id", None),
                        getattr(self, "_cap_after", None),
                        getattr(self, "_flash_id", None)):
            if pending:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
        super().destroy()

    @staticmethod
    def lamp_for(entry):
        """Which front-panel lamp one condition lights, or None.

        `front_panel_indicator` is 576013-610 Rev AC chapter 29's own
        `Front Panel Indicator` column, 68 messages plus five that depend
        on the sensor family. Where it does not speak -- 159 of the
        console's rows, most of them families whose chapter is in another
        manual -- this falls back to the substring test the lamp used to be
        chosen by, and to the `ACTIVE` exemption, which is this project's
        own and applies only to rows no page has settled.
        """
        found = front_panel_indicator(entry["description"], entry["aa"])
        if found:
            return found
        if entry["description"].upper().endswith("ACTIVE"):
            return None
        return ("warning" if "WARNING" in entry["description"].upper()
                else "alarm")

    def _set_led(self, key, on):
        cv, oval, colour = self.led[key]
        fill = colour if on else LED_OFF
        cv.itemconfig(oval, fill=fill)
        cv.itemconfig(self._glint[key], fill=blend(fill, "#ffffff", 0.45))

    # ---- the beeper -------------------------------------------------------
    # A TLS-350 has one, the Operator's Manual counts it as one of the ways
    # the console tells you something is wrong, and Setup can turn it off:
    # "System Beeper (Enable/Disable)", value S53000. Nothing here made a
    # sound until now, which made the setting decorative and the annual
    # audible test impossible to rehearse.
    BEEP_HZ, BEEP_MS = 2400, 220

    def beeper_enabled(self):
        """Is the beeper switched on in Setup? Default is on."""
        return (self.console.values.get("S53000") or "1") != "0"

    def _beep(self, times=1):
        """Make the noise, if this console is set to make it."""
        if not self.beeper_enabled() or not self.console.powered:
            return
        try:
            import winsound
            for _n in range(times):
                winsound.Beep(self.BEEP_HZ, self.BEEP_MS)
        except Exception:
            try:
                for _n in range(times):
                    self.bell()
            except Exception:
                pass

    # ---- the audible and visual test --------------------------------------
    HOLD_SECONDS = 3.0

    def _alarm_held_start(self, _ev=None):
        """ALARM/TEST going down starts the clock on the hold."""
        self._alarm_hold = self.after(int(self.HOLD_SECONDS * 1000),
                                      self._lamp_test)

    def _alarm_held_stop(self, _ev=None):
        """Let go early and nothing happened."""
        pending, self._alarm_hold = getattr(self, "_alarm_hold", None), None
        if pending:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass

    def _lamp_test(self):
        """The console's own self test, as the operability guide walks it.

        577013-814, "TLS-3xx Audible and Visual Test Procedure": "1. Press and
        Hold Alarm Test button for a minimum 3 seconds. 2. Verify Green Power,
        Yellow Warning and Red Alarm LED indicators are lit and audible alarm
        sounds. 3. The printer will automatically print a System Status
        report." 576013-818 says the same in one line: "Press the ALARM/TEST
        button to verify that the red ALARM and yellow WARNING LEDs illuminate
        and the console beeper switches On."

        This is step one of the annual inspection, so it is worth having the
        bench do it exactly: all three lamps on together, the beeper, and the
        report out of the slot.
        """
        self._alarm_hold = None
        if not self.console.powered:
            return
        for key in ("alarm", "warn", "power"):
            self._set_led(key, True)
        self._lamp_test_until = time.time() + 1.2
        self.log("-- ALARM/TEST held: lamp and beeper test")
        self._beep()
        try:
            self.paper_out(printer.status(self.console))
            self.log("-- PRINT: system status report")
        except Exception:
            pass
        # then the lamps go back to telling the truth
        self.after(1200, self._refresh_leds)

    def _refresh_leds(self):
        """Put the lamps back to what the console's state actually says."""
        try:
            self._render()
        except tk.TclError:
            pass

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

    def _guard_mode(self):
        """Ask for the System Security Code if the mode now standing needs it.

        "you will be required to enter this code before you can access any
        setup or diagnostic function": a property of BEING in a guarded
        mode, so every way into one asks. `k_mode` asked and BACKUP walked
        into Setup and Diagnostic without it. FIDELITY O20.
        """
        self.locked = (self.console.panel_security
                       and bool(self.console.security_code())
                       and MODES[self.mode] in ("SETUP", "DIAGNOSTIC"))

    def _leave_overlay(self):
        """Leave the ISD override or the Maintenance Tracker log-in on a key
        that is not one of theirs. True if one was up, and the key is spent.

        The override answered ENTER, CHANGE and ALARM/TEST and nothing
        else, so MODE, STEP, FUNCTION, BACKUP, TANK/SENSOR and PRINT left a
        technician on it with no way out but YES; and the log-in's own hint
        says any key leaves it while MODE and FUNCTION changed the mode
        underneath and left it on the glass.
        """
        if not (self.isd_override or self.mt_login):
            return False
        self.isd_override = None
        self._alarm_presses = 0
        self.mt_login = None
        self.mt_asked = None
        self._render()
        return True

    def _drop_archive_walk(self):
        """Forget an archive save or restore that was confirmed and left.

        The job was cleared only by the STEP that ran it, so BACKUP off
        `ARE YOU SURE? : YES` left it armed and the next STEP off ANY
        confirmation ran it: a restore that reverted a label changed in
        between. `armed` and `sure` stayed too, so the other archive step
        opened already answered YES twice.
        """
        self._archive_pending = None
        self.armed = self.sure = False

    def _to_status_display(self):
        """Operating Mode's status display, with nothing left half done.

        Where the fifteen-minute idle return goes, and where a boot lands:
        576013-637 p.16, "front panel display reads: MMM DD, YYYY HH:MM:SS
        XM / ALL FUNCTIONS NORMAL".
        """
        self.mode = MODES.index("NORMAL")
        self.func, self.step = 0, HEADER
        self.subs = []
        self._entered = False
        self.editing, self.buf, self.confirm = False, "", None
        self.locked = self.chart_open = False
        self._drop_archive_walk()
        self._sure_head = None
        self.vmc_remove = "no"
        self.isd_override = None
        self._alarm_presses = 0
        self.mt_login = None
        self.mt_asked = None
        self.maint_report = False
        self.isdflow = None
        self.sumpflow = None
        self._confirm_next = None
        self._clear_stages()
        self.console.in_setup = False

    def _bench_say(self, text):
        """Something the BENCH did, said where the bench says things.

        A real console shows nothing when somebody fits a card, loads a
        site, or changes its software: those are not things a console can be
        told. Twelve of them were painted on the 24-column glass through
        `_flash` anyway, which put `SITE LOADED`, `WILL NOT FIT` and
        `CONSOLE RESET` on the same two rows the manual reserves for the
        console's own screens.

        `_abandon` has the rule -- "the bench may say what the console must
        not" -- and the log under the panel is where the bench says it.
        See FIDELITY U5.
        """
        self.log("-- " + " ".join(text.split()))

    def _refuse(self, why):
        """Refuse a typed value the way the console refuses one: silently.

        `INVALID ENTRY` is this project's screen and no console's. It
        stood at seventeen sites, over a second line that named the rule the
        entry had broken -- NUMBERS ONLY, BAD DATE, ENTER 6 DIGITS, MAX n
        CHARS, a range. Searched across every manual on the shelf, the tape
        and 904 captured replies, the words appear nowhere.

        What the manuals DO draw is two error messages, both for one
        function: "the error message, 'DATE IS OUT OF RANGE,' will appear"
        and "you will see an 'INVALID INSERT' error", 576013-610 Rev AC
        p.5-2. Both of those are drawn here, in the manual's own words.
        Everywhere else the console takes the keystrokes it can use and says
        nothing about the ones it cannot.

        `_abandon` above states the rule this follows and names the same
        mistake made once before: "An earlier version flashed NOT SAVED
        here, which was this simulator inventing a screen the hardware does
        not have. The bench hint under the console still warns while an
        entry is open, because the bench may say what the console must
        not." So the reason goes to the log under the panel, where a
        number about what the simulator did belongs, and the glass keeps
        whatever screen the console was on.

        See FIDELITY U5.
        """
        self.editing, self.buf = False, ""
        self.log(f"-- entry refused: {why}")

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
        if self._leave_overlay():
            return
        self.chart_open = False
        self._abandon("MODE")
        self._drop_archive_walk()
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
        self._guard_mode()
        self.buf = ""
        self._render()

    # The stages of a diagnostic branch the figure walks with CHANGE and
    # ENTER. They are the panel's, not the console's, and leaving the
    # function ends them. FIDELITY D27.
    DIAG_STAGES = ("modem_config", "vac_pick")

    def _clear_stages(self):
        for key in self.DIAG_STAGES:
            getattr(self, "sel", {}).pop(key, None)

    def k_function(self):
        """FUNCTION scrolls the functions, and lands on the function screen.

        "IN-TANK SETUP / PRESS <STEP> TO CONTINUE" is a screen in its own
        right, which every chapter of the setup manual starts at.
        """
        self._keyed()
        if self._leave_overlay():
            return
        if self.locked:
            # Nothing behind the security prompt answers but the code.
            # FUNCTION and STEP walked the functions under it while the
            # glass still asked for it. FIDELITY O20.
            self._render()
            return
        self.chart_open = False
        self._drop_archive_walk()
        # "If FUNCTION is pressed while in a Step, the system will advance to
        # the next Function" (576013-623 Rev AN p.2-2), and p.3-1 says what
        # becomes of a half-typed entry on the way: "If you press the STEP,
        # FUNCTION, or MODE key without pressing ENTER, the data will not be
        # saved." One press, and the data is lost -- not a press that is
        # swallowed to cancel the entry and a second one to move. MODE has
        # always read it this way; these two did not. See FIDELITY U5.
        self._abandon("FUNCTION")
        self.sub = None
        self.confirm = None
        self.sumpflow = None
        self._confirm_next = None
        self._clear_stages()
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
        if self.locked:
            self._render()
            return
        if self.isd_override:
            self._leave_overlay()
            return
        if self.mt_login:
            # "Press Step and the display will read INSERT KEY IN PORT"
            if self.mt_login == "prompt":
                import time as _time
                self.mt_login = "insert"
                self.mt_asked = _time.mktime(self.console.now())
            else:
                self.mt_login = None
            self._render()
            return
        if self.sumpflow:
            # off the walk without running anything; no page says, and it
            # is what STEP does to the grade-hose flows beside it
            self.sumpflow = None
            self._render()
            return
        if self.isdflow:
            state = self.isdflow["state"]
            if state == "nospace":
                # Figure 11's S arrow runs back to SELECT HOSE: pick another
                # hose, or go and reassign this one's AFM
                self.isdflow["state"] = "select"
                self._render()
                return
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
        if (self.cur_step() or {}).get("vmc") == "remove":
            # p.27-2's own three STEPs. Off the `REMOVE VMC: YES`
            # confirmation to the second question; off `ARE YOU SURE? YES`
            # the controller is removed and the walk ends on the parent,
            # which is the page's last figure. What STEP off `ARE YOU
            # SURE? NO` does is on no page, so it leaves the same way
            # without removing anything.
            if self.confirm and self.vmc_remove == "yes":
                self.confirm = None
                self.vmc_remove = "sure_no"
                self._render()
                return
            if self.vmc_remove.startswith("sure"):
                if self.vmc_remove == "sure_yes":
                    held = self.console.vmc_serial(self.device)
                    self.console.vmc_serials.pop(self.device, None)
                    self.console.values.pop(f"S8C1{self.device:02d}", None)
                    self.console.save()
                    self.log(f"-- VMC {self.device} serial {held} removed")
                self.vmc_remove = "no"
                self.confirm = None
                if self.subs:
                    self._ascend()
                self._render()
                return
        if self.confirm and self._sure_head:
            # 576013-623 Rev AN p.5-30, the tail of System Beeper. "Press
            # ENTER: DISABLED / PRESS <STEP> TO CONTINUE. Press STEP:
            # DISABLED / ARE YOU SURE? : NO."
            #
            # Spaced either side of the colon, which is `diagdata.json`'s
            # form rather than the tighter one used elsewhere: UNKNOWNS B4
            # has the argument, and this page writes it spaced.
            if self.confirm[1] == CONT_STEP:
                self.confirm = [self._sure_head, "ARE YOU SURE? : NO"]
                self.sure = False
                self._render()
                return
            # STEP off the question itself. What answering NO does is on no
            # page -- there is no figure for it -- so it does nothing
            # rather than invent a re-enable, and the value the first ENTER
            # stored stands either way.
            self._sure_head = None
            self.sure = False
        if self.confirm:
            if self._confirm_next and self.confirm == self._confirm_next[0]:
                # "Press STEP to continue. The system confirms that the test
                # has started": a confirmation with a second screen behind
                # it, 576013-610 Rev AC p.20-2. FIDELITY U11.
                self.confirm, self._confirm_next = self._confirm_next[1], None
                self._render()
                return
            self.confirm = None
            pending = getattr(self, "_archive_pending", None)
            if pending and (self.cur_step() or {}).get("archive") != pending:
                # confirmed on an archive step the panel has since left
                self._drop_archive_walk()
                pending = None
            if pending:
                # "When you press STEP in response to this message, the system
                # starts saving your setup data to the EEPROM."
                self._archive_pending = None
                self.armed = self.sure = False
                # "When the save is completed, the system returns the
                # original message: ARCHIVE UTILITY / PRESS <STEP> TO
                # CONTINUE" -- 576013-623 Rev AN p.28-2, and the same
                # sentence for the restore on p.28-3. That message is the
                # FUNCTION's own screen, not the step the save was started
                # from, which is where this landed. See SU30.
                self.step = HEADER
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
        # p.3-1 again: STEP with an entry open loses the entry AND steps.
        # See the note in `k_function`.
        self._abandon("STEP")
        if self.step == MODE_SCREEN:
            self.step = HEADER
            self._render()
            return
        steps = self.steps()
        if not steps:
            return
        here = self.cur_step() if self.step != HEADER else None
        if MODES[self.mode] == "DIAGNOSTIC" and (here or {}).get("capture"):
            # "Wait until the read zero pressure value stabilizes and no
            # longer changes, then press STEP": STEP is what takes the
            # reading. 577013-937 Rev J Figure 46, FIDELITY I11.
            self.console.calibrations.capture(here["capture"], self.device)
        if MODES[self.mode] == "DIAGNOSTIC" and (here or {}).get("leave"):
            # Figure 6-27: WORKING, "Displayed while the modem is
            # configuring", and then COMM BOARD over MODEM, "Displayed when
            # modem is configured" -- the function's first screen, with the
            # branch over. FIDELITY D27.
            self.sel.update(here["leave"])
            self.step = 0
            self._render()
            return
        if self._live_mode() and (here or {}).get("head") == "shift":
            # "To view the beginning inventory for the next shift, continue
            # to press STEP until you display the BEGIN INVENTORY message for
            # the next shift", 576013-610 Rev AC p.8-1: the readings are a
            # ring per programmed shift, and CLOSE CURRENT SHIFT comes after
            # the last shift's. FIDELITY O2.
            readings = [i for i, st in enumerate(steps)
                        if st.get("head") == "shift"]
            if readings and self.step == readings[-1]:
                if self.shift_ix + 1 < len(self.console.shifts.programmed()):
                    self.shift_ix += 1
                    self.step = readings[0]
                    self.armed = self.sure = False
                    self._render()
                    return
                self.shift_ix = 0
        if self._live_mode() and (here or {}).get("dlv") in ("tank", "bol"):
            # 576013-610 Rev AC p.5-1: STEP off `SELECT: EDIT/VIEW` shows
            # "the last delivery for this tank", and p.5-2, off that
            # delivery's BOL, "Press STEP to view the previous delivery for
            # this tank" -- `T1: XXX XX, XXXX XX:XXXX` over `TICKET VOLUME:
            # XXX`, the ticket screen again one delivery older. A screen sat
            # between them reading `PRIOR DLVY FOR TANK`, which is the
            # page's sentence drawn as a message, with ENTER doing the
            # stepping. FIDELITY O7.
            records = self.console.deliveries.records.get(self.device) or []
            if here["dlv"] == "bol" and self.dlv + 1 < len(records):
                self.dlv += 1
                self.step = next(i for i, st in enumerate(steps)
                                 if st.get("dlv") == "ticket")
                self.armed = self.sure = False
                self._render()
                return
            # Off the tank screen, or off the OLDEST delivery's BOL, where
            # no page says what comes next: the walk goes on round to its
            # first screen, as every other function's does. UNKNOWNS A66.
            self.dlv = 0
        if self.step == HEADER and self._unconfigured(self.cur_function()):
            # `NO ACTIVE TANKS` is the whole of the function: there is no
            # screen behind it to step onto. What STEP does on the real
            # glass is not photographed, so it does nothing. UNKNOWNS A72.
            self.log("-- nothing programmed for this function")
            self._render()
            return
        if self.step == HEADER:
            self.step = 0
            self.shift_ix = 0
        elif (MODES[self.mode] == "SETUP" and self.subs
              and self.step % len(steps) == len(steps) - 1):
            # "Press STEP to return to the AUTO TRANSMIT SETUP message.
            # Press STEP again to continue to the next communications setup
            # function" -- 576013-623 Rev AN p.6-6, and p.27-2 leaves the
            # REMOVE VMC walk the same way, its last figure the parent's own
            # `REMOVE VMC SERIAL NUMBER` / `PRESS <ENTER>`. The last screen
            # of a branch STEPS back OUT of it rather than round it, which
            # is what makes a branch escapable with the key the manual uses
            # rather than only with BACKUP.
            #
            # Setup only. No chapter-6 figure nests, and the diagnostic
            # branches are walked as rings.
            self._ascend()
        else:
            self.step = (self.step + 1) % len(steps)
        self.armed = self.sure = False
        self.vmc_remove = "no"
        self._render()

    def k_backup(self):
        """The reverse of STEP, through the hierarchy the manual describes.

        "BACKUP will move through the hierarchy of commands as follows:
        through Steps within a Function to that Function; then back through
        Functions to Mode; then back through Modes." So the first step backs
        up to the function screen, not into the previous function's tail.
        """
        self._keyed()
        if self._leave_overlay():
            return
        if self._abandon("BACKUP"):
            return
        self.confirm = None
        self.vmc_remove = "no"
        self._drop_archive_walk()
        if self.subs and self.step <= 0:
            # back out of the screens ENTER went down into, onto the one that
            # offered them.
            #
            # Off `_offered`, not off `fn["steps"]` raw: `self.subs` indexes
            # the OFFERED list, so on a console where any screen is gated out
            # the two disagreed and BACKUP landed on the wrong top-level
            # screen. Exactly the fault D15 records two methods along, where
            # `_diag_children` counted over the raw list and ENTER descended
            # into a different branch from the one the panel was standing on.
            self._ascend()
            self._render()
            return
        if self.step > 0:
            self.step -= 1
        elif self.step == 0:
            self.step = HEADER
        elif self.step == MODE_SCREEN:
            self.mode = self._step_mode(-1)
            self._entered = False
            self._guard_mode()
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
                self._guard_mode()
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
        if self._leave_overlay():
            return
        if self.locked:
            self._render()
            return
        # A screen with no device on it has no next device.
        #
        # "The TANK/SENSOR key is used to advance by tank or sensor through
        # setup procedures or displayed data" (576013-623 Rev AN p.2-3), and
        # the Quick Help p.2 is blunter: "Press to change to the next tank
        # or sensor." Where the glass shows no tank, there is none to change
        # to -- but the pointer is one counter for the whole panel, so this
        # moved it anyway, on 119 screens, changing nothing anyone could
        # see. Two presses on the bare `SETUP MODE` screen and IN-TANK SETUP
        # then opened on TANK 3 with the technician believing they were
        # programming tank 1.
        #
        # `_devices()` is scoped to the FUNCTION, which is why it could not
        # catch this: SYSTEM SETUP has console-wide steps and repeating ones
        # in the same function, and its `_device_count()` fallback handed
        # four phantom devices to SYSTEM LANGUAGE.
        #
        # The discriminator already exists and `screens.py` already uses it
        # for this exact distinction -- `console_step(step) and not
        # step.get("repeat")`. The repeating console screens, Station Header
        # Line 1-4 and Shift #1-4 Start Time, are the two that DO walk, and
        # they keep walking.
        #
        # The function HEADER is deliberately not blocked here, though
        # TANK/SENSOR changes nothing on the glass there either. The
        # docstring on `_devices` says the key works "on any screen" and a
        # test asserts it from the function header, and that sentence is
        # this project's own paraphrase with no page behind it -- but a
        # paraphrase with a test on it is not something to overturn from a
        # page that does not mention headers either. It stays as it is,
        # written down rather than changed. See K3 and UNKNOWNS B6.
        # A third case the menu data does not mark: a step whose FIELD is
        # per-device even though the step calls itself console-wide. `ADD
        # VMC SERIAL NUMBER` draws `x 1:` from a field scoped to the device,
        # and TANK/SENSOR walks the controllers on it.
        from .console import FIELDS
        step = self.cur_step()
        field = FIELDS.get((step or {}).get("console")) or {}
        if self.step == MODE_SCREEN or (
                step and screens.console_step(step)
                and not step.get("repeat")
                and field.get("scope") != "device"):
            self.log("-- TANK/SENSOR: no device on this screen")
            return
        if (self.confirm and self.confirm[1] == "LEAK TEST NOT ACTIVE"
                and not any(self.console.leaks.active("tank", n)
                            for n in self.console.tank_level)):
            # "To advance to the next tank, press TANK/SENSOR. Continue until
            # you have stopped all the tests you want to discontinue. If all
            # active tests are stopped, the system displays the message:
            # LEAK TEST NOT ACTIVE / PRESS <FUNCTION> TO CONTINUE", p.21-2.
            # FIDELITY U9.
            self.confirm = None
            self._flash("LEAK TEST NOT ACTIVE" + chr(10) + CONT_FUNCTION)
            return
        self.chart_open = False
        self.editing, self.buf = False, ""
        self.confirm = None
        devices = self._devices()
        if not devices:
            # a function whose devices are all unconfigured has nothing to
            # step to, and TANK/SENSOR on it does nothing rather than
            # inventing a device 1 to point at
            return
        if self.device in devices:
            self.device = devices[(devices.index(self.device) + 1)
                                  % len(devices)]
        else:
            self.device = devices[0]
        kind = (self.cur_function() or {}).get("scope")
        if self._picked_line(kind):
            # a picked line moves with the key, so the screen and the line a
            # STOP acts on stay one line
            self.sel["line_scope"] = line_pick(kind, self.device)
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
        # The six START and STOP line test walks, which stepped TANK/SENSOR
        # round every position the card has: `STOP LINE TEST: LINE 6` on a
        # site with two lines, and ENTER on it answered as if the line were
        # there. The results function beside each was already here. The
        # operating-mode audit's OP13; FIDELITY O24.
        "START PRESSURE LINE TEST": "781", "STOP PRESSURE LINE TEST": "781",
        "START WPLLD LINE TEST": "7A1", "STOP WPLLD LINE TEST": "7A1",
        "START LINE LEAK TEST": "751", "STOP LINE LEAK TEST": "751",
        # "Your system will display only the diagnostic functions of
        # installed and configured modules and options", 576013-818 Rev AB
        # p.6-1. Four diagnostics were in this map and eight were not, so one
        # site was walked two ways a keypress apart: PRESSURE LINE RESULTS
        # stopped at the fourth line the truck stop programs and PRESSURE
        # LINE LEAK DIAG ran on to a sixth, and 2 WIRE CL DIAGNOSTIC read six
        # sensors nobody wired inside Figure 6-20's own Normal band. See
        # FIDELITY D21.
        "2 WIRE CL DIAGNOSTIC": "741", "3 WIRE CL DIAGNOSTIC": "746",
        "SMART SENSOR DIAGNOSTIC": "721", "PUMP SENSOR DIAGNOSTIC": "771",
        "PUMP RELAY MONITOR DIAG": "7C4", "PRESSURE LINE LEAK DIAG": "781",
        "WPLLD LINE LEAK DIAG": "7A1", "LINE LEAK DIAG DATA": "751",
    }

    # The input module carries two, and it shares a cage key with the relays.
    DEVICE_LIMIT = {"EXTERNAL INPUT SETUP": 2}

    # What the function's own screen says on a console with the card in it
    # and nothing programmed on the card. Photographed on a real TLS-350
    # whose I10200 lists a `4 PROBE / G.T.` and a `PLLD SENSOR BD` and whose
    # I60100 and I78100 read every position OFF: `IN-TANK INVENTORY` over
    # `NO ACTIVE TANKS`, and `PRESSURE LINE RESULTS`, `START PRESSURE LINE
    # TEST` and `STOP PRESSURE LINE TEST` over `SENSORS NOT CONFIGURED`.
    # This drew PRESS <STEP> TO CONTINUE and then walked four tanks and six
    # lines nobody had told it about. Only the four functions photographed
    # are here; what the others say is UNKNOWNS A72. CLOSED U18.
    UNCONFIGURED = {"IN-TANK INVENTORY": "NO ACTIVE TANKS",
                    "PRESSURE LINE RESULTS": "SENSORS NOT CONFIGURED",
                    "START PRESSURE LINE TEST": "SENSORS NOT CONFIGURED",
                    "STOP PRESSURE LINE TEST": "SENSORS NOT CONFIGURED"}

    def _unconfigured(self, fn):
        """The message this function's own screen shows in place of PRESS
        <STEP> TO CONTINUE, or None when there is something to walk."""
        said = self.UNCONFIGURED.get((fn or {}).get("function"))
        if not said or MODES[self.mode] != "NORMAL":
            return None
        c = self.console
        if said == "NO ACTIVE TANKS":
            # the tanks the wire's own inventory reports on, `_tanks`
            empty = not (c.tank_level or c.programmed_tanks())
        else:
            empty = not any(kind == "plld"
                            for kind, _n, _label in c.programmed_lines())
        return said if empty else None

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
        want = (self.cur_step() or {}).get("smart_kind")
        if want:
            # "Press Tank to view the next airflow meter", 577013-800 Rev P
            # Figure 7, and the next AIRFLOW METER is not the next smart
            # sensor: an air flow meter and a vapour pressure sensor are two
            # categories of one card, so AIRFLOW METER SELECT walked a mag
            # sensor's position and offered to enable it for ISD. This is
            # the one place in the menu where a STEP decides the walk rather
            # than the function, which is what two device families under one
            # function means.
            live = [n for n in range(1, max(self.console.capacity("smart"), 1)
                                     + 1)
                    if self.console.sensor_type("smart", n) == want]
            return live or [1]
        if name == "TEST OUTPUT RELAYS":
            # "Repeat this procedure for any additional relays" -- as many
            # as the cage carries, and `outputs.count()` is already the
            # console's answer to that: the greater of the Relay module's
            # capacity and the I/O module's, which is exactly what OUTPUT
            # RELAY SETUP prints. The rule below is per-CARD positions
            # raised to the card count, and for relays that is both wrong
            # ways round: a twelve-relay console walked four on the glass
            # and printed twelve on the paper, and a console carrying only
            # an I/O module walked four and printed two. The report and the
            # panel are one console. See FIDELITY U8.
            return list(range(1, max(self.console.outputs.count(), 1) + 1))
        if name in self.DEVICE_LIMIT:
            n = self.DEVICE_LIMIT[name]
        else:
            n = (max(SLOT_POSITIONS.get(code, 0), self._device_count())
                 if code else self._device_count())
        n = max(n, 1)
        if code and MODES[self.mode] != "SETUP":
            # Reporting on a position nobody wired up is reporting on
            # something that is not there: LIQUID STATUS walked eight sensors
            # and called five of them NORMAL on a site with three.
            live = self.console.configured_devices(code, n)
            # An empty list is NOT an answer, and a photograph said so.
            #
            # This was changed to return `live` even when empty, on the
            # reasoning that a site with no sensors should walk none -- the
            # same reasoning that gave the guard above its three-of-eight
            # case. Then a bare console was photographed: no probes, no
            # sensors, no line leak cards, and ALARM HISTORY walks `SYSTEM
            # ALARM HISTORY` and then `T 1:` `T 2:` `T 3:` `T 4:` exactly as
            # it would on a site with four tanks. IN-TANK INVENTORY is
            # OFFERED there too and answers `NO ACTIVE TANKS`.
            #
            # So a real console does not hide a device it has no programming
            # for -- it walks the position and says there is nothing on it.
            # The flat range is restored, and O16 is open again with the
            # photographs as its evidence rather than closed on an inference.
            if live:
                return live
        return list(range(1, n + 1))

    def _device_count(self):
        """How many devices this function has, rather than a flat sixteen.

        A console steps through the devices its cards can carry: four tanks
        to a probe module, eight sensors to a sensor module. The four
        console-wide ones (header lines, shift times) go round four. One
        function walks PRODUCTS instead, and there are as many of those as
        there are distinct product codes.
        """
        fn = self.cur_function()
        if fn is None:
            return 1
        if fn.get("scope") == "product":
            return max(len(self.console.fuel_products()), 1)
        name = fn["function"]
        need = FUNCTION_REQUIRES.get(name)
        if MODES[self.mode] != "SETUP":
            requires = {f["function"]: f.get("requires")
                        for f in (self.console.available_operating()
                                  + self.console.available_diagnostics()
                                  + self.console.available_reconciliation())}
            module = requires.get(name)
            need = (module,) if module else None
        if name == "GROUND TEMP DIAGNOSTIC":
            # ...except this one, which is a SITE's thermistor and not a
            # card's positions. "When using volumetric line leak detection
            # (VLLD), only one ground temperature thermistor is needed per
            # site and the thermistor must be wired to thermistor position
            # number 1 (positions 2 - 4 are not used)", 576013-879 Rev W
            # p.60, and Figure 6-22 draws one screen headed `g 1:`.
            #
            # It is gated on the VLLD card, so the flat rule walked
            # `capacity("vlld")` -- four positions, or eight on a console
            # with two of those cards -- while `IB21` answered for position
            # 1 alone and nothing else. The glass offered three thermistors
            # the port would not report and the site cannot have.
            # FIDELITY M4.
            return 1
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

    def _product_scope(self):
        """Is the function the panel is on walked by PRODUCT?

        FUEL MANAGEMENT is: "all information displayed is for products, not
        tanks", and TANK moves to the next product rather than the next
        tank. See FIDELITY O4.
        """
        fn = self.cur_function() or {}
        return fn.get("scope") == "product"

    def k_change(self):
        self._keyed()
        if self.locked:
            # A pick-list CHANGE saves as it goes, so this flipped and SAVED
            # a setting behind the security prompt. FIDELITY O20.
            self._render()
            return
        if self.sumpflow and self.sumpflow["stage"] == "select":
            # "press CHANGE until the desired sensor is displayed"
            self.sumpflow["idx"] += 1
            self._render()
            return
        if self.mt_login or self.isd_override == "enter":
            self._leave_overlay()
            return
        if self.isd_override in ("no", "yes"):
            self.isd_override = "yes" if self.isd_override == "no" else "no"
            self._render()
            return
        if self.boot_restore in ("ask", "yes"):
            self.boot_restore = "yes" if self.boot_restore == "ask" else "ask"
            self._render()
            return
        if (self._sure_head and self.confirm
                and self.confirm[1].startswith("ARE YOU SURE?")):
            # "Press CHANGE: DISABLED / ARE YOU SURE? : YES", p.5-30
            self.sure = not self.sure
            self.confirm = [self._sure_head,
                            f"ARE YOU SURE? : {'YES' if self.sure else 'NO'}"]
            self._render()
            return
        self.confirm = None
        e = self.cur_step()
        if e is None:
            return
        if e.get("vmc") == "remove":
            # "Press CHANGE." twice on p.27-2, once for each question
            self.vmc_remove = {"no": "yes", "yes": "no",
                               "sure_no": "sure_yes",
                               "sure_yes": "sure_no"}[self.vmc_remove]
            self._render()
            return
        if (e.get("body") or "").strip() == "PRESS <ENTER>":
            # A prompt is not a field. 576013-623 Rev AN p.5-19 draws
            # `CUSTOM ALARM LABELS` over `PRESS <ENTER>` and the screen's
            # whole content is an instruction to press a key: there is no
            # setting behind it to walk. CHANGE wrote over the manual's
            # second line anyway -- `ENABLED`, then `DISABLED`, from the
            # `flag` field S5BD00 the step happens to carry -- so the
            # console told a technician the feature was disabled when no
            # such state exists. `MODIFY TANK/METER MAP` blanked its prompt
            # the same way from a `list` field, and `MASS/DENSITY` and
            # `FISCAL HEIGHT SECURITY` three screens away refused the key
            # correctly, because they carry no code. The prompt is the test,
            # not the presence of a code. See FIDELITY U5.
            self.log("-- this step is navigable only")
            return
        if e.get("buf"):
            # "Press Change and enter the 4-digit service code" -- the same
            # shape one screen along: CHANGE opens the field, the keypad
            # fills it, ENTER takes it.
            self._change_into("")
            self._render()
            return
        if MODES[self.mode] == "DIAGNOSTIC" and e.get("sel"):
            # "Press CHANGE, then ENTER to access the previous month's
            # report", p.27-3. The two months were two STEP screens and
            # CHANGE did nothing on either. FIDELITY D23.
            names = self._choices(e)
            cur = self._choice(e)
            self.sel[e["sel"]] = names[(names.index(cur) + 1) % len(names)]
            self._render()
            return
        if e.get("act"):
            self.armed = not self.armed
            self._render()
            return
        if e.get("toggle") == "service_session":
            # Figure 6-5: CHANGE walks DISABLED to ENABLED and back, and the
            # console can refuse -- "If there is a delivery in progress, then
            # cannot change to Enable, and it will display 'DISABLED DEL IN
            # PROGRESS'", which is twenty-four characters and so is the whole
            # second line. See FIDELITY D9.
            c = self.console
            said = (c.end_service_session() if c.service_session()
                    else c.start_service_session())
            self.log(f"-- service notice session: {said.lower()}")
            if said.startswith("DISABLED DEL"):
                self.confirm = [e["text"][:COLS], said[:COLS]]
            self._render()
            return
        if self._live_mode():
            # Operating mode has choices too: which tanks to test, at which
            # rate, for how long. CHANGE walks them, ENTER accepts.
            if e.get("dlv"):
                if e["dlv"] == "tank":
                    self.log("-- TANK/SENSOR picks the tank")
                    return
                self._change_into(self._delivery_value(e))
                self._render()
                return
            if e.get("shift_adjust"):
                # "Press CHANGE, then enter the amount of the delivery
                # indicated on the slip given to you by the tanker
                # operator", 576013-610 Rev AC p.8-2. It was drawn as a
                # reading and CHANGE did nothing. FIDELITY O2.
                self._change_into(self.console.shifts.shown(
                    "deliveries", self._shift_no(), self.device))
                self._render()
                return
            if e.get("adjust"):
                self._change_into("")
                self._render()
                return
            if e.get("density"):
                # "Press CHANGE and enter any one of the three values below
                # from the product's delivery ticket". This was a read-only
                # `live` step with no code, so CHANGE did nothing at all and
                # the console had no route to a delivery density. FIDELITY
                # O13.
                self._change_into("")
                self._render()
                return
            if e.get("action") and (MODES[self.mode] == "RECONCILIATION"
                                    or e.get("body")):
                # "press CHANGE. The system displays SHIFT CLOSE NOW: YES",
                # and p.8-2 asks for the same on CLOSE CURRENT SHIFT
                self.armed = not self.armed
                self._render()
                return
            if self._typed_day(e):
                # "To select a different date, press CHANGE, enter the date,
                # and then press ENTER" -- so CHANGE here opens the date
                # template rather than walking CURRENT and PREVIOUS.
                self._begin_edit("")
                self._render()
                return
            if e.get("sel"):
                names = self._choices(e)
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
            # 576013-623 Rev AN p.5-27 draws this screen and both of its
            # lines: `TANK CHART SECURITY` over `ENTER PASSCODE->______<`,
            # six underscores for the six digits it wants. This drew
            # `ENTER PASSCODE / THEN <ENTER>`, whose second line is nobody's.
            # See FIDELITY U5.
            typed = "".join(ch for ch in self.buf if ch.isdigit())[:6]
            field = typed + "_" * (6 - len(typed))
            self._flash("TANK CHART SECURITY" + chr(10)
                        + f"ENTER PASSCODE->{field}<")
            return
        if e.get("console"):
            from .console import FIELDS
            f = FIELDS.get(e["console"], {})
            if f.get("kind") == "view":
                self.log("-- view only: this step takes no change")
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
            # A plain numeric scalar CLEARS on CHANGE and takes its digits
            # calculator-fashion -- `_change_into`'s docstring has the
            # video that settles it, and does this for every `int`, `float`
            # and `digits` FIELD. A console setting has no such kind, so
            # `BDIM TRANS ALARM DELAY` seeded with `024` and one press of
            # `1` overtyped it to `124`, where PRECISION TEST DURATION two
            # screens away cleared first. `number` is the same fact under
            # another name, and it is on the field already.
            # `chartcode` clears for the same reason a `digits` field does,
            # and p.5-27 draws the console agreeing: the screen that asks
            # for an existing passcode is `ENTER PASSCODE->______<`, six
            # underscores and no value in them. SYSTEM SECURITY is the same
            # six-digit code one screen away and clears because its kind is
            # `digits`.
            self._change_into("" if (f.get("number")
                                     or f.get("kind") == "chartcode")
                              else self._console_value(e))
            self._render()
            return
        if e.get("point") in ("height", "volume"):
            self._change_into(self._point.get(e["point"], ""))
            self._render()
            return
        if e.get("point") == "count":
            self.log("-- STEP adds a chart point")
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
            # Developer text, in lower case, on a 24-column all-caps
            # display. The only such string on the glass. See FIDELITY U5.
            self.log("-- this step is navigable only")
            return
        if e.get("offset"):
            # "Then press CHANGE. The Offset value flashes `0`. Enter the
            # desired offset in increments of +/-0.01% with a maximum range
            # of +/-9.99%" -- p.17-6. One cell of the four is a value, and
            # CHANGE clears it rather than walking anything.
            if e.get("meter") is None:
                # nothing mapped: there is no meter for an offset to be of
                self.log("-- no meter mapped to give an offset to")
                return
            self.editing, self.buf, self.cur = True, "", 0
            self._render()
            return
        if e.get("map"):
            # "Press [left arrow] or [right arrow] to move to the field you
            # want to change. Then press CHANGE until the correct choice
            # appears." p.17-5, and the cell the cursor is on is the only
            # one a press touches.
            if not self.editing:
                self.editing = True
                self.buf = MAP_BLANK
                self.slot = 0
            cells = self.buf.split()
            n = self.slot % len(MAP_CELLS)
            choices = self._map_choices(n, cells)
            here = choices.index(cells[n]) if cells[n] in choices else -1
            cells[n] = choices[(here + 1) % len(choices)]
            if n == 0 and cells[1] not in self._map_choices(1, cells):
                # a slot belongs to its own bus: "9 - 16 ... Type 2 bus",
                # "1 - 3 ... Type 3 bus". Changing the bus under a slot it
                # does not have leaves a row the command would refuse.
                cells[1] = "X" * MAP_CELLS[1][1]
            self.buf = " ".join(cells)
            self._render()
            return
        f = self.cur_field()
        if f is not None and f.get("kind") == "slots":
            # "To activate a PLLD, you replace the X with a number by pressing
            # the CHANGE key ... You move between PLLDs by pressing the right
            # or left arrow key."
            if not self.editing:
                wires = (self.console.positions(f["code"][1:4])
                         or f.get("slots") or 4)
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
                names = [screens.screen_word(f, lab) for _v, lab
                         in fieldio.choices_of(f, self.console)]
            cur = self.buf if self.editing else str(self._shown() or "")
            nxt = (names.index(cur) + 1) % len(names) if cur in names else 0
            self.buf = names[nxt] if names else ""
            self.editing = True
            self.cur = 0
        else:
            self._change_into(self._edit_seed())
        self._render()

    # Field kinds the keypad types as numbers.
    #
    # `chartcode` is here because 576013-623 Rev AN p.5-19 says what it
    # takes -- "press CHANGE and enter a 6-digit numeric passcode" -- and
    # because the handler that stores it has always agreed: `_enter_console`
    # keeps only `c.isdigit()` and refuses anything that is not six of them.
    # It was NOT in this set, so the six digit keys multi-tapped to letters
    # and that refusal was guaranteed: `1 2 3 4 5 6` entered `QADGJM` and
    # the console turned it down, every time, on every key sequence. The
    # whole feature was unreachable from the panel, with its ENTER handler
    # written correctly and unsatisfiable. `_chart_locked` -- the screen you
    # meet when a passcode already EXISTS -- has treated the field as
    # numeric all along, which is what makes this an omission rather than a
    # reading. See the setup-mode audit, SU8.
    #
    # `list` is here for the same reason and was found by the same sweep:
    # all eight of them are numbers with separators in them -- the siphon
    # and line manifolded partners are TANK NUMBERS, `T#:
    # 00,00,00,00,00,00,00`, and the rest are alarm numbers, dial times,
    # the meter map and a meter offset. One press of `6` on a manifold
    # partner list put an `M` in it.
    NUMERIC_KINDS = ("int", "flag", "float", "time", "date", "digits",
                     "chartcode", "list")

    def _signed_entry(self, field=None):
        """Does this field take a minus sign?

        "If the value is negative, press the +/- key so that a minus (-) sign
        appears on the display" is Tank Tilt, and the pressure offset screens
        say the same. A clock does not have a negative half.
        """
        if (self.cur_step() or {}).get("offset"):
            # "Enter the desired offset in increments of +/-0.01% with a
            # maximum range of +/-9.99%", p.17-6. The step's field is
            # `S7B400`, whose kind is the wire's packed `list` and carries
            # no sign of its own; the SCREEN types one number.
            return True
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
        if (step.get("adjust") or step.get("shift_adjust")
                or step.get("point") in ("height", "volume")):
            return True
        if step.get("entry"):
            # TEST DURATION, the one place in Operating Mode a technician
            # types a number, and it could not be typed: "press CHANGE,
            # enter the test duration, then press ENTER" (576013-610
            # Rev AC p.20-2), and a duration in hours is a numeric entry.
            # p.3-3 scopes the multi-tap rule to the other kind of field --
            # "To enter either an alphabetic or numeric character, press
            # the 2 key once to enter an 'A'" -- and lists the keys that
            # belong to numeric entries separately.
            #
            # The `6` key went M, N, O, 6, so a twelve hour test took eight
            # presses and the field spent three of every four showing a
            # LETTER where the screen says hours. The same console
            # already does it the other way one mode across: MANUAL
            # ADJUSTMENTS' volume and Delivery Maintenance's ticket both
            # take a digit on the first press, and both are in this method
            # one line up. See the operating-mode audit, OP12.
            return True
        if self._typed_day(step):
            return True
        if step.get("dlv") in ("insert", "date", "time"):
            return True
        if step.get("buf") in ("ps_zero", "ps_span"):
            # a reference pressure is a number
            return True
        console = step.get("console")
        if console:
            from .console import FIELDS
            spec = FIELDS.get(console, {})
            if spec.get("number") or spec.get("digits"):
                # `number` is a field with a stated range; `digits` is one
                # the manual gives no range for and still types as a
                # number. This branch had no `digits` field to find until
                # the seven AVG SALES days got one, and those are the
                # console's clearest case: 576013-623 Rev AN p.9-2 draws
                # `AVG SALES SUN:          983` and says "enter the average
                # daily sales", and the keypad was multi-tapping letters
                # into it. A mechanism written for a case and never given
                # one -- the mirror of this project's commonest shape.
                #
                # `PRODUCT CODE` sits beside them in the same sweep and is
                # deliberately NOT here: p.7-2 says "Enter the alphanumeric
                # code used by a point-of-sale terminal", and notes that a
                # UK four-star code is entered as `4*` with five presses of
                # the zero key. Letters are right there.
                return True
            if spec.get("kind") in self.NUMERIC_KINDS:
                # A console setting's KIND is judged by the same list a
                # code field's is. Only the test above the `console` branch
                # was reading it, and a console step has no `cur_field`, so
                # `chartcode` -- the one numeric kind that only ever
                # appears here -- never reached it. See the note on
                # `NUMERIC_KINDS`.
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
        if kind == "list":
            # A list is typed INTO ITS TEMPLATE -- `T#: 00,00,00,00,00,00,00`
            # with the commas already on the glass, and CHANGE puts the
            # cursor on the first digit -- so the seed is the value and not
            # the word the resting screen shows for it. The generator's tank
            # list is the one that has both: p.23-3 draws `TANK #: ALL TANK`
            # where nothing is entered and `TANK #: X, X` where something is,
            # and seeding with `ALL TANK` gave the keypad eight letters to
            # overtype.
            return screens.shown(self.console, f, self._stored())
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

    def _date_entry(self):
        """Is the screen in front of you asking for a date?

        Every one used to be a setup field with a `date` kind. SELECT DAY is
        the first that is not: it is a live-mode screen with no function code
        behind it at all, and the manual still says to type a date into it.
        See FIDELITY Q1.
        """
        return ((self.cur_field() or {}).get("kind") == "date"
                or self._typed_day(self.cur_step()))

    def _typed_day(self, step):
        """A SELECT DAY the manual types a date into rather than toggles.

        The two report families genuinely differ, which is what made one
        model wrong for both. 576013-610 Rev AC p.28-3, the reconciliation
        report: "To select a different date, press CHANGE, enter the date,
        and then press ENTER." p.28-7, the three variance chapters: "To
        select the previous month and day (as defined by the reconciliation
        period), press CHANGE, then press ENTER" -- which is a toggle, and is
        what those keep. See FIDELITY Q1.
        """
        if not (step or {}).get("typed_day"):
            return False
        return self._recon_word(step).startswith("SELECT DAY")

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

        Retain-or-clear is a property of the FIELD, not of the key, and video
        of real consoles now settles all three kinds it can be. A clock
        RETAINS and takes the cursor on its first character. A date BLANKS to
        its template. And a plain numeric scalar -- a limit, a percentage --
        CLEARS outright, with the cursor at the right-hand entry position and
        digits arriving calculator-fashion. That last one was unrecorded until
        a Spanish-firmware TLS-350 PLUS was filmed doing it on six separate
        limit fields in a row. See UNKNOWNS A3 for the video and the caveats:
        the console filmed is Spanish, and every prior it cleared was a zero.
        """
        if self.editing:
            self.buf, self.cur, self._tap = "", 0, None
            return
        if self._time_field():
            self.meridiem = self._seed_meridiem()
        if (self.cur_field() or {}).get("kind") in ("int", "float", "digits"):
            self._begin_edit("")
            return
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
        if (self._sure_head and self.confirm
                and self.confirm[1].startswith("ARE YOU SURE?")):
            # "Press ENTER: ARE YOU SURE? : YES / PRESS <STEP> TO
            # CONTINUE", p.5-30. The page draws ENTER only on YES; what it
            # does on NO is on no page, so it does nothing.
            if self.sure:
                self.confirm = ["ARE YOU SURE? : YES", CONT_STEP]
                self._sure_head = None
                self.sure = False
                self._render()
            return
        if self.mt_login:
            if self.mt_login == "insert" and not self._mt_timed_out():
                self._mt_present()
            else:
                # "Press any key to display the operating mode main screen"
                self.mt_login = None
                self._render()
            return
        if self.isdflow:
            st = self.isdflow
            c = self.console
            if st["state"] == "insufficient":
                # Retry? ENTER re-arms the window
                self._isdflow_start("automap")
            elif st["state"] == "select":
                hoses = c.isd_hoses() + [(0, "", "NON VAPOR RECOVERY HOSE")]
                d, _fp, _label = hoses[st["idx"] % len(hoses)]
                full = c.isd_afm_full(d) if d else None
                if full is not None:
                    # "You cannot map more than 2 fueling points (and
                    # related hoses) to one AFM", 577013-937 Rev J Figure 11
                    st.update(state="nospace", afm=full)
                    self.log(f"-- auto map: AFM{full} has no space for "
                             f"hose {d}'s fueling point")
                elif d:
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
        if self.sumpflow:
            self._sumpflow_enter()
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
                # Being let in is the acknowledgement, and being put back in
                # Normal Mode is the refusal. Neither is announced: `CODE
                # ACCEPTED` and `INVALID SECURITY CODE` were this project's.
                # See FIDELITY U5.
                self.log("-- security code accepted")
            else:
                self.buf = ""
                self.mode = MODES.index("NORMAL")
                self.locked = False
                self._entered = False
                self.log("-- security code refused")
            return
        e0 = self.cur_step()
        if e0 is not None and e0.get("pick") == "device":
            # "Enter the number of the relay you want to test, then press
            # ENTER. The system displays the number and name of the relay
            # you selected. For example: `R 1: OVERFILL ALARM` / `PUSH
            # ALARM/TEST KEY`" -- 576013-610 Rev AC p.22-1. That screen is
            # this function's next step and already draws itself; nothing
            # could reach it with a chosen relay. See FIDELITY U8.
            want = self.buf.strip()
            self.editing, self.buf = False, ""
            devices = self._devices()
            if want.isdigit() and int(want) in devices:
                self.device = int(want)
                self.step += 1
            else:
                # A relay the console does not have. The manual draws no
                # refusal for this and `_refuse`'s rule is that the console
                # takes the keystrokes it can use and says nothing about the
                # ones it cannot, so the screen stays where it is.
                self.log(f"-- no relay {want or 'number'} on this console")
            self._render()
            return
        if (self.editing and e0 is not None
                and e0.get("buf") in ("ps_zero", "ps_span")):
            # "Enter reference pressure value from calibrated test device at
            # pressure sensor via TLS Console front panel", 577013-800 Rev P
            # p.20-45. The screen goes on showing what was entered; no
            # page draws a confirmation for it. FIDELITY I11.
            try:
                value = float(self.buf.strip() or 0)
            except ValueError:
                self._refuse("numbers only")
                return
            self.console.calibrations.enter(e0["buf"][3:], self.device,
                                            value)
            self.editing, self.buf, self.cur = False, "", 0
            self._render()
            return
        if self.editing and e0 is not None and e0.get("buf") == "mt_id":
            # "ENTER ID TO BLOCK / ID: XXXXXX" and then ENTER: the typed key
            # is what the ARE YOU SURE screen below will block, which is
            # Figure 6-4's other branch. See FIDELITY D13.
            self.console.mt_pending = self.buf.strip().upper()
            self.editing, self.buf, self.cur = False, "", 0
            self.confirm = [f"ID: {self.console.mt_pending}"[:COLS],
                            CONT_STEP]
            self._render()
            return
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
        if (MODES[self.mode] == "DIAGNOSTIC" and e0 is not None
                and e0.get("sel") and not self.confirm
                and self._branch_at() is None):
            # `SELECT: PREVIOUS MONTH / PRESS <STEP> TO CONTINUE`, p.27-3:
            # ENTER acknowledges the choice with the line it was showing,
            # which is what a selection does in the two live modes too.
            # FIDELITY D23.
            self.confirm = [self._lines()[1][:COLS], CONT_STEP]
            self._render()
            return
        if e0 is not None and e0.get("run"):
            # "PRESS <ENTER>", and that is the whole interaction -- no YES/NO
            # to arm first, which is what `act` is for. Figure 6-29's manual
            # test and Figure 6-30's evac hold are both this shape: ENTER on
            # the confirming screen does it and the acknowledgement is the
            # figure's own line over PRESS <STEP> TO CONTINUE.
            pick = str(self.sel.get(e0.get("pick") or "", "") or "ALL")
            if e0.get("pick") and pick != "ALL":
                # the one sensor SELECT VAC SENSOR was turned to. FIDELITY D28.
                said = self.console.diag_action(e0["run"], int(pick),
                                                "single")
            else:
                said = self.console.diag_action(e0["run"], self.device)
            self.log(f"-- diag {e0['run']}: {said.lower()}")
            self.confirm = [said[:COLS], CONT_STEP]
            # and a branch the figure goes on with after the confirmation
            # opens its next stage: Figure 6-27's ARE YOU SURE, then WORKING.
            # FIDELITY D27.
            self.sel.update(e0.get("then") or {})
            self._render()
            return
        if e0 is not None and e0.get("act"):
            if not self.armed:
                self.log("-- nothing to do on this step")
                return
            self.armed = False
            said = self.console.diag_action(e0["act"], self.device,
                                            e0.get("ident"))
            self.log(f"-- diag {e0['act']}: {said.lower()}")
            self.confirm = [said[:COLS], CONT_STEP]
            self.sel.update(e0.get("then") or {})
            self._render()
            return
        parent = self._branch_at()
        if parent is not None:
            # "PRESS <ENTER>": and down a level you go. Appended rather than
            # assigned, because a branch can hold a branch -- see `sub`.
            self.subs = self.subs + [parent]
            self.step = 0
            self.confirm = None
            self._render()
            return
        e = self.cur_step()
        if self._chart_locked(e):
            if self.buf == self.console.chart_code:
                self.chart_open = True
                self.buf = ""
                self.log("-- tank chart passcode accepted")
            else:
                self.buf = ""
                self.log("-- tank chart passcode refused")
            return
        if e is not None and e.get("toggle"):
            # "E: ENABLED / PRESS <STEP> TO CONTINUE". The acknowledgement is
            # the line the screen was already showing, which is O9's shape in
            # a third mode.
            self.confirm = None
            self.confirm = [self._lines()[1][:COLS], CONT_STEP]
            self._render()
            return
        if e is not None and e.get("map"):
            self._enter_map()
            return
        if e is not None and e.get("offset"):
            self._enter_offset(e)
            return
        if e is not None and e.get("vmc") == "remove":
            # "Press ENTER to confirm.." is drawn on the YES of the FIRST
            # question and nowhere else in the walk: the second question is
            # answered with STEP. p.27-2.
            if self.vmc_remove == "yes":
                self.confirm = ["REMOVE VMC: YES", CONT_STEP]
                self._render()
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
                self.log("-- nothing to do on this step")
                return
            label = e["text"].upper()
            if not self.sure:
                self.confirm = [f"{label}: YES"[:COLS], CONT_STEP]
                self.sure = True
                self.armed = False
            else:
                # spaced, off the glass -- see the note in `_lines`
                self.confirm = ["ARE YOU SURE? : YES", CONT_STEP]
                self._archive_pending = e["archive"]
            self._render()
            return
        if self._live_mode() and e is not None:
            # A results screen is a reading, and ENTER is not one of its
            # keys. 576013-610 Rev AC ch.11 gives every screen of
            # PRESSURE LINE RESULTS three keys and says so for each of
            # them: STEP to reach it, "to view 3.0 gph test results for
            # other lines in the system, press TANK/SENSOR", "to print
            # ... press PRINT". Starting a test is chapter 12, a separate
            # function with its own three-screen confirmation, which this
            # console implements correctly beside it.
            #
            # ENTER here started a real 3.0 gph test on the line whose
            # result you were reading -- on real hardware, from a screen
            # that cannot do it, and a 3.0 gph failure shuts a pump down.
            # The code's own comment argued the convenience: "the same
            # engine, reached from the line rather than from the function."
            # A training console that offers a shortcut the hardware does
            # not have teaches the shortcut. The six `runtest` keys went
            # out of `normaldata.json` with it, so there is no step left
            # for this branch to have run on. See FIDELITY O15 and the
            # operating-mode audit's OP11.
            if e.get("dlv"):
                self._enter_delivery(e)
                return
            if e.get("shift_adjust"):
                self._enter_shift_adjustment()
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
                if not self.armed and (MODES[self.mode] == "RECONCILIATION"
                                       or e.get("body")):
                    self.log("-- nothing to do on this step")
                    return
                self.armed = False
                self._flash(self._run_action(e["action"]))
                return
            if self._typed_day(e) and self.editing:
                self._accept_day(e)
                return
            if e.get("sel"):
                # "Press CHANGE to choose INSERT, then press ENTER ... The
                # system displays this message": every selection in both
                # live modes is acknowledged, and the acknowledgement is the
                # line the selection screen was already showing, over PRESS
                # <STEP> TO CONTINUE. Thirteen screen shapes and about thirty
                # instances, and ENTER on a selection used to be a no-op.
                # The mechanism was here for TYPED values all along.
                # FIDELITY O9.
                # The acknowledgement is the line the screen was showing,
                # whichever renderer drew it: Reconciliation Mode words its
                # own selections -- "ADJUSTMENT TYPE: DAILY", "SELECT
                # PERIOD: PREVIOUS" -- and Operating Mode draws some bare,
                # "SINGLE TANK", "3.0 GPH".
                if e.get("enter_steps"):
                    # Except where the page says ENTER goes on. The two
                    # pressure STOP walks: "To stop PLLD tests on all lines,
                    # press ENTER or STEP. When you press ENTER or STEP, the
                    # system displays the message: STOP LINE TEST: ALL LINES
                    # / PRESS <ENTER>", 576013-610 Rev AC p.11-4, and on the
                    # line picker, p.11-5, "then press ENTER. The system
                    # displays the message: STOP LINE TEST: LINE (#)". p.12-4
                    # and p.12-5 say both again for WPLLD. The START walks'
                    # own sentence is "CHANGE, then ENTER to select a single
                    # line, then press STEP", so they keep the
                    # acknowledgement. FIDELITY O24, UNKNOWNS A70.
                    self.k_step()
                    return
                self.confirm = None
                self.confirm = [self._lines()[1][:COLS], CONT_STEP]
                self._render()
                return
            if e.get("entry") and self.editing:
                self._accept_entry(e)
                return
            if e.get("density") and self.editing:
                self._accept_density(e)
                return
            return
        code, f = self.cur_code(), self.cur_field()
        if not self.editing or not code:
            return
        if (f or {}).get("kind") == "slots":
            wires = (self.console.positions(f["code"][1:4])
                     or f.get("slots") or 4)
            base = ((self.device - 1) // wires) * wires
            self.console.set_slots(f["code"][1:4], self.buf, base)
            self.confirm = [f"SLOT #: {self.buf}"[:COLS],
                            CONT_STEP]
            self.editing, self.buf = False, ""
            self._refresh_site()
            self.refresh_traffic()
            self._render()
            return
        typed = self.buf
        if self._time_field() and self.meridiem:
            # the keypad enters a twelve hour clock and the arrows say which
            # half of the day, so both go to the encoder
            typed = f"{typed} {self.meridiem}"
        try:
            data = fieldio.encode(f or {}, code,
                                  screens.wire_word(f, typed),
                                  self.console.stored(code),
                                  self.console.metric(), self.console)
        except ValueError as e:
            self._refuse(str(e))
            return
        if data is None:
            self.log("-- nothing entered")
        else:
            # The step this ENTER is ON, read before the store. A stored
            # value can change which steps are visible -- writing the test
            # date makes Test Start Time appear -- and `cur_step()` is an
            # INDEX into a list rebuilt on every call, so reading it after
            # the write confirmed the date under the word `TIME:`, the label
            # of the step the write had just pushed into this slot.
            # 576013-623 Rev AN p.8-2 confirms `DATE: XX/XX/XXXX`, and that
            # page is the first walk in chapter 8. FIDELITY U50.
            step = self.cur_step()
            # through the console's door: 52B keeps a store of its own,
            # and the screen, the port and the paper have to read one value.
            # FIDELITY S25.
            self.console.store(code, data)
            if code.upper().startswith("S501"):
                # SET TIME is the console's clock, not a stored number
                self.console.set_clock()
            self.console.save()
            self._refresh_site()
            self.refresh_traffic()
            # "PRODUCT CODE: X / PRESS <STEP> TO CONTINUE": the console
            # holds the confirmation until you step off it.
            shown = screens.screen_word(
                f, fieldio.decode(f, code, data, self.console) if f
                else self.buf)
            if self._is_label_step(step):
                head = f"{self._device_code()}{self.device}: {shown}"
            else:
                # In the field's own mask, which is the same mask the
                # screen one line above it uses.
                #
                # This drew whatever `fieldio.decode` returned, and decode's
                # float branch is `"%g"` -- the shortest round-trip form,
                # which is precisely what a fixed-width console field is
                # not. So the console said `TANK DIAMETER: 96.5` on the
                # confirmation and `TANK DIAMETER: 096.50` one STEP later:
                # one value, two shapes, a keypress apart. Worse, typing
                # `499.995` was CONFIRMED as `499.995` and stored as
                # `500.00`, so the confirmation asserted a precision the
                # console does not keep.
                #
                # The manuals never draw a confirmation in a different shape
                # from the field it confirms -- 576013-623 Rev AN p.7-5
                # `TANK DIAMETER: XXX.XX`, p.7-14 `WATER WARNING: XX.X`,
                # p.17-3 `ALARM THRESHOLD: X.XX`, p.17-4 `ALARM OFFSET:
                # XXXXXX`, each the same mask as the field above it on the
                # same page. Twenty-three fields across nine functions
                # disagreed. See R1.
                # In the field read BEFORE the store, for the same reason
                # `step` is. `_masked` goes through `cur_field()`, an index
                # into a list the write can lengthen: entering a leak test
                # DURATION makes the Test Date step appear eleven rows above
                # it, so the mask that formatted the confirmation was the
                # mask of whatever field the index had slid onto and
                # `DURATION: 03` came back `DURATION: 3`. FIDELITY U50.
                shown = screens.masked(f, shown)
                scoped = self._setup_scope_head(step)
                if scoped is not None and step.get("confirm") == "screen":
                    # The one acknowledgement on the shelf that IS the
                    # screen. 576013-623 Rev AN p.8-8 -- "To enable Leak Test
                    # Early Stop, press CHANGE and then ENTER and the system
                    # displays:" -- draws `TST EARLY STOP: ALL TANKS` over
                    # `ENABLED`, and there is no PRESS <STEP> TO CONTINUE
                    # anywhere on that page. FIDELITY U50.
                    self.confirm = [scoped[:COLS],
                                    self._second(step.get("l2", ""),
                                                 shown)[:COLS]]
                    self.editing, self.buf = False, ""
                    self._render()
                    return
                # the confirmation is the screen's own second line, held: the
                # manual shows "TIME: XX:XX XM" over CONT_STEP
                words = step["text"].split("(")[0].strip().upper()
                _head, label = self._setup_context(step, words)
                if scoped is not None:
                    # A step that names its own scope has no label to invent
                    # one from. `_setup_context`'s fallback is the step's
                    # DEVELOPER text plus a colon, which is written for the
                    # register and not for the glass, so ENTER drew
                    # `TEST FREQUENCY: AUTOMATI` (25 columns, cut mid-word)
                    # where p.8-5 draws `AUTOMATIC` bare, and
                    # `TEST MONTH WEEK DAY: JAN` (35) where p.8-3 draws
                    # `JUNE WEEK 1 FRI`. The scoped screens that DO carry a
                    # label keep it -- `DATE: XX/XX/XXXX` p.8-2, `TIME: XX:XX
                    # XM` and `DURATION: XX` p.8-8, `Pd = 99%` p.8-6 -- and
                    # the rest confirm with the chosen word on its own, which
                    # is what the manuals draw wherever they draw one.
                    #
                    # `confirm_label` is for the screen that draws neither:
                    # p.8-8's rate confirmation is `TEST RATE: 0.10 GAL/HR`,
                    # using the scope PREFIX as a label the resting screen
                    # does not use -- and the same screen in chapter 13
                    # confirms `X.X GAL/HR` bare, so it is one screen's
                    # spelling and not a rule. FIDELITY U50.
                    label = step.get("confirm_label") or step.get("l2") or ""
                # and with the step's own GAP, for the same reason as the
                # mask: the field screen uses `step["gap"]` and this used
                # the default space, so `RECON WARN LIMIT:000003` -- which
                # 576013-623 Rev AN p.116 draws with nothing between the
                # colon and the digits -- was confirmed with a space in it.
                # Found by the test for the mask, walking the same function.
                head = self._second(label, shown, step.get("gap", " "))
            self.confirm = [head[:COLS], CONT_STEP]
            if str((step or {}).get("sure", "\0")) == str(data):
                # "Press STEP: DISABLED / ARE YOU SURE? : NO" -- the beeper
                # is the one setup value the console asks twice about, and
                # it asks only on the answer that silences it. STEP off the
                # ordinary acknowledgement goes to the question instead of
                # to the next step. See the setup-mode audit, SU11.
                self._sure_head = head[:COLS]
        self.editing = False
        self.buf = ""
        # And put it on the glass. This path built the acknowledgement
        # and never repainted, which is not a cosmetic omission: `_poll`
        # redraws only in Operating Mode, mid-entry or on a live screen --
        # and ENTER has just cleared `editing` -- so in Setup Mode nothing
        # redrew until the NEXT key, and the next key clears `confirm` on
        # its way past. Every typed setup value's `XXX: value / PRESS
        # <STEP> TO CONTINUE` was therefore built, held, and never seen.
        #
        # It reads as correct from `_lines()`, which recomputes from
        # `confirm` on demand, and that is what the tests call; the canvas
        # is where it was missing. `_enter_console` and the `sel`, `slots`,
        # `toggle` and archive branches all render for themselves, which is
        # why only this one -- the S-code fields, the commonest screen in
        # Setup Mode -- was silent. See FIDELITY U5 and the note on
        # `_lines` in `audits/2026-09-10-key-behaviour.md`.
        self._render()

    # ---- starting and stopping tests from the panel ------------------------
    def _accept_entry(self, step):
        """A number typed at a step that wants one, in its own range."""
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        if not text.isdigit():
            self._refuse("numbers only")
            return
        value = int(text)
        lo, hi = step.get("min"), step.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            self._refuse(f"{lo} to {hi} hours")
            return
        self.sel[step["entry"]] = str(value)
        # 576013-610 Rev AC p.20-2 confirms the typed duration the way it
        # confirms everything else: the value over PRESS <STEP> TO CONTINUE.
        # This flashed `ENTERED` over `3 HOURS`, a screen no manual draws.
        self.confirm = [f"DURATION: {value}"[:COLS], CONT_STEP]
        self._render()

    def _accept_density(self, step):
        """A delivery density typed on the NEXT or LAST DELIVERY screen.

        "Press ENTER and the console converts the entered value to the
        actual density" -- and the value the console REPORTS is the one that
        was entered, which is what 61F answers with and what the setup
        manual says of the same entry on the tank's own density: "if this
        value is accepted, it will be converted to actual density (but the
        user entered value is displayed)". Which of the three forms a number
        is, and the conversion, is on no page here: UNKNOWNS A23.
        """
        text = self.buf.strip()
        self.editing, self.buf = False, ""
        try:
            value = float(text)
        except ValueError:
            self._refuse("numbers only")
            return
        self.console.set_delivery_density(self.device, step["density"], value)
        self.confirm = [f"DENSITY         :{value:.4f}"[:COLS], CONT_STEP]
        self._render()

    def _accept_day(self, step):
        """A date typed on SELECT DAY, which is the day the report covers.

        The manual does not draw the typing screen, only the instruction, so
        the shape is the one this chapter uses for every other typed value:
        the choice you made last on line one, and the field being typed on
        line two (576013-610 Rev AC p.28-19). What it MEANS is settled --
        p.28-20 asks for "the desired closing date". See FIDELITY Q1.
        """
        digits = "".join(ch for ch in self.buf if ch.isdigit())
        self.editing, self.buf = False, ""
        if len(digits) != 8:
            self._refuse("a date is MM/DD/YYYY")
            return
        try:
            when = time.mktime(time.strptime(digits, "%m%d%Y"))
        except (ValueError, OverflowError):
            self._refuse("not a date")
            return
        self.sel["day"] = when
        self._recon_sync()
        self.confirm = [self._lines()[1][:COLS], CONT_STEP]
        self.log("-- reconciliation day set to "
                 + clock_date(time.localtime(when)))
        self._render()

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
            self._refuse("numbers only")
            return
        kind, previous = self._recon_period()
        # the period the panel is pointed at, closed or not: SELECT SHIFT:
        # PREVIOUS, or the closing date typed on SELECT DAY. The second half
        # of that pair was read and thrown away. FIDELITY Q1.
        day = self._recon_day() if kind == "daily" else None
        landed = self.console.bir.adjust(self.device, gallons, kind,
                                         previous=previous and day is None,
                                         day=day)
        if landed is None:
            # a shift or a day this console no longer holds: no page draws
            # what the console says, so it says nothing
            self._refuse(f"no {kind} period to adjust")
            return
        self.console.save()
        word = self._recon_word(step)
        self.confirm = [f"{word}: {gallons:.0f}"[:COLS],
                        CONT_STEP]
        self.log(f"-- {kind} adjustment {gallons:+.0f} gallons on tank "
                 f"{self.device}")
        self._render()

    # The three Mag sump walks' confirmation lines, p.24-4 and p.24-6.
    SUMP_CONFIRM = {"start": "START LEAK TEST", "height": "START MEASURING HT",
                    "stop": "STOP LEAK TEST"}

    def _sumpflow_lines(self):
        """SELECT MAG SENSOR, then the confirmation, 576013-610 Rev AC p.24-4:

            SELECT MAG SENSOR        SELECT MAG SENSOR
            ALL MAG SENSORS          s1: (MAG SENSOR LABEL)

            START LEAK TEST: s 1
            PRESS <ENTER>

        The page draws the confirmation for one sensor only; ALL on it is
        UNKNOWNS A53.
        """
        st = self.sumpflow
        if st["stage"] == "select":
            pick = self._sump_picks()[st["idx"] % len(self._sump_picks())]
            if pick is None:
                return ["SELECT MAG SENSOR", "ALL MAG SENSORS"]
            label = self.console.text("722", pick) or f"SUMP {pick}"
            return ["SELECT MAG SENSOR", f"s{pick}: {label}"[:COLS]]
        which = f"s {st['pick']}" if st["pick"] else "ALL"
        return [f"{self.SUMP_CONFIRM[st['kind']]}: {which}"[:COLS],
                "PRESS <ENTER>"]

    def _sump_picks(self):
        """ALL MAG SENSORS, then each Mag sensor CHANGE steps through."""
        return [None] + self.console.mag_sensors()

    def _sumpflow_enter(self):
        """"Press ENTER to confirm selection", then "Press ENTER to begin
        test": the second ENTER runs it."""
        st = self.sumpflow
        if st["stage"] == "select":
            picks = self._sump_picks()
            st["pick"] = picks[st["idx"] % len(picks)]
            st["stage"] = "confirm"
            self._render()
            return
        self.sumpflow = None
        devices = ([st["pick"]] if st["pick"]
                   else self.console.mag_sensors() or [self.device])
        self._flash(self._sump_run(st["kind"], devices))

    def _sump_run(self, kind, devices):
        """Run 099, 09A or 09B on each sensor, and draw the first one's
        answer the way p.24-4 draws it: `s 1:FILL SUMP` over PRESS <STEP> TO
        CONTINUE. A test is a test now -- it can abort the moment it starts
        or already be running -- so the screen says what the sensor IS
        doing, not what it was asked. FIDELITY U1b.
        """
        what = {"start": "sump_start", "height": "sump_height",
                "stop": "sump_stop"}[kind]
        ran = []
        for device in devices:
            before = self.console.sumps.status(device)
            if kind != "start" and before not in ("02", "03"):
                continue
            ran.append((device, self.console.control_device(what, device)))
        self.console.save()
        self.log(f"-- mag sump {kind} on {len(ran)} sensor(s)")
        if not ran:
            # Chapter 24 draws no screen for stopping, or for measuring, a
            # test that is not running; p.21-1 draws this one for the same
            # key on the tank's test. UNKNOWNS A53.
            return "LEAK TEST NOT ACTIVE" + chr(10) + CONT_FUNCTION
        device, state = ran[0]
        said = {"02": ":FILL SUMP", "03": ":MEASURING HEIGHT",
                "01": ": TEST ABORTED", "04": ": TEST PASSED"}.get(
                    state, ": TEST ABORTED")
        self.confirm = [f"s {device}{said}"[:COLS], CONT_STEP]
        return None

    def _test_devices(self, kind):
        """Which devices the SELECT ALL/SINGLE step is pointing at."""
        picked = self._picked_line(kind)
        if picked:
            return [picked]
        scope = self.sel["scope" if kind == "tank" else "line_scope"]
        if scope.startswith("SINGLE"):
            return [self.device]
        if kind == "tank":
            return sorted(self.console.tank_level) or [self.device]
        # ALL LINES is the lines the console HAS, which is the same rule
        # `programmed_lines` states for every other list of them: a line
        # exists once its position is switched on at LINE CONFIG or it has
        # been given a label, and a card in the cage is wires. This asked
        # the CARD how many positions it could serve, so ALL LINES on a
        # two-line site started tests on six -- four of them on pipe nobody
        # has told the console about, which the console then reports on.
        mine = [n for k, n, _label in self.console.programmed_lines()
                if k == kind]
        return mine or [self.device]

    def _picked_line(self, kind):
        """The line a PLLD or WPLLD SELECT LINE screen was left on, or None
        for ALL LINES. The selection is one of the console's own lines
        (`line_pick`), so a line nobody has programmed cannot be picked."""
        if kind not in ("plld", "wplld"):
            return None
        said = self.sel.get("line_scope")
        for k, n, _label in self.console.programmed_lines():
            if k == kind and said == line_pick(kind, n):
                return n
        return None

    def _rate_key(self, kind, step):
        """The engine's name for the rate the panel is showing."""
        label = self.sel["rate" if kind == "tank" else "line_rate"]
        for choice in step.get("choices") or []:
            if isinstance(choice, list) and choice[0] == label:
                return choice[1]
        return {"3.0 GPH": "gross", "0.2 GPH": "periodic",
                "0.1 GPH": "annual"}.get(label, "periodic")

    def _test_screen(self, kind, device, rate_key):
        """The two lines a test walk leaves on the glass for one device.

        576013-610 Rev AC p.11-4 draws both ends of a LINE test and both are
        the device's own status: "Press ENTER to begin the test. The system
        displays the message:" over `Q #: RUNNING PUMP` / `PRESS <STEP> TO
        CONTINUE`, and the stop walk over `Q #: TEST ABORTED` / the same.
        The console reports the DEVICE, not a tally of devices, and the
        `sump` branch below has drawn its screen this way all along -- which
        makes the counts the odd ones out rather than the shape.

        The tank half used to be an inference. Chapters 20 and 21 draw
        their confirmations as screens a text extraction interleaves, so this
        was built from strings the manual draws elsewhere. Read off the
        pages' own character grids since, both chapters draw screens of
        their own, and `_tank_started` and `_run_action`'s stop now draw
        them (FIDELITY U9 and U11). What is left here for a tank is the
        screen for one that REFUSED to start: chapter 9's `T #: (Product
        Name)` over `leaks.status_line`, the console's own words for why.

        See FIDELITY U5.
        """
        if kind == "tank":
            label = self.console.text("602", device) or ""
            return [f"T {device}:{label}"[:COLS],
                    self.console.leaks.status_line("tank", device,
                                                   rate_key)[:COLS]]
        from .pressure import Lines
        line = self.console.lines.line(kind, device)
        # the KIND's own word: 576013-610 Rev AC p.11-4 draws `Q #: RUNNING
        # PUMP` for PLLD and p.12-4 draws `W #: TEST PENDING` for WPLLD,
        # because Figure 20's status list has no RUNNING PUMP on it
        return [f"{Lines.code(kind)} {device}: {line.shown_status()}"[:COLS],
                CONT_STEP]

    def _run_action(self, action):
        verb, kind = action.split(":")
        if verb == "close" and MODES[self.mode] == "NORMAL":
            # CLOSE CURRENT SHIFT, 576013-610 Rev AC p.8-2: "A shift
            # inventory report is printed and the next shift automatically
            # begins when you manually close a shift. Also, the current
            # shift's 'Shift Ending Inv' and the next shift's 'Shift
            # Starting Inv' info will be updated with the current inventory
            # data." Last-Shift Inventory's shift: this closed BIR's, printed
            # BIR's report, and was refused on a console without the key.
            # FIDELITY O22.
            if not self.console.shifts.close_now():
                # "This command can only be invoked once an hour". What the
                # glass shows when it is refused is on no page, so it shows
                # nothing. UNKNOWNS A67.
                self.log("-- shift close refused: once an hour")
                return None
            self.paper_out(printer.inventory(self.console))
            self.log("-- shift closed")
            return None
        if verb == "close":
            # "Manual Shift lets you close the shift and generate a Shift
            # Reconciliation Report."
            if not self.console.licensed("bir"):
                # A console without the key does not offer the mode at all
                # -- `_mode_offered` is that gate -- so there is no screen
                # for it to draw and `BIR NOT INSTALLED` was this project's.
                self.log("-- close refused: no BIR key")
                return None
            rows = self.console.bir.close(kind)
            self.paper_out(printer.reconcile(self.console, kind=kind,
                                             previous=True))
            # The count is a fact about what the simulator did, so it goes
            # in the bench log; `{kind} CLOSED / {n} TANK(S)` was a screen
            # no page draws. See FIDELITY U5.
            self.log(f"-- {kind} closed on {len(rows)} tank(s)")
            return None
        steps = self.steps()
        rate_step = next((s for s in steps if s.get("sel", "").endswith("rate")),
                         {})
        if verb == "sump":
            # 576013-610 Rev AC p.24-4 and p.24-6: ENTER on each of the three
            # opens SELECT MAG SENSOR, and the sensor is chosen before
            # anything runs. `_sumpflow_enter` takes it from there.
            self.sumpflow = {"kind": kind, "stage": "select", "idx": 0,
                             "pick": None}
            return None
        if verb == "stop":
            devices = self._test_devices(kind)
            done = [self.console.leaks.stop(kind, d) for d in devices]
            stopped = [d for d, said in zip(devices, done)
                       if said != "NO TEST RUNNING"]
            self.log(f"-- {kind} test stopped on {len(stopped)} device(s)")
            if kind == "tank":
                # 576013-610 Rev AC chapter 21, read off its own character
                # grid. ALL TANKS, p.21-1: "Press ENTER to confirm that you
                # want to stop the leak test on all tanks. The system
                # confirms that the test has stopped:" `LEAK TEST NOT
                # ACTIVE` / `PRESS <FUNCTION> TO CONTINUE`. SINGLE TANK,
                # p.21-2: "The system stops the test on the selected tank
                # and displays the message:" `STOP LEAK TEST: TANK #` /
                # `LEAK TEST NOT ACTIVE`, and TANK/SENSOR goes on (`k_tank`).
                # A stop that WORKED drew the tank's result screen, which
                # for a test stopped early reads NO TEST DATA AVAILABLE --
                # the condition inverted. FIDELITY U9.
                if not str(self.sel.get("scope", "")).startswith("SINGLE"):
                    return "LEAK TEST NOT ACTIVE" + chr(10) + CONT_FUNCTION
                self.confirm = [f"STOP LEAK TEST: TANK {self.device}"[:COLS],
                                "LEAK TEST NOT ACTIVE"]
                return None
            if not stopped:
                # 576013-610 Rev AC p.21-1, and these are the manual's own
                # two lines: "If all active tests are stopped, the system
                # displays the message: LEAK TEST NOT ACTIVE / PRESS
                # <FUNCTION> TO CONTINUE."
                return "LEAK TEST NOT ACTIVE" + chr(10) + CONT_FUNCTION
            if kind == "vlld":
                # The VLLD chapter draws its own answer and it is not the
                # line's status. 576013-610 Rev AC p.13-5: "Press ENTER to
                # stop the leak test on all lines. The system displays the
                # message: `STOP LEAK TEST: ALL LINES` / `PRESS <ENTER>`"
                # -- the screen it was already on. Chapter 11's PLLD walk
                # answers `Q #: TEST ABORTED` and chapter 13's does not,
                # and they are different chapters about different cards.
                return None
            picked = self._picked_line(kind)
            if picked:
                # "Press ENTER to stop the PLLD test on the selected line.
                # The system stops the test and advances to the next line in
                # test", p.11-5, and p.12-5 for WPLLD: SELECT LINE is left on
                # the next line still testing, so STEP back to it and ENTER
                # stops that one. Which line the status screen between them
                # names is on no page; it is the one just stopped. UNKNOWNS
                # A70.
                mine = [n for k, n, _l in self.console.programmed_lines()
                        if k == kind]
                at = mine.index(picked)
                testing = [n for n in mine[at + 1:] + mine[:at]
                           if self.console.lines.line(kind, n).running()]
                if testing:
                    self.sel["line_scope"] = line_pick(kind, testing[0])
                    self.device = testing[0]
            self.confirm = self._test_screen(kind, stopped[0], None)
            return None
        rate_key = self._rate_key(kind, rate_step)
        if rate_key == "purge":
            # "Air Purge purges air from the VLLD Controller by performing six
            # consecutive VLLD Controller 3.0 gph selftests"
            for dev in self._test_devices(kind):
                self.console.leaks.air_purge(dev)
            # "Air Purge purges air from the VLLD Controller by performing
            # six consecutive VLLD Controller 3.0 gph selftests" -- which is
            # what it DOES, and no page draws a screen saying it is done.
            # See FIDELITY U5.
            self.log(f"-- air purge on {len(self._test_devices(kind))} line(s)")
            return None
        hours = float(self.sel["hours"]) if kind == "tank" else None
        manual = kind == "tank" and self.sel["stop_mode"].startswith("MANUAL")
        devices = self._test_devices(kind)
        started, said = [], {}
        for dev in devices:
            if not self.console.line_rate_allowed(kind, dev, rate_key):
                # ALL LINES offers a rate one of its lines allows, and
                # starts it on those: "you can not start those test types
                # manually" on a line whose schedule has them Disabled,
                # p.11-3. FIDELITY O24.
                said[dev] = "NOT ENABLED IN SETUP"
                continue
            said[dev] = self.console.leaks.start(kind, dev, rate_key, hours,
                                                 manual)
            if said[dev] == "TEST STARTED":
                started.append(dev)
        self.log(f"-- {rate_key} test started on {len(started)} {kind}(s)"
                 + ("" if len(started) == len(devices) else
                    "; " + ", ".join(f"{d}: {said[d]}" for d in devices
                                     if said[d] != "TEST STARTED")))
        # And how long the bench is about to sit here. This warning hung off
        # the results screen's ENTER, which was a shortcut no console has
        # and is gone (FIDELITY O15) -- so it now hangs off the walk the
        # manual routes a hand-started test through, which is where a
        # technician actually starts one. See FIDELITY U3.
        if started:
            self._say_how_long(kind, rate_key, said[started[0]])
        # ENTER always lands on the device's own status screen, whether a
        # test started or not, because on this walk the status word IS the
        # answer. 576013-610 Rev AC p.11-4 draws ENTER over `Q #: RUNNING
        # PUMP`, and 577013-344 Rev H p.22 lists what else that same line
        # can read -- DISPENSING, TEST 3.0, HANDLE ON, LINE LOCKOUT, DISABLE
        # ALARM, TEST PENDING, STP POWER OFF. Every one of those is a reason
        # a test did not start, said in the console's own words on the
        # console's own screen. There is no separate refusal message on this
        # walk and there does not need to be.
        #
        # This counted only "TEST STARTED" and threw the rest away, so ENTER
        # on a line that was dispensing, or already testing, or shut down,
        # changed nothing on the glass and printed nothing anywhere: a dead
        # key. A technician cannot be taught why a test will not run by a
        # console that will not say. The STOP half of this same function has
        # drawn the manual's own `LEAK TEST NOT ACTIVE` all along, which is
        # what makes this an oversight rather than a decision.
        #
        # FIDELITY U5's rule is that this project does not INVENT screens.
        # This invents none: the screen is the one the walk already draws,
        # shown when the manual shows it.
        #
        # Except on the VLLD walk, where chapter 13 draws no screen at
        # all. p.13-2: "Press ENTER to start the test. The system begins
        # the line leak, displays the message: `START LEAK TEST: ALL LINES`
        # / `PRESS <ENTER>` and prints a report that the test has started."
        # The message it displays is the one it was already displaying. The
        # single-line walk on the same page says the same thing in fewer
        # words -- "Press ENTER to start the line leak test. The system
        # prints a confirmation that it has started the test" -- and draws
        # no screen either.
        #
        # This answered `W 1: TEST COMPLETE`: the WPLLD letter on a VLLD
        # line, the word COMPLETE at the instant a test starts, and a
        # screen change the chapter does not draw. See CLOSED U34, and
        # UNKNOWNS A33 for the report, which every VLLD page says prints
        # and no page anywhere draws.
        if kind == "vlld":
            return None
        if kind == "tank" and started:
            return self._tank_started(started)
        self.confirm = self._test_screen(kind, (started or devices)[0],
                                         rate_key)
        return None

    def _tank_started(self, started):
        """What the glass says once an in-tank test has started: four
        answers, one per walk, 576013-610 Rev AC chapter 20 read off its own
        character grids. FIDELITY U11.

        * ALL TANKS, TIMED DURATION, p.20-2: ENTER draws `START IN-TANK LEAK
          TEST` / `PRESS <STEP> TO CONTINUE`, and "Press STEP to continue.
          The system confirms that the test has started:" `TEST CONTROL: ALL
          TANKS` / `LEAK TEST IN PROGRESS`.
        * ALL TANKS, MANUAL STOP, p.20-3: "Press ENTER. The system starts the
          test and prints a report indicating that the test has started.
          Press FUNCTION to exit." No screen of its own.
        * SINGLE TANK, TIMED DURATION, p.20-5: "The system confirms that the
          test has started:" `TEST CONTROL: TANK #` / `TIMED DURATION`.
        * SINGLE TANK, MANUAL STOP, p.20-5: "The system will automatically
          advance to the next tank, displaying the TEST CONTROL: TANK (#)
          message."

        This answered every one of them with the tank's status line, `T 1:
        REGULAR UNLEADED` / `TEST ACTIVE  2.0 HRS`, and STEP then walked to
        the top of the function. A tank that REFUSED still gets that screen,
        because its status line is the reason in the console's own words.
        """
        single = str(self.sel.get("scope", "")).startswith("SINGLE")
        manual = str(self.sel.get("stop_mode", "")).startswith("MANUAL")
        if not single:
            if manual:
                return None
            first = ["START IN-TANK LEAK TEST", CONT_STEP]
            self.confirm = list(first)
            self._confirm_next = (first, ["TEST CONTROL: ALL TANKS",
                                          "LEAK TEST IN PROGRESS"])
            return None
        if not manual:
            self.confirm = [f"TEST CONTROL: TANK {started[0]}"[:COLS],
                            "TIMED DURATION"]
            return None
        devices = self._devices() or [self.device]
        if self.device in devices:
            self.device = devices[(devices.index(self.device) + 1)
                                  % len(devices)]
        self.step = [i for i, s in enumerate(self.steps())
                     if s.get("sel") == "stop_mode"][0]
        return None

    def _enter_map(self):
        """MODIFY TANK/METER MAP: the row the five cells have been dialled to.

        "Press ENTER to confirm your change", 576013-623 Rev AN p.17-5, and
        the change goes to `Console.map_meter` -- 7B1's own store, which is
        where the serial command writes. A panel that had put the five
        numbers under `S7B100` in `values` would have stored a row nothing
        reads: BIR takes its tank per meter off `meter_map` and nowhere
        else.

        Two of the tank column's three meanings are this screen's own:
        "99: Tank with no probe" is the -1 the command takes, and
        "00: Remove meter from tank/meter map" is its 0.
        """
        if not self.editing:
            self.log("-- nothing entered: press CHANGE first")
            return
        cells = self.buf.split()
        if len(cells) != 5 or not all(c.isdigit() or set(c) == {"X"}
                                      for c in cells):
            # typed rather than dialled, and not five numbers: this unpacked
            # it with int() and raised out of the key handler
            self.log("-- meter map: every field wants a number")
            return
        if any(set(cell) == {"X"} for cell in cells):
            # a row with a cell nobody has dialled is not a row. No page
            # draws a refusal for it, so the screen stays where it is with
            # the entry still open and the reason goes to the log.
            self.log("-- meter map: every field wants a value")
            return
        self.editing, self.buf = False, ""
        bus, slot, fp, meter, tank = (int(c) for c in cells)
        number = -1 if tank == 99 else tank
        fields = [str(bus), str(slot), str(fp), str(meter), str(number)]
        bad = wirelists.map_errors(self.console, fields)
        if bad:
            # The wire answers this with the row and `??` in the field it
            # could not take. The panel cannot draw two lines of that on a
            # two line display and no page says it tries, so the rule is
            # `_refuse`'s: the console says nothing and keeps its screen.
            self.log("-- meter map refused: " + " ".join(sorted(
                ("bus", "slot", "position", "meter", "tank")[i]
                for i in bad)))
            self._render()
            return
        self.console.map_meter(bus, slot, fp, meter, number)
        self.log(f"-- meter map: FP {fp} M {meter} "
                 + ("unmapped" if not number else f"on tank {number}"))
        # The acknowledgement is the line the screen was already showing
        # over PRESS <STEP> TO CONTINUE, which is what every other typed
        # value in Setup Mode does. See FIDELITY O9.
        self.confirm = [self._map_row(cells), CONT_STEP]
        self._refresh_site()
        self._render()

    def _enter_offset(self, step):
        """INDIVIDUAL METER OFFSET: this meter's own calibration percent.

        "Enter the desired offset in increments of +/-0.01% with a maximum
        range of +/-9.99% and Press ENTER to confirm your change" -- and it
        goes to `Console.set_meter_offset`, which is where `S7B400` writes.
        The step used to carry a `list` field and so went down the ordinary
        S-code path, which stored eleven characters under `S7B400` in
        `values`: BIR reads `meter_offsets` and nothing has ever read that.
        """
        key = step.get("meter")
        if not self.editing or key is None:
            self.log("-- nothing entered: press CHANGE first")
            return
        typed = self.buf.strip()
        self.editing, self.buf = False, ""
        try:
            pct = float(typed)
        except ValueError:
            self.log(f"-- meter offset refused: {typed!r} is not a number")
            self._render()
            return
        if abs(pct) > 9.99:
            # the page's own range, and `_offset_ok` is the wire's half of
            # the same rule
            self.log(f"-- meter offset refused: {pct:+.2f}% is over 9.99%")
            self._render()
            return
        tank = int((self.console.meter_map.get(key) or {}).get("tank") or 0)
        self.console.set_meter_offset(key.fp, key.meter, max(tank, 0), pct)
        self.log(f"-- meter {key.meter} at FP {key.fp}: offset {pct:+.2f}%")
        self.confirm = [screens.offset_row_text(
            screens.offset_cells(self.console, key)), CONT_STEP]
        self._render()

    def _enter_console(self, step):
        """Store one of the settings the console keeps for itself.

        ENTER without CHANGE stores nothing. This had no such guard, so
        a bare ENTER -- no CHANGE, nothing typed -- wrote `self.buf`, which
        is the empty string, and ERASED the setting. On thirteen screens:

            AVG SALES SUN:         0   ->   AVG SALES SUN:
            MIN: +1.00                 ->   MIN:
            TIME: 11:59 PM             ->   TIME:

        all seven Fuel Management days and six EVR/ISD screens, including
        the nozzle air-to-liquid range and the analysis start time. And it
        did it under a `PRESS <STEP> TO CONTINUE` that reads like success,
        so the console told you it had taken the entry while destroying
        one. A technician pressing ENTER to see what a screen does loses
        the site's programming.

        The sibling path for ordinary S-code fields has had the guard all
        along -- `if not self.editing or not code: return` -- and this is
        the same rule: 576013-623 Rev AN's procedures all read "press
        CHANGE, enter the value, then press ENTER", so ENTER is the second
        half of a CHANGE and does nothing on its own.

        CHANGE sets `editing`, so a DELIBERATE empty entry still reaches
        the branches that treat it as "remove this" -- which is what the
        serial number screen wants, and is a different thing from never
        having pressed CHANGE at all. See K1.
        """
        from .console import FIELDS
        f = FIELDS.get(step["console"], {})
        if (not self.editing and f.get("kind") == "setting"
                and f.get("choices")):
            # A pick-list has nothing to guard. CHANGE walks the list
            # and stores as it goes, so `editing` is never set and ENTER
            # fell into the guard below and drew nothing -- on a screen
            # 576013-623 Rev AN p.6-2 says is confirmed: "Press ENTER to
            # confirm your choice. The system displays the message:
            # `PARITY: XXX` / `PRESS <STEP> TO CONTINUE`." Nothing can be
            # erased here, which is what the guard exists to prevent.
            device = self.device if f.get("scope") == "device" else 0
            shown = self.console.setting(f["which"], device,
                                         f.get("default", ""))
            prompt = f.get("prompt") or ""
            self.confirm = [f"{prompt} {shown}".strip()[:COLS], CONT_STEP]
            self._render()
            return
        if not self.editing:
            self.log("-- nothing entered: press CHANGE first")
            return
        text, kind = self.buf.strip(), f.get("kind")
        self.editing, self.buf = False, ""
        console = self.console
        if kind == "chartcode":
            digits = "".join(c for c in text if c.isdigit())
            if len(digits) != 6:
                self._refuse("six digits")
                return
            on = console.set_chart_code(digits)
            self.chart_open = on         # whoever set it knows it
            self.confirm = ["CODE: ******" if on else "CODE: 000000",
                            CONT_STEP]
        elif kind == "consolefloat":
            try:
                getattr(console, f["which"])[self.device] = float(text)
            except ValueError:
                self._refuse("numbers only")
                return
            console.record_chart_change(self.device)
            console.save()
            self.confirm = [f"{step['text'].upper()}: {text}"[:COLS],
                            CONT_STEP]
        elif kind == "pmc_threshold":
            try:
                value = float(text.replace("IWC", "").strip())
            except ValueError:
                self._refuse("numbers only")
                return
            if not console.set_pmc_threshold(f["which"], value):
                # "-8 < off < on < +3"
                self._refuse("-8 < off < on < +3")
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
                # "This feature lets you enter a delay (of from 5 to 999
                # hours) ... Enter 000 to disable this feature": a range
                # with a hole under it, where zero is not the bottom of the
                # range but the way to turn the thing off. 576013-623 Rev
                # AN p.51, FIDELITY N3.
                spare = f.get("zero_disables") and text.strip("0") == ""
                if not text.isdigit() or not (spare or number[0] <= int(text)
                                              <= number[1]):
                    self._refuse(f"{number[0]} to {number[1]}"
                                 + (" or 0" if f.get("zero_disables")
                                    else ""))
                    return
                text = text.rjust(int(f.get("width") or 0), "0")
            elif allowed and text.upper() not in allowed:
                self._refuse("one of " + "/".join(allowed))
                return
            elif f.get("maxlen") and len(text) > int(f["maxlen"]):
                self._refuse(f"at most {f['maxlen']} characters")
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
            self._refuse("numbers only")
            return
        self._point[which] = value
        if which == "height":
            self.confirm = [f"HEIGHT : {value:g}"[:COLS],
                            CONT_STEP]
            self._render()
            return
        height = self._point.get("height")
        if height is None:
            self.log("-- a chart point wants its height first")
            return
        self.console.add_chart_point(self.device, height, value)
        self._point.clear()
        # 576013-623 Rev AN p.7-8: `88.32 INCH VOL : 9200` over
        # `PRESS <STEP> TO CONTINUE`, the same shape as the HEIGHT
        # confirmation one step earlier. `OF 50 POINTS` was in no manual,
        # no register and no citation -- it existed at this one line.
        # FIDELITY U50.
        self.confirm = [f"{height:g} INCH VOL : {value:g}"[:COLS],
                        CONT_STEP]
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
                self.log("-- tank profile unchanged")
                return
            self.armed = False
            erased = self.console.set_tank_profile(tank, wanted)
            self._refresh_site()
            self.refresh_traffic()
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
            self.log("-- tank profile unchanged")
            return
        if self.console.profile_erasable(tank, chosen):
            self._profile_pending = chosen
            self.armed = False
            self._render()
            return
        self.console.set_tank_profile(tank, chosen)
        self._refresh_site()
        self.refresh_traffic()
        self.confirm = [f"TANK PROFILE: {_C.PROFILE_NAME[chosen]}"[:COLS],
                        CONT_STEP]
        self._render()

    # "This process may take a minute or so." A minute is a long time to sit
    # in front of a bench, so the simulator takes seconds; what matters is
    # that the console is BUSY and does not answer the keypad while it is.
    ARCHIVE_SECONDS = 3.0

    # 576013-623 Rev AN, beside the restore procedure: "If you are restoring
    # after a reboot (switching the console Off and then back On), the system
    # will wait 5 minutes before processing your request to restore archived
    # setup data. This delay is to allow all hardware to initialize."
    # WHETHER the console is inside those five minutes is the console's own
    # question and it counts the real 300 seconds down in `restore_hold()`;
    # what the bench sits through in their place is this, for the reason
    # ARCHIVE_SECONDS is three and not sixty.
    REBOOT_HOLD_SECONDS = 5.0

    def _run_archive(self, what):
        """Save, restore or clear. The console goes away while it works."""
        self.log(f"-- archive {what}: working")
        if what in ("save", "restore"):
            # 576013-637 Rev M p.4 step 8 and p.15 step 6: "Press the STEP
            # key and the printer prints" the START TIME record. A clear
            # prints nothing, because that manual draws no record for one.
            self.paper_out(printer.archive_record(self.console, what,
                                                  "START TIME"))
            self.log(f"-- PRINT: archive {what} START TIME")
        seconds = self.ARCHIVE_SECONDS
        if what == "restore" and self.console.restore_hold() > 0:
            # The request is taken and then sat on -- "the system will wait 5
            # minutes before processing your request to restore archived
            # setup data" -- so the console is away for the hold as well as
            # for the work.
            seconds = max(seconds, self.REBOOT_HOLD_SECONDS)
            self.log("-- archive restore: holding for hardware to initialize")
        if seconds <= 0:
            self._finish_archive(what)
            return
        # 576013-637 Rev M p.4 step 9: while a SAVE runs the panel reads
        # "ARCHIVE UTILITY / SAVE SETUP DATA: BUSY". The same manual walks a
        # restore without any BUSY screen -- it goes straight from the
        # confirmation to the printer's START TIME record -- so only the save
        # gets one, and the others just stop answering.
        self.busy_line = "SAVE SETUP DATA: BUSY" if what == "save" else None
        self.busy_until = time.time() + seconds
        self._render()
        self.after(int(seconds * 1000), lambda: self._finish_archive(what))

    def _finish_archive(self, what):
        console = self.console
        self.busy_until = 0.0
        self.busy_line = None
        if what == "save":
            n = console.archive_save()
            if n >= 0:
                # 576013-637 Rev M p.5 step 10: "After this task is
                # completed, the printer prints" the END TIME record, with
                # the size of what went into the chip under it. Nothing is
                # printed for a save that failed: the manuals draw no
                # failure record for the Archive Utility at all.
                self.paper_out(printer.archive_record(
                    console, "save", "END TIME", console.archive_bytes()))
                self.log("-- PRINT: archive save END TIME")
        elif what == "restore":
            n = console.archive_restore()
            if n >= 0:
                # 576013-637 Rev M p.16: "When the restoring process is
                # complete the printer prints out" the END TIME record, and
                # "this information is followed by a complete printout of the
                # system setup" -- in that order, the record first.
                self.paper_out(printer.archive_record(console, "restore",
                                                      "END TIME"))
                self.log("-- PRINT: archive restore END TIME")
                # "The system also prints a complete listing of all restored
                # setup data."
                self.paper_out(printer.setup(console))
                self.log("-- PRINT: restored setup data")
        else:
            n = console.archive_clear()
        self.log(f"-- archive {what}: {n} value(s)" if n >= 0
                 else f"-- archive {what}: nothing")
        # The comment below was already right and the line under it was
        # not. 576013-637 p.8 step 11 draws the display after the save
        # completes, and it is the screen the function started on:
        # `ARCHIVE UTILITY` over `PRESS <STEP> TO CONTINUE`. This flashed a
        # COUNT there instead -- `{n} VALUE(S) SAVED`, and `SAVE FAILED`,
        # `NO ARCHIVE SAVED` and `CLEAR FAILED` for the ways it can go
        # wrong, none of which any page draws. The count is in the bench log
        # now, which is where a number about what the simulator did belongs.
        # See FIDELITY U5.
        #
        # "When the save is completed, the system returns the original
        # message: ARCHIVE UTILITY / PRESS <STEP> TO CONTINUE."
        self.step = HEADER
        self.armed = self.sure = False
        self._refresh_site()
        self.refresh_traffic()
        self._flash("ARCHIVE UTILITY" + chr(10) + "PRESS <STEP> TO CONTINUE")

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
        if (MODES[self.mode] == "NORMAL" and self._entered and fn
                and fn["function"] == "TEST OUTPUT RELAYS"
                and self.step >= 0):
            # "This key also activates and deactivates output relays when
            # using the Output Relay Test function."
            #
            # And not one screen earlier than that. 576013-610 Rev AC
            # p.2-5 walks the whole function in four rows -- FUNCTION to
            # `TEST OUTPUT RELAYS`, STEP to `ENTER RELAY NUMBER`, then
            # ALARM/TEST -- so the key is reached AFTER a STEP has left the
            # function screen. This branch tested the mode, `_entered` and
            # the function's name and not the step, so ALARM/TEST on the
            # function's own header -- a screen whose whole second line is
            # `PRESS <STEP> TO CONTINUE` -- closed relay 1. A relay is a
            # physical contact that runs a pump, a horn or a shutdown, and
            # teaching that the key is safe to press anywhere in the
            # function is the kind of training this simulator exists not to
            # give. `HEADER` is -1 and every real step is 0 or more.
            n = self.device
            self.console.relays[n] = not self.console.relays.get(n)
            state = "ON" if self.console.relays[n] else "OFF"
            self.log(f"-- relay {n} switched {state} by ALARM/TEST")
            # 576013-610 Rev AC p.23 draws this screen, and both of this
            # console's lines were its own: `RELAY 1 ON` over `PRESS ANY KEY
            # FOR SETUP` where the page has `R 1: (Device Name)` over
            # `ON - PRESS ANY KEY`. The device line is the same `R n:LABEL`
            # every other relay screen on the console draws, and the second
            # line is the manual's own words for what to do next.
            #
            # The page draws ON and does not draw OFF: its flow is press
            # ALARM/TEST, the relay energizes, press any key and you are
            # back at ENTER RELAY NUMBER. This console lets ALARM/TEST
            # toggle, so it needs a word for the other half and uses the
            # same line with OFF in it. See FIDELITY U5.
            # `RELAY n` when the site has not labelled it, which is what
            # every other caller in this package falls back to -- `relays`,
            # `printer` and `console` all do. This was a bare `""`, so
            # `R 2: RELAY 2` became `R 2:` on the very next keystroke and
            # the console contradicted itself one key apart. The manual
            # draws `R 1: (Device Name)`, 576013-610 Rev AC p.22-1.
            # FIDELITY U8.
            label = self.console.outputs.label(n)
            self._flash(f"R {n}: {label}"[:COLS]
                        + chr(10) + f"{state} - PRESS ANY KEY")
            # And the screen's second line is an instruction the console
            # has to honour: "Press any key to printout Relay Setup"
            # (p.2-5, the same four-row walk). The line was drawn and
            # nothing came off the roll for any key -- only PRINT printed,
            # and it printed the function's own report rather than Relay
            # Setup. `printer.relays` IS that report, headed OUTPUT RELAY
            # SETUP, and it existed all along with nothing on this walk
            # calling it. `_keyed` runs at the top of every key handler, so
            # the NEXT key -- whichever it is -- is where this is cashed.
            self.relay_setup_due = True
            return
        # "If your system has a printer, it will print an alarm or warning
        # report when this button is pressed."
        self.paper_out(printer.alarms(self.console))
        shown, _still, cleared = self.console.acknowledge(
            self.console.mt_logged_in())
        # The console does not report what the keypress did. Four
        # messages stood here and all four were this project's -- a count of
        # what was cleared, a count of what is still standing, the word
        # SILENCED, the word ACKNOWLEDGED. 576013-610 Rev AC says what
        # happens instead, twice and unambiguously:
        #
        #   p.3-2  "ALARM/TEST silences the alarm. It does not clear the
        #           alarm message from the display or disable the alarm."
        #   p.29-2 "Warning and Alarm Messages display until you correct the
        #           cause of the problem. After you correct the cause, you
        #           must press the ALARM/TEST button to acknowledge the alarm
        #           and clear the display. The system will then display the
        #           ALL FUNCTIONS NORMAL message."
        #
        # So there are two outcomes and the resting screen already draws
        # both of them: an alarm whose cause still stands keeps its own
        # message on the glass, and a console with nothing standing reads
        # ALL FUNCTIONS NORMAL. Flashing anything here overwrote the very
        # message the manual says must stay. Say nothing, and `_lines` is
        # right either way.
        #
        # The one thing that IS drawn is the Maintenance Tracker's refusal,
        # because that is a screen 576013-610 chapter 33 draws: a key has to
        # be presented before a protected alarm can be acknowledged at all.
        # Its count line went with the others.
        #
        # See FIDELITY U5.
        if (shown and self.console.has("mt")
                and not self.console.mt_logged_in() and shown != cleared):
            self._flash("INSERT KEY IN PORT" + chr(10) + "PRESS <ENTER>")

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

    def _set_lever(self):
        """Push the feed roller release down, or back up.

        "Swing up the printer cover and push the paper feed release lever
        down" is how a roll is changed, and a lever left down is PRINTER
        ERROR: "Printer feed roller release is open. Push the release lever
        ... to the up position." The printer is inoperative while it is
        down. BENCH.md P6.
        """
        self.console.printer_lever_open = bool(self.lever_down.get())
        # The console's own channel for this is the ALARM -- `PRINTER
        # ERROR` is `alarmlabels.json`'s own words for 01/02, read off the
        # manual -- so the light comes on and the display carries it. The
        # flash was a second, invented phrasing of a condition the console
        # already words correctly. Same for the paper below, whose docstring
        # says so already. See FIDELITY U5.
        self.log("-- printer: feed release lever "
                 + ("DOWN (PRINTER ERROR)" if self.console.printer_lever_open
                    else "up"))
        self._render()

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
        if self.console.printer_lever_open:
            # "or the printer is inoperative": with the feed roller
            # released there is nothing driving the paper
            self.log("-- nothing printed: feed release lever is down")
            return
        # A real console signs off every report, which is what tells you
        # where one stops on a continuous roll.
        folded = printer.fit(lines) + ["", printer.END_MARK]
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

    def _chosen_day(self):
        """The day typed on SELECT DAY, or None for the one in progress.

        It only means anything on the daily RECONCILIATION report: the shift
        and periodic screens are toggles, and so is the variance chapters'
        daily one. See FIDELITY Q1.
        """
        kind, _previous = self._recon_period()
        fn = self.cur_function() or {}
        if kind != "daily" or fn.get("function") not in (
                "DISPLAY AND PRINT", "MANUAL ADJUSTMENTS"):
            # MANUAL ADJUSTMENTS types its date too: p.28-20, "To select a
            # different date, press CHANGE. Enter the desired closing date
            # for the adjustment, then press ENTER." FIDELITY Q1.
            return None
        return self.sel.get("day")

    def _recon_day(self):
        """The day a SELECT DAY screen is naming, as a moment.

        The typed day if one was typed; otherwise the day the toggle is on --
        the one in progress, or the day the last closed daily row belongs to,
        falling back to the calendar day before this one on a console that
        has never closed a day.
        """
        chosen = self._chosen_day()
        if chosen is not None:
            return chosen
        now = time.mktime(self.console.now())
        _kind, previous = self._recon_period()
        if not previous:
            return now
        row = self.console.bir.last(self.device, "daily")
        return row["closed"] if row else now - 86400.0

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
        if "%D" in said:
            # 576013-610 Rev AC p.28-3 draws this screen as `(Day) OPEN:
            # (Date)`, where its two siblings on pp.28-5 and 28-6 are drawn
            # literally -- `CUR SHFT OPEN` and `CUR PERI OPEN`. So the daily
            # one names something the console substitutes, and `(Day)` is a
            # WEEKDAY.
            #
            # The token's only attested resolution anywhere in the corpus is
            # 576013-623 Rev AN p.14-2, which draws `START DAY: (Day)` with
            # `START DAY: MON` four boxes above it ON THE SAME PAGE, and
            # `LOCKOUT #1: (Day) (START)` below. The Spanish and German
            # setup manuals draw the same pair -- `DÍA INICI: LUN` beside
            # `DÍA INICI: (Día)`, `START TAG: MON` beside `START TAG: (Tag)`
            # -- which also shows the substituted value is localised, so it
            # is a value and not a word of the label.
            #
            # And the Spanish Operator's Manual argues it from the other
            # side: the same translator RESOLVED and REORDERED both literal
            # siblings into Spanish word order (`CUR PERI OPEN` became `PER
            # ACT INIC`, `CUR SHFT OPEN` became `ABIER TUR ACT`) and left
            # `(Día) ABR: (Fecha)` leading and untouched -- which is what
            # you do with a runtime token like `(Fecha)`, not with the words
            # "current day".
            #
            # This used to write `DAY OPEN`, which is attested nowhere; so
            # is `CUR DAY OPEN`, the rival reading. See UNKNOWNS A46.
            # DAY_NAMES runs Sunday first and `tm_wday` runs Monday first,
            # which is the off-by-one this has to carry. THR, not THU: see
            # its own note in `console`.
            wday = time.localtime(self._recon_day()).tm_wday
            said = said.replace("%D", DAY_NAMES[(wday + 1) % 7])
        if step.get("adjust_prefix") and "%S" in said:
            # "(Selected) SHFT ADJ VOL" and "(Date) ADJ VOL": the manual's
            # two placeholders are the shift you chose and the day you chose
            # (576013-610 Rev AC p.28-19 and p.28-20; function codes 79B and
            # 79C answer "CURRENT SHFT ADJ" and "MAR 26  ADJ VOL")
            if word == "DAILY":
                # the day chosen, which was today's date whatever was chosen
                fill = clock_date(time.localtime(self._recon_day()),
                                  year=False)
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
            kind, previous = self._recon_period()
            # the row the adjustment will land in, which is the one the panel
            # names -- a closed shift or a typed closing date as well as the
            # running one. FIDELITY Q1.
            daily = kind == "daily"
            row = self.console.bir.row(self.device, kind,
                                       previous and not daily,
                                       self._recon_day() if daily else None)
            held = row["adjust"] if row else 0.0
            return [head[:COLS], f"{word}: {held:.0f}"[:COLS]]
        if step.get("action"):
            return [head[:COLS],
                    f"{word}: {'YES' if self.armed else 'NO'}"[:COLS]]
        if step.get("sel"):
            value = str(self.sel.get(step["sel"], ""))
            if word.startswith("SELECT DAY"):
                # "SELECT DAY: (Date)", and p.28-20 draws the same screen as
                # `SELECT DAY: (Current Date)`. It used to read the selected
                # ROW's closing date, so a console with no previous day put a
                # status message on it and the display cut it mid-word:
                # `SELECT DAY: NO DATA AVAI`. A day selector names a day
                # whether or not there is a row for it; whether there IS one
                # is what the report says. See FIDELITY Q1.
                value = clock_date(time.localtime(self._recon_day()))
                if self.editing and self._typed_day(step):
                    value = self._edit_text()
            return [head[:COLS], f"{word}: {value}"[:COLS]]
        live = ""
        if step.get("live"):
            live = self.console.recon_reading(step["live"][6:], self.device,
                                              day=self._chosen_day())
        # This clips, and a clipped number is a wrong number.
        # `CALCULATED INVNTRY: 14000 GALS` is thirty characters against a
        # twenty-four column display, so the glass reads `CALCULATED
        # INVNTRY: 1400` on a tank holding fourteen thousand -- silently,
        # and only when the figure has five digits, so the same screen is
        # right for the next tank along.
        #
        # 576013-610 Rev AC p.28-2 prints every one of these on TWO lines,
        # the label and then the value, precisely because they do not fit on
        # one; what the PANEL does with them is on no page here. Abbreviating
        # the label to make room was tried and reverted: it draws text no
        # manual draws, which `tests/test_citations.py` refuses and FIDELITY
        # U5 is the rule for. Left clipping, and open as FIDELITY O14, until
        # a real console or a page says what the short form is.
        return [head[:COLS], f"{word}: {live.strip()}"[:COLS]]

    def _recon_report(self, name, step):
        """PRINT in Reconciliation Mode, which is the report you are standing
        in, for every product or for the one selected."""
        console = self.console
        self._recon_sync()
        kind, previous = self._recon_period()
        device = (self.device if (step or {}).get("print_scope") == "device"
                  else None)
        tanks = [device] if device else None
        fn = self.cur_function()
        which = (fn or {}).get("print")
        report = {"reconcile": printer.reconcile,
                  "delivery_variance": printer.delivery_variance,
                  "book_variance": printer.book_variance,
                  "variance_analysis": printer.variance_analysis}.get(which)
        if report is None:
            return name, printer.status(console)
        if report is printer.reconcile:
            return name, report(console, tanks, kind, previous,
                                self._chosen_day())
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

        if self.maint_report:
            # The white key's screen says `PRESS <PRINT>` and this is what
            # answers it. 576013-610 Rev AC Figure 32-2 prints function
            # 119's own report, which the wire has served all along as
            # `MAINTENANCE HISTORY` -- the paper's title, where
            # `MAINTENANCE REPORT` is the screen's. See W13 and R3.
            return "MAINTENANCE HISTORY", printer.maintenance(console)

        if mode == "SETUP":
            if self.step == MODE_SCREEN:
                return "SETUP DATA REPORT", printer.setup(console)
            if (step or {}).get("report") == "metermap":
                # "Press PRINT to output a report of the current meter map
                # for reference before proceeding", 576013-623 Rev AN
                # p.17-5, said of this screen and of no other -- so it is
                # the one place in Setup where PRINT is not the function's
                # own programming. The paper's own 24-column layout, not
                # `I7B100`'s 34: see `screens.map_lines`.
                return ("METER MAP", screens.map_lines(console))
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
        # a screen that prints something of its own says so: IN-TANK
        # INVENTORY's DELIVERY prints the increase, not the inventory
        kind = (step or {}).get("print_kind") or (fn or {}).get("print")
        device = (self.device if (step or {}).get("print_scope") == "device"
                  else None)
        return name, self._operating_report(kind, device, step)

    def _operating_report(self, kind, device=None, step=None):
        """One of the reports the operating mode table names."""
        console = self.console
        tanks = [device] if device else None
        step = step or {}
        if step.get("history"):
            # "press STEP to display Q #: PRESS PRINT FOR HISTORY"
            if step["history"] == "sump":
                return printer.sump_history(console, device)
            return printer.leak_history(console, step["history"], device)
        # "PRINT - Last load report for selected tank" on the load screen,
        # "Last 40 load reports" on the ones above it
        index = self.load if step.get("load") == "number" else None
        if kind is None:
            return printer.status(console)
        if kind.startswith("sensors:"):
            return printer.sensors(console, kind.split(":")[1], device)
        if kind == "fuel":
            # "All information displayed is for products, not tanks": a
            # product's screen prints the tanks carrying the product on it,
            # with the seven average-sales rows p.7-3 draws. FIDELITY O4.
            #
            # The first two screens are not a product's. p.7-1, on the
            # function's own screen: "Press PRINT to print a Fuel Management
            # report for all products"; and on PRINT SHORT REPORT, "A Short
            # Report lists the days of fuel remaining, inventory, and 95%
            # ullage for all tanks", which p.2-2 annotates "Short report for
            # all tanks". Both printed whichever product the panel was last
            # on. The operating-mode audit's OP8; FIDELITY O23.
            if self.step == HEADER:
                return printer.fuel_all_products(console)
            if step.get("print_scope") == "short":
                return printer.fuel(console, long=False)
            return printer.fuel(console, console.fuel_tanks(self.device))
        return {
            "inventory": lambda: printer.inventory(console, tanks),
            # p.4-4, on DELIVERY: "To print an inventory increase report for
            # the selected tank, press PRINT." It printed the four-tank
            # INVENTORY REPORT every other screen does. The operating-mode
            # audit's OP10; FIDELITY O23.
            "increase": lambda: self._increase_report(device),
            "deliveries": lambda: printer.deliveries(
                console, tanks, self._delivery_day(step)),
            "loads": lambda: printer.loads(console, tanks, index),
            "fuel": lambda: printer.fuel(console, tanks),
            "shift": lambda: printer.shift(console, tanks),
            # "Press PRINT to print a Last-Shift Inventory Report", p.8-1,
            # and on END INVENTORY "To generate an Ending Inventory Report,
            # press PRINT", p.8-2. FIDELITY O22.
            "last_shift": lambda: printer.last_shift(
                console, tanks,
                ending=(step or {}).get("live") == "shift_physical"),
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
            # "Press PRINT to printout the status of the current test, if
            # in progress, or the last completed test", p.24-5; and p.23-1
            # prints the last one that passed
            "sump_test": lambda: printer.sump_report(console, device),
            "sump_passed": lambda: printer.sump_last_passed(console,
                                                            device),
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
        if kind == "nospace":
            # one line in Figure 11, and nothing under it on the page
            return [f"AFM{st['afm']} No Space for FP", ""]
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

    def _stand_on_console(self, token, depth=0):
        """Put the panel on the step whose `console` setting is `token`.

        Wherever it is: Setup Mode has levels, so a screen a flow wants to
        drop the operator onto is not always a step of the function.
        """
        path = list(self.subs)
        for i, st in enumerate(self.steps()):
            self.subs = list(path)
            self.step = i
            if st.get("console") == token:
                return True
            parent = self._branch_at() if depth < 3 else None
            if parent is not None:
                self.subs = list(path) + [parent]
                if self._stand_on_console(token, depth + 1):
                    return True
        self.subs = list(path)
        return False

    def _isdflow_start(self, which):
        """ENTER on one of the mapping steps opens its flow."""
        c = self.console
        if which == "addhose":
            # the new hose drops you onto its own FUEL POS LABEL screen,
            # which is a level DOWN now: p.20-13 puts it under EDIT FUEL
            # HOSE 1, which is itself under FUEL HOSE TABLE SETUP. This
            # searched one level and so found nothing and went nowhere.
            n = c.isd_add_hose()
            self.device = n
            self._stand_on_console("set.evr_fuel_pos")
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
        what = (step or {}).get("diagprint")
        if what == "meter_events":
            # "Prints Last 4 Meter Events", Figure 6-25's own P
            return name, printer.meter_events(console)
        if what == "tank_history":
            # 576013-818 Rev AB Figure 6-9 annotates the three PRINT screens
            # of this function separately, and they are not the same
            # printout: the two rate screens say "Printout contains static
            # tank test results" and this one says "This printout gives
            # the tank result for every month". Every screen of the
            # function answered PRINT with the same leak-test report, so the
            # monthly history was not reachable from the panel at all --
            # though `leak_history_lines` builds it correctly and `I207`
            # has served it over the wire all along. See FIDELITY H13.
            return name, printer.tank_leak_history(console, self.device)
        if what in ("tank_periodic", "tank_annual"):
            # "Printout contains static tank test results", per rate
            return name, printer.leak_tests(console, "tank", self.device)
        if what in ("csld_current", "csld_previous"):
            # "Press PRINT to print out the report for the tank shown",
            # p.27-3 -- the month the screen names, for the tank it names.
            # Both screens printed CSLD TEST RESULTS for every tank, which is
            # the 24-hour report and not this one. FIDELITY D23.
            return name, printer.csld_monthly(console, self.device,
                                              what == "csld_previous")
        if what == "ps_calibration":
            # "Prints out sensor calibration history", 577013-937 Rev J
            # Figure 46. FIDELITY I11.
            return name, printer.ps_calibration(console, self.device)
        if what:
            # "press STEP until the 3.0 Diag screen appears, and press Print"
            return name, printer.line_diag(console, self._diag_kind(),
                                           self.device, what)
        if name == "PMC DIAGNOSTIC":
            # "Prints out a copy of the PMC Diagnostic report", 577013-937
            # Rev J Figures 48 and 49, one form per processor. FIDELITY I11.
            return name, printer.pmc_diagnostics(console)
        if name == "ISD DIAGNOSTIC":
            # 577013-819 Rev F p.35 puts P on the CLEAR TEST AFTER REPAIR
            # menu, "See example printout at right": TEST FAIL CLEAR DATES.
            # It printed the menu's own screens. FIDELITY I11.
            return name, printer.isd_clear_dates(console)
        if name == "SYSTEM DIAGNOSTIC":
            # "press the PRINT key and the printer prints: SOFTWARE REVISION
            # LEVEL ..."
            return name, printer.revision(console)
        if name == "SERVICE REPORT":
            if self.step == HEADER:
                # 576013-818 Rev AB Figure 6-3 puts P on the function's own
                # screen: "Press Print to printout a list of the 25 most
                # recent services codes entered. If none exist, there will
                # be no printout." It printed the whole code catalogue -- the
                # SERVICE CODE LIST screen's report, 140 lines -- where the
                # console prints the site's own history or nothing at all.
                # FIDELITY D24.
                return name, printer.service_history(console)
            return name, printer.service_codes(console)
        if name == "MAINT HARDWARE KEY BLOCK":
            # Figure 6-4 annotates its first screen: "Press PRINT to
            # printout a list of blocked keys." See FIDELITY D13.
            return name, printer.blocked_keys(console)
        if name == "ALARM HISTORY REPORT":
            # "SYSTEM ALARM HISTORY" is the console's own; every other screen
            # is headed with its device code, "T X:", "L X:", "Q X:"
            if line.startswith("SYSTEM"):
                return name, printer.alarm_history(console, system=True)
            # "Press PRINT to print the report for the tank DISPLAYED. Press
            # TANK/SENSOR to access other tanks in the system": the screen
            # says `T 3: ALARM HISTORY` and this printed every tank in the
            # log. See FIDELITY W24.
            return name, printer.alarm_history(console, line[:1],
                                               device=self.device)
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
            return name, printer.under_one_header(
                printer.accuchart(console, [self.device], "status"),
                printer.accuchart(console, [self.device], "history"))
        if name == "SMART SENSOR DIAGNOSTIC":
            # The P beside the head of each sensor's walk, and the P on the
            # install log under all three: 576013-818 Rev AB Figures 6-28,
            # 6-29, 6-31 and 6-32 draw both papers. Both printed the generic
            # sensor status report. FIDELITY D30.
            #
            # The Vac figure hangs its P off a DIFFERENT screen from the
            # other two -- 6-28 and 6-32 mark `s 1: MAG SENSOR DIAGS` and
            # `ATM P SENSOR DIAGS`, the ENTER gate, and 6-29 marks the
            # sensor's own `TYPE: VAC SENSOR` head. So the Vac one matches on
            # the second line: its first is `s 1: (Vac Sensor Label)`, which
            # five other screens of the same walk share.
            head = (step or {}).get("text") or ""
            paper = None
            if head == "SMART SENSOR INSTALL LOG":
                paper = printer.smart_install_log(console, self.device)
            elif (head in ("s 1: MAG SENSOR DIAGS", "ATM P SENSOR DIAGS")
                    or (step or {}).get("l2") == "TYPE: VAC SENSOR"):
                paper = printer.smart_diagnostic(console, self.device)
            if paper is not None:
                return name, paper
        if (name == "SMART SENSOR DIAGNOSTIC"
                and console.sensor_type("smart", self.device)
                in ("01", "02", "03", "04", "05")):
            # An ISD sensor's three PRINT screens are three printouts, SS
            # COMM DIAG, SS CONSTANTS DIAG and SS CHANNEL DIAG, in
            # 577013-800 Rev P p.20-44, 577013-937 Rev J Figure 45 and
            # 577013-819 Rev F p.34 alike. FIDELITY I11.
            #
            # And so are a Mag, a Vac and an ATMP sensor's: 576013-818
            # Rev AB Figures 6-28, 6-31 and 6-32 draw the same three
            # printouts beside the same three screens, where these printed
            # the generic sensor status report. The Mag figure spells its
            # third screen CHNNL. FIDELITY D26.
            said = (step or {}).get("l2") or ""
            for start, report in (("COMM DATA", printer.ss_comm_diag),
                                  ("CONSTANTS", printer.ss_constants_diag),
                                  ("CHANNELS", printer.ss_channel_diag),
                                  ("CHNNL", printer.ss_channel_diag)):
                if said.startswith(start):
                    return name, report(console, self.device)
        if name.startswith("SMART SENSOR"):
            return name, printer.sensors(console, "smart")
        # Everything else prints its own screens. 576013-818 Rev AB, walking
        # a probe fault: "Press Function until In-Tank Diagnostics appear.
        # Press Print. (If the console does not have a printer, manually
        # record the diagnostic data from each diag screen)" -- so the
        # report IS the screens, the way the setup report is the setup
        # screens. This used to fall through to the system status report,
        # which meant 154 of the 295 diagnostic screens printed something
        # with nothing to do with what was in front of you. FIDELITY U1.
        return name, self._diag_screen_dump(name)

    def _diag_screen_dump(self, name):
        """Every screen of the diagnostic function you are standing in.

        Rendered by walking the function's screens through `_lines`, which
        is the only thing that knows how to draw one, so the paper says
        exactly what the panel says. The step is put back afterwards: PRINT
        does not move you.
        """
        keep = self.step
        # `_lines` draws a standing confirmation or message in place of the
        # screen, so with one up every step drew it and the report came out
        # a header and END. Put aside for the walk and put back after it.
        held = (self.confirm, self.msg, self.maint_report, self.editing)
        self.confirm, self.msg = None, ""
        self.maint_report = self.editing = False
        out = printer.header(self.console, name)
        last_head = None
        try:
            for n in range(len(self.steps())):
                self.step = n
                drawn = [str(x).rstrip() for x in self._lines()]
                if len(drawn) > 1 and screens.KEYPRESS.match(drawn[1].strip()):
                    # a screen that asks for a key is a way in, not a
                    # reading, and a report has no way in -- the same rule
                    # the setup report follows
                    continue
                drawn = [x for x in drawn if x]
                if len(drawn) > 1:
                    # the device head once, not over every reading, the way
                    # the setup report heads a tank once
                    head = printer.report_head(drawn[0])
                    if head == last_head:
                        drawn = drawn[1:]
                    else:
                        drawn = [head] + drawn[1:]
                        last_head = head
                out.extend(drawn)
        finally:
            self.step = keep
            (self.confirm, self.msg, self.maint_report,
             self.editing) = held
        return out

    def k_print(self):
        # `_keyed` clears the maintenance report screen, and PRINT is the key
        # chapter 32's screen asks for, so it goes back up for this one
        maint = self.maint_report
        held = self.confirm
        self._keyed()
        if maint:
            self.maint_report, self.confirm = True, held
        if self._leave_overlay():
            return
        if self.locked:
            self._render()
            return
        title, lines = self._report()
        if lines is None:
            # a report the manual says does not come out at all when it has
            # nothing in it: Figure 6-3's "If none exist, there will be no
            # printout". FIDELITY D24.
            self.log(f"-- PRINT: {title}: nothing to print")
            return
        self.paper_out(lines)
        if self.console.out_of_paper:
            # `PAPER OUT` is already standing as alarm 01/01 and the display
            # carries it; pressing PRINT does not draw a second message, and
            # no page shows the display changing when it prints at all.
            # See FIDELITY U5.
            self.log("-- PRINT refused: out of paper")
            return
        if self.console.printer_lever_open:
            # `paper_out` has said nothing came out; `PRINT: <title>` under
            # it read as though something had
            return
        self.log(f"-- PRINT: {title}")

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
            self.log("-- PAPER FEED refused: out of paper")
            return
        if not self.live_paper.get():
            # the paper is being fed whether or not the bench is showing it
            self.live_paper.set(True)
        self._slip_feed([""])
        self.log("-- PAPER FEED")

    def k_blue(self):
        """The blue Maintenance Tracker key.

        576013-610 chapter 33, step by step: "Press the blue key on the front
        panel. The display will read 'Disabled' or 'Enabled'. 'Disabled'
        appears the very first time Maintenance Tracker is accessed.
        'Enabled' appears thereafter. Press Step and the display will read"
        `INSERT KEY IN PORT / PRESS <ENTER>`. "You have one minute to plug
        your ID key into the MT Comm card and press Enter, or the system will
        timeout." Without the board there is nothing to log in to.

        Pressing it again while somebody is logged in takes the key out --
        "when all codes are entered for this work session, remove your
        Contractor's ID key". See FIDELITY D13.
        """
        self._keyed()
        if not self.console.has("mt"):
            # `NO MT COMM` is an ALARM -- Table 29-2 -- and not a screen
            # the blue key draws. A console with no MT Comm board has
            # nothing to log in to and says nothing about it.
            # See FIDELITY U5.
            self.log("-- blue key: no MT Comm board")
            return
        if self.console.mt_logged_in():
            self.console.remove_tracker_key()
            self.mt_login = None
            # "when all codes are entered for this work session, remove
            # your Contractor's ID key" -- the removal is the act, and no
            # page draws a screen acknowledging it. See FIDELITY U5.
            self.log("-- blue key: tracker key removed")
            self._render()
            return
        self.mt_login = "prompt"
        self._render()

    def _mt_lines(self):
        """The two lines the login sequence is showing, if it is showing."""
        if self.mt_login == "prompt":
            return ["MAINTENANCE TRACKER",
                    "ENABLED" if self.mt_ever else "DISABLED"]
        if self.mt_login == "insert":
            return ["INSERT KEY IN PORT", "PRESS <ENTER>"]
        return ["MAINTENANCE TRACKER", self.mt_login or ""]

    def _log_visit(self):
        text = (self.service_var.get() if hasattr(self, "service_var")
                else "").strip()
        if not text or text == "--":
            self.bell()
            return
        code = text.split()[0]
        ident = (self.key_id.get() if hasattr(self, "key_id") else "").strip()
        self.console.log_service(code, ident)
        self.log(f"-- service visit logged: {text} by key {ident or '??????'}")

    def _mt_present(self):
        """ENTER with a key in the port: what the console makes of it."""
        ident = (self.key_id.get() if hasattr(self, "key_id") else "").strip()
        label = (self.key_label.get() if hasattr(self, "key_label")
                 else "").strip()
        expired = bool(hasattr(self, "key_expired") and self.key_expired.get())
        said = self.console.present_tracker_key(ident, label, expired)
        self.mt_ever = True
        self.mt_login = said
        self.log(f"-- maintenance tracker: {said.lower()}")
        self._render()

    def _mt_timed_out(self):
        """"or the system will timeout", one console minute after the ask."""
        if self.mt_login != "insert" or self.mt_asked is None:
            return False
        import time as _time
        if (_time.mktime(self.console.now()) - self.mt_asked
                < self.console.KEY_PROMPT_SECONDS):
            return False
        self.mt_login = None
        self.mt_asked = None
        self.log("-- maintenance tracker: key insertion timed out")
        return True

    def k_white(self):
        """The white Maintenance Report key. 576013-610 Rev AC chapter 32.

        This drew `MAINT REPORT` over `NO HISTORY`, on every screen, in
        every mode, on every console -- with the history enabled, with the
        NVMEM203 in, and with records in the log. `NO HISTORY` is on no page
        of any manual on the shelf.

        p.32-1: "The Maintenance Report feature is available in the TLS-350
        with version 27 software and a NVMEM 203 card installed. Maintenance
        History must be enabled in System Setup for this feature to
        function. To access the Maintenance Report menu, press the white key
        on the front panel left keypad". p.32-3 draws what comes up:

            MAINTENANCE REPORT
            PRESS <PRINT>

        What a console WITHOUT the three does is not on any page, so it does
        nothing and says so to the bench log only -- the same reading FIDELITY
        U5 settled for the chart passcode, where `INVALID PASSCODE` was this
        project's invention and a real console is silent. See R3.
        """
        self._keyed()
        if not self.console.maintenance_report_ready():
            self.log("-- white key: no maintenance report on this console")
            return
        self.maint_report = True
        self.confirm = ["MAINTENANCE REPORT", "PRESS <PRINT>"]
        self._render()

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
        if self.editing and (self.cur_step() or {}).get("buf"):
            # A Contractor's ID key is six characters and they are not all
            # digits: "cccccc - Six digit ID code (ASCII)", and the manual's
            # own are A12345 and A54321.
            if key == "+":
                self.buf = self.buf[:-1]
            elif key.isalnum() and len(self.buf) < self.console.KEY_ID_WIDTH:
                self.buf += key.upper()
            self._render()
            return
        if (self.cur_step() or {}).get("pick") == "device":
            # The digits go straight in, with no CHANGE first.
            # 576013-610 Rev AC p.22-1: "Press STEP. The system displays the
            # message: `TEST OUTPUT RELAYS` / `ENTER RELAY NUMBER #`. Enter
            # the number of the relay you want to test, then press ENTER."
            # There is no CHANGE in that sentence and no field behind the
            # step, so nothing echoed and ENTER did not advance: the only
            # route to relay 12 was to STEP past the screen that asks for a
            # number and press TANK/SENSOR eleven times. A documented panel
            # action that could not be performed at all, on the function a
            # technician uses to prove a pump contactor. See FIDELITY U8.
            if key.isdigit():
                self.editing = True
                # two digits is every relay a TLS-350 can carry
                self.buf = (self.buf + key)[-2:]
            elif key == "+":
                self.buf = self.buf[:-1]
                self.editing = bool(self.buf)
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
                    self.load = ((self.load + (1 if key == "," else -1))
                                 % len(records))
                self._render()
            return
        if (self.cur_step() or {}).get("map"):
            # p.17-5's own two keys. The map row has cells, not a text
            # cursor, and the right arrow arrives as "," -- one key, arrow
            # and decimal both.
            if key == ",":
                self.slot = (self.slot + 1) % len(MAP_CELLS)
            elif key == "+":
                self.slot = (self.slot - 1) % len(MAP_CELLS)
            self._render()
            return
        f = self.cur_field() or {}
        if f.get("kind") in ("enum", "flag"):
            # A pick-list is not an entry. 576013-623 Rev AN p.6-2 is
            # explicit for the one this was found on: "To choose another
            # Baud Rate, press CHANGE until you see the correct baud rate.
            # Then press ENTER to confirm your choice." The keypad has no
            # part in it, and p.2-3 gives the keys a lettering job only
            # "when you can enter either alphabetic or numeric characters".
            #
            # A technician typing 9600 -- the rate Table 6-1 gives for two
            # of the shipped modems -- got `BAUD RATE: WM00`, because 9 and
            # 6 multi-tap to W and M, and then a silent refusal from ENTER.
            # No console can display that. The keys do nothing here now,
            # which is what a list of five fixed choices can take.
            return
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
            self.on_meridiem = True
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
            if (f.get("kind") == "float"
                    or (self.cur_step() or {}).get("offset")):
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
        # `ev.char` is empty for Shift, Ctrl, the arrows, Home and End, and
        # `"" in "-. /:"` is True: each of them typed nothing over the
        # character under the cursor, and the arrow branch below was never
        # reached.
        if self.editing and ev.char and (ev.char.isalnum()
                                         or ev.char in "-. /:"):
            kind = (self.cur_field() or {}).get("kind")
            if (kind in ("int", "float", "time", "date", "digits")
                    and not (ev.char.isdigit() or ev.char in "-./:")):
                return
            if (self.cur_step() or {}).get("map") and not ev.char.isdigit():
                # five numbers; a letter in a cell raised out of `_enter_map`
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
