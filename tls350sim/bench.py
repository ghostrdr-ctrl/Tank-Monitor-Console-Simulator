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

FONT = ("Segoe UI", 9)
FONT_SM = ("Segoe UI", 8)
FONT_HEAD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 10, "bold")
MONO = ("Consolas", 9)
MONO_SM = ("Consolas", 8)


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
        self.current = None
        for name in names:
            holder = tk.Frame(self, bg=BG)
            holder.pack(side="left", padx=(0, 4))
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

    def __init__(self, parent, tile_w, pad=6):
        super().__init__(parent, bg=BG)
        self.tile_w = tile_w
        self.pad = pad
        self.tiles = []
        self._cols = 0
        self.bind("<Configure>", self._reflow)

    def add(self, tile):
        self.tiles.append(tile)
        self._place(force=True)

    def _reflow(self, _e=None):
        self._place()

    def _place(self, force=False):
        width = self.winfo_width() or self.winfo_reqwidth()
        cols = max(1, (width + self.pad) // (self.tile_w + self.pad))
        if cols == self._cols and not force:
            return
        self._cols = cols
        for i, t in enumerate(self.tiles):
            t.grid(row=i // cols, column=i % cols,
                   padx=(0, self.pad), pady=(0, self.pad), sticky="nw")


class HStrip(tk.Frame):
    """A horizontal strip of cards with its own scrollbar.

    Tanks are tall and narrow, so they go beside each other; when there
    are more than the window is wide, the strip scrolls sideways and the
    wheel scrolls it while the pointer is over it.
    """

    def __init__(self, parent, height):
        super().__init__(parent, bg=BG)
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
        self.cv.configure(scrollregion=self.cv.bbox("all"))

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

        self.cv = tk.Canvas(inner, width=self.W - 18, height=self.CV_H,
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

        self.redraw()

    # -- state ------------------------------------------------------------

    def _levels(self):
        st = self.console.tank_level.setdefault(
            self.n, {"volume": self.full * 0.6, "water": 0.0})
        fuel_h = (st["volume"] / self.full) * self.D
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
        key = (round(fuel_h, 2), round(water_h, 2),
               self.n in self.console.probe_out)
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
        self._float(cx, yf, 15, 6, "#8d8a80" if out else FLOAT_P, "pfloat")
        self._float(cx, yw, 12, 5, "#93a2b5" if out else FLOAT_W, "wfloat")

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
            st["volume"] = self.full * h / self.D
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
        if self.dragging:
            return
        _st, fuel_h, water_h = self._levels()
        key = (round(fuel_h, 2), round(water_h, 2),
               self.n in self.console.probe_out)
        if self._last != key:
            self.redraw()


# ---------------------------------------------------------------------------
# the sensors


class SensorTile(tk.Frame):
    """One sensor: a lamp, a name, what kind of thing it is, and a state
    button that offers only the states this sensor's own type can be in."""

    W = 178

    def __init__(self, parent, app, mod, num, label):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        self.console = app.console
        self.mod = mod
        self.num = num

        inner = tk.Frame(self, bg=CARD, width=self.W - 2)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)
        inner.configure(height=62)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        self.lamp = tk.Canvas(top, width=10, height=10, bg=CARD,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=(2, 0))
        self._dot = self.lamp.create_oval(1, 1, 9, 9, fill=OK, outline="")
        tk.Label(top, text=f" {label[:16]}", bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left")

        bottom = tk.Frame(inner, bg=CARD)
        bottom.pack(fill="x", padx=8, pady=(2, 0))
        note = self.app._sensor_type_note(mod, num).strip()
        tk.Label(bottom, text=note[:20] or mod, bg=CARD, fg=FAINT,
                 font=FONT_SM, anchor="w").pack(side="left")

        states = ["normal"] + list(self.console.sensor_states(mod, num))
        cur = self.console.sensor_state.get((mod, str(num)), "normal")
        if cur not in states:
            cur = "normal"
            self.console.sensor_state[(mod, str(num))] = cur
        self.var = tk.StringVar(value=cur)
        self.pill = tk.Label(bottom, text=cur.upper(), bg="#24262b",
                             fg=state_colour(cur), font=("Segoe UI", 8,
                                                         "bold"),
                             padx=8, pady=1, cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda _e: self._menu(states))
        Tip(self.pill, "What this sensor is sensing. Only the states its "
                       "own type can report are on the list: a single-float "
                       "sump sensor cannot say WATER, so WATER is not "
                       "offered.")
        self._paint(cur)

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


# ---------------------------------------------------------------------------
# the lines and the dispensers


class LineCard(tk.Frame):
    """One programmed line: its name, its leak, and what its tests said."""

    W = 250

    def __init__(self, parent, app, kind, n, label):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        self.app = app
        console = app.console
        inner = tk.Frame(self, bg=CARD, width=self.W - 2, height=58)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        head = f"{kind.upper()} {n}  {label}".rstrip()
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        tk.Label(top, text=head[:24], bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left")
        leakf = tk.Frame(top, bg=CARD)
        leakf.pack(side="right")
        tk.Label(leakf, text="leak", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")
        leak = tk.StringVar(value=f"{console.line_leak.get((kind, n), 0.0):g}")
        e = tk.Entry(leakf, textvariable=leak, width=5, bg="#24262b", fg=INK,
                     font=MONO_SM, insertbackground=INK, relief="flat",
                     justify="right")
        e.pack(side="left", padx=(4, 2))
        tk.Label(leakf, text="g/h", bg=CARD, fg=FAINT,
                 font=FONT_SM).pack(side="left")

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

        self.state = tk.Label(inner, bg=CARD, fg=MUTED, font=MONO_SM,
                              anchor="w")
        self.state.pack(fill="x", padx=8, pady=(3, 0))

        def sync():
            run = console.leaks.active(kind, n)
            if run:
                self.state.config(text=f"testing {run.rate_key}", fg=WARN)
            elif (kind, n) in console.leaks.disabled:
                self.state.config(text="SHUT DOWN by failed test", fg=BAD)
            else:
                res = console.leaks.results.get((kind, n)) or {}
                last = ", ".join(f"{k[:4]} {r.result[:4]}"
                                 for k, r in sorted(res.items()))
                self.state.config(text=last or "no test data", fg=MUTED)

        self.sync = sync
        sync()


class MeterCard(tk.Frame):
    """One dispenser meter: which tank it draws from, and how fast."""

    W = 250

    def __init__(self, parent, app, meter):
        super().__init__(parent, bg=CARD_EDGE, padx=1, pady=1)
        console = app.console
        inner = tk.Frame(self, bg=CARD, width=self.W - 2, height=58)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x", padx=8, pady=(7, 0))
        tk.Label(top, text=f"METER {meter}", bg=CARD, fg=INK, font=FONT_HEAD,
                 anchor="w").pack(side="left")

        tank = tk.StringVar(value=str(console.meters.get(meter, 0)))
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

        def reflow(*_a):
            try:
                console.meter_flow[meter] = float(gal.get() or 0)
            except ValueError:
                pass

        right = tk.Frame(top, bg=CARD)
        right.pack(side="right")
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

        def sync():
            through = console.bir.totals.get(meter, 0.0)
            where = console.meters.get(meter)
            self.total.config(text=(f"{through:,.1f} gal through"
                                    if where else "not mapped to a tank"))

        self.sync = sync
        sync()


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

        inner = tk.Frame(self, bg=CARD, width=self.W - 2, height=62)
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
