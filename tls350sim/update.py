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
"""Ask GitHub whether there is a newer release, and install it if asked to.

Nothing here happens without the user saying so. The check is a single HTTPS
GET of the releases API; the download only starts after a person has seen the
version number and the release notes and pressed a button; and the installer
only runs after the download has been checked against the SHA-256 the release
publishes.

WHAT THE CHECKSUM IS AND IS NOT. It proves the bytes that arrived are the
bytes that were published -- a truncated download, a proxy that mangled the
transfer, a corrupted CDN copy. It does NOT prove the publisher is us, because
the checksum travels from the same place the installer does; anyone who could
replace the one could replace the other. What proves the publisher is an
Authenticode signature on the installer, and until there is a signing
certificate this program does not have that. This is worth being honest about
rather than implying more safety than is here.

Only stdlib: this program has no third-party dependencies and the updater is
not going to be the thing that introduces one.
"""
import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from . import APP_NAME, __version__
from . import paths

# The public repository releases are published to. This is the ONE string to
# change if the project moves; the API URL and the download URLs are all
# derived from it.
UPDATE_REPO = "ghostrdr-ctrl/Tank-Monitor-Console-Simulator"

API = "https://api.github.com/repos/{repo}/releases/latest"

# GitHub rejects API requests that do not identify themselves.
USER_AGENT = f"{APP_NAME.replace(' ', '')}/{__version__}"

# The release asset that lists the SHA-256 of every other asset, one
# "<hex>  <filename>" per line, which is what `sha256sum` writes.
CHECKSUM_ASSET = "SHA256SUMS.txt"

TIMEOUT = 20
CHUNK = 64 * 1024


class UpdateError(Exception):
    """Anything that stopped an update, phrased for a person to read."""


class Release:
    """A published release, reduced to what this program needs."""

    def __init__(self, tag, version, notes, installer_url, installer_name,
                 size, checksum_url):
        self.tag = tag
        self.version = version
        self.notes = notes
        self.installer_url = installer_url
        self.installer_name = installer_name
        self.size = size
        self.checksum_url = checksum_url

    def __repr__(self):
        return f"<Release {self.tag} {self.installer_name}>"


# ---------------------------------------------------------------------------
# versions


def parse_version(text):
    """"v1.2.3" -> (1, 2, 3). Anything unparseable sorts as (0,).

    Deliberately forgiving: a release tagged in a way this does not
    understand should mean "no update offered", not a traceback.
    """
    if not text:
        return (0,)
    text = str(text).strip().lstrip("vV")
    # drop any pre-release or build suffix: 1.2.3-beta.1 -> 1.2.3
    for sep in ("-", "+", " "):
        text = text.split(sep)[0]
    out = []
    for part in text.split("."):
        if not part.isdigit():
            break
        out.append(int(part))
    return tuple(out) if out else (0,)


def is_newer(remote, local=__version__):
    """True when `remote` is a strictly higher version than `local`."""
    return parse_version(remote) > parse_version(local)


# ---------------------------------------------------------------------------
# talking to GitHub


def _context():
    """A TLS context that verifies certificates. The default already does."""
    return ssl.create_default_context()


def _open(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_context())


def _friendly(err, what):
    """Turn a urllib failure into a sentence rather than a stack trace."""
    if isinstance(err, urllib.error.HTTPError):
        if err.code == 404:
            return UpdateError(
                f"No published release found for {UPDATE_REPO}. If this is a "
                "brand new project there may not be one yet.")
        if err.code in (403, 429):
            return UpdateError(
                "GitHub is rate-limiting update checks from this network. "
                "Try again in an hour.")
        return UpdateError(f"GitHub returned {err.code} while {what}.")
    if isinstance(err, urllib.error.URLError):
        return UpdateError(
            f"Could not reach GitHub while {what}: no network, or a firewall "
            "is blocking it.")
    return UpdateError(f"Failed while {what}: {err}")


def check(repo=UPDATE_REPO):
    """Return a Release if one is published and newer, otherwise None.

    Raises UpdateError if the check could not be made at all, which is a
    different thing from "you are up to date" and should not be reported as
    though it were.
    """
    try:
        with _open(API.format(repo=repo)) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:                      # noqa: BLE001 - all reported
        raise _friendly(e, "checking for updates") from e

    tag = data.get("tag_name") or ""
    if not is_newer(tag):
        return None

    installer = checksums = None
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name == CHECKSUM_ASSET:
            checksums = asset
        elif name.lower().endswith(".exe"):
            installer = asset

    if installer is None:
        raise UpdateError(
            f"Release {tag} is published but carries no installer to "
            "download. It may still be being uploaded.")

    return Release(
        tag=tag,
        version=tag.lstrip("vV"),
        notes=(data.get("body") or "").strip(),
        installer_url=installer["browser_download_url"],
        installer_name=installer["name"],
        size=installer.get("size") or 0,
        checksum_url=(checksums or {}).get("browser_download_url"),
    )


