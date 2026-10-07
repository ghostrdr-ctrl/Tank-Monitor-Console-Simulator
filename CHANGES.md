# Changes

What changed in each release, briefly. One entry per release, a line or two
per change, written for somebody using the simulator rather than building it.

## Unreleased

## 0.5.0 - 2026-10-07

- The active alarms report lists only the alarms whose cause is still
  there, in category and then device order, as a real console does. An
  alarm that has cleared but not been acknowledged stays on the display
  only. The computer-format 121 report now has its own record layout, taken
  from a real console, where it used to repeat the active alarms report.
- An inventory record time that is set prints as a clock time (` 2:30 PM`),
  not as the four stored digits.
- A line feed no longer ends a serial command; only a carriage return or
  ETX does, as on a real console. The delivery density inquiry answers as
  soon as its delivery type arrives and refuses a bad one with a `?` per
  tank. A label sent over the port with a tab or line feed in it is
  refused, and one with a NUL in it is cut short there.
- The RS-232 end-of-message characters (537 for display format, 538 for
  computer format) can be set per port and are shown in their reports. With
  end of message enabled (531) they take the place of the ETX at the end of
  each reply, as on a real console.
- The serial remote alarm reset (S00300) now does what ALARM/TEST does:
  it silences the alarm and clears messages whose cause has gone. A
  pressure line's open-transducer alarm stays until the line is fixed,
  however it is acknowledged, as on a real console.
- The computer-format receiver autodial alarm status (52D) lists every
  receiver whichever one is asked for, as a real console does.
- Notes only: an empty alarm list in a console backup (autodial alarms,
  custom alarm labels) cannot be written back to a real console either, and
  the restore tests now say so.
- A tank switched on with no probe raises SETUP DATA WARNING and PROBE OUT
  as a real console does, and the tank status report lists it. As on a real
  console these checks are quiet straight after a cold start, until setup
  mode is left, the clock is set or midnight passes.
- Loading a site backup whose line had a PLLD open alarm standing leaves
  that line's transducer open, so its tests abort as the site's did.
- The pressure line reports match a real console more closely: one station
  header, not two; line and alarm names at their full width; and the right
  blank lines at the end of each report.
- The system configuration report's POWER ON RESET column is the reading
  taken at the last cold start, as on a real console: a card fitted since
  reads as an empty slot there until the next cold start, and a card pulled
  since keeps its old reading over UNUSED. Loading a site backup takes this
  from the site's own report.
- A WPLLD communications module in the comm bay is reported as a real
  console reports it: its name and module type on the system configuration
  report, its software on the WPLLD diagnostic, and no serial port for it on
  the comm reports. The system configuration report's module types for the
  PLLD sensor and controller boards and the serial satellite are corrected
  too.
- Notes only: the WPLLD communications module's other part numbers are
  recorded, so a board in hand can be matched to the one the simulator
  models, and the comparisons against a real console are written up with
  the experiments still to run.
- A command too short to be one (half a code and then Enter) now gets no
  reply, as a real console gives none. It used to get 9999FF.
- Replies now match a real console byte for byte in more places: a report's
  station header lines keep their full width once set, a heading or a row
  keeps the trailing spaces a real console sends, and one report keeps the
  extra carriage return a real console puts at the end of each line.
- A command ended with ETX is read as ended, as a carriage return is, and a
  backspace sent by a tool is part of the command, as a real console takes
  it; it still rubs out for somebody typing at a terminal.
- Loading a site backup brings each tank's temperature compensated volume
  back to the gallon, where it could come back one gallon off depending on
  the time of day.
- Restoring a real console's backup onto the simulator with the Multitool
  now brings back nearly everything as it went in. The simulator no longer
  answers for positions a console does not have (a fifth header line, a
  ninth receiver, a comm port with no board, a fourth pressure line), packs
  the port settings default as the 1200 baud its display shows, and answers
  a few per-device records it left empty.
- Some inquiries take a data field, and a real console waits for it before
  answering. The simulator now waits too, until a carriage return, instead
  of answering the moment the code is typed, and answers as the console does
  when none is given. Two codes it did not know are now answered.
