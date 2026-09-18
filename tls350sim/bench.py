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
"""The bench, drawn as the site it stands for.

The console above is the console; the bench below is the forecourt. A tank
is a buried cylinder seen end-on, with the probe dropped down its riser and
two floats riding the shaft, which is what the hardware actually is: drag
the product float and the tank has that much fuel in it, drag the water
float and there is that much water under the fuel. A sensor is a tile with
a lamp. The explanation paragraphs that used to sit above everything now
live behind small circled-i marks, read when wanted and out of the way when
not.

Geometry note. The tank is drawn end-on because that is the view in which
level IS height: a horizontal line across a circle. The fuel and water are
the true circular segments below their surface lines, so a half-full tank
looks half full the way a half-full tank does, bulging at the middle. The
probe's own arithmetic stays the console's: height-to-volume is the linear
rule `stick_height` uses, so what the bench draws and what the console
reports never disagree.
"""
import math
import tkinter as tk
from tkinter import ttk

from . import inputs, readings, relays
from .meterid import DEFAULT_BUS, DEFAULT_SLOT, MeterId

# Nothing in this file uses those three. The panel and the bench's own tests
# reach them through it -- `bench.DEFAULT_BUS`, `bench.DEFAULT_SLOT` and
# `bench.MeterId` -- so they are re-exported on purpose. Naming them here says
# so to a reader and to pyflakes, which honours neither `noqa` nor the
# `import X as X` convention; a fourth name added above and left unused is
# still reported.
_RE_EXPORTED = (DEFAULT_BUS, DEFAULT_SLOT, MeterId)

# ---------------------------------------------------------------------------
# the design tokens: one place for every colour and face the bench uses

BG = "#26282d"            # the bench top itself
CARD = "#2e3138"          # a card sitting on it
CARD_EDGE = "#3c4049"     # its edge
CARD_HI = "#363a42"       # a card the pointer is over

INK = "#e8eae3"           # primary text
BODY = "#c2c6bc"          # body text
MUTED = "#8b9087"         # secondary text
FAINT = "#6a6f68"         # tertiary

ACCENT = "#6f9fd8"        # the one accent: selection, focus, links
OK = "#5fc472"            # a lamp that is happy
WARN = "#e0b64f"          # one that is not sure
BAD = "#e06c6c"           # one that is not happy

FUEL = "#c99136"          # gasoline, lit from above
FUEL_DEEP = "#96691f"     # the same fuel, deeper
FUEL_LINE = "#ecc25f"     # its surface
WATER = "#3f6fb5"         # the water under it
WATER_DEEP = "#2d5390"
WATER_LINE = "#7fb0e8"

SOIL = "#3a342c"          # what the tank is buried in
SOIL_DOT = "#4c453a"
GRADE = "#565b52"         # the line the forecourt is poured to
SHELL = "#565b64"         # the tank's steel
SHELL_DARK = "#41454d"
VOID = "#1d1f23"          # ullage: the empty space over the fuel
SHAFT = "#9aa0a8"         # the probe shaft
FLOAT_P = "#efe5d0"       # the product float
FLOAT_W = "#cfe0f5"       # the water float
FLOAT_PS = "#b9d7c9"      # the 4 inch phase separation float, in its place

FONT = ("Segoe UI", 9)
FONT_SM = ("Segoe UI", 8)
FONT_HEAD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 10, "bold")
MONO = ("Consolas", 9)
MONO_SM = ("Consolas", 8)


# ---------------------------------------------------------------------------
# and one place for the fact that a pixel is not a pixel
#
# Every width and height written in this file was measured on a 96 DPI
# screen, which is what Windows reports to a program that has not asked
# otherwise. `enable_dpi_awareness()` asks otherwise, and then a machine at
# 150% hands Tk a 144 DPI screen: the FONTS are given in points, so Tk's own
# scaling grows them by half, and every one of these constants stays exactly
# where it was. A tile 178 pixels wide then has to hold text that wants 267,
# and the bench that looked right on the machine it was drawn on has its
# words cut off on the machine it is used on -- a meter card reading "FP 1
# ME1", a line card reading "TEST COMPLE", a sensor whose state is half a
# word. The console face has had `SimApp.S()` for this since it was drawn
# from a photograph; the bench never did.
_SCALE = 1.0


def set_scale(px):
    """What one of this file's pixels is worth on the screen in use.

    Called once by `SimApp`, before a single tile is built. Never below 1:
    the constants are minimums that came off a real layout, and shrinking
    them clips just as surely as leaving them alone magnifies.
    """
    global _SCALE
    _SCALE = max(1.0, float(px))


def S(n):
    """One of this file's measured dimensions, on the screen in use."""
    return max(1, int(round(n * _SCALE)))


# ---------------------------------------------------------------------------
# small parts


class Tip:
    """A hover card. The paragraphs that used to fill the bench live here.

    Follows the pointer's widget, not the pointer: shows under the widget
    after a beat, goes away on leave. One instance per widget.
    """

    def __init__(self, widget, text, wrap=380):
        self.widget = widget
        self.text = text
        self.wrap = wrap
        self.win = None
        self._id = None
        widget.bind("<Enter>", self._plan, add="+")
        widget.bind("<Leave>", self._drop, add="+")
        widget.bind("<ButtonPress>", self._drop, add="+")

    def _plan(self, _e=None):
        self._id = self.widget.after(350, self._show)

    def _show(self):
        if self.win is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry(f"+{x}+{y}")
        frame = tk.Frame(self.win, bg=CARD_EDGE, padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=self.text, bg="#33363d", fg=BODY, font=FONT,
                 justify="left", wraplength=self.wrap, padx=10, pady=8
                 ).pack()

    def _drop(self, _e=None):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None
        if self.win is not None:
            self.win.destroy()
            self.win = None


def info_dot(parent, text, wrap=380):
    """A small circled i. Hover it and the explanation appears."""
    dot = tk.Label(parent, text="ⓘ", bg=parent["bg"], fg=FAINT,
                   font=("Segoe UI", 9), cursor="question_arrow")
    Tip(dot, text, wrap)
    dot.bind("<Enter>", lambda _e: dot.config(fg=ACCENT), add="+")
    dot.bind("<Leave>", lambda _e: dot.config(fg=FAINT), add="+")
    return dot


def section(parent, title, tip=None):
    """A section heading: small caps, a rule running to the right edge,
    and the explanation behind an info dot instead of on the bench."""
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", pady=(12, 5))
    tk.Label(row, text=title.upper(), bg=BG, fg=MUTED,
             font=("Segoe UI", 8, "bold")).pack(side="left")
    if tip:
        info_dot(row, tip).pack(side="left", padx=(5, 0))
    rule = tk.Frame(row, bg=CARD_EDGE, height=1)
    rule.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(1, 0))
    return row


def card(parent, **kw):
    """A card: the bench's one container. A 1px edge, a little padding."""
    outer = tk.Frame(parent, bg=CARD_EDGE, padx=1, pady=1)
    inner = tk.Frame(outer, bg=CARD, **kw)
    inner.pack(fill="both", expand=True)
    outer.inner = inner
    return outer


class Segmented(tk.Frame):
    """The view switcher: flat segments with an accent bar under the one
    that is showing. Replaces the notebook's tab ears, which were small,
    dim, and easy to miss on a crowded screen."""

    def __init__(self, parent, names, command):
        super().__init__(parent, bg=BG)
        self.command = command
        self.buttons = {}
        self.bars = {}
        self.holders = {}
        self.order = list(names)
        self.hidden = set()
        self.current = None
        for name in names:
            holder = tk.Frame(self, bg=BG)
            holder.pack(side="left", padx=(0, 4))
            self.holders[name] = holder
            b = tk.Label(holder, text=name, bg=BG, fg=MUTED, font=FONT_HEAD,
                         padx=14, pady=6, cursor="hand2")
            b.pack()
            bar = tk.Frame(holder, bg=BG, height=3)
            bar.pack(fill="x")
            b.bind("<Button-1>", lambda _e, n=name: self.select(n))
            b.bind("<Enter>", lambda _e, n=name: self._hover(n, True))
            b.bind("<Leave>", lambda _e, n=name: self._hover(n, False))
            self.buttons[name] = b
            self.bars[name] = bar

    def _hover(self, name, on):
        if name != self.current:
            self.buttons[name].config(fg=BODY if on else MUTED)

    def set_visible(self, name, on):
        """Show or hide one segment without disturbing the others' order.

        Re-packing every holder rather than just the one, because `pack`
        appends: a segment hidden and shown again would otherwise come back
        at the right-hand end, and the views would shuffle every time the
        exposure changed. `self.order` is the order they were built in and
        it is the order they are always drawn in.
        """
        if name not in self.holders:
            return
        if on:
            self.hidden.discard(name)
        else:
            self.hidden.add(name)
        for n in self.order:
            self.holders[n].pack_forget()
        for n in self.order:
            if n not in self.hidden:
                self.holders[n].pack(side="left", padx=(0, 4))
        if not on and self.current == name:
            for n in self.order:
                if n not in self.hidden:
                    self.select(n)
                    break

    def visible(self, name):
        return name in self.holders and name not in self.hidden

    def select(self, name):
        if name == self.current:
            return
        self.current = name
        for n, b in self.buttons.items():
            b.config(fg=INK if n == name else MUTED,
                     bg=CARD if n == name else BG)
            self.bars[n].config(bg=ACCENT if n == name else BG)
        self.command(name)


