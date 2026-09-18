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
"""Files that are either the old version or the new one, never half of each.

`open(path, "w")` empties a file before the first byte of its replacement is
written. A save that fails between the two -- a value json cannot encode, a
full disk, the process killed -- leaves programming that was good a moment
ago truncated, and the next start reads nothing back. `replacing` writes the
new content beside the old file and swaps it in only once all of it is on
disk.
"""
import contextlib
import os


@contextlib.contextmanager
def replacing(path):
    """A UTF-8 text file whose content becomes `path` when the block ends.

    The temporary file sits in the same directory because `os.replace` is
    only atomic within one filesystem. It is opened the way `open(path, "w",
    encoding="utf-8")` opens a new file -- the same newline translation, the
    same permissions after the umask -- so what lands is byte for byte what
    that wrote. If the block raises, or the flush, fsync or replace fails,
    the temporary file is removed, `path` is left as it was, and the error
    propagates.
    """
    tmp = f"{path}.{os.urandom(4).hex()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