- The updater reduces a downloaded file's name to its last part whichever
  slash it uses, on every system. On Linux and macOS a name written with
  backslashes was kept whole.
- Checked against a real console again, this time asking every report for
  device 1 as well as for all devices, which had never been compared. A
  console-wide setting asked for a device number now answers as the console
  does, with the whole setting, where this answered nothing. A tank or line
  asked for by number that is switched off answers with nothing, and so do a
  relay nobody configured and device 00 of a comm port. The comm port
  settings reports are drawn the way the console draws them, board by board.
- A site backup loads more faithfully. Every receiver's auto dial time comes
  through, where all eight read DISABLED; tanks keep their one point profile,
  where every one read 50 PTS; and the maximum volume, the line pump type and
  the low pressure threshold print as values instead of packed text. The leak
  test fail alarms and the AccuChart end shape print their table whatever is
  stored.
- An active alarm that has dropped out of the fifty row alarm history is
  printed with no date, as a real console prints it, instead of the time the
  report was asked for.
- The low pressure threshold's computer format Set waits for all eight
  characters of its packed number before it answers, as a real console
  does; it answered after two.

- A pressure line that is switched off is no longer reported just because
  it still has a label. A real console answers every line leak report with
  nothing in that state, and this printed the line in full. And the stored
  inventory, extended delivery and ticketed delivery reports now answer
  with nothing when no tank is reporting, as a real console does, instead
  of a heading over an empty page.
- Load a real site from its backup. Console > Load site backup (.vrset),
  also at the bottom of Load example site, and `--site FILE.vrset` on the
  command line. It takes the programming as seeding does, and from a
  Multitool site snapshot's reports it also takes the cards in the cage,
  the fuel, water and temperature in each tank, the sensors in alarm, the
  alarm history, the alarms standing, the deliveries and the leak test
  results, then runs on from there. A plain backup from Multitool 0.62.0
  on still brings its cards, from the installed modules list it carries.
  Console > Site reports shows each of the site's own reports beside what
  the simulator prints for it now, and can clear the alarms the backup put
  up that the simulator could not raise by itself.
- The first line leak diagnostic screen now says whether the line it is
  showing is dispensing. It read DISPENSING DISABLED on every line, always,
  because it was drawing the line its manual figure draws: with the 3.0
  gallon an hour test passed, the shutdown cleared and fuel leaving the
  nozzle, the screen still said the line was dead. It reads the same flag
  the DISPENSING column of the line status report reads, for both the
  pressurised and the wireless pressurised families.
- A line that comes back while a handle is still up now gets its pump.
  A shutdown cleared by a passing test, or by an acknowledgement on a site
  set to re-enable that way, left the pump off and the line reading
  DISPENSING DISABLED until the nozzle was hung up, while the pressure
  came back anyway.
- Lifting and hanging up a nozzle on a shut down line no longer counts as a
  dispense. Nothing was pumped, so there is nothing to measure: it used to
  take a dispensing pressure off a line standing still and start the gross
  test that follows a sale.
- The Leak Verification Procedure is built. A vacuum line leak detector
  that fails its 3.0 gallon an hour test shuts the pump down, and the
  console now does what its manual says next: it prints PERFORM LVP TEST,
  and pressing ALARM/TEST puts up a screen that starts the verification.
  ENTER runs it. A handle lifted or an in-tank test under way interrupts
  it and the pump stays down; a pass enables the pump and prints the
  result; a failure leaves it down. None of this existed before, so
  silencing the alarm offered nothing at all.
- A line leak test no longer prints an in-tank test slip. Starting a
  vacuum line test from the panel printed START IN-TANK LEAK TEST over a
  tank's name, for a test of a pipe.
- Lowering the exposure setting now moves the listeners before it gives up
  the protections. Selecting Bench used to drop the connection limits,
  allow saving again and switch the capture off while leaving the card
  exactly where it was, so a card answering the whole network went on
  answering it with nothing in front. The card retreats to this machine
  only, and drops the connections open on it, before anything is given up;
  where the window cannot move the listener it says so and leaves the
  setting alone.