# ---------------------------------------------------------------------------
# fetching


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def published_checksum(release):
    """The SHA-256 the release claims for its installer, or None."""
    if not release.checksum_url:
        return None
    try:
        with _open(release.checksum_url) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:                      # noqa: BLE001
        raise _friendly(e, "fetching the checksum file") from e

    for line in text.splitlines():
        parts = line.split()
        # "<hex>  <name>", and sha256sum writes a "*" before binary names
        if len(parts) >= 2 and parts[-1].lstrip("*") == release.installer_name:
            return parts[0].lower()
    return None


def download(release, progress=None):
    """Fetch the installer to a temp file and verify it. Returns the path.

    `progress(done_bytes, total_bytes)` is called as it goes, so a window can
    show a bar. Raises UpdateError, and deletes the partial file, if anything
    is wrong -- including a checksum that does not match, which is the whole
    reason for checking.
    """
    expected = published_checksum(release)

    d = tempfile.mkdtemp(prefix="tmcs-update-")
    dest = os.path.join(d, release.installer_name)
    try:
        with _open(release.installer_url) as r:
            total = int(r.headers.get("Content-Length") or release.size or 0)
            done = 0
            with open(dest, "wb") as f:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
    except Exception as e:                      # noqa: BLE001
        _discard(dest)
        raise _friendly(e, "downloading the update") from e

    if expected is None:
        _discard(dest)
        raise UpdateError(
            f"Release {release.tag} does not publish a {CHECKSUM_ASSET}, so "
            "the download cannot be verified. Refusing to run it.")

    got = sha256_of(dest)
    if got != expected:
        _discard(dest)
        raise UpdateError(
            "The downloaded installer does not match the checksum the "
            f"release publishes.\n\nexpected  {expected}\ngot       {got}\n\n"
            "It has been deleted. Try again, and if it happens twice, "
            "download the installer from the releases page by hand.")
    return dest


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# installing


def install(installer_path):
    """Start the installer and return, so the caller can close the window.

    The installer cannot replace files that are open, so the running program
    has to exit; Inno Setup's /CLOSEAPPLICATIONS would do it forcibly, but
    quitting first and letting the user watch the installer is politer and
    loses nothing.
    """
    if not paths.frozen():
        raise UpdateError(
            "This is running from a source tree, not an installed copy. "
            "Update it with `git pull` instead.")
    try:
        subprocess.Popen([installer_path], close_fds=True)
    except OSError as e:
        raise UpdateError(f"Could not start the installer: {e}") from e


# ---------------------------------------------------------------------------
# preferences


def _load_settings():
    try:
        with open(paths.settings_file(), encoding="utf-8") as f:
            s = json.load(f)
            return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(s):
    try:
        with open(paths.settings_file(), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass                                    # a preference is not worth a crash


def check_on_startup():
    """Whether to look for updates when the program opens. Off by default.

    Off, because a training tool that phones out on launch without being
    asked is a thing a site's IT department is entitled to be annoyed about.
    The Help menu turns it on.
    """
    return bool(_load_settings().get("check_on_startup", False))


def set_check_on_startup(value):
    s = _load_settings()
    s["check_on_startup"] = bool(value)
    _save_settings(s)


# ---------------------------------------------------------------------------
# the command line


def cli_check():
    """`run.py --check-update`. Returns a process exit code."""
    print(f"{APP_NAME} {__version__}")
    try:
        rel = check()
    except UpdateError as e:
        print(f"\n{e}")
        return 2
    if rel is None:
        print("\nUp to date.")
        return 0
    print(f"\nVersion {rel.version} is available ({rel.installer_name}, "
          f"{rel.size / 1048576:.1f} MB).")
    if rel.notes:
        print("\n" + rel.notes)
    print(f"\nhttps://github.com/{UPDATE_REPO}/releases/latest")
    if not paths.frozen():
        print("\nThis is a source checkout: `git pull` rather than the installer.")
    return 0


if __name__ == "__main__":
    sys.exit(cli_check())
