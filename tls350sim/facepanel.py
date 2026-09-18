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
"""The two things on the face that are drawn rather than borrowed from Tk.

The display is a two-line character LCD of the reflective STN kind: a pale
grey-green glass, each character a 5 x 7 dot matrix in dark indigo. The
dots are square and touch, edge to edge, with no gap between them -- what
separates one character from the next is the gap between cells, not gaps
inside them -- and the dots run right to the edge of the glass, with no
border of dead glass round the outside. Tk has no such widget, so the
glass is a canvas with a small rectangle for every dot.

The keys are moulded keycaps: a flat top standing up from a black grid on
four sloped sides, the top and left sides catching the light and the right
and bottom in shade. Tk's raised relief is one pixel of that; the keycap
here draws the four sides as trapezoids and sinks when pressed.

A keycap's legend can run to two lines, and on the number keys the two are
not the same size: the letters above are small and the digit below is
about twice as tall, so each line is drawn with its own font and
`big_line` says which one gets the larger.

Two of the legends are not type at all. The arrows on the cursor keys are
long solid arrows, half the width of the keycap, far heavier and longer
than any arrow glyph a text font would give; and the open box that stands
for a space on the zero key is a square of cap height. Both are drawn as
canvas shapes so that they carry the weight they do on the moulding.

Everything is a canvas primitive; nothing is loaded from disk.
"""
import tkinter as tk

