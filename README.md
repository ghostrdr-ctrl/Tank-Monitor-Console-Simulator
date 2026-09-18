# Tank Monitor Console Simulator

A training simulator for TLS-350 compatible tank monitor consoles. It aims to
be a one-to-one copy of the real console: the front panel, the card cage, the
setup and diagnostic screens, the printer, and the serial protocol any TLS
tool can connect to. If a real console does it, this one is meant to do it the
same way.

Built to practise on and to test against, without a console on the bench.

![The simulated console; the bench opens in a window beside it](screenshot.png)

## Installing it

On Windows, download from the
[latest release](https://github.com/ghostrdr-ctrl/Tank-Monitor-Console-Simulator/releases).
There are two downloads and they are the same program:

- **`...-Setup.exe`** installs it per-user, with no administrator rights and
  no UAC prompt. Take this one unless it will not download.
- **`...-Portable.zip`** is a folder to extract and run, with nothing
  installed and nothing to uninstall. Take this one if your workplace blocks
  the `.exe` download, which some do without offering a way past it. It
  carries a `HOW-TO-RUN.txt` that says what to do with it.

Both are currently unsigned, so SmartScreen warns on first run; check your
download against the release's `SHA256SUMS.txt`. Help then Check for updates
fetches new versions from the same place, and fetches the installer.

## Running from source

Python 3.8 or newer, standard library only. Tkinter for the window, which
ships with Python on Windows and macOS and is `python3-tk` on Debian and
Ubuntu. Nothing to install.

```
python run.py                     console window and bench window, serial on 127.0.0.1:10001
python run.py --port 10002        somewhere else
python run.py --seed site.vrset   start out looking like a real site
python run.py --headless          serial only, no window
python run.py --xport             emulate the TCP/IP card: setup menu, web manager, discovery
python run.py --exposed           put it on a public address as a honeypot (read Exposure below)
python -m unittest discover tests
```

Without `--xport`, point a tool at `127.0.0.1` and the port it prints.

### The card, and the address it answers at

With `--xport` the console is behind an emulated TCP/IP Interface Module, and
then **the address programmed into the card is the address to use** -- not
127.0.0.1. Ping it, point DeviceInstaller at it, open its web pages, pull
inventory from its tunnel port. Reprogram it and everything moves, because on
a real site it would: the card reboots and the old address stops answering.

```
python run.py --xport --card-ip 192.168.1.50   program it before it starts
python run.py --xport --reset-card             put it back to its defaults
python run.py --xport --claim-ip               make the address answer ping
python run.py --xport --xport-web-port 8080    when something else has port 80
```

The window's title bar and its Network tab show the address and port to
connect to, and the log says if the card had to fall back.

**`--claim-ip` is the one that needs administrator rights.** Without it the
card binds the programmed address when this machine already has it, and
listens on every address when it does not -- connecting, programming and
reading inventory all work either way. With it, and running elevated, the
address is added to a network adapter for as long as the simulator runs, so
it answers ping and other machines on the subnet reach it exactly as they
would a real card. It is removed again on the way out.

**Reset card** on the Network tab, or `--reset-card`, puts the whole card
back including its address, which the card's own "7 Defaults" deliberately
does not. It is there for when a trainee programs an address they cannot then
reach.

### Commissioning a new card

`--blank-card` gives you a module as it comes out of its box: **no address at
all**. There is no factory default to fall back on -- Veeder-Root's
installation guide is explicit that the address is customer-supplied -- so
the card answers nothing that is addressed by IP. No tunnel, no setup menu,
no web manager. Only the two things that reach an unaddressed card work:

- **Discovery** on UDP 30718, which is why DeviceInstaller can find a module
  you cannot ping. It lists by MAC.
- **The port-1 knock**, which is the manufacturer's own command-prompt
  procedure:

```
arp -s 192.168.12.53 00-20-4A-AD-64-70     put the wanted address against the card's MAC
telnet 192.168.12.53 1                     the card takes that address
telnet 192.168.12.53 9999                  now program the rest
```

The card takes the address it was knocked at, saves it, and comes back on it
with everything answering. That is the first job on a site, and it is the
exercise this starts from.

A card that already has an address is one somebody has already programmed --
which is also why an address survives the menu's own "7 Defaults" while
everything else goes back.

Each example site is programmed with its own card address, as real sites are,
so loading a different site means going to find the new card.

## Using it

Because it is a one-to-one copy, operate it the way you operate a real
console: the same keys, the same MODE and FUNCTION and STEP walk, the same
operating, setup and diagnostic menus -- and reconciliation too, on a console
with the BIR software key. The manufacturer's manuals are the manual for this
simulator too. Program it, read it over the serial port, print from it, and it
answers as the hardware answers.

## What this adds around the console

The console is faithful; the parts around it are what make it a bench.

- **The site, drawn as a site.** A separate bench window shows each tank as a
  buried cylinder with a probe down it. Drag the product float to set the fuel,
  drag the water float to set the water under it, and the console gauges what
  you set. Sensors, lines and dispensers are there too, and the alarms follow
  from the physical state against the limits you programmed.
- **A forecourt that runs itself.** A Traffic view sets the site's own day:
  how many cars (a closed store, a slow one, a middling one, a busy one, or
  a number you type) and which hours they come in (day-heavy, night-heavy,
  flat around the clock, weekend). Cars arrive, lift nozzles, and drive
  away; the grades come off the tanks' own product labels; the tanks go
  down; a tanker is called when one gets low and drops down the fill riser
  so the console infers the delivery rather than being told. Blended grades
  are there too -- set up regular and E-85 and blend E15 out of them at
  whatever ratio, and both tanks go down together.

  What it is FOR is the thing a technician cannot practise on a quiet
  bench: with the forecourt running, a line leak test will not start,
  because a handle is up. Shut the site down and it starts. Put a leak on
  the line first and watch the gross test that follows the next dispense
  fail. That is the job, and until now nothing here could stage it.
- **Shutdowns that actually shut things down.** A failed test, a line
  disable alarm the site has programmed, or a relay wired to a tank whose
  alarm has dropped the contactor -- any of the three takes the pump out,
  so the handle gets no pressure and no fuel moves. The Traffic view lists
  what is dead and why.
- **A probe you can unplug**, at the connector on the tank, which raises the
  Probe Out alarm the way a pulled probe does in the field.
- **The TCP/IP Interface Module** is emulated from a real one, byte for byte:
  the telnet setup menu on port 9999, the web manager on port 80, and
  DeviceInstaller discovery on UDP 30718, alongside the serial tunnel. A card
  on a bench was captured and the emulator's every screen was diffed against
  it; `reference/lantronix_xport_capture.md` records what that settled and
  the ten places the manuals turned out to be wrong. Program it the way you
  would program a real one -- including the trap where typing `Y` and Enter
  at the gateway question shifts the address by an octet. Off until you start
  it.
- **A capture of everything said over the wire.** A Capture view lists each
  exchange with the source address, the direction, the command code and the
  bytes, and appends the same rows to a file as JSON lines. It appears when
  something is being captured; see Exposure below.
- **Your own site, loaded and saved from the window.** Console then Seed
  programming from file pours a saved setup in, and Save programming to file
  writes one out, so a site worked up on one bench can be carried to another
  machine. Same format as `--seed` and as the console's own SAVE SETUP DATA.
- **A self-updater.** Help then Check for updates fetches and verifies new
  releases.

## Exposure, and running this as a honeypot

This simulator answers the real TLS-350 protocol on the real port, behind a
byte-for-byte emulation of the Lantronix XPort that puts a console on a
network. That makes it usable as a high-fidelity ATG honeypot -- the thing
[GasPot](https://github.com/sjhilt/GasPot) has done since 2015 with a much
smaller emulation, and the reason tcp/10001 gets scanned at all. GasPot's
design decisions are good ones and several are matched here deliberately;
`tls350sim/exposed.py` says which and why.

But the console was written for a bench, where the other end means well: it
keeps what it is programmed with, and it answers everything. Neither is safe
on a public address, so the choice is explicit and it is one setting.

    python run.py --exposed                      # a public address
    python run.py --exposure lan                 # a network you trust
    python run.py                                # loopback, the default

The dropdown on the bench's Network view is the same setting, and it puts the
warning up before it takes effect.

**What `--exposed` changes.** Nothing the network says reaches the disk: the
card cannot be moved, the state file is not rewritten, and a restart is
always a clean restart. Apply Settings and the setup menu's save still report
success and still "reboot" the unit -- a card that refused its own save would
be a louder fingerprint than anything else on the wire -- but nothing
underneath moves, and the attempt is captured. The card's MAC stops being
derived from this machine's host name. Connections are capped in total and
per source, rate-limited per source, and given a 30 second idle timeout.
Replies are delayed 0.15-0.75 s, because a reply that comes back in 40
microseconds did not come from a console on a 9600 baud serial line, and
answering instantly is the fingerprint that gives a software emulation away
no matter how perfect its bytes are. Every byte in and out is captured.

**What it does not change.** It is not a sandbox. It closes the holes this
program has that a honeypot must not have; it does not make a 60,000 line
Python process safe to run as root on a box that matters. Run it as an
unprivileged user, in a container, on a host you can throw away.

**Before you leave one running:**

- You will be indexed as exposed critical infrastructure and you may get
  abuse reports from your ISP. Do not do this on a home connection or on any
  network you do not own.
- **Change the site.** The demo site ships with this program, so it is a
  fingerprint: a scanner that has seen one of these recognises the tank names
  in the next one. GasPot's own default station names became the published
  signature that identified a GasPot on sight. Use `--seed`, or Console then
  Seed programming from file.
- Give the card a fixed identity with `--card-mac`, or it draws a new one
  each restart. An exposed card also refuses to take an address from the
  network, so give it one with `--card-ip`.
- Collecting other people's traffic may be regulated where you are.

**The capture.** `--capture FILE.jsonl` (implied by `--exposed`) appends one
JSON object per direction: `ts`, `src`, `sport`, `proto`, `dir`, `cmd`,
`bytes`, `raw`. The field names follow GasPot's so an existing Splunk or ELK
pipeline built for one reads the other. Bytes are hex, never decoded -- what
was actually sent is the whole value of the capture, and a stranger's traffic
is not UTF-8.
- **A Windows installer** with a per-user install that needs no administrator
  rights, and an in-app update from there on.

## Licence

GNU General Public License, version 3 or later. See `LICENSE`. Use it, study
it, share it, change it; a version you pass on stays GPL-3.0 and its source
reaches whoever receives it.

## Not affiliated

This program is not affiliated with, authorized by, sponsored by, or endorsed
by Veeder-Root, Gilbarco Veeder-Root, or Vontier Corporation.

"Veeder-Root", "TLS-350" and "AccuChart" are trademarks of their respective
owners. They appear here only to state, factually, what hardware this
simulator is compatible with. It is an independent simulator for training and
testing, written from published manuals; those manuals are not redistributed
here. It is not a substitute for a real console or for the manufacturer's
documentation.

Published by Verbose Software.