class FlowGrid(tk.Frame):
    """A grid that reflows to the width it has, like text wrapping.

    Give it same-size tiles and it decides the column count itself, which
    is the whole answer to "does not work good on many screens": the tiles
    go beside each other where there is room and below each other where
    there is not.
    """

    def __init__(self, parent, tile_w=None, pad=6):
        super().__init__(parent, bg=BG)
        # `None` means MEASURE the tiles instead of being told. A number in
        # this file is a number of pixels at 96 DPI, and this machine is
        # not at 96 DPI: Windows at 150% hands Tk a 144 DPI screen, every
        # font comes out half again as tall and half again as wide, and a
        # tile sized from a constant is a tile whose words no longer fit
        # in it. A tile that sizes itself from its own content cannot have
        # that problem, whatever the screen or the font.
        self.tile_w = S(tile_w) if tile_w else None
        self.pad = S(pad)
        self.tiles = []
        self._cols = 0
        self._tile = 0
        self._wide = 0
        self._settle = None          # the pending re-measure, if any
        self.bind("<Configure>", self._reflow)

    def add(self, tile):
        self.tiles.append(tile)
        self._place(force=True)
        if not self.tile_w and self._settle is None:
            # A tile asked for its width before Tk has laid it out answers
            # 1, so measure again once the idle queue has drained. One
            # callback for the whole batch, and it is CANCELLED if the grid
            # goes first: a view rebuilt between the add and the idle pass
            # leaves Tk trying to run a command that was destroyed with the
            # widget, which it reports as `invalid command name ...
            # <lambda>` from inside the event loop, where no try/except of
            # ours is running.
            self._settle = self.after_idle(self._remeasure)
            self.bind("<Destroy>", self._cancel, add="+")

    def _remeasure(self):
        self._settle = None
        if self.winfo_exists():
            self._place(force=True)

    def _cancel(self, _e=None):
        if self._settle is not None:
            try:
                self.after_cancel(self._settle)
            except Exception:
                pass
            self._settle = None

    def _reflow(self, _e=None):
        self._place()

    def _width(self):
        if self.tile_w:
            return self.tile_w
        return max([t.winfo_reqwidth() for t in self.tiles] or [1])

    def _place(self, force=False):
        if not self.tiles:
            return
        width = self.winfo_width() or self.winfo_reqwidth()
        tile_w = self._width()
        cols = max(1, (width + self.pad) // (tile_w + self.pad))
        if cols == self._cols and tile_w == self._tile and not force:
            return
        self._cols, self._tile = cols, tile_w
        # Measured tiles stretch to their column and every column is the
        # same width, so a grid of self-sized tiles still lines up; tiles
        # given a fixed width keep sitting at their own size.
        stick = "nw" if self.tile_w else "nsew"
        for i, t in enumerate(self.tiles):
            t.grid(row=i // cols, column=i % cols,
                   padx=(0, self.pad), pady=(0, self.pad), sticky=stick)
        if not self.tile_w:
            for col in range(max(cols, self._wide)):
                # a column past the last one still holds the minsize it
                # was given, and reserves that width with nothing in it
                on = col < cols
                self.columnconfigure(col, minsize=tile_w if on else 0,
                                     uniform="tile" if on else "")
            self._wide = cols


class HStrip(tk.Frame):
    """A horizontal strip of cards with its own scrollbar.

    Tanks are tall and narrow, so they go beside each other; when there
    are more than the window is wide, the strip scrolls sideways and the
    wheel scrolls it while the pointer is over it.
    """

    def __init__(self, parent, height):
        super().__init__(parent, bg=BG)
        # `height` is a floor. The strip grows to fit its cards, because a
        # fixed height clipped the bottom rows of a tank card the moment
        # the fonts scaled -- at 133% the temperature row was under the
        # edge, and nothing said so.
        self.min_h = height
        self.cv = tk.Canvas(self, bg=BG, highlightthickness=0, height=height)
        self.bar = ttk.Scrollbar(self, orient="horizontal",
                                 command=self.cv.xview)
        self.body = tk.Frame(self.cv, bg=BG)
        self._win = self.cv.create_window((0, 0), window=self.body,
                                          anchor="nw")
        self.cv.configure(xscrollcommand=self._barset)
        self.cv.pack(fill="x")
        self.body.bind("<Configure>", self._fit)
        # No wheel binding of its own. An earlier version did the classic
        # bind_all-on-Enter, unbind_all-on-Leave dance, and <Leave> fires
        # the moment the pointer crosses onto a CHILD of the canvas -- so
        # hovering a tank card killed the whole window's wheel. The app has
        # one wheel router now; this tag is how it finds us.
        self.cv._wheel = "x"

    def _fit(self, _e=None):
        self.cv.configure(scrollregion=self.cv.bbox("all"),
                          height=max(self.min_h, self.body.winfo_reqheight()))

    def _barset(self, lo, hi):
        # the scrollbar only appears when there is something to scroll to
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.bar.pack_forget()
        else:
            self.bar.pack(fill="x")
        self.bar.set(lo, hi)

    def _wheel(self, ev):
        self.cv.xview_scroll(-1 * (ev.delta // 120), "units")


def state_colour(state):
    """What colour a sensor state's lamp is, from what the word means."""
    s = state.lower()
    if s == "normal":
        return OK
    if "water" in s:
        return WATER_LINE
    if "out" in s or "open" in s:
        return FAINT
    if "warn" in s or "low" in s:
        return WARN
    return BAD


# ---------------------------------------------------------------------------
# the tank


class TankCard(tk.Frame):
    """One tank: the buried cylinder, the probe, and the two floats.

    Drag the product float and you are setting how much fuel is in the
    tank; drag the water float and you are setting the water under it.
    The water float cannot pass the product float in either direction,
    because the interface it rides is the bottom of the fuel: on this
    card, as on the hardware, the water is always under the product.
    """

    W = 172          # card width; everything inside is placed from this
    CV_H = 236       # the drawing

    def __init__(self, parent, app, n, label, full):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.n = n
        self.full = max(full, 1.0)
        self.D = self.console.limit("607", n) or 96.0
        self.dragging = None       # "p", "w", or None
        self._last = None          # what the canvas last drew

        inner = tk.Frame(self, bg=CARD)
        inner.pack(fill="both", expand=True)

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(head, text=f"{n}", bg=CARD_EDGE, fg=INK, font=MONO_SM,
                 padx=4).pack(side="left")
        tk.Label(head, text=f" {label[:18]}", bg=CARD, fg=INK,
                 font=FONT_HEAD, anchor="w").pack(side="left", fill="x")

        self.cv = tk.Canvas(inner, width=S(self.W) - S(18),
                            height=S(self.CV_H),
                            bg=CARD, highlightthickness=0)
        self.cv.pack(padx=8, pady=(2, 0))
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Motion>", self._hover)

        # the readouts: fuel on one line, water and the leak on the next
        rows = tk.Frame(inner, bg=CARD)
        rows.pack(fill="x", padx=8, pady=(2, 7))
        r1 = tk.Frame(rows, bg=CARD)
        r1.pack(fill="x")
        tk.Frame(r1, bg=FUEL, width=8, height=8).pack(side="left",
                                                      pady=(1, 0))
        self.lbl_vol = tk.Label(r1, bg=CARD, fg=INK, font=MONO, anchor="w")
        self.lbl_vol.pack(side="left", padx=(5, 0))
        self.lbl_h = tk.Label(r1, bg=CARD, fg=MUTED, font=MONO_SM,
                              anchor="e")
        self.lbl_h.pack(side="right")
        r2 = tk.Frame(rows, bg=CARD)
        r2.pack(fill="x", pady=(1, 0))
        tk.Frame(r2, bg=WATER, width=8, height=8).pack(side="left",
                                                       pady=(1, 0))
        self.lbl_wat = tk.Label(r2, bg=CARD, fg=BODY, font=MONO_SM,
                                anchor="w")
        self.lbl_wat.pack(side="left", padx=(5, 0))
        leak = tk.Frame(r2, bg=CARD)
        leak.pack(side="right")
        tk.Label(leak, text="leak", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        self.leak_var = tk.StringVar(
            value=f"{self.console.tank_leak.get(n, 0.0):g}")
        e = tk.Entry(leak, textvariable=self.leak_var, width=5, bg="#24262b",
                     fg=INK, font=MONO_SM, insertbackground=INK,
                     relief="flat", justify="right")
        e.pack(side="left", padx=(4, 2))
        e.bind("<KeyRelease>", self._set_leak)
        tk.Label(leak, text="g/h", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        Tip(e, "What the tank is actually losing, in gallons per hour. "
               "A leak test MEASURES this; it does not read it, which is "
               "why a 0.2 gph test can pass a 0.05 gph leak.")

        # And what the product is at, when somebody wants it held. Buried
        # fuel moves with the season, so the free reading never leaves the
        # high forties to low sixties -- and 576013-610 Table 29-4
        # invalidates a leak test whose "temperature reading is below 0 F
        # (-17.8 C) or above 100 F (37.8 C)", which was a criterion the
        # console had written and could not reach. See FIDELITY Y10.
        r3 = tk.Frame(inner, bg=CARD)
        r3.pack(fill="x", padx=8, pady=(1, 0))
        tk.Label(r3, text="temp", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        held = self.console.tank_temp.get(n)
        self.temp_var = tk.StringVar(value="" if held is None else f"{held:g}")
        t = tk.Entry(r3, textvariable=self.temp_var, width=5, bg="#24262b",
                     fg=INK, font=MONO_SM, insertbackground=INK,
                     relief="flat", justify="right")
        t.pack(side="left", padx=(4, 2))
        t.bind("<KeyRelease>", self._set_temp)
        tk.Label(r3, text="F", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        Tip(t, "Hold the product at a temperature, in Fahrenheit. Leave it "
               "empty and the tank follows the season, which is where a "
               "buried tank lives. Below 0 or above 100 invalidates an "
               "in-tank leak test, which is Table 29-4's own criterion.")

        # Which probe is down the riser. A real console never asks -- it reads
        # the circuit code off the probe and tells YOU what it found -- so
        # this belongs on the bench beside the tank, not in Setup Mode. The
        # list is what this console's software admits: fit an older console
        # and the CAP probes appear, fit a modern one and they are gone,
        # because Cap 1 stopped at version 8 and Cap 0 at version 17.
        r3 = tk.Frame(rows, bg=CARD)
        r3.pack(fill="x", pady=(3, 0))
        tk.Label(r3, text="probe", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        self.probe_var = tk.StringVar(value=self._probe_now())
        self.probe_menu = tk.OptionMenu(r3, self.probe_var, *self._probe_choices(),
                                        command=self._set_probe)
        self.probe_menu.config(bg="#24262b", fg=INK, font=MONO_SM,
                               relief="flat", highlightthickness=0,
                               activebackground=CARD_EDGE, width=9,
                               anchor="w", padx=4, pady=0)
        self.probe_menu["menu"].config(bg="#24262b", fg=INK, font=MONO_SM)
        self.probe_menu.pack(side="left", padx=(4, 0))
        Tip(self.probe_menu,
            "The probe fitted on this tank, by its circuit code. The console "
            "reads this off the probe -- there is no Set Probe Type command "
            "anywhere in the protocol -- and everything else follows from it: "
            "the name the diagnostics print, how many floats it has, whether "
            "it reads water at all, and which float sizes Setup will offer. "
            "Only the families this console's software supports are listed.")

        # The truck. A console is never TOLD a tanker has arrived: it
        # watches the level, and 577013-814 p.22 says what it takes to be
        # seen -- "move the float 1-2 in/sec ... If you move the float too
        # quickly the system may not register the delivery flag." Dragging
        # the float is a jump; this pours, at a rate, over console time.
        r4 = tk.Frame(rows, bg=CARD)
        r4.pack(fill="x", pady=(3, 0))
        self.truck_lbl = tk.Label(r4, text="truck", bg=CARD, fg=FAINT,
                                  font=FONT_SM, anchor="w")
        self.truck_lbl.pack(side="left")
        self.truck = tk.Label(r4, text="SEND TRUCK", bg="#24262b", fg=ACCENT,
                              font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                              cursor="hand2")
        self.truck.pack(side="right")
        self.truck.bind("<Button-1>", lambda _e: self._truck_click())
        Tip(self.truck,
            "A delivery as an EVENT: a tanker that pours at a rate, over "
            "console time, so the console sees the level RISE the way it "
            "does on a site -- the delivery report, the settling delay, "
            "RECENT DELIVERY on the tests and FUEL OUT clearing all follow "
            "from that rise. Dragging the float is a jump, and p.22 of the "
            "training manual warns a jump may never register. Click while "
            "a truck is dropping to send it away.")

        # The siphon-break valve, on a tank that is on a siphon bar with the
        # valve fitted (S632). The valve is the SET's: shutting it here shuts
        # it for every tank on the bar, TANK SIPHON BREAK posts on all of
        # them, and the levels stop finding each other. BENCH.md T11.
        joined = [t for t in self.console.siphon_set(n)
                  if t in self.console.tank_level]
        self.siphon = None
        if len(joined) > 1 and any(self.console.siphon_break_fitted(t)
                                   for t in joined):
            r5 = tk.Frame(rows, bg=CARD)
            r5.pack(fill="x", pady=(3, 0))
            others = ",".join(str(t) for t in joined if t != n)
            tk.Label(r5, text=f"siphon \u00b7 T{others}"[:14], bg=CARD,
                     fg=FAINT, font=FONT_SM, anchor="w").pack(side="left")
            self.siphon = tk.Label(r5, text="VALVE OPEN", bg="#24262b",
                                   fg=FAINT, font=("Segoe UI", 8, "bold"),
                                   padx=8, pady=1, cursor="hand2")
            self.siphon.pack(side="right")
            self.siphon.bind("<Button-1>", lambda _e: self.toggle_siphon())
            Tip(self.siphon, "The siphon-break valve on this tank's bar. "
                             "Shut it and the set's tanks stop finding one "
                             "level, and TANK SIPHON BREAK posts on every "
                             "tank of the set -- which is what a tank test "
                             "on a set with the valve does by itself. Open "
                             "it and the siphon carries product again.")

        self.redraw()

    def toggle_siphon(self):
        shut = self.n not in self.console.siphon_shut
        joined = self.console.shut_siphon(self.n, shut)
        self.app.log(f"-- tank {self.n}: siphon valve "
                     + ("SHUT" if shut else "opened")
                     + " across tanks " + ",".join(str(t) for t in joined))
        self.sync()

    # -- the truck ----------------------------------------------------------

    def _truck_click(self):
        if self.n in self.console.drops.running:
            self.stop_truck()
        else:
            TruckDialog(self)

    def send_truck(self, gallons, rate=None, compartments=1, ticket=None,
                   bol="", gap=2.0):
        """Send a tanker: `gallons` altogether, in `compartments` hoses
        with `gap` minutes between them, at `rate` gallons a minute."""
        parts = max(1, int(compartments))
        each = float(gallons) / parts
        drop = self.console.drops.start(self.n, [each] * parts, rate,
                                        ticket=ticket, bol=bol)
        if drop is None:
            return None
        drop.gap = float(gap)
        paper = ("no ticket" if ticket is None
                 else f"ticket {float(ticket):,.0f} gal"
                 + (f", BOL {bol}" if bol else ""))
        self.app.log(f"-- tank {self.n}: truck dropping {drop.total:,.0f} gal"
                     f" at {drop.rate:g} gal/min"
                     + (f" in {parts} compartments" if parts > 1 else "")
                     + f", {paper}")
        self.sync()
        return drop

    def stop_truck(self):
        drop = self.console.drops.stop(self.n)
        if drop is not None:
            self.app.log(f"-- tank {self.n}: truck sent away after "
                         f"{drop.dropped:,.0f} of {drop.total:,.0f} gal")
        self.sync()

    def adjust(self, gallons):
        """Move the level by a route no watcher sees: not a delivery, not
        a load, and whatever the reconciliation makes of it is the point."""
        moved = self.console.adjust_volume(self.n, gallons)
        self.app.log(f"-- tank {self.n}: level moved {moved:+,.0f} gal "
                     "quietly (no delivery, no load: the book will not "
                     "agree with the probe)")
        self.redraw()
        return moved

    def _siphon_sync(self):
        if self.siphon is None:
            return
        shut = self.n in self.console.siphon_shut or any(
            self.console.leaks.active("tank", t)
            for t in self.console.siphon_set(self.n))
        self.siphon.config(text="VALVE SHUT" if shut else "VALVE OPEN",
                           fg=WARN if shut else FAINT)

    def _truck_sync(self):
        drop = self.console.drops.running.get(self.n)
        if drop is None:
            self.truck_lbl.config(text="truck", fg=FAINT, font=FONT_SM)
            self.truck.config(text="SEND TRUCK", fg=ACCENT)
            return
        self.truck_lbl.config(
            text=f"{drop.dropped:,.0f}/{drop.total:,.0f}", fg=MUTED,
            font=MONO_SM)
        self.truck.config(text="HOSE OFF" if drop.pause > 0 else "DROPPING",
                          fg=WARN)

    def _probe_choices(self):
        """The probes this console could have, newest families last."""
        codes = self.console.probe_codes_available()
        return [f"{readings.PROBE_MODELS[c][0]} {c}" for c in codes] or ["--"]

    def _probe_now(self):
        code = self.console.probe_circuit_code(self.n)
        model = readings.PROBE_MODELS.get(code)
        return f"{model[0]} {code}" if model else f"? {code}"

    def _set_probe(self, chosen):
        """Fit that probe. The console notices on its next look."""
        code = chosen.split()[-1]
        if code in readings.PROBE_MODELS:
            self.console.probe_fitted[self.n] = code
            self.app.log(f"-- tank {self.n}: fitted probe {chosen}")
        self.redraw()

    # -- state ------------------------------------------------------------

    def _levels(self):
        """Where the two floats are, by the CONSOLE's arithmetic.

        This card used to draw the product float at `volume / full * D`, a
        straight line, while the console reports `height_at`, the circular
        chart. They agree at half full and nowhere else: on the truck stop's
        tank the bench drew 6.00 inches where the console said 11.68, and
        114.00 where it said 108.32 -- nearly seven inches out at both ends,
        under a module docstring promising that "what the bench draws and
        what the console reports never disagree". A bench whose own ruler
        disagrees with the gauge it is there to demonstrate teaches the wrong
        thing twice. FIDELITY Y9.
        """
        st = self.console.tank_level.setdefault(
            self.n, {"volume": self.full * 0.6, "water": 0.0})
        fuel_h = self.console.height_at(self.n, st["volume"])
        water_h = min(st.get("water", 0.0), fuel_h)
        return st, fuel_h, water_h

    def _set_leak(self, _e=None):
        text = self.leak_var.get().strip()
        if not text:
            self.console.tank_leak[self.n] = 0.0
            return
        try:
            self.console.tank_leak[self.n] = float(text)
        except ValueError:
            pass

    def _set_temp(self, _e=None):
        """Empty is not zero here: it is "let it follow the season"."""
        text = self.temp_var.get().strip()
        if not text or text in ("-", "."):
            self.console.hold_temperature(self.n, None)
            return
        try:
            self.console.hold_temperature(self.n, float(text))
        except ValueError:
            pass

    # -- geometry ---------------------------------------------------------
    # The circle: centre (cx, cy), radius r. Height h inches above the tank
    # bottom is canvas y = cy + r - (h / D) * 2r.

    @property
    def _circle(self):
        w = self.W - 18
        r = (w - 26) / 2                # room for the depth ticks at right
        return (w / 2 - 4, 118, r)

    def _y(self, h):
        cx, cy, r = self._circle
        return cy + r - (max(0.0, min(h, self.D)) / self.D) * 2 * r

    def _h(self, y):
        cx, cy, r = self._circle
        return max(0.0, min(self.D, (cy + r - y) / (2 * r) * self.D))

    def _segment(self, h, steps=36):
        """The polygon of the liquid below height h: the true circular
        segment, walked around the arc through the bottom of the tank."""
        cx, cy, r = self._circle
        u = max(0.0, min(h, self.D)) / self.D           # 0..1 of diameter
        ys = cy + r - u * 2 * r                          # the surface line
        dy = max(-1.0, min(1.0, (ys - cy) / r))
        # Canvas y grows downward, so angles in (0, pi) are the lower half
        # of the circle. The surface cuts it at t = asin(dy) on the right
        # and pi - asin(dy) on the left; walking between them passes
        # through pi/2, the bottom of the tank.
        start = math.asin(dy)
        pts = []
        for i in range(steps + 1):
            t = start + (math.pi - 2 * start) * i / steps
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        return [c for p in pts for c in p]

    # -- drawing ----------------------------------------------------------

    def redraw(self):
        st, fuel_h, water_h = self._levels()
        drop = self.console.drops.running.get(self.n)
        key = (round(fuel_h, 2), round(water_h, 2),
               self.n in self.console.probe_out,
               None if drop is None else drop.pause > 0)
        cv = self.cv
        cv.delete("all")
        cx, cy, r = self._circle
        w = self.W - 18

        # the ground it is buried in
        cv.create_rectangle(0, 26, w, self.CV_H, fill=SOIL, outline="")
        for gx in range(6, w - 4, 14):
            for gy in range(34, self.CV_H - 6, 22):
                cv.create_line(gx, gy + ((gx * 7) % 9), gx + 3,
                               gy + ((gx * 7) % 9), fill=SOIL_DOT)
        cv.create_line(0, 26, w, 26, fill=GRADE, width=2)

        # the riser, and the probe's connector above grade. The connector
        # is a control: click it and the probe is unplugged, which is how
        # a PROBE OUT alarm is made on a bench, same as in the field.
        out = self.n in self.console.probe_out
        cv.create_rectangle(cx - 6, self._y(self.D) - 16, cx + 6, 26,
                            fill=SHELL_DARK, outline=SHELL)
        cv.create_rectangle(cx - 9, 12, cx + 9, 26, fill=SHELL,
                            outline=SHELL_DARK)
        if out:
            # the plug hangs beside the riser on its cable
            cv.create_line(cx, 12, cx + 16, 6, cx + 22, 12, fill=SHAFT,
                           smooth=True)
            cv.create_rectangle(cx + 18, 10, cx + 26, 20, fill=SHELL_DARK,
                                outline=BAD)
            cv.create_text(cx, cy - r + 26, text="PROBE OUT", fill=BAD,
                           font=("Segoe UI", 8, "bold"))
        else:
            cv.create_rectangle(cx - 3, 4, cx + 3, 12, fill=SHELL_DARK,
                                outline="")

        # the fill riser, which only shows itself while a truck is on it:
        # the drop tube down the left of the tank, and the hose across the
        # forecourt to it, live when fuel is moving and slack between
        # compartments
        if drop is not None:
            fx = cx - r + 14
            cv.create_rectangle(fx - 5, self._y(self.D) - 12, fx + 5, 26,
                                fill=SHELL_DARK, outline=SHELL)
            hose = WARN if drop.pause <= 0 else MUTED
            cv.create_line(-2, 4, fx - 14, 8, fx - 4, 20, fx, 26,
                           fill=hose, width=3, smooth=True)
            cv.create_text(fx + 10, 12, text="DROP" if drop.pause <= 0
                           else "HOSE", fill=hose, anchor="w",
                           font=("Segoe UI", 7, "bold"))

        # the shell, the ullage, the fuel, the water
        cv.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3,
                       fill=SHELL_DARK, outline=SHELL, width=2)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=VOID, outline="")
        if fuel_h > 0.02:
            cv.create_polygon(*self._segment(fuel_h), fill=FUEL, outline="")
            # deeper colour in the lower half of the fuel, for body
            if fuel_h > self.D * 0.08:
                cv.create_polygon(*self._segment(min(fuel_h * 0.45,
                                                     self.D)),
                                  fill=FUEL_DEEP, outline="")
        if water_h > 0.02:
            cv.create_polygon(*self._segment(water_h), fill=WATER,
                              outline="")
            if water_h > self.D * 0.05:
                cv.create_polygon(*self._segment(water_h * 0.5),
                                  fill=WATER_DEEP, outline="")

        # surface lines, drawn after both liquids so they read as surfaces
        yf, yw = self._y(fuel_h), self._y(water_h)
        dyf = abs(yf - cy) / r if r else 1.0
        halff = r * math.sqrt(max(0.0, 1.0 - min(1.0, dyf) ** 2))
        if fuel_h > 0.02:
            cv.create_line(cx - halff, yf, cx + halff, yf, fill=FUEL_LINE,
                           width=2)
        dyw = abs(yw - cy) / r if r else 1.0
        halfw = r * math.sqrt(max(0.0, 1.0 - min(1.0, dyw) ** 2))
        if water_h > 0.02:
            cv.create_line(cx - halfw, yw, cx + halfw, yw, fill=WATER_LINE,
                           width=2)

        # depth ticks up the right side: quarters of the diameter
        tx = w - 13
        for i in range(5):
            hy = self._y(self.D * i / 4)
            cv.create_line(tx, hy, tx + (6 if i % 2 == 0 else 4), hy,
                           fill=FAINT)
        cv.create_text(tx + 3, self._y(self.D) - 7, text=f"{self.D:.0f}″",
                       fill=FAINT, font=("Segoe UI", 7), anchor="s")

        # the probe shaft, and its anchor on the bottom of the tank. An
        # unplugged probe is still physically in the tank -- the shaft and
        # floats stay -- but the console cannot hear it, so the shaft is
        # drawn dead grey and the floats stop being handles.
        shaft_colour = FAINT if out else SHAFT
        cv.create_line(cx, self._y(self.D) - 8, cx, cy + r - 3,
                       fill=shaft_colour, width=2)
        cv.create_rectangle(cx - 5, cy + r - 6, cx + 5, cy + r - 2,
                            fill=shaft_colour, outline="")

        # the floats. Product rides the fuel surface, water rides the
        # fuel/water interface; both are pills on the shaft.
        #
        # There are TWO of them on a phase separation probe as well, which is
        # worth saying because it is the thing everyone expects to be three.
        # Veeder-Root's Phase Separation Float Kit, 0886100-xx0, ships "a
        # product float, water float, boot, two adapters and a cable" and
        # REPLACES the ordinary gasoline float kit; 577013-940's probe table
        # calls D004, D005 and D006 "2-float" in the same column where it
        # calls other codes "1-float". What makes it a phase separation float
        # is not a third body but its ballast: it is re-weighted to ride the
        # ethanol-water layer, whose density is below water's, as well as free
        # water. So it is drawn as the water float, larger -- a 4 inch
        # assembly where the ordinary one is smaller.
        self._float(cx, yf, 15, 6, "#8d8a80" if out else FLOAT_P, "pfloat")
        ps = self.console.float_size(self.n) == "4"
        self._float(cx, yw, 15 if ps else 12, 7 if ps else 5,
                    "#93a2b5" if out else (FLOAT_PS if ps else FLOAT_W),
                    "wfloat")

        # the value bubble while dragging
        if self.dragging:
            h = fuel_h if self.dragging == "p" else water_h
            y = yf if self.dragging == "p" else yw
            if self.dragging == "p":
                text = f"{st['volume']:,.0f} gal · {h:.1f}″"
            else:
                text = f"water {h:.1f}″"
            bx = cx + r - 4
            anchor = "e"
            t = cv.create_text(bx, max(14, y - 14), text=text, fill=INK,
                               font=MONO_SM, anchor=anchor)
            box = cv.bbox(t)
            cv.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4,
                                box[3] + 2, fill="#1b1d21", outline=CARD_EDGE)
            cv.tag_raise(t)

        self._last = key
        self._readouts(st, fuel_h, water_h)

    def _float(self, cx, y, hw, hh, colour, tag):
        self.cv.create_oval(cx - hw, y - hh, cx + hw, y + hh, fill=colour,
                            outline="#15161a", width=1, tags=(tag,))
        self.cv.create_line(cx - hw + 3, y, cx + hw - 3, y, fill="#8d8674",
                            tags=(tag,))

    def _readouts(self, st, fuel_h, water_h):
        self.lbl_vol.config(text=f"{st['volume']:,.0f} gal")
        self.lbl_h.config(text=f"{fuel_h:5.1f}″")
        self.lbl_wat.config(text=f"{water_h:.1f}″ water")

    # -- interaction ------------------------------------------------------

    def _on_connector(self, x, y):
        """Is this press on the probe connector above grade?"""
        cx, _cy, _r = self._circle
        return y < 28 and abs(x - cx) < 30

    def _near(self, y):
        """Which float a press at canvas-y means. The nearer one wins;
        both get a generous reach because a 10px pill is a small target."""
        if self.n in self.console.probe_out:
            return None
        _st, fuel_h, water_h = self._levels()
        dp = abs(y - self._y(fuel_h))
        dw = abs(y - self._y(water_h))
        if min(dp, dw) > 20:
            return None
        # when the two floats sit together, the press between them takes
        # the product float from above and the water float from below
        if abs(dp - dw) < 3:
            return "p" if y <= self._y(fuel_h) else "w"
        return "p" if dp < dw else "w"

    def _hover(self, ev):
        if self.dragging:
            return
        if self._on_connector(ev.x, ev.y):
            self.cv.configure(cursor="hand2")
        else:
            self.cv.configure(cursor="sb_v_double_arrow"
                              if self._near(ev.y) else "")

    def _press(self, ev):
        if self._on_connector(ev.x, ev.y):
            if self.n in self.console.probe_out:
                self.console.probe_out.discard(self.n)
                self.app.log(f"-- tank {self.n}: probe plugged back in")
            else:
                self.console.probe_out.add(self.n)
                self.app.log(f"-- tank {self.n}: probe UNPLUGGED at the "
                             "riser (PROBE OUT posts)")
            self.redraw()
            return
        self.dragging = self._near(ev.y)
        if self.dragging:
            self._drag(ev)

    def _drag(self, ev):
        if not self.dragging:
            return
        st, fuel_h, water_h = self._levels()
        h = self._h(ev.y)
        if self.dragging == "p":
            # the product float stops at the water float: fuel cannot be
            # below the water that is under it
            h = max(h, water_h)
            # and back the same way: the chart, not the straight line, so
            # dragging the float to a depth puts the gallons the console
            # would report at that depth into the tank
            st["volume"] = self.console.volume_at(self.n, h)
        else:
            # and the water float stops at the product float
            h = min(h, fuel_h)
            st["water"] = h
        self.console.tank_level[self.n] = st
        self.redraw()

    def _release(self, _e=None):
        if self.dragging:
            self.dragging = None
            self.redraw()

    # -- the 700ms tick ---------------------------------------------------

    def sync(self):
        """Follow the console: a selling meter or a leak drains the tank
        while nobody is touching it, and the floats ride down with it."""
        self._truck_sync()
        self._siphon_sync()
        if self.dragging:
            return
        _st, fuel_h, water_h = self._levels()
        drop = self.console.drops.running.get(self.n)
        key = (round(fuel_h, 2), round(water_h, 2),
               self.n in self.console.probe_out,
               None if drop is None else drop.pause > 0)
        if self._last != key:
            self.redraw()


class TruckDialog(tk.Toplevel):
    """The tanker's paperwork, filled in before it pours.

    One small window in the bench's own colours, placed beside the card it
    came from. The upper half sends a truck; the lower half is the other
    way fuel moves -- quietly, by a route no watcher sees -- which is what
    576013-818 12-3 lists under the causes of lost volume. BENCH.md T1,
    T2, T3 and T5.
    """

    def __init__(self, card):
        super().__init__(card, bg=CARD_EDGE, padx=1, pady=1)
        self.card = card
        console = card.console
        n = card.n
        self.withdraw()
        self.overrideredirect(True)
        self.transient(card.winfo_toplevel())
        body = tk.Frame(self, bg=CARD, padx=14, pady=10)
        body.pack(fill="both", expand=True)

        st = console.tank_level.get(n) or {}
        full = console.full_volume(n)
        room = max(0.0, full * 0.9 - st.get("volume", 0.0))
        gallons = max(100.0, room // 100 * 100)

        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=f"{n}", bg=CARD_EDGE, fg=INK, font=MONO_SM,
                 padx=4).pack(side="left")
        tk.Label(head, text="  Send a truck", bg=CARD, fg=INK,
                 font=FONT_TITLE).pack(side="left")
        close = tk.Label(head, text="✕", bg=CARD, fg=MUTED, font=FONT,
                         cursor="hand2", padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.destroy())

        self.v_gal = tk.StringVar(value=f"{gallons:.0f}")
        self.v_rate = tk.StringVar(value=f"{console.drops.RATE:g}")
        self.v_parts = tk.StringVar(value="1")
        self.v_gap = tk.StringVar(value="2")
        self.v_ticket = tk.StringVar(value=f"{gallons:.0f}")
        self.v_none = tk.BooleanVar(value=False)
        self.v_bol = tk.StringVar(value="")
        self.v_adj = tk.StringVar(value="")

        row = self._row(body, pady=(10, 0))
        self._field(row, "gallons", self.v_gal, 6,
                    "How much the tanker is dropping, altogether. The "
                    "default is what brings the tank to 90%.")
        self._field(row, "at", self.v_rate, 5, "Gallons a minute down the "
                    "fill. A real drop runs a few hundred; a slow one is "
                    "how you watch the delivery report wait for it.",
                    unit="gal/min", pad=(12, 0))
        self.eta = tk.Label(body, text="", bg=CARD, fg=MUTED, font=FONT_SM,
                            anchor="w")
        self.eta.pack(fill="x", pady=(2, 0))

        row = self._row(body, pady=(6, 0))
        self._field(row, "compartments", self.v_parts, 3,
                    "A tanker carries its load in compartments and drops "
                    "them one hose at a time. S610's DELIVERY DELAY exists "
                    "to bridge the gaps between them -- 576013-623 7-26 -- "
                    "and a gap longer than the delay makes two reports of "
                    "one delivery.")
        self._field(row, "gap", self.v_gap, 3, "Minutes between one "
                    "compartment closing and the next hose going on.",
                    unit="min", pad=(12, 0))

        row = self._row(body, pady=(6, 0))
        self.e_ticket = self._field(row, "ticket", self.v_ticket, 6,
                    "What the driver's paperwork says was dropped. The "
                    "console gauges the drop itself and prints the two "
                    "side by side on the ticketed delivery report; the "
                    "difference is the VARIANCE, and the ticket is what "
                    "the reconciliation's TICKETED column is made of.",
                    unit="gal")
        none = tk.Checkbutton(row, text="no ticket", variable=self.v_none,
                              bg=CARD, fg=BODY, font=FONT_SM,
                              selectcolor="#24262b", activebackground=CARD,
                              activeforeground=INK, highlightthickness=0,
                              command=self._paperwork)
        none.pack(side="left", padx=(12, 0))
        Tip(none, "The driver left without handing one over. With ticketed "
                  "delivery on, that is a MISSING TICKET WARN until one is "
                  "entered at DELIVERY MAINTENANCE.")
        row = self._row(body, pady=(6, 0))
        self._field(row, "BOL", self.v_bol, 14, "The bill of lading "
                    "number, for the BOL report. Leave it empty if the "
                    "site does not keep them.")

        acts = self._row(body, pady=(10, 0))
        self._button(acts, "Send truck", self._send, primary=True
                     ).pack(side="right")
        self._button(acts, "Cancel", self.destroy).pack(side="right",
                                                        padx=(0, 6))

        rule = tk.Frame(body, bg=CARD_EDGE, height=1)
        rule.pack(fill="x", pady=(12, 8))
        quiet = tk.Frame(body, bg=CARD)
        quiet.pack(fill="x")
        tk.Label(quiet, text="OR MOVE THE LEVEL QUIETLY", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        info_dot(quiet,
                 "Fuel that comes or goes by a route the console never "
                 "sees: water pumped out, a stick reading, theft. The level "
                 "moves and NO watcher is told, so it is not a delivery and "
                 "not a tanker load -- and the book still says what it said, "
                 "which is exactly the variance the reconciliation chapter is "
                 "there to expose. Positive is fuel in, negative is fuel "
                 "out.").pack(side="left", padx=(5, 0))
        row = self._row(body, pady=(6, 0))
        e = self._field(row, "gallons", self.v_adj, 7,
                        "Plus for fuel arriving, minus for fuel leaving.",
                        unit="+ in / − out")
        e.bind("<Return>", lambda _e: self._adjust())
        self._button(row, "Adjust", self._adjust).pack(side="right")

        self._eta()
        self.v_gal.trace_add("write", lambda *_a: self._eta())
        self.v_rate.trace_add("write", lambda *_a: self._eta())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._send())
        self.update_idletasks()
        self._place()
        self.deiconify()
        self.grab_set()
        self.focus_set()

    # -- pieces ------------------------------------------------------------

    @staticmethod
    def _row(parent, pady=0):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=pady)
        return row

    @staticmethod
    def _field(row, label, var, width, tip, unit=None, pad=(0, 0)):
        tk.Label(row, text=label, bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left", padx=pad)
        e = tk.Entry(row, textvariable=var, width=width, bg="#24262b",
                     fg=INK, font=MONO_SM, insertbackground=INK,
                     relief="flat", justify="right")
        e.pack(side="left", padx=(4, 2), ipady=2)
        if unit:
            tk.Label(row, text=unit, bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left")
        Tip(e, tip)
        return e

    @staticmethod
    def _button(parent, text, command, primary=False):
        bg = ACCENT if primary else "#24262b"
        fg = "#15161a" if primary else INK
        b = tk.Label(parent, text=text, bg=bg, fg=fg,
                     font=("Segoe UI", 8, "bold"), padx=12, pady=3,
                     cursor="hand2")
        b.bind("<Button-1>", lambda _e: command())
        b.bind("<Enter>", lambda _e: b.config(
            bg="#8ab4e6" if primary else CARD_EDGE))
        b.bind("<Leave>", lambda _e: b.config(bg=bg))
        return b

    def _place(self):
        """Beside the card, and on the screen."""
        pill = self.card.truck
        x = pill.winfo_rootx() + pill.winfo_width() + 8
        y = pill.winfo_rooty() - self.winfo_reqheight() + pill.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(x, sw - self.winfo_reqwidth() - 8))
        y = max(0, min(y, sh - self.winfo_reqheight() - 8))
        self.geometry(f"+{x}+{y}")

    # -- what it says ------------------------------------------------------

    def _num(self, var, default=None):
        try:
            return float(var.get().strip().replace(",", ""))
        except ValueError:
            return default

    def _eta(self):
        gal, rate = self._num(self.v_gal), self._num(self.v_rate)
        if not gal or not rate or rate <= 0:
            self.eta.config(text="")
            return
        minutes = gal / rate
        speed = self.card.console.clock_speed
        note = f"about {minutes:.0f} min of console time to pour"
        if speed and speed != 1.0:
            note += f", {minutes / speed:.1f} min at the clock's x{speed:g}"
        self.eta.config(text=note)

    def _paperwork(self):
        """No ticket greys the ticket."""
        gone = self.v_none.get()
        self.e_ticket.config(state="disabled" if gone else "normal",
                             disabledbackground="#24262b",
                             disabledforeground=FAINT)

    # -- what it does ------------------------------------------------------

    def _send(self):
        gal = self._num(self.v_gal)
        rate = self._num(self.v_rate)
        if not gal or gal <= 0 or not rate or rate <= 0:
            self.bell()
            return
        parts = int(self._num(self.v_parts, 1) or 1)
        gap = self._num(self.v_gap, 2.0) or 0.0
        ticket = None if self.v_none.get() else self._num(self.v_ticket)
        bol = self.v_bol.get().strip()[:20]
        self.destroy()
        self.card.send_truck(gal, rate, compartments=parts, ticket=ticket,
                             bol=bol, gap=gap)

    def _adjust(self):
        gallons = self._num(self.v_adj)
        if not gallons:
            self.bell()
            return
        self.destroy()
        self.card.adjust(gallons)


# ---------------------------------------------------------------------------
# the sensors


# What a sensor position is called in the corner of its tile. The module
# names are the console's own ("Eight-Input Liquid Sensor Module"), which
# is a sentence rather than a tag, and a tile has room for a tag.
SENSOR_SHORT = {"liquid": "LIQUID", "vapor": "VAPOR", "gw": "GW",
                "2wire": "2-WIRE", "3wire": "3-WIRE", "smart": "SMART",
                "universal": "UNIV"}


class SensorTile(tk.Frame):
    """One sensor: a lamp, its name, what kind of thing it is, and a state
    button that offers only the states this sensor's own type can be in.

    Everything on this tile is the whole word. The first version was
    178px wide and put the type and the state side by side on one line,
    which cannot be done: a console location label is twenty characters
    ("STP SUMP TANK 1 UNLE"), the widest type the manual offers is "dual
    float discriminating" at 126px, and the widest state a smart sensor
    can report is WATERWARN at 89px in a pill. Side by side that is 221px
    of content in 160px of tile, and Tk resolves it by handing the first
    widget packed what it asked for and the second whatever is left -- so
    the type survived, the label was hard-cut at sixteen characters, and
    the STATE, the one thing on the tile somebody is actually reading,
    was clipped to 52px of its 69. A tile that cannot show a sensor's
    state is not a sensor tile.

    So one thing per line, each sized for its own worst case, and the
    state is a full-width button rather than a pill squeezed into a
    corner -- it opens a menu, and now it looks like it does.

    And NO fixed width or height. Every pixel dimension in this file was
    measured on a 96 DPI screen; Windows at 150% hands Tk a 144 DPI one,
    and every font on it comes out half again as large. A tile built to a
    constant clips its own words on that screen no matter how generous
    the constant was -- which is what this looked like on the bench it
    was reported from. This one is as big as what is written on it, and
    `FlowGrid` measures it rather than being told.
    """

    def __init__(self, parent, app, mod, num, label):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.mod = mod
        self.num = num

        inner = tk.Frame(self, bg=CARD)
        inner.pack(fill="both", expand=True)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        # the lamp is a drawing, not text, so it is the one thing here that
        # does have to be told how big a pixel is
        self.lamp = tk.Canvas(top, width=S(10), height=S(10), bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        tk.Label(top, text=f"  {label}", bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left", fill="x", expand=True)
        self._dot = self.lamp.create_oval(1, 1, S(10) - 1, S(10) - 1,
                                          fill=OK, outline="")

        # Which position this is, which the label hides the moment somebody
        # gives the sensor a location: "MW-1 NORTH" does not say whether the
        # console will report it as LIQUID 3 or SMART 3, and every screen and
        # every serial reply names it by the position.
        note = self.app._sensor_type_note(mod, num).strip()
        where = f"{SENSOR_SHORT.get(mod, mod.upper())} {num}"
        tk.Label(inner, text=f"{where} \u00b7 {note}" if note else where,
                 bg=CARD, fg=FAINT, font=FONT_SM, anchor="w").pack(
                     fill="x", padx=8, pady=(1, 0))

        states = ["normal"] + list(self.console.sensor_states(mod, num))
        cur = self.console.sensor_state.get((mod, str(num)), "normal")
        if cur not in states:
            cur = "normal"
            self.console.sensor_state[(mod, str(num))] = cur
        self.var = tk.StringVar(value=cur)
        self.pill = tk.Label(inner, text=cur.upper(), bg="#24262b",
                             fg=state_colour(cur),
                             font=("Segoe UI", 8, "bold"),
                             padx=8, pady=3, anchor="w", cursor="hand2")
        self.pill.pack(fill="x", padx=8, pady=(4, 0))
        self.pill.bind("<Button-1>", lambda _e: self._menu(states))
        Tip(self.pill, "What this sensor is sensing. Only the states its "
                       "own type can report are on the list: a single-float "
                       "sump sensor cannot say WATER, so WATER is not "
                       "offered.")

        # A vacuum sensor is the one sensor on this bench with PHYSICS behind
        # it rather than a state: it watches an interstitial space held under
        # vacuum, and what it reports follows from how fast air is getting
        # in. 576013-818 Figure 6-29 puts a threshold on the rate and another
        # on the time the space has left, so the bench sets the leak and the
        # console does the rest -- the pressure climbs, the readings move,
        # and the two alarms post themselves. See FIDELITY D1.
        if mod == "smart" and self.console.sensor_type(mod, num) == "04":
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", padx=8, pady=(4, 0))
            tk.Label(row, text="into the interstice", bg=CARD, fg=FAINT,
                     font=FONT_SM, anchor="w").pack(side="left")
            self.leak_var = tk.StringVar(
                value=f"{self.console.vac_leak_rate(num):g}")
            e = tk.Entry(row, textvariable=self.leak_var, width=5,
                         bg="#24262b", fg=INK, font=MONO_SM,
                         insertbackground=INK, relief="flat",
                         justify="right")
            e.pack(side="left", padx=(4, 2))
            e.bind("<KeyRelease>", self._set_vac_leak)
            tk.Label(row, text="g/h", bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left")
            Tip(e, "Gallons an hour of air getting into the interstitial "
                   "space. The pressure climbs with it, the manual test "
                   "measures it, and a Vac Warning is posted above 22.4 "
                   "gph or with under eight hours of vacuum left.")

        # A Mag sump sensor gets the sump. 576013-610 Rev AC chapter 24's
        # leak test measures water going out of a sump the technician has
        # filled, and nothing on this bench had any water in a sump to
        # measure -- so the four things the test reads are set here: how
        # deep it stands, how fast it leaves, how warm it is and how fast
        # that is moving. ENTER or leaving the box sets it; a keystroke
        # does not, because "1" on the way to "12" is a sump with one inch
        # of water in it and a running test would abort on it. FIDELITY U1b.
        if mod == "smart" and self.console.sensor_type(mod, num) == "03":
            sumps = self.console.sumps
            for text, unit, key, value, tip in (
                    ("water in sump", "in", "water",
                     f"{sumps.height(num):g}",
                     "Inches of water standing in the sump. A leak test "
                     "wants 6 to 22 on a 24-inch sensor, and the console "
                     "posts its water warning and alarm off this height "
                     "except while a test holds them off."),
                    ("leaving at", "in/h", "leak",
                     f"{sumps.leak.get(int(num), 0.0):g}",
                     "How fast the water goes down. The test passes under "
                     "0.0104 in/h and aborts once a quarter inch has gone."),
                    ("water temp", "\u00b0F", "temp",
                     f"{sumps.temperature(num):.1f}",
                     "The water's temperature. The test aborts below 36 F "
                     "or above 115 F."),
                    ("changing at", "\u00b0F/h", "drift",
                     f"{sumps.drift.get(int(num), 0.0):g}",
                     "How fast the temperature is moving. The test waits "
                     "until it is under 5 F an hour, and gives up after "
                     "four hours.")):
                row = tk.Frame(inner, bg=CARD)
                row.pack(fill="x", padx=8, pady=(4, 0))
                tk.Label(row, text=text, bg=CARD, fg=FAINT, font=FONT_SM,
                         anchor="w").pack(side="left")
                var = tk.StringVar(value=value)
                box = tk.Entry(row, textvariable=var, width=6, bg="#24262b",
                               fg=INK, font=MONO_SM, insertbackground=INK,
                               relief="flat", justify="right")
                box.pack(side="left", padx=(4, 2))
                for event in ("<Return>", "<FocusOut>"):
                    box.bind(event, lambda _e, k=key, v=var:
                             self._set_sump(k, v))
                tk.Label(row, text=unit, bg=CARD, fg=FAINT,
                         font=FONT_SM).pack(side="left")
                Tip(box, tip)

        # the bottom margin, as a widget: with nothing holding the tile's
        # height open any more, the last row would otherwise sit on the edge
        tk.Frame(inner, bg=CARD, height=7).pack(fill="x")
        self._paint(cur)

    def _set_sump(self, key, var):
        try:
            value = float(var.get().strip() or 0)
        except ValueError:
            return
        sumps = self.console.sumps
        {"water": sumps.pour, "leak": sumps.set_leak,
         "temp": sumps.set_temperature,
         "drift": sumps.set_drift}[key](self.num, value)

    def _set_vac_leak(self, _e=None):
        text = self.leak_var.get().strip()
        try:
            self.console.vac_leak[int(self.num)] = float(text or 0)
        except ValueError:
            pass

    def _menu(self, states):
        m = tk.Menu(self, tearoff=0, bg="#2c2f35", fg=INK,
                    activebackground=ACCENT, activeforeground="#101216",
                    font=FONT)
        for s in states:
            m.add_radiobutton(label=s, variable=self.var, value=s,
                              command=lambda s=s: self._pick(s),
                              foreground=state_colour(s))
        m.tk_popup(self.pill.winfo_rootx(),
                   self.pill.winfo_rooty() + self.pill.winfo_height())

    def _pick(self, state):
        self.console.sensor_state[(self.mod, str(self.num))] = state
        self._paint(state)

    def _paint(self, state):
        colour = state_colour(state)
        self.pill.config(text=state.upper(), fg=colour)
        self.lamp.itemconfig(self._dot, fill=colour)


class InputTile(tk.Frame):
    """One external input: a lamp, its name, what the console has been told
    it is, and the contact.

    The contact is the whole of the control -- a dry pair of terminals on
    the I/O module, open or closed -- and the TYPE programmed at EXTERNAL
    INPUT SETUP is what the console makes of it: an alarm, a generator, a
    pump, an ALARM/TEST key, a vapour processor. See `inputs.py` and
    BENCH.md P1.
    """

    W = 178

    TIPS = {
        inputs.STANDARD:
            "A dry contact the console reports on: ON posts EXTERN INPUT "
            "ALARM, which the output relays, the auto-transmit signals and "
            "the input history all read.",
        inputs.GENERATOR:
            "The generator's run signal. \"The system runs a continuous leak "
            "test in the generator's tank(s) until the generator turns On. "
            "When the generator shuts Off, the system returns to its Leak "
            "Test mode\" -- and prints GENERATOR ON and OFF, and logs what "
            "each run drew for the generator report (404).",
        inputs.PUMP_SENSE:
            "\"Used to indicate the On/Off state of the pump.\" While it is "
            "ON the forecourt is busy: CSLD is denied its idle time, and the "
            "AUTOMATIC test option is offered on the tank it is assigned to.",
        inputs.ACK:
            "\"A remote pushbutton ... as an ALARM/TEST key.\" Press it and "
            "the console does what its own ALARM/TEST does: silences, "
            "acknowledges, prints the alarm report.",
        inputs.VAPOR_PROCESSOR:
            "The run signal from a vapour processor the console does not "
            "control (OPW, Hirt). ON starts a processor cycle; OFF ends it "
            "and records it for the ISD processor status.",
    }

    def __init__(self, parent, app, number):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.n = number
        eng = self.console.inputs

        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)
        inner.configure(height=S(62))

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {eng.label(number)[:16]}", bg=CARD, fg=INK,
                 font=FONT_HEAD, anchor="w").pack(side="left")

        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        kind, orient, tanks = eng.setup(number)
        self.kind = kind
        # the pill is packed FIRST so it always has its room; the type
        # note takes what is left and is the thing clipped, never the
        # control
        self.pill = tk.Label(bottom, text="", bg="#24262b", fg=FAINT,
                             font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                             cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self.click())
        Tip(self.pill, "The contact. " + (
            "A momentary button: click to press it."
            if kind == inputs.ACK else
            "Click to close or open it; what the terminals read follows "
            "the programmed orientation."))
        note = inputs.TYPE_WORDS[kind].lower()
        if tanks:
            note += " \u00b7 T" + ",".join(str(t) for t in tanks)
        elif kind in (inputs.GENERATOR, inputs.PUMP_SENSE):
            note += " \u00b7 all tanks"
        self.note = tk.Label(bottom, text=note[:22], bg=CARD, fg=FAINT,
                             font=FONT_SM, anchor="w")
        self.note.pack(side="left", fill="x", expand=True)
        Tip(self.note, self.TIPS[kind] + " Programmed "
            + ("normally closed" if orient == inputs.NORMALLY_CLOSED
               else "normally open") + ".")
        self.sync()

    def click(self):
        eng = self.console.inputs
        if self.kind == inputs.ACK:
            said = eng.press(self.n)
            self.app.log(f"-- input {self.n}: remote ALARM/TEST pressed"
                         + (f" -- {said}" if said else ""))
        else:
            on = not eng.is_on(self.n)
            said = eng.set(self.n, on)
            self.app.log(f"-- input {self.n}: {'ON' if on else 'OFF'} "
                         f"(contact {eng.contact(self.n).lower()})"
                         + (f" -- {said}" if said else ""))
        self.sync()

    def sync(self):
        eng = self.console.inputs
        on = eng.is_on(self.n)
        if self.kind == inputs.ACK:
            self.pill.config(text="PRESS", fg=ACCENT)
            self.lamp.itemconfig(self._dot, fill=WARN if on else OK)
            return
        # ON and OFF for every kind: the note says what is on, and I401's
        # STATUS column uses the same two words for all of them
        self.pill.config(text="ON" if on else "OFF", fg=WARN if on else FAINT)
        self.lamp.itemconfig(self._dot, fill=WARN if on else OK)


class RelayTile(tk.Frame):
    """One output relay: a lamp for the coil, its name, what it is wired
    as, and the contact.

    A relay is not a control on a real bench -- the console drives it --
    so the pill mostly SHOWS: the coil follows the relay's type, an
    assigned alarm, a pump request, a precision test. Two things a hand
    can do to one are here. TEST OUTPUT RELAYS holds a relay on, which is
    the panel's own function and the same dictionary; and a pump relay's
    contactor can weld, so the pump runs on with the coil dropped, which
    is the one condition a Pump Relay Monitor exists to catch. See
    `relays.py` and BENCH.md P2, P3.
    """

    W = 178

    def __init__(self, parent, app, number):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.n = number
        eng = self.console.outputs

        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)
        inner.configure(height=S(62))

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {eng.label(number)[:16]}", bg=CARD, fg=INK,
                 font=FONT_HEAD, anchor="w").pack(side="left")

        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        self.kind = eng.kind(number)
        self.pumpish = self.kind in (relays.PUMP_CONTROL, relays.PUMP_COMM)
        self.pill = tk.Label(bottom, text="", bg="#24262b", fg=FAINT,
                             font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                             cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self.click())
        if self.pumpish:
            Tip(self.pill, "The coil, following the pump request on tank "
                f"{eng.tank(number) or '?'}. Click to WELD the contactor: "
                "the pump runs on with the coil dropped, and a Pump Relay "
                "Monitor watching this relay counts its Stuck Delay and "
                "posts PUMP RELAY ALARM. Click again to free it.")
        else:
            Tip(self.pill, "The coil, following what the relay is assigned "
                "to. Click to hold it on by hand, which is what TEST OUTPUT "
                "RELAYS does from the panel; click again to let it go.")
        # short, because the pill beside it can read WELDED
        words = {relays.STANDARD: "standard", relays.MOMENTARY: "momentary",
                 relays.PUMP_CONTROL: "pump out",
                 relays.PUMP_COMM: "pump comm",
                 relays.VAPOR_PROCESSOR: "vapor proc"}
        note = words.get(self.kind, "standard")
        if self.pumpish and eng.tank(number):
            note += f" \u00b7 T{eng.tank(number)}"
        if eng.orientation(number) == relays.NORMALLY_CLOSED:
            note += " \u00b7 NC"
        self.note = tk.Label(bottom, text=note[:22], bg=CARD, fg=FAINT,
                             font=FONT_SM, anchor="w")
        self.note.pack(side="left", fill="x", expand=True)
        want = eng.assignment(number)
        Tip(self.note, relays.TYPE_WORDS.get(self.kind, "STANDARD")
            + ", " + ("normally closed" if eng.orientation(number)
                      == relays.NORMALLY_CLOSED else "normally open")
            + (". Assigned to alarm " + want[0][:2] + "/" + want[0][2:]
               + (" on every device" if want[1] == "00"
                  else f" on device {int(want[1])}") if want
               else ". No alarm assigned") + ".")
        self.sync()

    def click(self):
        eng = self.console.outputs
        if self.pumpish:
            stuck = not eng.is_welded("11", self.n)
            eng.weld("11", self.n, stuck)
            self.app.log(f"-- relay {self.n}: contactor "
                         + ("WELDED: the pump runs on with the coil dropped"
                            if stuck else "freed"))
        else:
            held = not eng.held(self.n)
            self.console.relays[self.n] = held
            self.app.log(f"-- relay {self.n}: "
                         + ("held ON by hand" if held else "released"))
        self.sync()

    def sync(self):
        eng = self.console.outputs
        on = eng.energised(self.n)
        if self.pumpish and eng.is_welded("11", self.n):
            self.pill.config(text="WELDED", fg=BAD)
            self.lamp.itemconfig(self._dot, fill=BAD)
            return
        if eng.held(self.n):
            self.pill.config(text="HELD", fg=WARN)
        else:
            self.pill.config(text="ON" if on else "OFF",
                             fg=WARN if on else FAINT)
        self.lamp.itemconfig(self._dot, fill=WARN if on else OK)


class ProcessorTile(tk.Frame):
    """The vapour processor: is it running?

    `vapor_processor_on` was wire-only: V80's cycle buffer, V82's five
    verdicts, the runtime fault and the category-33 PMC alarms all hang off
    the processor starting and stopping, and nothing on the bench started
    it. A processor the console does not control has its run signal on an
    external input (see `InputTile`); this is the other kind, and the
    switch a technician throws on it. BENCH.md V6.
    """

    W = 178

    def __init__(self, parent, app):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2,
                         height=S(62))
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {self.console.vapor_processor_type()[:16]}",
                 bg=CARD, fg=INK, font=FONT_HEAD, anchor="w").pack(side="left")
        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        self.pill = tk.Label(bottom, text="", bg="#24262b", fg=FAINT,
                             font=("Segoe UI", 8, "bold"), padx=8, pady=1,
                             cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self.toggle())
        Tip(self.pill, "Start or stop the processor. A run is a cycle: its "
                       "start, its length, the vapour pressure at each end, "
                       "and whether it outran V45's maximum runtime -- V80 "
                       "keeps the buffer, V82 draws its verdicts from it, "
                       "and the PMC alarms follow.")
        tk.Label(bottom, text="vapour processor", bg=CARD, fg=FAINT,
                 font=FONT_SM, anchor="w").pack(side="left")
        self.sync()

    def toggle(self):
        running = self.console.vp_started is None
        self.console.vapor_processor_on(running)
        self.app.log("-- vapour processor "
                     + ("started" if running else "stopped: cycle recorded"))
        self.sync()

    def sync(self):
        on = self.console.vp_started is not None
        self.pill.config(text="RUNNING" if on else "STOPPED",
                         fg=WARN if on else FAINT)
        self.lamp.itemconfig(self._dot, fill=WARN if on else OK)


# ---------------------------------------------------------------------------
# the lines and the dispensers


class LineCard(tk.Frame):
    """One programmed line: its name, its leak, its dispenser handle, and
    what the console can see of it -- the same state word and pressure the
    PRESSURE LINE LEAK DIAG screen shows.

    The handle is the whole of dispensing as far as a line is concerned.
    "A gross test always follows the completion of a dispense", and "if a
    dispense request occurs during any test, the test is aborted and the
    pump is turned On" -- so lifting it is how a technician makes the
    console DISPENSE, hanging it up is how the gross test starts, and
    leaving it up for a shift is how the Continuous Handle alarm is staged
    (577013-344 p.19: "A continuous Pump-in signal will activate a
    Continuous Handle alarm after 16 hours"). The engine had the setter
    all along; nothing on the bench called it. See BENCH.md L1.
    """

    # Wider than the other cards, because the readout is the diagnostic
    # screen's own two lines and those are twenty-four columns of Consolas.
    W = 320

    def __init__(self, parent, app, kind, n, label):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.kind, self.n = kind, n
        console = app.console
        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2,
                         height=S(106))
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        head = f"{kind.upper()} {n}  {label}".rstrip()
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {head[:16]}", bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left")
        leakf = tk.Frame(top, bg=CARD)
        leakf.pack(side="right")
        # a VLLD card carries two leaks on this row, so its first is
        # named for the pipe it is in and the unit is written once
        two = kind == "vlld"
        tk.Label(leakf, text="line" if two else "leak", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        leak = tk.StringVar(value=f"{console.line_leak.get((kind, n), 0.0):g}")
        e = tk.Entry(leakf, textvariable=leak, width=4 if two else 5,
                     bg="#24262b", fg=INK, font=MONO_SM, insertbackground=INK,
                     relief="flat", justify="right")
        e.pack(side="left", padx=(4, 2))
        if not two:
            tk.Label(leakf, text="g/h", bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left")
        Tip(e, "Gallons an hour leaving the line while it is holding "
               "pressure. 3.0 is the gross threshold, 0.2 the periodic, "
               "0.1 the annual. The operability procedure sets one as a "
               "calibrated orifice in ml/min at the pump pressure: at 10 "
               "psi, 3.0 gph is 189 ml/min.")

        def set_leak(*_a):
            text = leak.get().strip()
            if not text:
                console.line_leak[(kind, n)] = 0.0
                return
            try:
                console.line_leak[(kind, n)] = float(text)
            except ValueError:
                pass

        e.bind("<KeyRelease>", set_leak)

        if kind == "vlld":
            # The other piece of pipe. "After the system conducts a line
            # leak test, the line leak detector also runs a pump side test
            # for a pressure loss in the piping and connections between the
            # in-line check valve and the submersible pump" -- a leak there
            # never shows in a line test, which is what the pump side test
            # is for, and what nothing on the bench could stage. BENCH L6.
            tk.Label(leakf, text="pump", bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left", padx=(6, 0))
            pump_leak = tk.StringVar(
                value=f"{console.pump_leak.get((kind, n), 0.0):g}")
            pe = tk.Entry(leakf, textvariable=pump_leak, width=4,
                          bg="#24262b", fg=INK, font=MONO_SM,
                          insertbackground=INK, relief="flat",
                          justify="right")
            pe.pack(side="left", padx=(4, 2))
            tk.Label(leakf, text="g/h", bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left")
            Tip(pe, "Gallons an hour leaving the pipe BETWEEN the check "
                    "valve and the submersible pump. A line test never sees "
                    "it; the pump side test that follows every VLLD test "
                    "does, where PUMP SIDE TEST is enabled, and fails on its "
                    "own alarms -- GROSS, PERIODIC or ANNUAL PUMP TEST "
                    "FAIL. 576013-623: \"Failure to provide leak detection "
                    "capability for components prior to the VLLD check "
                    "valve could allow undetected product leakage.\"")

            def set_pump_leak(*_a):
                text = pump_leak.get().strip()
                try:
                    console.pump_leak[(kind, n)] = float(text) if text else 0.0
                except ValueError:
                    pass

            pe.bind("<KeyRelease>", set_pump_leak)
            self.pump_entry, self.set_pump_leak = pe, set_pump_leak

        # what the console sees: the diagnostic screen's own two lines,
        # left to right so nothing ever truncates from the wrong end
        mid = tk.Frame(inner, bg=CARD)
        mid.pack(fill="x", padx=8, pady=(4, 0))
        self.psi = tk.Label(mid, bg=CARD, fg=INK, font=MONO, anchor="w")
        self.psi.pack(side="left")
        self.pump = tk.Label(mid, bg=CARD, fg=MUTED, font=MONO_SM,
                             anchor="w")
        self.pump.pack(side="left", padx=(8, 0))
        self.state = tk.Label(mid, bg=CARD, fg=MUTED, font=MONO_SM,
                              anchor="w")
        self.state.pack(side="left", padx=(10, 0))

        # the handle, and under it the tests' last word
        foot = tk.Frame(inner, bg=CARD)
        foot.pack(fill="x", padx=8, pady=(3, 0))
        self.tests = tk.Label(foot, bg=CARD, fg=MUTED, font=MONO_SM,
                              anchor="w")
        self.tests.pack(side="left")
        self.pill = tk.Label(foot, text="HANDLE DOWN", bg="#24262b",
                             fg=FAINT, font=("Segoe UI", 8, "bold"),
                             padx=8, pady=1, cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self.toggle())
        Tip(self.pill, "The dispenser handle on this line. Lift it and the "
                       "console DISPENSES: the pump runs, any test on the "
                       "line is aborted, and the pressure is the pump's. "
                       "Hang it up and the gross test follows, as it does "
                       "on a real console. Leave it up for a whole shift "
                       "and the Continuous Handle alarm posts.")

        # the ground and the wire: a thermal slope, and the transducer
        wire = tk.Frame(inner, bg=CARD)
        wire.pack(fill="x", padx=8, pady=(3, 0))
        self.xducer = tk.Label(wire, text="TRANSDUCER OK", bg="#24262b",
                               fg=FAINT, font=("Segoe UI", 8, "bold"),
                               padx=8, pady=1, cursor="hand2")
        self.xducer.pack(side="right")
        self.xducer.bind("<Button-1>", lambda _e: self._xducer_menu())
        Tip(self.xducer, "The pressure transducer, as wired. OPEN is the "
                         "sensor not connected -- \"the pressure reading is "
                         "negative\" -- and posts the line's OPEN alarm; "
                         "SHORT is the pump-on and pump-off pressures "
                         "reading the same value, which posts its SHORT "
                         "alarm. 577013-344 pp.11-12.")
        tk.Label(wire, text="thermal", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        self.thermal_var = tk.StringVar(
            value=f"{console.lines.line(kind, n).thermal:g}")
        te = tk.Entry(wire, textvariable=self.thermal_var, width=5,
                      bg="#24262b", fg=INK, font=MONO_SM,
                      insertbackground=INK, relief="flat", justify="right")
        te.pack(side="left", padx=(4, 2))
        tk.Label(wire, text="psi/h", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        Tip(te, "A thermal slope on the line, in psi an hour, decaying as "
                "the fuel comes to the ground's temperature. \"Thermally-"
                "induced pressure change occurs when the ground temperature "
                "at the depth of the tank is different from the ground "
                "temperature at the line\" -- a falling slope looks exactly "
                "like a leak until it decays, which is what lengthens a "
                "test. Minus is cooling. 577013-344 pp.12-13.")

        def set_thermal(*_a):
            text = self.thermal_var.get().strip()
            try:
                console.lines.thermals(kind, n, float(text) if text
                                       and text not in ("-", ".") else 0.0)
            except ValueError:
                pass

        te.bind("<KeyRelease>", set_thermal)
        self.set_thermal = set_thermal

        # Four rows now, and their height is the fonts' to decide: a fixed
        # number that fits at 100% clips the last row at 133%, and it was
        # the last row that carried the transducer.
        inner.update_idletasks()
        need = sum(r.winfo_reqheight() for r in (top, mid, foot, wire)) + 24
        inner.configure(height=max(S(106), need))

        def sync():
            ln = console.lines.line(kind, n)
            run = console.leaks.active(kind, n)
            self.psi.config(text=f"{ln.reading:6.3f} PSI")
            fault = "noise" if ln.noise else ln.transducer
            self.xducer.config(
                text=("COMM NOISE" if fault == "noise" else
                      "TRANSDUCER " + (fault.upper() if fault else "OK")),
                fg=BAD if fault else FAINT)
            self.pump.config(text="PUMP ON" if ln.pump else "PUMP OFF",
                             fg=WARN if ln.pump else MUTED)
            colour = (WARN if ln.handle else
                      WARN if ln.running() or run else
                      BAD if (kind, n) in console.leaks.disabled else OK)
            self.state.config(text=ln.state[:14], fg=colour)
            self.lamp.itemconfig(self._dot, fill=colour)
            self.pill.config(text="HANDLE UP" if ln.handle else "HANDLE DOWN",
                             fg=WARN if ln.handle else FAINT)
            if (kind, n) in console.leaks.disabled:
                self.tests.config(text="SHUT DOWN by failed test", fg=BAD)
            else:
                res = console.leaks.results.get((kind, n)) or {}
                last = ", ".join(f"{k[:4]} {r.result[:4]}"
                                 for k, r in sorted(res.items()))
                self.tests.config(text=last or "no test data", fg=MUTED)

        self.sync = sync
        sync()

    def _xducer_menu(self):
        m = tk.Menu(self, tearoff=0, bg="#2c2f35", fg=INK,
                    activebackground=ACCENT, activeforeground="#101216",
                    font=FONT)
        choices = [("OK", None), ("OPEN", "open"), ("SHORT", "short")]
        if self.kind == "wplld":
            # a WPLLD's transducer talks over the STP's power line, and
            # noise on it is the one line alarm PLLD does not have
            choices.append(("NOISE", "noise"))
        for word, fault in choices:
            m.add_command(label=word,
                          command=lambda f=fault: self.fault_transducer(f))
        m.tk_popup(self.xducer.winfo_rootx(),
                   self.xducer.winfo_rooty() + self.xducer.winfo_height())

    def fault_transducer(self, fault):
        """Wire the transducer open or shorted, put noise on a WPLLD's
        power line, or put it right."""
        ln = self.app.console.lines.line(self.kind, self.n)
        ln.noise = fault == "noise"
        ln.transducer = None if fault == "noise" else fault
        self.app.log(f"-- {self.kind} line {self.n}: transducer "
                     + ("noise on the line (WPLLD COMM ALARM)"
                        if fault == "noise" else
                        fault.upper() if fault else "wired correctly"))
        self.sync()

    def toggle(self):
        """Lift the handle, or hang it up."""
        ln = self.app.console.lines.line(self.kind, self.n)
        self.app.console.lines.handle(self.kind, self.n, not ln.handle)
        self.app.log(f"-- {self.kind} line {self.n}: handle "
                     f"{'up' if ln.handle else 'down'}")
        self.sync()


class MeterCard(tk.Frame):
    """One dispenser meter: which tank it draws from, how fast it runs
    when the bench leaves it running, and a nozzle to lift for one sale.

    The gal/h entry is a rate, which is what a busy forecourt looks like
    from a tank's end. The sale is a TRANSACTION -- N gallons and then the
    nozzle hangs up -- which is what a POS sends a DIM, what BIR's meter
    events table is a table of, and what CSLD's idle time is the absence
    of. Both go through `meter_flow`; the sale is the rate switched on for
    exactly as long as its gallons take. BENCH.md D2.
    """

    # wide enough for the tank, the rate and the nozzle row at any font
    # scaling: at 250 the rate entry ran off the edge
    W = 280

    def __init__(self, parent, app, meter):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        console = app.console
        # the card is keyed by the meter's whole identity -- bus, slot,
        # position, meter -- so a second DIM's meter 1 is not the first's.
        # A bare number is the default board's meter of that number, which
        # is what every older caller meant by it. BENCH.md D4.
        self.meter = console.meter_key(meter)
        self.number = self.meter.meter
        meter = self.meter
        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2,
                         height=S(80))
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        # A meter is a fueling position and a meter number, not a number:
        # see meterid.py and FIDELITY G7. The bench sits on the default DIM,
        # which is where a site with one EDIM has its meters.
        key = console.meter_key(meter)
        name = tk.Label(top, text=f"FP {key.fp} METER {key.meter}", bg=CARD,
                        fg=INK, font=FONT_HEAD, anchor="w")

        tank = tk.StringVar(value=str(console.meters.get(meter, 0)))
        self.tank_var = tank
        gal = tk.StringVar(value=f"{console.meter_flow.get(meter, 0.0):g}")

        def remap(*_a):
            try:
                n = int(tank.get() or 0)
            except ValueError:
                return
            if n:
                console.meters[meter] = n
            else:
                console.meters.pop(meter, None)
            console.save()

        self.remap = remap

        def reflow(*_a):
            try:
                console.meter_flow[meter] = float(gal.get() or 0)
            except ValueError:
                pass

        # the controls are packed before the name, so at any font scaling
        # it is the name that gives way and never the rate entry
        right = tk.Frame(top, bg=CARD)
        right.pack(side="right")
        name.pack(side="left", fill="x")
        tk.Label(right, text="tank", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        spin = tk.Spinbox(right, from_=0, to=16, width=3, textvariable=tank,
                          bg="#24262b", fg=INK, font=MONO_SM,
                          buttonbackground=CARD_EDGE, relief="flat",
                          insertbackground=INK, command=remap)
        spin.pack(side="left", padx=(3, 8))
        spin.bind("<KeyRelease>", remap)
        tk.Label(right, text="gal/h", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        e = tk.Entry(right, textvariable=gal, width=5, bg="#24262b", fg=INK,
                     font=MONO_SM, insertbackground=INK, relief="flat",
                     justify="right")
        e.pack(side="left", padx=(3, 0))
        e.bind("<KeyRelease>", reflow)

        self.total = tk.Label(inner, bg=CARD, fg=MUTED, font=MONO_SM,
                              anchor="w")
        self.total.pack(fill="x", padx=8, pady=(3, 0))

        # the nozzle: how many gallons this sale is, and a pill to lift it
        foot = tk.Frame(inner, bg=CARD)
        foot.pack(fill="x", padx=8, pady=(2, 0))
        self.pill = tk.Label(foot, text="LIFT NOZZLE", bg="#24262b",
                             fg=ACCENT, font=("Segoe UI", 8, "bold"),
                             padx=8, pady=1, cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self.nozzle())
        Tip(self.pill, "One sale through this meter: the nozzle lifts, the "
                       "gallons go through at a nozzle's rate, the nozzle "
                       "hangs up. It leaves what a POS transaction leaves: "
                       "a start and an end in the meter events table, the "
                       "tank drawn down, the shift's sales booked, and a "
                       "DIM that has reported. Click again to hang up "
                       "early.")
        tk.Label(foot, text="sale", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        self.sale_var = tk.StringVar(value="10")
        se = tk.Entry(foot, textvariable=self.sale_var, width=5,
                      bg="#24262b", fg=INK, font=MONO_SM,
                      insertbackground=INK, relief="flat", justify="right")
        se.pack(side="left", padx=(4, 2))
        tk.Label(foot, text="gal", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        Tip(se, "Gallons for the next sale. A fill-up is ten to twenty; a "
                "nozzle passes about ten a minute.")
        self.progress = tk.Label(foot, text="", bg=CARD, fg=MUTED,
                                 font=MONO_SM, anchor="w")
        self.progress.pack(side="left", padx=(8, 0))

        def sync():
            through = console.bir.totals.get(meter, 0.0)
            where = console.meters.get(meter)
            self.total.config(text=(f"{through:,.1f} gal through"
                                    if where else "not mapped to a tank"))
            selling = meter in console.sales.running
            self.pill.config(text="HANG UP" if selling else "LIFT NOZZLE",
                             fg=WARN if selling else ACCENT)
            self.progress.config(text=console.sales.describe(meter)[:14])

        self.sync = sync
        sync()

    def nozzle(self):
        """Lift it for the sale in the box, or hang it up."""
        console = self.app.console
        if self.meter in console.sales.running:
            sale = console.sales.stop(self.meter)
            self.app.log(f"-- meter {self.meter}: nozzle hung up at "
                         f"{sale.sold:.1f} of {sale.gallons:.1f} gal")
        else:
            try:
                gallons = float(self.sale_var.get().strip() or 0)
            except ValueError:
                gallons = 0.0
            if gallons <= 0 or not console.meters.get(self.meter):
                self.bell()
                return
            console.sales.start(self.meter, gallons)
            self.app.log(f"-- meter {self.meter}: nozzle lifted for "
                         f"{gallons:g} gal")
        self.sync()


class VmcTile(tk.Frame):
    """One side of one vapour monitor controller, and what it is reporting.

    576013-610 Rev AC Table 29-23 is four rows and every one of them is a
    status this controller sends up the S-Link: the console does not decide
    a VMC alarm, it is told it. So the bench sets the status the way it sets
    a sensor's state and the console does the rest -- the alarm posts, both
    alarm histories carry it, and 412 has rows in it.
    """

    W = 178

    # BB1's own eight, which is the controller's whole vocabulary. The four
    # that are alarms are the four of Table 29-23.
    WORDS = ("IDLE", "RUNNING", "LAST TRANSACTION FAILED",
             "METER NOT CONNECTED", "FP SHUTDOWN WARNING",
             "FP SHUTDOWN ALARM", "VMC COMM TIMEOUT", "STATUS UNKNOWN")
    HEALTHY = ("IDLE", "RUNNING")
    WARNING = ("FP SHUTDOWN WARNING", "STATUS UNKNOWN",
               "LAST TRANSACTION FAILED")

    def __init__(self, parent, app, number, side):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.number = number
        self.side = side

        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2,
                         height=S(62))
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" VMC {number} SIDE {side}", bg=CARD, fg=INK,
                 font=FONT_HEAD, anchor="w").pack(side="left")

        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        tk.Label(bottom, text=self.console.vmc_serial(number), bg=CARD,
                 fg=FAINT, font=FONT_SM, anchor="w").pack(side="left")
        cur = self.console.vmc_side(number, side)["status"]
        self.var = tk.StringVar(value=cur)
        self.pill = tk.Label(bottom, text=self._short(cur), bg="#24262b",
                             fg=self._colour(cur),
                             font=("Segoe UI", 8, "bold"),
                             padx=8, pady=1, cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", self._menu)
        Tip(self.pill, "What this controller is reporting for this side. "
                       "Four of the eight are alarms -- Table 29-23's four "
                       "-- and picking one posts it in category 36.")
        self._paint(cur)

    @staticmethod
    def _short(word):
        """The pill is narrow and two of the eight are not."""
        return {"LAST TRANSACTION FAILED": "LAST TRANS FAIL",
                "METER NOT CONNECTED": "METER NC"}.get(word, word)

    @classmethod
    def _colour(cls, word):
        if word in cls.HEALTHY:
            return OK
        return WARN if word in cls.WARNING else BAD

    def _menu(self, _e=None):
        m = tk.Menu(self, tearoff=0, bg="#2c2f35", fg=INK,
                    activebackground=ACCENT, activeforeground="#101216",
                    font=FONT)
        for word in self.WORDS:
            m.add_radiobutton(label=word, variable=self.var, value=word,
                              command=lambda w=word: self._pick(w),
                              foreground=self._colour(word))
        m.tk_popup(self.pill.winfo_rootx(),
                   self.pill.winfo_rooty() + self.pill.winfo_height())

    def _pick(self, word):
        self.console.set_vmc_status(self.number, self.side, word)
        self._paint(word)
        self.app.log(f"-- VMC {self.number} side {self.side}: "
                     + word.lower())

    def _paint(self, word):
        self.pill.config(text=self._short(word), fg=self._colour(word))
        self.lamp.itemconfig(self._dot, fill=self._colour(word))


class IsdTile(tk.Frame):
    """One ISD monitoring test: a lamp and its outcome.

    PASS is the healthy state; WARN posts the warning alarm; FAIL posts the
    failure, and a failure is a site shutdown until it clears or a
    technician overrides it from the panel.
    """

    W = 178

    def __init__(self, parent, app, test, label):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.test = test

        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2,
                         height=S(62))
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {label}", bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left")

        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        tk.Label(bottom, text="ISD test", bg=CARD, fg=FAINT,
                 font=FONT_SM, anchor="w").pack(side="left")
        cur = self.console.isd_forced.get(test)
        word = {"warn": "WARN", "fail": "FAIL"}.get(cur, "PASS")
        self.pill = tk.Label(bottom, text=word, bg="#24262b",
                             fg=self._colour(word),
                             font=("Segoe UI", 8, "bold"),
                             padx=8, pady=1, cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", self._menu)
        self._paint(word)

    @staticmethod
    def _colour(word):
        return {"PASS": OK, "WARN": WARN}.get(word, BAD)

    def _menu(self, _e=None):
        m = tk.Menu(self, tearoff=0, bg="#2c2f35", fg=INK,
                    activebackground=ACCENT, activeforeground="#101216",
                    font=FONT)
        for word in ("PASS", "WARN", "FAIL"):
            m.add_command(label=word,
                          command=lambda w=word: self._pick(w))
        m.tk_popup(self.pill.winfo_rootx(),
                   self.pill.winfo_rooty() + self.pill.winfo_height())

    def _pick(self, word):
        state = {"WARN": "warn", "FAIL": "fail"}.get(word)
        self.console.isd_force(self.test, state)
        self._paint(word)
        if word == "FAIL":
            self.app.log(f"-- ISD {self.test} FAIL: site shutdown "
                         "(ALARM/TEST x3 on the panel to override)")
        else:
            self.app.log(f"-- ISD {self.test}: {word.lower()}")

    def _paint(self, word):
        self.pill.config(text=word, fg=self._colour(word))
        self.lamp.itemconfig(self._dot, fill=self._colour(word))


# ---------------------------------------------------------------------------
# the forecourt's own day


def meter_choices(console):
    """[(MeterId, caption)] for every meter mapped to a tank.

    The caption is what a technician reads off I7B100 -- the position, the
    meter and the product it draws -- because that is how the blend editor
    below has to name a component: "70% of FP 1 M 1 REGULAR UNLEADED" is a
    sentence, and "70% of meter 1" is not.
    """
    out = []
    for meter in sorted(console.meters):
        tank = console.meters.get(meter)
        label = (console.text("602", tank) or f"TANK {tank}").strip()
        out.append((meter, f"FP {meter.fp} M {meter.meter}  {label}"))
    return out


def free_meter(console):
    """A meter number nothing is using, for a new blend's own nozzle."""
    taken = {m.meter for m in console.meters} | {m.meter
                                                 for m in console.blends}
    for number in range(1, 17):
        if number not in taken:
            return console.meter_key(number)
    return None


class TrafficCurve(tk.Canvas):
    """The day as twenty-four bars, with the hour the console is in lit.

    The two pickers above set a number and a word; this is what those two
    MEAN, and it is the only control on the bench whose whole job is to
    make a setting legible before a day of it has run.
    """

    W, H = 620, 92

    def __init__(self, parent, app):
        super().__init__(parent, width=S(self.W), height=S(self.H),
                         bg=CARD,
                         highlightthickness=0)
        self.app = app
        self.bind("<Configure>", lambda _e: self.sync())
        Tip(self, "Cars an hour across the console's own day, midnight at "
                  "the left. The lit bar is the hour it is now. The height "
                  "is the SHAPE and the picker above it is the VOLUME, so "
                  "a truck stop is a night-heavy shape at whatever volume "
                  "it does rather than a busy forecourt with its hours "
                  "moved.")

    def sync(self):
        import time as _time
        self.delete("all")
        c = self.app.console
        traffic = c.traffic
        weights = traffic.weights()
        total = float(sum(weights)) or 1.0
        peak = max(weights) / total * traffic.cars_per_day
        width = max(self.winfo_width(), S(120))
        # Both from the canvas it actually got, not from the constants it
        # was drawn to: the canvas is made at S(W) x S(H) for the screen in
        # use, and a `sync` laying its bars out against the raw 92 left the
        # bottom 68 of a 138px widget blank with every bar squashed into
        # the top half.
        height = max(self.winfo_height(), S(self.H))
        # the top strip is the caption's, so a peak bar never runs under it
        pad, floor, ceiling = S(6), height - S(22), S(16)
        slot = (width - pad * 2) / 24.0
        now_hour = _time.localtime(_time.time()
                                   + c.clock_offset).tm_hour
        for hour, weight in enumerate(weights):
            cars = weight / total * traffic.cars_per_day
            tall = 0.0 if peak <= 0 else (cars / peak) * (floor - ceiling)
            x0 = pad + hour * slot
            x1 = x0 + slot - 2
            lit = hour == now_hour
            colour = ACCENT if lit else (FUEL if traffic.on else CARD_EDGE)
            self.create_rectangle(x0, floor - tall, x1, floor,
                                  fill=colour, outline="")
            if hour % 6 == 0:
                self.create_text((x0 + x1) / 2, floor + S(8),
                                 text=f"{hour:02d}", fill=FAINT,
                                 font=("Segoe UI", 7))
        self.create_line(pad, floor, width - pad, floor, fill=CARD_EDGE)
        cars_now = traffic.arrival_rate()
        # under the floor line, beside the hour marks: above it, a
        # night-heavy shape puts its tallest bars exactly there
        self.create_text(width - pad, floor + S(8), anchor="e",
                         text=(f"{cars_now:,.0f} cars/h now"
                               if traffic.on else "off"),
                         fill=MUTED if traffic.on else FAINT,
                         font=MONO_SM)


class GradeRow(tk.Frame):
    """One grade the site sells: its share of the cars, and what that costs.

    The share is a weight and not a percentage, because the site's own
    tanks decide which grades are on offer at all: a truck stop with no
    premium does not lose those customers, they buy what is there. So the
    weights are normalised over the families the site HAS, and the row
    shows the percentage that comes out the other end.
    """

    def __init__(self, parent, app, fam, meters):
        super().__init__(parent, bg=CARD)
        from . import traffic as _traffic
        self.app = app
        self.fam = fam
        self.meters = meters
        c = app.console
        tk.Label(self, text=_traffic.FAMILY_WORDS.get(fam, fam.upper()),
                 bg=CARD, fg=INK, font=FONT_HEAD, width=11, anchor="w"
                 ).pack(side="left", padx=(8, 0))
        self.var = tk.StringVar(
            value=f"{c.traffic.mix.get(fam, 0.0):g}")
        entry = tk.Entry(self, textvariable=self.var, width=5, bg="#24262b",
                         fg=INK, font=MONO_SM, insertbackground=INK,
                         relief="flat", justify="right")
        entry.pack(side="left", padx=(4, 2))
        entry.bind("<KeyRelease>", self._reweight)
        tk.Label(self, text="share", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        self.note = tk.Label(self, bg=CARD, fg=MUTED, font=MONO_SM,
                             anchor="w")
        self.note.pack(side="left", fill="x", expand=True, padx=(10, 8))
        Tip(entry, "How many of the cars want this grade, relative to the "
                   "others. The site's own tanks decide which grades are on "
                   "offer, so these are shared out over the ones it has.")
        self.sync()

    def _reweight(self, _e=None):
        try:
            self.app.console.traffic.mix[self.fam] = float(self.var.get()
                                                           or 0)
        except ValueError:
            return
        self.app.console.save()

    def sync(self):
        c = self.app.console
        traffic = c.traffic
        selling = traffic.selling()
        total = sum(max(0.0, float(traffic.mix.get(f, 0.0)))
                    for f in selling) or 1.0
        share = max(0.0, float(traffic.mix.get(self.fam, 0.0))) / total
        nozzles = len(self.meters)
        # this grade's OWN gallons, not the site's total split by how many
        # cars want it -- a diesel customer takes four times what a regular
        # one does. See `Traffic.grade_gallons`.
        gallons = traffic.grade_gallons(self.fam) if traffic.on else 0.0
        self.note.config(
            text=f"{share * 100:4.0f}%  {nozzles} nozzle"
                 f"{'' if nozzles == 1 else 's'}"
                 + (f"  {gallons:,.0f} gal/day" if traffic.on else ""))


class BlendCard(tk.Frame):
    """One blended grade button on a dispenser.

    The console never learns this exists, and that is the fidelity
    point rather than a shortcut. 576013-818 p.12-7: "A tank can be mapped
    to only one meter for a given Fuel Position (FP)", and the only
    blender support anywhere in the manuals is a DIM parameter saying how
    to read the POS -- p.10-2's "T Blender Only Site" and "P Plus one
    dispensers at site". The mixing is in the dispenser. What reaches the
    console is the components, each reported against its own meter and its
    own tank, so BIR, CSLD and the meter map need to know nothing at all.

    Which means a blend on this bench is exactly what a blend is on a
    site: a grade button that runs two meters at a ratio.
    """

    W = 430

    def __init__(self, parent, app, meter):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.meter = meter
        c = app.console
        # No fixed height and no `pack_propagate(False)`, unlike the tiles
        # that hold one fixed row: this card GROWS with its components, and
        # a frame with propagation off and no height set is a frame with no
        # height at all -- the first version of this rendered as a one-pixel
        # line under its own heading.
        inner = tk.Frame(self, bg=CARD, width=S(self.W) - 2)
        inner.pack(fill="both", expand=True)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.label_var = tk.StringVar(value=c.blend_label(meter) or "MID")
        entry = tk.Entry(top, textvariable=self.label_var, width=12,
                         bg="#24262b", fg=INK, font=MONO_SM,
                         insertbackground=INK, relief="flat")
        entry.pack(side="left")
        entry.bind("<KeyRelease>", self._rename)
        Tip(entry, "What this grade is called on the dispenser. The console "
                   "never sees it -- it sees the two component meters -- but "
                   "the traffic generator reads it to decide who buys it, so "
                   "a label with E15, PLUS or MID in it sells as a "
                   "mid-grade.")
        tk.Label(top, text=f"NOZZLE FP {meter.fp} M {meter.meter}", bg=CARD,
                 fg=FAINT, font=FONT_SM).pack(side="left", padx=(8, 0))
        kill = tk.Label(top, text="REMOVE", bg=CARD, fg=BAD, font=FONT_SM,
                        cursor="hand2")
        kill.pack(side="right")
        kill.bind("<Button-1>", lambda _e: self._remove())

        self.rows = tk.Frame(inner, bg=CARD)
        self.rows.pack(fill="x", padx=8, pady=(4, 0))

        foot = tk.Frame(inner, bg=CARD)
        foot.pack(fill="x", padx=8, pady=(2, 7))
        self.add = tk.Label(foot, text="+ COMPONENT", bg="#24262b",
                            fg=ACCENT, font=("Segoe UI", 8, "bold"),
                            padx=8, pady=1, cursor="hand2")
        self.add.pack(side="left")
        self.add.bind("<Button-1>", lambda _e: self._add_part())
        Tip(self.add, "Add a component meter. A blend runs real METERS at a "
                      "ratio, so there has to be a meter mapped to a tank "
                      "before there is anything to add -- map one on the "
                      "Site view, under Dispensers.")
        self.note = tk.Label(foot, bg=CARD, fg=MUTED, font=MONO_SM,
                             anchor="e")
        self.note.pack(side="right", fill="x", expand=True)
        self._draw_parts()

    # ---- the store ----------------------------------------------------------
    def _blend(self):
        return self.app.console.blends.setdefault(
            self.meter, {"label": "MID", "parts": []})

    def _rename(self, _e=None):
        self._blend()["label"] = self.label_var.get()
        self.app.console.save()
        self.sync()

    def _remove(self):
        self.app.console.blends.pop(self.meter, None)
        self.app.console.save()
        self.app.refresh_traffic()

    def _add_part(self):
        choices = meter_choices(self.app.console)
        if not choices:
            # This used to return in silence, which made + COMPONENT a pill
            # that could be clicked all day and did nothing -- and it is
            # exactly the pill somebody reaches for on a console with no DIM
            # in it, because a blend is the only thing on this view that
            # LOOKS like it could be set up without one.
            self.app.log("-- a blend runs component meters, and no meter is "
                         "mapped to a tank yet: map one on the Site view, "
                         "under Dispensers")
            return
        parts = self._blend()["parts"]
        used = {str(row[0]) for row in parts}
        spare = [m for m, _caption in choices if str(m) not in used]
        if not spare:
            # The other silent return, and the same shape as the one above:
            # every mapped meter is already a component of this blend, the
            # loop fell off its end, and save() and _draw_parts() ran on an
            # unchanged list. Ten clicks, no rows, nothing said.
            self.app.log(f"-- every mapped meter is already a component of "
                         f"{self._blend().get('label') or 'this blend'}: "
                         f"map another on the Site view, under Dispensers")
            return
        parts.append([str(spare[0]), 0.0])
        # An even split, so the numbers on the card are the numbers the
        # engine runs. The share used to be 50 for every component -- the
        # ternary that chose it had the same value in both arms -- so a
        # three-way blend showed three rows of "50% of" totalling 150 while
        # `blend_meters` quietly renormalised them to a third each. A card
        # whose percentages are not the percentages is worse than no card.
        for row in parts:
            row[1] = round(100.0 / len(parts), 1)
        self.app.console.save()
        self._draw_parts()

    def _draw_parts(self):
        for child in self.rows.winfo_children():
            child.destroy()
        choices = meter_choices(self.app.console)
        captions = {str(m): cap for m, cap in choices}
        names = [cap for _m, cap in choices]
        by_caption = {cap: str(m) for m, cap in choices}
        parts = self._blend()["parts"]
        # A component whose meter has since been unmapped on the Site view
        # is still stored here, and it used to fall back to the FIRST
        # caption in the list -- so a blend of meters 8 and 1, with 8
        # unmapped, drew two rows both reading "FP 1 M 1 REGULAR UNLEADED",
        # neither saying one was dead, over an engine running the nozzle
        # 100% off tank 1. Worse, touching either row's percentage wrote
        # that caption back, silently repointing the blend at a meter
        # nobody chose. A dead component now says so and keeps its name.
        for row in parts:
            if str(row[0]) not in captions:
                dead = f"{row[0]}  -- NOT MAPPED"
                captions[str(row[0])] = dead
                by_caption[dead] = str(row[0])
                names.append(dead)
        # A blend with nothing to be made of is not the same as a blend
        # nobody has filled in yet, and the card said the same thing for
        # both. The first one cannot be fixed on this view at all.
        self.add.config(fg=ACCENT if choices else FAINT,
                        cursor="hand2" if choices else "arrow")
        if not choices:
            tk.Label(self.rows, text="no meter is mapped to a tank, so "
                     "there is no component to blend", bg=CARD, fg=WARN,
                     font=FONT_SM, anchor="w", justify="left",
                     wraplength=self.W - 24).pack(fill="x")
        elif not parts:
            tk.Label(self.rows, text="no components yet", bg=CARD, fg=FAINT,
                     font=FONT_SM, anchor="w").pack(fill="x")
        for index, row in enumerate(list(parts)):
            line = tk.Frame(self.rows, bg=CARD)
            line.pack(fill="x", pady=1)
            pct = tk.StringVar(value=f"{float(row[1]):g}")
            entry = tk.Entry(line, textvariable=pct, width=5, bg="#24262b",
                             fg=INK, font=MONO_SM, insertbackground=INK,
                             relief="flat", justify="right")
            entry.pack(side="left")
            tk.Label(line, text="% of", bg=CARD, fg=FAINT,
                     font=FONT_SM).pack(side="left", padx=(3, 4))
            pick = tk.StringVar(value=captions.get(str(row[0]),
                                                   names[0] if names else ""))
            menu = tk.OptionMenu(line, pick, *(names or [""]))
            # a width, or the menu takes the width of its longest caption
            # and the card grows to whatever somebody called a tank
            menu.config(bg="#24262b", fg=INK, font=MONO_SM, relief="flat",
                        highlightthickness=0, activebackground=CARD_EDGE,
                        anchor="w", padx=4, pady=0, width=26)
            menu["menu"].config(bg="#24262b", fg=INK, font=MONO_SM)
            menu.pack(side="left", fill="x", expand=True)
            drop = tk.Label(line, text="x", bg=CARD, fg=FAINT,
                            font=FONT_SM, cursor="hand2", padx=4)
            drop.pack(side="right")

            def rewrite(*_a, i=index, p=pct, k=pick):
                rows = self._blend()["parts"]
                if i >= len(rows):
                    return
                try:
                    rows[i][1] = float(p.get() or 0)
                except ValueError:
                    pass
                rows[i][0] = by_caption.get(k.get(), rows[i][0])
                self.app.console.save()
                self.sync()

            def cut(_e=None, i=index):
                rows = self._blend()["parts"]
                if i < len(rows):
                    rows.pop(i)
                self.app.console.save()
                self._draw_parts()

            entry.bind("<KeyRelease>", rewrite)
            pick.trace_add("write", rewrite)
            drop.bind("<Button-1>", cut)
        self.sync()

    def sync(self):
        c = self.app.console
        parts = c.blend_meters(self.meter)
        if not parts:
            self.note.config(text="not a blend yet", fg=FAINT)
            return
        tanks = c.blend_parts(self.meter)
        words = " + ".join(f"{frac * 100:.0f}% T{tank}"
                           for tank, frac in tanks)
        running = c.sales.describe(self.meter)
        self.note.config(text=running or words,
                         fg=ACCENT if running else MUTED)