# ---- the 5 x 7 character set of a character LCD --------------------------
# Rows top to bottom, five columns each, '#' for a lit dot. This is the shape
# of the standard character-generator ROM every 5 x 7 module ships with; the
# two arrows are the keypad's own legends.
_GLYPHS = {
    " ": ["     "] * 7,
    "!": ["  #  ", "  #  ", "  #  ", "  #  ", "     ", "     ", "  #  "],
    '"': [" # # ", " # # ", " # # ", "     ", "     ", "     ", "     "],
    "#": [" # # ", " # # ", "#####", " # # ", "#####", " # # ", " # # "],
    "$": ["  #  ", " ####", "# #  ", " ### ", "  # #", "#### ", "  #  "],
    "%": ["##   ", "##  #", "   # ", "  #  ", " #   ", "#  ##", "   ##"],
    "&": [" ##  ", "#  # ", "# #  ", " #   ", "# # #", "#  # ", " ## #"],
    "'": [" ##  ", "  #  ", " #   ", "     ", "     ", "     ", "     "],
    "(": ["   # ", "  #  ", " #   ", " #   ", " #   ", "  #  ", "   # "],
    ")": [" #   ", "  #  ", "   # ", "   # ", "   # ", "  #  ", " #   "],
    "*": ["     ", "  #  ", "# # #", " ### ", "# # #", "  #  ", "     "],
    "+": ["     ", "  #  ", "  #  ", "#####", "  #  ", "  #  ", "     "],
    ",": ["     ", "     ", "     ", "     ", " ##  ", "  #  ", " #   "],
    "-": ["     ", "     ", "     ", "#####", "     ", "     ", "     "],
    ".": ["     ", "     ", "     ", "     ", "     ", " ##  ", " ##  "],
    "/": ["     ", "    #", "   # ", "  #  ", " #   ", "#    ", "     "],
    "0": [" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "],
    "1": ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "2": [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
    "3": ["#####", "   # ", "  #  ", "   # ", "    #", "#   #", " ### "],
    "4": ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
    "5": ["#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "],
    "6": ["  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "],
    "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
    "8": [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
    "9": [" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "],
    ":": ["     ", " ##  ", " ##  ", "     ", " ##  ", " ##  ", "     "],
    ";": ["     ", " ##  ", " ##  ", "     ", " ##  ", "  #  ", " #   "],
    "<": ["   # ", "  #  ", " #   ", "#    ", " #   ", "  #  ", "   # "],
    "=": ["     ", "     ", "#####", "     ", "#####", "     ", "     "],
    ">": ["#    ", " #   ", "  #  ", "   # ", "  #  ", " #   ", "#    "],
    "?": [" ### ", "#   #", "    #", "   # ", "  #  ", "     ", "  #  "],
    "@": [" ### ", "#   #", "    #", " # ##", "# # #", "# # #", " ### "],
    "A": [" ### ", "#   #", "#   #", "#   #", "#####", "#   #", "#   #"],
    "B": ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
    "C": [" ### ", "#   #", "#    ", "#    ", "#    ", "#   #", " ### "],
    "D": ["###  ", "#  # ", "#   #", "#   #", "#   #", "#  # ", "###  "],
    "E": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
    "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
    "G": [" ### ", "#   #", "#    ", "# ###", "#   #", "#   #", " ####"],
    "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "I": [" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "J": ["  ###", "   # ", "   # ", "   # ", "   # ", "#  # ", " ##  "],
    "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
    "M": ["#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"],
    "N": ["#   #", "#   #", "##  #", "# # #", "#  ##", "#   #", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
    "Q": [" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #"],
    "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "V": ["#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  "],
    "W": ["#   #", "#   #", "#   #", "# # #", "# # #", "# # #", " # # "],
    "X": ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
    "Y": ["#   #", "#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  "],
    "Z": ["#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"],
    "[": [" ### ", " #   ", " #   ", " #   ", " #   ", " #   ", " ### "],
    "\\": ["     ", "#    ", " #   ", "  #  ", "   # ", "    #", "     "],
    "]": [" ### ", "   # ", "   # ", "   # ", "   # ", "   # ", " ### "],
    "^": ["  #  ", " # # ", "#   #", "     ", "     ", "     ", "     "],
    "_": ["     ", "     ", "     ", "     ", "     ", "     ", "#####"],
    "`": [" #   ", "  #  ", "   # ", "     ", "     ", "     ", "     "],
    "a": ["     ", "     ", " ### ", "    #", " ####", "#   #", " ####"],
    "b": ["#    ", "#    ", "# ## ", "##  #", "#   #", "#   #", "#### "],
    "c": ["     ", "     ", " ### ", "#    ", "#    ", "#   #", " ### "],
    "d": ["    #", "    #", " ## #", "#  ##", "#   #", "#   #", " ####"],
    "e": ["     ", "     ", " ### ", "#   #", "#####", "#    ", " ### "],
    "f": ["  ## ", " #  #", " #   ", "###  ", " #   ", " #   ", " #   "],
    "g": ["     ", " ####", "#   #", "#   #", " ####", "    #", " ### "],
    "h": ["#    ", "#    ", "# ## ", "##  #", "#   #", "#   #", "#   #"],
    "i": ["  #  ", "     ", " ##  ", "  #  ", "  #  ", "  #  ", " ### "],
    "j": ["   # ", "     ", "  ## ", "   # ", "   # ", "#  # ", " ##  "],
    "k": ["#    ", "#    ", "#  # ", "# #  ", "##   ", "# #  ", "#  # "],
    "l": [" ##  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "m": ["     ", "     ", "## # ", "# # #", "# # #", "#   #", "#   #"],
    "n": ["     ", "     ", "# ## ", "##  #", "#   #", "#   #", "#   #"],
    "o": ["     ", "     ", " ### ", "#   #", "#   #", "#   #", " ### "],
    "p": ["     ", "     ", "#### ", "#   #", "#### ", "#    ", "#    "],
    "q": ["     ", "     ", " ####", "#   #", " ####", "    #", "    #"],
    "r": ["     ", "     ", "# ## ", "##  #", "#    ", "#    ", "#    "],
    "s": ["     ", "     ", " ### ", "#    ", " ### ", "    #", "#### "],
    "t": [" #   ", " #   ", "###  ", " #   ", " #   ", " #  #", "  ## "],
    "u": ["     ", "     ", "#   #", "#   #", "#   #", "#  ##", " ## #"],
    "v": ["     ", "     ", "#   #", "#   #", "#   #", " # # ", "  #  "],
    "w": ["     ", "     ", "#   #", "#   #", "# # #", "# # #", " # # "],
    "x": ["     ", "     ", "#   #", " # # ", "  #  ", " # # ", "#   #"],
    "y": ["     ", "     ", "#   #", "#   #", " ####", "    #", " ### "],
    "z": ["     ", "     ", "#####", "   # ", "  #  ", " #   ", "#####"],
    "{": ["   # ", "  #  ", "  #  ", " #   ", "  #  ", "  #  ", "   # "],
    "|": ["  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    "}": [" #   ", "  #  ", "  #  ", "   # ", "  #  ", "  #  ", " #   "],
    "~": ["     ", "  #  ", "   # ", "#####", "   # ", "  #  ", "     "],
    "←": ["     ", "  #  ", " #   ", "#####", " #   ", "  #  ", "     "],
    "→": ["     ", "  #  ", "   # ", "#####", "   # ", "  #  ", "     "],
}
_BLOCK = ["#####"] * 7          # what a module shows for a code it lacks

# The cursor is a solid block, not an underscore, photographed on a real
# console mid-entry: `TIME: 07:0#  AM PM`, the block sitting over the digit
# it is about to replace rather than under it. Every edit screen in this
# simulator drew an underscore, which is what a terminal does and not what
# this glass does -- a 5x7 cell has no room for a character AND a rule under
# it, so the hardware fills the cell.
CURSOR = ""
_GLYPHS[CURSOR] = _BLOCK
_BLANK = _GLYPHS[" "]

# The dot geometries, biggest first: (dot, gap between dots, gap between
# characters). The gap between dots is nought -- on the glass a stroke is
# a solid bar, not a row of separated squares -- and one dot of glass
# stands between a character and the next.
_GEOMETRY = [(dot, 0, dot) for dot in range(9, 0, -1)]


def blend(a, b, t):
    """A colour t of the way from a to b."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    r2, g2, b2 = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (int(r1 + (r2 - r1) * t),
                              int(g1 + (g2 - g1) * t),
                              int(b1 + (b2 - b1) * t))


class DotMatrixLCD:
    """A rows x cols character LCD drawn dot by dot on a canvas.

    Rows are addressed by index, and `itemconfig(row, text=...)` and
    `itemcget(row, "text")` are kept so that code written against a canvas
    text item still works: the row indexes are the item ids.

    `lit` and `dead` are the two palettes, each a dict of glass, cell,
    ghost and ink: the glass between the cells, the block each character
    sits in, the dots that are off and the dots that are on. A reflective
    panel with no power looks much as it does with power and nothing to
    say, so the two are close.
    """

    def __init__(self, parent, cols, rows, room, lit, dead):
        self.cols, self.rows = cols, rows
        self.palettes = {True: lit, False: dead}
        self.lit = True
        # The glass is exactly `room` wide, and the characters fill it:
        # on a real module the outermost dots sit hard against the edge of
        # the glass, with no border of dead glass round them, so of the
        # geometries that fit, take the one that comes closest to filling
        # the room and, on a tie, the biggest dot.
        # A dot is a whole number of pixels, so the widths available come
        # in big steps -- one dot more across a character is twenty-four
        # dots more across the glass. Rounding those down every time cost
        # the glass most of a dot's worth of width and left it narrower
        # than the keys below it, so a little overshoot is allowed and the
        # closest width to the room wins.
        fits = []
        for dot, gap, cgap in _GEOMETRY:
            pitch = 5 * dot + 4 * gap + cgap
            # the character block, plus the one dot of glass that stands
            # outside it on either side
            wide = pitch * cols - cgap + 2 * dot
            if wide <= room * 1.06:
                fits.append((abs(room - wide), -dot, dot, gap, cgap))
        if not fits:
            fits.append((0, -1, 1, 0, 1))
        _, _, dot, gap, cgap = min(fits)
        self.dot, self.gap = dot, gap
        pitch = 5 * dot + 4 * gap + cgap
        self.pitch_x = pitch
        # how far the cell block runs past its dots: enough to leave a line
        # of glass between neighbours
        # the cell block is exactly its dots: they touch, so there is no
        # room inside a character for the glass to show through
        self.cell_m = 0
        # The rows stand one dot apart -- barely apart at all. Measured
        # down the glass of a running console, where a character stands
        # seven dots and twenty-one pixels tall, the space between the two
        # rows of text is two pixels: less than a single dot. The rows are
        # nearly touching, and anything wider reads as two separate lines
        # of text rather than one display.
        self.pitch_y = 7 * dot + 6 * gap + 1 * (dot + gap)
        chars = pitch * cols - cgap
        # One dot of glass stands outside the outermost dots, all the way
        # round: the characters come within a hair of the edge, but they
        # do not run off it.
        self.pad_x = self.pad_y = dot
        self.width = chars + self.pad_x * 2
        rows_h = self.pitch_y * rows - 1 * (dot + gap)
        self.height = rows_h + self.pad_y * 2
        self.canvas = tk.Canvas(parent, width=self.width, height=self.height,
                                bg=lit["glass"], highlightthickness=0, bd=0)
        self._text = [""] * rows
        self._shown = [[None] * cols for _ in range(rows)]
        self._cells = []
        self._dots = [[self._cell(r, c) for c in range(cols)]
                      for r in range(rows)]

    def _cell(self, r, c):
        d, g = self.dot, self.gap
        x0 = self.pad_x + c * self.pitch_x
        y0 = self.pad_y + r * self.pitch_y
        pal = self.palettes[self.lit]
        # the block the character sits in, a shade off the glass
        m = self.cell_m
        self._cells.append(self.canvas.create_rectangle(
            x0 - m, y0 - m, x0 + 5 * d + 4 * g + m, y0 + 7 * d + 6 * g + m,
            fill=pal["cell"], outline=""))
        ids = []
        for row in range(7):
            for col in range(5):
                x = x0 + col * (d + g)
                y = y0 + row * (d + g)
                ids.append(self.canvas.create_rectangle(
                    x, y, x + d, y + d, fill=pal["ghost"], outline=""))
        return ids

    # ---- the canvas-text-item face ----
    def pack(self, **kw):
        self.canvas.pack(**kw)

    def itemconfig(self, row, text=None, **_ignored):
        if text is not None:
            self.set_row(row, text)

    def itemcget(self, row, option):
        if option == "text":
            return self._text[row]
        return self.canvas.itemcget(row, option)

    # ---- drawing ----
    def set_row(self, row, text):
        text = text.ljust(self.cols)[:self.cols]
        self._text[row] = text
        for c, ch in enumerate(text):
            self._paint(row, c, ch)

    def _paint(self, r, c, ch):
        if not self.lit:
            ch = " "
        if self._shown[r][c] == ch:
            return
        self._shown[r][c] = ch
        glyph = _GLYPHS.get(ch) or (_BLANK if ch == " " else _BLOCK)
        pal = self.palettes[self.lit]
        ids = self._dots[r][c]
        for row in range(7):
            line = glyph[row]
            for col in range(5):
                self.canvas.itemconfig(
                    ids[row * 5 + col],
                    fill=pal["ink"] if line[col] == "#" else pal["ghost"])

    def backlight(self, on):
        """Power to the glass, or none: the text goes, the glass stays."""
        on = bool(on)
        if on == self.lit:
            return
        self.lit = on
        pal = self.palettes[on]
        self.canvas.configure(bg=pal["glass"])
        for item in self._cells:
            self.canvas.itemconfig(item, fill=pal["cell"])
        for r in range(self.rows):
            self._shown[r] = [None] * self.cols
            self.set_row(r, self._text[r])


# the legend characters that are drawn rather than set
ARROWS = {"\u2190": -1, "\u2192": 1}      # left, right
BOX = "\u25a1"                            # the space, on the zero key
DOT = "\u2022"                            # the full stop, on the right key


class KeyCap(tk.Canvas):
    """A moulded key: a flat top on four sloped sides, that sinks when pressed.

    The command fires on release over the key, the way a Tk button's does,
    so a press that slides off the key does nothing.
    """
    BEVEL = 5
    SUNK = 2

    def __init__(self, parent, label, command, face, text, width, height,
                 grid, font, bevel=None, big_font=None, big_line=None):
        super().__init__(parent, width=width, height=height, bg=grid,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bevel = bevel or self.BEVEL
        self.sunk = max(1, round(self.bevel * 0.4))
        self.big_font = big_font
        self.big_line = big_line
        self.command = command
        self.label = label
        self.face = face
        self.text_colour = text
        self.font = font
        self.w, self.h = width, height
        self.down = False
        self._draw(False)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _draw(self, down):
        self.delete("all")
        w, h = self.w, self.h
        b = self.sunk if down else self.bevel
        face = blend(self.face, "#000000", 0.12) if down else self.face
        # the sides: light along the top and left, shade along the right and
        # bottom, and the bottom the darkest of the four
        top = blend(self.face, "#ffffff", 0.55)
        left = blend(self.face, "#000000", 0.12)
        right = blend(self.face, "#000000", 0.28)
        bottom = blend(self.face, "#000000", 0.48)
        x0, y0, x1, y1 = 0, 0, w, h
        if down:
            # sunk into the grid: a sliver of black opens all round
            s = self.sunk
            x0, y0, x1, y1 = s, s, w - s, h - s
        ix0, iy0, ix1, iy1 = x0 + b, y0 + b, x1 - b, y1 - b
        self.create_polygon(x0, y0, x1, y0, ix1, iy0, ix0, iy0,
                            fill=top, outline="")
        self.create_polygon(x0, y0, ix0, iy0, ix0, iy1, x0, y1,
                            fill=left, outline="")
        self.create_polygon(x1, y0, x1, y1, ix1, iy1, ix1, iy0,
                            fill=right, outline="")
        self.create_polygon(x0, y1, ix0, iy1, ix1, iy1, x1, y1,
                            fill=bottom, outline="")
        self.create_rectangle(ix0, iy0, ix1, iy1, fill=face, outline="")
        shift = self.sunk // 2 if down else 0
        # each line in its own font, the block centred on the face
        lines = self.label.split("\n")
        fonts = [self.big_font
                 if (self.big_font is not None and i == self.big_line)
                 else self.font
                 for i in range(len(lines))]
        highs = [f.metrics("linespace") for f in fonts]
        y = h / 2 - sum(highs) / 2 + shift
        for line, f, high in zip(lines, fonts, highs):
            cy = y + high / 2
            if line in ARROWS:
                self._arrow(w / 2 + shift, cy, ARROWS[line], b)
            elif line == DOT:
                self._dot(w / 2 + shift, cy, b)
            elif BOX in line:
                self._boxed(w / 2 + shift, cy, line, f)
            else:
                self.create_text(w / 2 + shift, cy, text=line, font=f,
                                 fill=self.text_colour, justify="center")
            y += high

    def _arrow(self, cx, cy, way, bevel):
        """A cursor arrow, drawn to the proportions it is moulded in.

        Measured off the photographed keycap: the arrow runs about half the
        width of the key face, its shaft is a slim eighteenth of that face
        thick and its head not quite a fifth of it tall. It is a lighter
        arrow than it looks at a glance -- drawn any heavier it turns into
        a black bar with a triangle on the end.
        """
        face = self.w - 2 * bevel
        half = face * 0.26                     # half the arrow's length
        t = max(1.5, face * 0.055) / 2         # half the shaft thickness
        hh = face * 0.09                       # half the head's height
        hl = face * 0.17                       # the head's length
        tip = cx + half * way
        back = cx - half * way
        neck = tip - hl * way
        self.create_polygon(
            tip, cy,
            neck, cy - hh,
            neck, cy - t,
            back, cy - t,
            back, cy + t,
            neck, cy + t,
            neck, cy + hh,
            fill=self.text_colour, outline="")

    def _boxed(self, cx, cy, line, font):
        """A legend with the open box that stands for a space in it.

        The box is a square of cap height. Set as a character it comes out
        far smaller than the letters beside it, which is what made the
        zero key's legend look shrunken next to every other key's.
        """
        rest = line.replace(BOX, "")
        cap = font.metrics("ascent") * 0.72
        side = max(3.0, cap * 0.68)
        gap = max(1.0, side * 0.3)
        wide = font.measure(rest)
        x = cx - (side + gap + wide) / 2
        self.create_rectangle(x, cy - side / 2, x + side, cy + side / 2,
                              outline=self.text_colour, fill="",
                              width=max(1, round(side * 0.16)))
        self.create_text(x + side + gap, cy, text=rest, font=font,
                         anchor="w", fill=self.text_colour)

    def _dot(self, cx, cy, bevel):
        """The full stop on the right-hand cursor key: a solid round dot,
        far plainer and rounder than a period set in type at this size."""
        r = max(1.5, (self.w - 2 * bevel) * 0.045)
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         fill=self.text_colour, outline="")

    def _press(self, _e=None):
        self.down = True
        self._draw(True)

    def _release(self, e=None):
        was_down = self.down
        self.down = False
        self._draw(False)
        inside = e is None or (0 <= e.x < self.w and 0 <= e.y < self.h)
        if was_down and inside and self.command:
            self.command()

    def invoke(self):
        """Press and release, for whoever drives the keys by name."""
        if self.command:
            self.command()
