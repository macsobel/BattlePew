# Glossary — Battle Tag Reverse-Engineering Terms

Concise definitions grounded in the research sources. Each entry states
what the term means in this system; entries that are detailed elsewhere
link to the relevant document in this directory. "Original" means the
1.1.354 game unless stated otherwise.

## A

**Ammo Pack** — An RFID T-Box that restores a gun's ammunition when
scanned. Scanning is handled by the gun itself; the host sees only the
ammunition reset in the next status report. See also *T-Base*, *T-Box*.

**Announcer / Game Master** — The host-side voice system that plays
spoken callouts during setup, play, and rankings, driven by an engine
with priorities, probabilities, and time offsets. The spoken files are
the `DLG_GM_*` WAVs. See [11-asset-inventory.md](11-asset-inventory.md)
and [10-host-software-behavior.md](10-host-software-behavior.md).

**Assist / assistance** — Gun voice prompts that tell the player to scan
a T-Box (for example, to scan an ammo pack when empty, or a medkit at
low health). Controlled by an assistance setting (default on) and
implemented through the `ASSIST_*` sound names. See
[09-gun-audio.md](09-gun-audio.md).

**Avatar** — One of 12 animal mascot heads (Bear, Bird, Bull, Cat, Dog,
Dragon, Eagle, Lion, Panda, Puma, Shark, Snake) used as a player's visual
identity. The gun-stored profile records an avatar as an icon filename.
See [08-profile-write.md](08-profile-write.md).

## B

**Bank (sound bank)** — A `.bin` file holding a set of gun sounds in a
container with a magic, entry table, payloads, trailer, and MD5 checksum.
Families are DSN1–DSN5 (audio) and DANI (animations). See
[09-gun-audio.md](09-gun-audio.md).

**Base / T-Base** — See *T-Base*.

**Beam power** — A 1–5 gun setting (default 3) sent in the game
configuration, controlling infrared behaviour (range / ambient-light
suitability). The scale direction is known (1 shortest/indoors, 5
longest/sunny); the physical infrared mapping is unverified. See
[14-open-questions.md](14-open-questions.md).

**Bit / byte order note** — Configuration values in host messages are
16-bit big-endian. The profile-block offset and data-length fields are
16-bit little-endian. Status-report numeric fields are decoded by the
Lua as big-endian 16-bit pairs in the decoded argument list. Do not
assume one endianness everywhere.

**btasm** — An abandoned gun-bytecode compiler project (perotinus) plus
an unfinished specification. Establishes the CONFIG/SEND/RECEIVE
variable model. See [13-community-mods.md](13-community-mods.md).

**btbase** — An abandoned T-Base tooling project (perotinus) documenting
the gun-to-tag radio format. T-Bases are Mifare Ultralight tags. Not a
PC-to-UbiConnect protocol source. See
[13-community-mods.md](13-community-mods.md).

**Bytecode (gun program)** — The gun-side program for a game mode,
written in a human-readable `.ByteCode.txt` form (states, events, sound /
motor / LED / infrared directives) and uploaded to each gun at game
setup via the flash-write commands. The gun executes it locally during
play; the host polls status and sends configuration, but the gun owns
moment-to-moment behaviour (shooting, hits, scanning, sounds, lights).

**Bytecode upload** — The host-to-gun transfer of a mode's gun program at
setup, using flash commands at address `0x4000` in 58-byte chunks. Not
to be confused with the profile write, which uses the same flash
commands at address `0x00000000` with a single chunk. See
[08-profile-write.md](08-profile-write.md).

## C

**Capture (evidence)** — A USBPcap/Wireshark recording (`.pcapng`) of USB
traffic between the original game and the UbiConnect, labelled by the
action performed. Captures 00–47 are the evidence base for the protocol
map. Frame numbers cited in these documents refer to those captures.

**CC1111** — The TI system-on-chip (8051 + radio) used in the UbiConnect,
noted in forum discussion. Radio-layer context only; irrelevant to the
USB HID layer.

**ClassId** — The player number assigned sequentially from 1 by the
proxy during pairing. The classId *is* the player number: the displayed
name and the rankings callouts are derived from it, and the gun display
shows it. It differs from the protocol id by one (protocol id = classId
+ 1). See [10-host-software-behavior.md](10-host-software-behavior.md).

**CONFIG / RECEIVE / SEND** — The three variable classes of a gun
program's interface. CONFIG variables are uploaded at game start; RECEIVE
variables are pushed from host to gun during play; SEND variables are
read back in status reports. The counts must match exactly between host
and gun program.