- A listener that has been stopped now stops answering. Closing its socket
  did not interrupt a thread already waiting for a connection, so the
  serial tunnel, the setup menu, the discovery responder and the
  assignment port could all serve one more caller after being shut down,
  and after the card had moved to a new address.
- The capture now records the traffic most worth having: a malformed
  request is recorded before it is refused rather than answered and
  forgotten, the answers are recorded as well as the questions, and the
  setup menu records what it prints as well as what it is told.
- The capture is now bounded. There is a size limit on the file with older
  files kept behind it, a limit on how much of one connection's traffic is
  stored, and a limit on how fast bytes are accepted. It stored a little
  over two bytes for every byte received, with no limit of any kind, so
  one connection sending nonsense filled the disk faster than it sent.
- A console on a public address no longer answers discovery for as many
  forged senders as arrive. There is a limit across all senders as well as
  the one per sender, and the tables that track them have a hard ceiling
  instead of a tidy-up that could not keep pace.
- A sale now only progresses on fuel that actually left the tank. A twenty
  gallon sale completed against a tank holding one, and a sale during a
  shutdown completed having moved nothing at all, with the meter turning
  for both, so the console booked a transaction for fuel that never left.
  A nozzle getting nothing now goes back on the cradle with what it got.
- Closing the program now gives back an address it claimed with
  `--claim-ip`. It used to leave the address on the network adapter after
  the program was gone, on the window and the headless paths alike, so the
  next run collided with it.
- `--capture FILE` now records whatever the exposure is set to. It used to
  do nothing at all unless the exposure had been raised, so a capture asked
  for by name on a bench run recorded nothing and did not even create the
  file. The Capture view is shown whenever a capture is running, not only
  on a raised exposure, and a bench run prints where the capture is going.
- The Capture view now says which file it is filling. That line was blank
  unless you had opened the exposure dropdown and changed the level.
- The capture now says when it stops reaching the disk. A full disk, a
  revoked permission or a removed volume used to stop the recording in
  silence while the exchange counter went on climbing, which looks exactly
  like a quiet night. It names the reason, counts the exchanges lost to it,
  and says so once on the terminal and in the window, and again when the
  recording comes back.
- A short public record of what changed in each release, which is this file.
  The long one stays private: it carries internal register ids and the
  reasoning behind each fix, neither of which a public reader needs.
- The Input Generator Report draws the table its manual draws: two header
  lines, the start and end volume columns it was missing, a rate column,
  and the duration as hours and minutes rather than decimal hours.
- Known issues recorded against the exposure setting added in 0.4.0. Until
  they are fixed, do not leave this on a public address unattended.
- Checked against a real TLS-350 that could be programmed, not only read.
  A Set now answers the way the console does: an accepted Set replies with
  that device's report, and a refused one replies with a question mark for
  each character sent, not with the unknown-command reply.
- The console's own rules for what a Set will take: the length range of
  every pressure line pipe type, the three bands a tank density is told
  apart by, how bytes above 7F in a label are stored, and the autodial
  methods and times. Entering a density now sets the tank's thermal
  coefficient, as the console does.
- A blank console reads as a real one out of a cold start: its defaults,
  three pressure lines per controller, a row for every tank and line
  position, bare replies for devices that are not there, the reconciliation
  and receiver reports on a console without their key or modem, and the
  pressure line setup report laid out as the console prints it.
- The setup data warning on a pressure line is its unentered length and
  nothing else, and asking over the serial port no longer shows as a
  connection in the communication status report.
- The Input Generator Report's table now sits at its page's own columns; it
  was three columns to the right.
- Every documented setting was sent the edges of its range on the real
  console, and a Set now takes, refuses or ignores the same values: the
  ranges the serial port holds, how far a value is read before the rest is
  dropped, which settings are answered the same whatever they carry when
  their card, key, probe or pipe type is absent, and settings that change
  another (a thermal coefficient and its density, a full volume and its
  profile, the older test warning codes and their tank and line twins).
- Several setup reports lay their values out as the console does for more
  than one value: numbers aligned with their units after them, negative
  numbers, and the words it prints.
