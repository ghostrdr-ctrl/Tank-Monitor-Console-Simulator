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
"""The windows the updater puts up, and the thread it does the waiting on.

Tk is single-threaded and the network is slow, so every request runs on a
worker thread and comes back through `after(0, ...)`, which is the only way
to touch a widget from anywhere but the main thread.

Two ways in. The Help menu asks out loud and reports whatever it finds,
including "up to date" and including failures. The startup check, if the user
has turned it on, says nothing at all unless there is genuinely a new version
-- nobody wants a "could not reach GitHub" box every morning at a site with
no outbound internet.
"""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_NAME, DISCLAIMER, PUBLISHER, __version__
from . import update


def check_for_updates(parent, silent=False):
    """Look for a newer release. `silent` suppresses everything but good news."""
    def work():
        try:
            rel = update.check()
            err = None
        except update.UpdateError as e:
            rel, err = None, e
        parent.after(0, lambda: _report(parent, rel, err, silent))

    threading.Thread(target=work, daemon=True).start()


def _report(parent, rel, err, silent):
    if err is not None:
        if not silent:
            messagebox.showwarning("Check for updates", str(err), parent=parent)
        return
    if rel is None:
        if not silent:
            messagebox.showinfo(
                "Check for updates",
                f"{APP_NAME} {__version__} is the current version.",
                parent=parent)
        return
    _OfferWindow(parent, rel)


class _OfferWindow(tk.Toplevel):
    """What is new, how big it is, and two buttons."""

    def __init__(self, parent, rel):
        super().__init__(parent)
        self.rel = rel
        self.parent = parent
        self.title("Update available")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(padx=16, pady=14)

        tk.Label(self, font=("Segoe UI", 11, "bold"), anchor="w",
                 text=f"Version {rel.version} is available").pack(anchor="w")
        tk.Label(self, fg="#555", anchor="w",
                 text=f"You have {__version__}.  Download is "
                      f"{rel.size / 1048576:.1f} MB.").pack(anchor="w", pady=(2, 10))

        if rel.notes:
            box = tk.Text(self, width=68, height=12, wrap="word",
                          font=("Segoe UI", 9), relief="solid", borderwidth=1)
            box.insert("1.0", rel.notes)
            box.configure(state="disabled")
            box.pack(fill="both", expand=True)

        self.bar = ttk.Progressbar(self, length=460, mode="determinate")
        self.status = tk.Label(self, fg="#555", anchor="w", text="")

        row = tk.Frame(self)
        row.pack(fill="x", pady=(12, 0))
        self.go = ttk.Button(row, text="Download and install",
                             command=self._download)
        self.go.pack(side="right")
        self.later = ttk.Button(row, text="Not now", command=self.destroy)
        self.later.pack(side="right", padx=(0, 8))

        self.grab_set()

    # -- downloading --------------------------------------------------------

    def _download(self):
        self.go.configure(state="disabled")
        self.later.configure(state="disabled")
        self.bar.pack(fill="x", pady=(12, 4))
        self.status.pack(fill="x")
        self.status.configure(text="Contacting GitHub...")

        def progress(done, total):
            self.after(0, lambda: self._progress(done, total))

        def work():
            try:
                path = update.download(self.rel, progress)
                err = None
            except update.UpdateError as e:
                path, err = None, e
            self.after(0, lambda: self._done(path, err))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, done, total):
        if total:
            self.bar.configure(maximum=total, value=done)
            self.status.configure(
                text=f"Downloading  {done / 1048576:.1f} / "
                     f"{total / 1048576:.1f} MB")
        else:
            self.status.configure(text=f"Downloading  {done / 1048576:.1f} MB")

    def _done(self, path, err):
        if err is not None:
            self.destroy()
            messagebox.showerror("Update failed", str(err), parent=self.parent)
            return

        self.status.configure(text="Verified.")
        go = messagebox.askokcancel(
            "Install update",
            f"{APP_NAME} will close so the installer can replace it.\n\n"
            "Any programming you have done is kept.",
            parent=self)
        if not go:
            self.destroy()
            return
        try:
            update.install(path)
        except update.UpdateError as e:
            self.destroy()
            messagebox.showerror("Update failed", str(e), parent=self.parent)
            return
        self.parent.destroy()


def about(parent):
    """Who wrote it, what it is licensed as, and whose trademarks it is not."""
    messagebox.showinfo(
        f"About {APP_NAME}",
        f"{APP_NAME}\nVersion {__version__}\n"
        f"Copyright (C) 2026 {PUBLISHER}\n\n"
        "Free software under the GNU General Public License, version 3 or "
        "later. It comes with ABSOLUTELY NO WARRANTY. You may redistribute "
        "it, and the source is published with it.\n\n"
        f"{DISCLAIMER}\n\n"
        f"https://github.com/{update.UPDATE_REPO}",
        parent=parent)