**Countdown** — The pre-round countdown sent to guns as a value (10
seconds for automatic launch, 5 for manual). Guns count down locally in
silence; there is no countdown-complete message.

## D

**DANI** — The animation-bank family: LED/gauge animation programs, not
audio. 13 entries in the original bank. See
[09-gun-audio.md](09-gun-audio.md).

**Dashboard** — The T-Blaster's built-in LED display showing a large
digit (ammunition or health, toggled by a button) with a small indicator
light. Red-on-black seven-segment style.

**DATA (profile block)** — The second magic word in the profile block's
table of contents, preceding the offset and data-length fields. See
[08-profile-write.md](08-profile-write.md).

**Device reference (ref)** — An 8-byte identifier presented by a device
(gun or UbiConnect) during connection and carried in pairing
acknowledgements and the revision request. The gun validates the
reference in the revision request: a stale reference goes unanswered.
References are per-device and stable across hosts. Real reference values
are never reproduced in these documents; implementations must use the
live reference presented by the hardware. The 16-byte connection-request
argument is understood as two 8-byte references.

**DSN1–DSN5** — Audio bank families. DSN1 holds short effects (SC codes),
DSN2 holds SW-coded effects, DSN3/DSN4 hold spoken prompts (SD codes),
DSN5 (tkdrob only) holds SG-coded prompts. See
[09-gun-audio.md](09-gun-audio.md).

**Dual-wield** — A pairing in which two guns with the same profile name
on the same team merge into one player; the secondary gun mirrors the
primary's trigger state. Documented in the tkdrob bundle; wire
verification is open.

## E

**Echo / echo rule** — The behaviour by which every host message is
echoed back as an inbound report, with the target id substituted for the
broadcast id. The first report after a send is routinely the echo, not a
reply. See [08-profile-write.md](08-profile-write.md) §6.

**Efeion** — Author of "Unofficial BattleTag", the only prior code that
spoke HID to a UbiConnect (Java + hidapi, 2012–2015). Mostly confirmed by
captures, with specific contradictions documented. See
[13-community-mods.md](13-community-mods.md).

**EndMatch / EndRound** — Match-flow stages that close a round/match,
play end-of-match announcements, and send the game-over message. See
[10-host-software-behavior.md](10-host-software-behavior.md).

## F

**Flash commands** — The host messages that read, erase, and write gun
flash memory: unlock/lock, flash read, flash write header, and flash
write (chunk). Used both for bytecode upload (address `0x4000`,
broadcast) and profile write (address `0x00000000`, unicast). See
[08-profile-write.md](08-profile-write.md).

**Frag** — An elimination: a victim reduced to zero lifepoints. Scores 3
points in the frag modes (a hit scores 1). The gun program tracks the
transition to the death state; the host scores from status changes.

**FreeFrag / Free For All / TeamFrag / etc.** — Game modes. The original
1.1.354 modes are Free For All, Free Frag, Last Man Standing, Wild West
Duel, Starter Frag, Tag'n Shoot, and Team Frag, plus the Introduction
tutorial. Mode rules are summarised in the master document; host
behaviour per mode is in
[10-host-software-behavior.md](10-host-software-behavior.md).

**Friendly fire** — The TeamFrag-only setting controlling whether
same-team hits score. Default off: same-team hits reduce the victim's
lifepoints in the gun program but score nothing. See
[10-host-software-behavior.md](10-host-software-behavior.md) §7.

## G

**Game Master** — See *Announcer*.