- Checked in computer format against the real console for the first time,
  every code it answers. A code for a card, key or feature the console has
  not got now answers an empty reply, as the console does, instead of the
  unknown-command reply; a setting asked for every device lists every
  position with its default; and reports of tank measurements are empty
  when no probe is reporting.
- Metric and imperial units work over the serial port, as measured on the
  real console: values are stored in U.S. units and converted on the way in
  and out, with the console's own factors, unit words and rounding, in both
  formats. The panel and the printed reports still read U.S. units.
- A Set in computer format follows the console's rules too: data too short
  for its field gets an empty reply, whole numbers are held to their
  ranges, and changing a pressure line's pipe type clears the settings the
  new type does not use.
- A device number past what the console has answers an empty reply, and
  the receiver hangup method (535) can be set.
- Every reply ends with the number of blank lines the real console sends
  for that report, which turned out to be fixed per report.
- The 50 point tank chart report and the fuel management setup report are
  laid out and filled as the console does it.
- Recorded how the real console reports a pressure line that is switched on
  with no sensor connected, for a later change; nothing about it is built
  yet.
- The pressure line test results and history reports read as the console's
  do, including two report codes no manual lists; the line setup and alarm
  assignment reports list every line; an unset maximum volume follows the
  tank's full volume.
- All six date and time formats stamp replies, alarm rows and the display
  as the real console does.
- The ground temperature inputs on the probe and thermistor card read the
  resistance wired to them, the way the real console reads it (measured
  with resistors on its own card), and the bench window can put a
  resistance on each input, so a shorted or open thermistor can be staged.
- The pressure line diagnostic's A/D counts follow the real console's
  sensor board, measured with resistors on its transducer inputs, including
  the line's pressure offset; a line with no transducer reads -1 counts.
- Checked at the real console's keypad. The status line now shows each
  standing alarm for one second in turn without blanking, and both warning
  lamps flash. A refused entry stays on the display as typed instead of
  snapping back. The time setup screen pads its hour. The temperature
  compensation screen takes the range, sign and entry style the real one
  does. A printer error that clears on its own leaves the display at once.
- A receiver's autodial date starts empty, as the real console's does after
  a cold start, and prints as it does: question marks for the month, then
  the console's own day and year.
- A tank's full volume is one setting however it is entered, as on the real
  console, and the fifty point full volume reads zero unless the tank uses
  that chart. The leak test method report shows tank 1's early stop setting.
- A Petrotechnik pressure line has a thermal coefficient and a passive
  0.10 test setting, as a user defined one does.
- Five more computer format replies match the real console: the receiver
  report list, the tanks' last annual test, the line lockout schedule, the
  communication status, and the pressure line setup report, which the real
  console answers empty rather than refusing.
- A console on software version 26 or older refuses the ALTERNATE-HT
  dispense mode for a pressure line, as the real version 26 console does.
- A receiver can only dial from a port with a card in it other than the
  RS-232 board, as on the real console. Every setting the real console was
  sent over the serial port is now taken or refused as it did.
- The software revision report tells the truth about the console: its
  S-Module part number follows the feature keys installed, version 26
  reports the real chip's build, the peripheral controller reports its own
  date and live counters, and line leak test rates follow the system units.
- After a cold boot the clock comes back at 8:00 AM on the date the
  console's software was made, as the real console's does, instead of
  keeping the time and raising a clock alarm.
- A command cut short by the start of the next one is dropped, as on the
  real console, instead of being read together with it.
- With serial security switched off, a security code in front of a command
  is not understood, as on the real console, and a device number with a
  letter in second place answers empty instead of being refused.
- On a public address, replies now start and flow at the pace the real
  console's were measured at, a short pause and then the serial line's
  speed, instead of a long pause and then everything at once.
- A pressure line with its transducer disconnected behaves as the real
  console's did: its test is aborted, dispensing is disabled while the open
  alarm stands, and the pressure reads zero. The line status, diagnostic,
  profile test, offset test and offset monitor reports are laid out as the
  real console lays them out, and report no data until a test has run.
- A tank's alarms are listed under the category TANK in the alarm reports,
  as on the real console.