**gameoverSnd** — The gun-bank sound code sent in the game-over message
(`SD16` by default, `SD07` for Duel and Tag'n Shoot). A 4-byte ASCII bank
reference, not a host audio file. See [09-gun-audio.md](09-gun-audio.md).

**gameplayData** — The downlink state the host carries in each status
poll. Its second element holds the trigger state: 0 normal (trigger
live), 6 dual-wield secondary mirror, 7 trigger disabled.

**gunseconds** — The gun's own clock field in the status report, used as
a heartbeat. A clock stalled for more than 5 seconds indicates vest
disconnection. Units beyond the stall rule are unverified.

## H

**Harness** — The vest/harness connection as seen by the host. The
harness check polls whether a vest is connected; the reply's first
argument is 1 when connected. "Harness" and "vest" refer to the same
physical connection in different sources.

**Heap** — The per-player set of configuration values the host builds
from mode settings (lifepoints, ammunition, clips, beam power, volume,
swap, respawn time, medkit, and per-mode extras) and sends to the gun.
The heap contents are the authoritative checklist for the game
configuration message.

**HID report** — A USB Human Interface Device transfer unit. The
UbiConnect uses 64-byte interrupt reports (65 bytes including the
implicit report-ID byte at the hidapi level). Only the leading bytes up
to the message length are meaningful; the tail is stale buffer residue.

**Hit-id shift register** — The 5-slot, most-recent-first list of
shooter ids carried in status reports (wire bytes 12–16 in TeamFrag,
8–12 in FreeFrag). Each new hit inserts the shooter id at slot 0 and
shifts older ids right. Slots are not cleared at game start. See the
protocol documents and [10-host-software-behavior.md](10-host-software-behavior.md).

## I

**id (protocol id / radioProtocolId)** — The wire identifier for a
device in host messages: 1 for the UbiConnect, 2 and up for guns in
pairing order. Distinct from classId (player number) and from the device
reference. Broadcast messages use id `0xFF`; echoes substitute the
receiving gun's id.

**IMA ADPCM** — The audio codec used in the gun sound banks: IMA
adaptive differential pulse-code modulation, high-nibble-first, mono.
See [09-gun-audio.md](09-gun-audio.md).

## L

**Lock / whitelist** — Proxy connection control. When the proxy is
locked, a device may connect only if whitelisted and the game is in
progress (or the reconnect setting permits it). See
[10-host-software-behavior.md](10-host-software-behavior.md).

## M

**Manual launch** — A launch mode in which players press the trigger when
ready, followed by a 5-second countdown (against 10 seconds for automatic
launch). Tag'n Shoot defaults to manual launch.

**Medbox / medkit (Med-Kit)** — An RFID T-Box that restores a gun to full
health when scanned. The gun handles the scan; the host sees the health
restoration in the next status report. "Medbox" and "medkit" are used
interchangeably in the sources for the same hardware. The medkit
expansion-pack gating in the original (extra teams/bases) is dropped in
later builds; treat all of it as included.

**Mifare Ultralight** — The RFID tag type used by T-Bases (Ultralight /
Ultralight C), documented by the btbase project. Gun-to-tag layer only.

## N

**nbHit** — The hit-count field as decoded by the Lua from the status
report's argument list (hits received by the reporting gun in the frag
modes). Not to be confused with hits *landed*, which the host derives
from opponents' hit counts.

## P

**Pinger** — The host module that sends keep-alive pings, tracks device
silence, and unregisters devices silent for at least 80 seconds. It also
covers the harness-check polling behaviour during setup. See
[10-host-software-behavior.md](10-host-software-behavior.md) §4.

**PlayersSetup / PlayersManagement** — Match-flow stages. Management
detects guns; Setup assigns profiles, avatars, and teams, sends the game
configuration, and polls harness state while waiting for game start. See
[10-host-software-behavior.md](10-host-software-behavior.md).

**PRFL** — The magic word at the start of the gun-stored profile block's
table of contents. See [08-profile-write.md](08-profile-write.md).

**Profile** — The name + avatar icon stored in a gun's flash, read back
during pairing and written by the profile-write sequence. See
[08-profile-write.md](08-profile-write.md).

**Proxy (ULUsbProxy)** — The host-side USB proxy object that owns the
device list, dispatches inbound messages (initialisation, connection
request, firmware update), handles pairing acknowledgements and the
lock/whitelist logic, and assigns protocol ids and classIds.

## Q

**Quartz engine** — Ubisoft's closed native engine hosting the game,
including the HID transport layer. The Lua scripts run on top of it; the
native layer originates no game commands — it is a pure transport for
Lua-built messages.

## R

**RadioProtocolId** — See *id*. The Lua name for the protocol id carried
in messages.

**ref** — See *Device reference*.

**Rejoin** — Reconnection of a dropped gun to a running match, governed
by the reconnect setting and the whitelist. The rejoin flag clears once
the gun reports a hit count greater than zero.

**Respawn** — Return to play after elimination. Routes in the original:
shoot the UbiConnect, scan a T-Base, or wait the configured respawn time
(0 seconds means instant in the settings as documented). The
scan-to-restore timing is unverified in detail. See
[14-open-questions.md](14-open-questions.md).

**Revision** — A 4-byte device revision number exchanged during
connection (distinct values for guns and the UbiConnect). What the
numbers denote is not established. The Revision application state is the
firmware/update check. Forced revision (`REG_FORCEREVISION`) is a
tkdrob-bundle mechanism that forces a gun reflash — do not use it.

**RFID** — Radio-frequency identification. Guns carry an RFID scanner
used to read T-Boxes (ammo, medkit, bases). The gun reads tags itself;
no distinct RFID USB message exists.

**RoundLoop** — The match-flow stage that runs the game: polling,
scoring, kill feed, timed announcements, vest supervision, and rejoin
handling. Each mode has its own RoundLoop module. See
[10-host-software-behavior.md](10-host-software-behavior.md).

## S

**SD codes (SD01–SD19)** — Spoken-prompt entries in the DSN3/DSN4 banks
(SD01–SD17 original; SD18–SD19 tkdrob-only). Addressed by 4-character
code in gun programs and in the game-over message. See
[09-gun-audio.md](09-gun-audio.md).

**SC codes (SC01–SC11)** — Short effect entries in the DSN1 bank. Only
SC01 and SC06 have proposed mappings; SC11 is dangling. See
[09-gun-audio.md](09-gun-audio.md).

**SG codes (SG01–SG07)** — Spoken prompts in the tkdrob-only DSN5 bank
for the bundle's new modes. See [09-gun-audio.md](09-gun-audio.md).

**SW codes (SW12/SW22/SW42/SW56)** — Effect entries in the DSN2 bank,
role unknown. See [09-gun-audio.md](09-gun-audio.md).

**SND names** — The symbolic sound names used in gun programs (SHOOT,
HURT, RELOAD, RELOAD_CLIP, EMPTY, SCAN_GOOD, SCAN_BAD, DEAD, RESPAWN,
START, OK, BIP, and the ASSIST_* set). The mapping from names to bank
codes lives in the native engine and is only partly established. See
[09-gun-audio.md](09-gun-audio.md).

**Stale tail** — The meaningless trailing bytes of a 64-byte HID report
past the message length: residue from an earlier, longer message in the
same buffer/ring slot. Never compare full reports or interpret tail
bytes as protocol.

**Status poll / status report (`0x95`)** — The host-to-gun poll and the
gun's reply carrying all gameplay state (shots, hits, health, ammunition,
clips, shooter ids, gun clock). There are no separate event messages for
trigger pulls, hits, or RFID scans; everything arrives through polled
status. The poll also carries downlink gameplay data to the gun.

**Swap** — A setting (default off) under which scanning the same T-Box
twice in a row is rejected; the player must alternate boxes. Renamed
"Alternate boxes" in plain-language UI proposals.

## T

**T-Base** — An RFID base unit: team/base blocks used for respawning
(scan your own team's base), scoring in objective modes, and (in
Tag'n Shoot) ordered stage tags. Team bases exist for teams 1–4 in the
original hardware. The original game worked with a single T-base per
computer; multiple distant bases would need multiple computers.

**T-Blaster** — The handheld gun: trigger, mode button, RFID scanner,
flash LED, and LED dashboard showing health/ammunition. Wired to the
sensor vest. Contains an 8051-based controller, a speaker, a vibration
motor, and LEDs, and runs the uploaded gun program.

**T-Box** — Generic term for the RFID boxes: Ammo Packs, Med-Kits, and
T-Bases collectively.

**tkdrob** — Author of the Battle-Tag community mod (2016–2018), a Lua +
asset overlay on 1.1.354 adding modes, classes, supervision features,
and new audio. See [13-community-mods.md](13-community-mods.md).

**TOC (table of contents)** — The 17-byte header of the gun-stored
profile block (`PRFL`, number, revision, `DATA`, offset, data length),
preceding the name/icon record. See
[08-profile-write.md](08-profile-write.md).

## U

**UbiConnect** — The USB radio relay linking guns to the host PC: USB
HID device, vendor/product ids `0x044F`/`0xB365`, vendor usage page
`0xFF01`, protocol id 1. It also serves as a shootable target in
Tag'n Shoot and the tutorial (the `0xC3` path, unverified on hardware).
Radio: 915 MHz band, TI CC1111. It is a HID device, not a serial port.

**Unregister** — Removal of a device from the proxy's device list, on
timeout (80 seconds of silence, when enabled or in-game) or other
disconnect handling.

## V

**Vest (sensor vest)** — The wearable sensor unit wired to the T-Blaster,
with 4 infrared sensors (front, back, left/right shoulder). Vest
connection state is checked by harness polling and supervised via the
gun-clock stall rule.

## Sources

- `BATTLE_TAG_MASTER.md` (system overview, hardware, protocol evidence,
  modes, settings, flows).
- `TKDROB_RESEARCH.md` (application-level protocol, supervision terms,
  variable model).
- `GUN_BANK_DECODING.md` (bank/code terms, codec).
- `SPEEDUP_RESEARCH_2026-11-10.md` and `GHIDRA_PASS_2026-11-10.md`
  (HID packing, native transport, command inventory).
- `sniffing-results/protocol_map.json` (wire terms, field layouts,
  cadences, closure-by-absence findings).