- With mass/density switched off, a tank's density cannot be set, as on the
  real console, and setting a thermal coefficient clears an entered density
  only while mass/density is on.
- The cleared alarms report lists priority alarms before the rest, as the
  real console's does.
- The undocumented "print precision line test results" setting can be
  switched on and off over the serial port, as on the real console.
- The undocumented tank maximum volume limit can be set over the serial
  port, with the real console's own quirks: whole gallons, six digits read,
  and a value past 65535 wrapping round to zero.
- Reading the display over the serial port shows what the panel is showing,
  menus and part-typed entries included, as the real console's mirror does.
- Each alarm history keeps fifty rows, as the real console's does.
- The tank alarm history reads as the real console's: grouped by alarm type,
  the three most recent of each, listing a tank while it is switched on
  rather than only while its probe reports.
- A pressure line with nothing on its transducer reads DISABLED and TEST
  ABORTED and will not dispense, as the real console's does, and no longer
  raises a line open alarm until a test is run on it.
- The line alarm history lists the lines that are switched on, and counts
  alarms only, not the clears, as the real console's does.
- Setting the clock over the serial port works as the real console's does:
  the date and time are read as YYMMDDHHMM, a date that is not a date is
  refused rather than rolled over into the next month, and the reply carries
  the time the console is leaving with the digits it took.
- A setting sent over the serial port is acted on as soon as its value is
  complete, with no carriage return needed, as the real console does. A value
  short of its field waits, and a name or a decimal still ends at the return.
  The computer format reads its own widths, which are not the display's.
- The print header and the shift start times answer one line at a time, as
  the real console does, and a shift time is right aligned like the paper's.
- The communication status report records a session that was cut off in the
  middle of a command, as the real console does, and says nothing about one
  that came and went cleanly.
- A tank's thermal coefficient is the real console's own curve, measured from
  it at 46 densities rather than taken from the published table, and the
  report rounds it the way the real console rounds it.
- The meter and tank map answers nothing at all in computer format, as the
  real console does, where this answered an empty reply.

## 0.4.0 - 2026-09-18

- An exposure setting decides who may reach the ports this program opens:
  this machine only, a network you trust, or a public address. On a public
  address nothing the network says is written to disk, connections are
  capped and rated per source, replies are delayed so an instant answer does
  not give the emulation away, and every byte is captured.
- A Capture view shows each exchange with its source address, direction and
  command, and appends the same rows to a file as JSON lines.
- A site can be loaded and saved from the Console menu, rather than only
  from the command line.
- Security fixes across the serial port, the web manager, the setup menu and
  discovery. A stored value could forge a reply frame; one command could
  stop the console answering for the life of the process; the setup password
  was readable by any unauthenticated probe; two web pages changed the card
  on a GET; and the console's own timer could be stopped by a fault while
  the program still looked healthy.
- Thirteen fidelity corrections, including volumetric line tests that ran
  far longer than the table allows, two reports that ran over the paper
  width, a setup field that forgot the height just entered, and
  twenty-three screens that had never been drawn.

## 0.3.0 - 2026-09-14

- The four alarm-assignment functions ask the questions their manual lists.
- The tank and meter map has a setup screen.
- An output relay can carry more than one alarm.
- An emergency generator can be told which tanks feed it.
- Several setup screens named the wrong device, or none at all.

## 0.2.0

Not released publicly. The version exists in the source history only.

## 0.1.2 - 2026-08-31

- A portable download, for sites that cannot fetch an installer at all.
- The checksum file covers the portable zip as well as the installer.

## 0.1.1 - 2026-08-26

- The installer's file name no longer disagrees with its checksum.
- Two source files were missing their licence header.

## 0.1.0 - 2026-08-26

- First release. The console face, its keys and its menus, driven the way
  the hardware is driven.
- A bench window beside the console, showing the site as what it is: each
  tank buried with a probe down it, floats you can drag, sensors, lines and
  dispensers.
- A probe can be unplugged at the tank, which posts the alarm a pulled
  probe posts in the field.
- The TCP/IP interface card is emulated: the setup menu, the web manager
  and device discovery.
- A self-updater, under Help, and a Windows installer that needs no
  administrator rights.
