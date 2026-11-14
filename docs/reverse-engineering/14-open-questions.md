# 14 — Open Questions

Everything unverified found across the reverse-engineering sources, with
the experiment that would settle each item. Items are graded
**UNVERIFIED** unless stated otherwise. "Settling experiment" means a
concrete capture, replay, or measurement — not further desk reading,
which has already been exhausted for these items. Items marked
**do-not-test** must not be settled by the obvious experiment, for the
reason stated.

Sources: `BATTLE_TAG_MASTER.md` §10 (questions Q1–Q25, with resolutions
noted), `sniffing-results/protocol_map.json` (merge notes and per-entry
open notes), `GUN_BANK_DECODING.md` §7, `EAR_CHECK_PROMPT.md`,
`TKDROB_RESEARCH.md` §6, `GHIDRA_PASS_2026-11-10.md` (caveats),
`GUI_SCREENSHOTS.md` §10.3 (unresolved video details),
`SPEEDUP_RESEARCH_2026-11-10.md` §§3/8. Resolved items are listed in §4
so they are not re-asked.

## 1. Protocol and hardware behaviour

### OQ-01 — `0xC3` UbiConnect-hit path: UNVERIFIED on real hardware

**What is known:** Tag'n Shoot Lua treats command `0xC3` as a UbiConnect
hit: the player shoots the UbiConnect itself for a point, and the host
plays one of three UbiConnect-hit announcer reactions at priority 1. The
Introduction tutorial also has a "shoot the UbiConnect" stage. No capture
in the set demonstrates the `0xC3` traffic: no frame, direction, payload,
or reply is documented, and the protocol map contains no `0xC3` entry.
Whether the UbiConnect reports the hit as a distinct message, whether it
arrives through the status stream, and what the host sends in response
are all unknown. "Tower-hit" is used here as a synonym for this path
(the UbiConnect as a shootable target); no separate tower device is
documented.

**Settling experiment:** Run Tag'n Shoot on the Windows machine under
USBPcap with a shot marker. Shoot the UbiConnect deliberately several
times, including at least one confirmed hit (score increment on screen)
and at least one miss. Diff inbound traffic in tight windows around the
markers against baseline polling traffic. Record the full report bytes,
direction, and the host's following messages, and correlate with the
on-screen score and announcer reaction. Repeat in the Introduction
tutorial's shoot-the-UbiConnect stage to check whether the same path is
used there.

### OQ-02 — Profile write: never observed on the wire

**What is known:** The write sequence, block format, and retry rule are
reconstructed from Lua only (see
[08-profile-write.md](08-profile-write.md)). No capture covers a profile
edit. Unknown: the exact acknowledgement bytes for each step (especially
the `0x83` chunk step and the offset-advanced criterion that distinguishes
a genuine acknowledgement from an echo); the semantics of the TOC number
byte and offset field; maximum name length and allowed characters; the
full set of valid icon filenames and gun behaviour on an invalid one;
the erased page size at address 0; behaviour if the sequence is
interrupted between erase and lock; and whether lock/unlock argument
values mean anything beyond the two observed values.

**Settling experiment:** Edit a profile in the original game under
USBPcap, with markers at edit-confirm time, using a distinctive test
name and icon. Capture the full unicast sequence and the replies. Compare
each reply against the echo of the preceding send to isolate the genuine
acknowledgement bytes. Afterwards, read the profile back with the `0x84`
request and confirm the stored values. Repeat with a second name/icon
pair to separate fixed bytes from name/icon-dependent bytes. Do not run
this experiment by replaying invented bytes — capture the original game
first.

### OQ-03 — Respawn-timer precision

**What is known:** Respawn via a team-base scan restores the health field
in the next status reports, with observed scan-to-restore lags of about
3.0–3.3 s in captures 15 and 19, and about 8.2 s in capture 34 with the
respawn setting at 5 — inconsistent with a simple 5-second scan-to-restore
timer, though capture 34's marker is approximate. Capture 32 shows no
automatic return over 107 seconds without a scan. What the respawn
configuration value actually times (delay before the scan is accepted?
delay before restoration? something else?) is unknown.

**Settling experiment:** Isolated respawn test at each respawn setting
(0, 5, 10) with precise markers: knock a gun out, record the exact scan
moment (marker key at scan, plus on-screen/game-log time), and measure
the scan-to-restore interval in the status stream to sub-second
precision. Run at least 3 trials per setting. Also test with no scan at
each setting to confirm whether any automatic return exists, and test a
scan attempted before any putative delay expires.

### OQ-04 — Beam-power infrared mapping

**What is known:** Beam power is a 1–5 setting (default 3), sent in the
configuration and confirmed byte-exact in captures (a one-setting
experiment moved the byte from 3 to 5). The in-game guidance associates
1 with indoors/low light, 3 with in a house, and 5 with outside in bright
sun. The actual infrared behaviour — pulse power, range, cone, ambient-
light rejection — is not in any file. The class defaults that touch beam
power (longest range at 5, shortest at 1) confirm only the direction of
the scale.

**Settling experiment:** Controlled range test per beam-power value
(1–5): same two guns, same lighting, measure the maximum reliable hit
distance and the hit rate at fixed distances (for example 3/5/10/15 m),
indoors and in bright sunlight. Record misses as well as hits. This is a
physical measurement, not a capture task; captures only confirm which
value was sent.

### OQ-05 — Multi-UbiConnect support

**What is known:** A web claim (Wikipedia) says 5 UbiConnects support 40
players. No multi-UbiConnect code was found in the local 1.1.354 files,
which evidence a single-UbiConnect build capped at 8 players per
activity. The Efeion Java project's multi-UbiConnect support was
unfinished. A related constraint from the hardware owner: the original
game worked with a single T-base per computer, and multiple distant
T-bases would require multiple computers — deferred, not pursued. Forum
discussion mentions multiple hubs on multiple laptops and notes the
UbiConnect uses a TI CC1111 system-on-chip, but provides no protocol
data.

**Settling experiment:** Enumerate two UbiConnects on one host (HID
enumeration: are both visible with distinct paths?), then attempt pairing
guns through each under the original game and observe whether the host
addresses both, one, or neither. Separately, test two hosts each with one
UbiConnect in the same physical space and observe interference/assignment
behaviour. Do not assume multi-hub operation from enumeration alone.

### OQ-06 — Harness-check disconnected case (`0xC1`, argument 0)

**What is known:** The harness (vest) check poll and the connected reply
(argument 1) are CONFIRMED in captures 16, 17, 46, and 47. The
disconnected case (argument 0) has never been captured. The host-side
handling of a disconnected reply during setup (beyond the flag being set)
is Lua-only.

**Settling experiment:** During PlayersSetup polling, disconnect a vest
(and separately, run a gun with no vest connected from the start) under
USBPcap. Capture the `0xC1` replies and the host's subsequent messages
and on-screen state. Optional and non-blocking for basic play.

### OQ-07 — Command `0x06`: purpose unknown beyond its send site

**What is known:** Lua-only. The message is sent from connected-state
handling (`UTGame.Ui.Connected.lua:287`). No capture frame has been
isolated for it, and no reply or effect is documented. The Ghidra pass
lists it as a boot acknowledgement, but that label is not backed by a
capture in the sources.

**Settling experiment:** Capture a clean launch-to-connected sequence
with markers at each UI transition, and isolate `0x06` frames (if any) by
prefix search. Correlate with the UI state at send time and with any
following traffic. A clean-shutdown capture (OQ-08) may also surface it.

### OQ-08 — Command `0x02` shutdown: Lua-only, no clean-shutdown capture

**What is known:** The shutdown message exists in Lua only
(`ULUsbProxy.lua:99`). No capture of a clean shutdown exists in the set,
so it is unknown whether the game sends it on exit, what (if anything)
replies, and whether guns change state on receiving it.

**Settling experiment:** Capture the original game exiting cleanly
(quit from the menu) with the UbiConnect still attached, and separately
capture the UbiConnect being unplugged while the game runs. Search for
the shutdown message and any replies.

### OQ-09 — Command `0x03` device-reference message and revision numbers

**What is known:** The proxy decodes a device-reference message (`0x03`)
and a 4-byte revision number (`0x04`) in Lua
(`ULUsbDevice.lua:84-86,132-160`). Revision replies are CONFIRMED (guns
decode to one 32-bit value, the UbiConnect to another), but what the
numbers denote is not established. The `0x03` message itself remains
Lua-only: no capture frame is cited for it.

**Settling experiment:** Re-parse the init/pairing captures for `0x03`
traffic with the Lua decode in hand; if absent, capture fresh
init/pairing sequences and search for it. For the revision numbers,
compare values across guns, firmware/bank states, and the UbiConnect,
and check whether forcing a revision changes them (without flashing —
observation only; see OQ-14).

### OQ-10 — Command `0x85` bank/revision check: reply format unknown

**What is known:** The Ghidra pass surfaced an outbound bank/revision
check request sent via the generic send path
(`RevisionCheck.lua`), carrying a 4-byte bank identifier. It was not in
the earlier Lua command table, and no capture frame or reply format is
documented in the sources.

**Settling experiment:** Capture the Revision state of application
startup (fresh launch, guns present) and isolate `0x85` frames and any
following inbound reports. Record the bank identifiers sent and the
reply bytes, if any.

### OQ-11 — Command `0x86` repeated form: field split unknown

**What is known:** The flash command's unlock form is observed, and a
repeated form appears after uploads (five repetitions in capture 16,
also in capture 17) carrying two further bytes that appear to
acknowledge the upload length. The exact field split is unknown.

**Settling experiment:** Compare the repeated form's trailing bytes
across uploads of different lengths (FreeFrag 120 bytes against TeamFrag
1,152 bytes and other modes) and check whether they track total length,
chunk count, final offset, or something else. This is a re-parse of
existing captures plus, if needed, fresh setup captures for other modes.

### OQ-12 — Unexplained status-stream bytes (minor, non-blocking)

**What is known:** Two small anomalies remain in the protocol map's merge
notes: bytes 17/18 oscillate in capture 47 game 1, and stream id 04
starts capture 47 game 2 with one hit-id slot already holding value 05
(consistent with slots not being cleared, but noted separately as minor).

**Settling experiment:** Re-parse capture 47's status streams with a
marker-aware differ, correlating bytes 17/18 changes with game events
(shots, hits, scans, deaths). If no correlation appears, capture a fresh
4-gun game and check whether the oscillation reproduces. Non-blocking:
no gameplay field depends on these bytes in the current model.

### OQ-13 — Stream-id stability across re-pairing

**What is known:** Stream ids held across all games in the sniffing
session (two guns kept the same ids throughout). Whether the same
physical gun receives the same id after re-pairing (as opposed to within
one pairing session) is untested. Live replay later used a stable
reference-to-player mapping as a host-side solution, which does not
settle what the original game does.

**Settling experiment:** Pair the same guns, unpair/re-pair (including
via a full game restart and via running the original game to reset),
and record the ids assigned each time against the guns' references.
Test announcement-order dependence by varying which gun announces first.

### OQ-14 — Firmware-update path (`0x22`): DO NOT TEST by flashing

**What is known:** The native layer has a dedicated upload binding that
builds firmware-update messages (command `0x22`, chunks capped at 58
data bytes), and the proxy dispatches an inbound firmware-update message.
No firmware-update flow was ever run, and no update bytes were captured.
The project rule is no flashing and no custom gun firmware (brick risk);
the tkdrob author likewise disclaims responsibility for flashing damage.

**Settling experiment:** None recommended. Do not run the original
updater's firmware-update path, do not send `0x22` traffic to hardware,
and do not flash banks to test this path. The only safe further work is
static: re-read the native upload binding and the Lua revision flow to
document the message shape without executing it. This item stays open
deliberately.

### OQ-15 — Dual-wield secondary mirror against captures

**What is known:** In the tkdrob bundle, two same-name/same-team guns
merge into one player and the secondary gun mirrors the primary's trigger
state via downlink value 6. No capture demonstrates the mirror behaviour
on the wire, and dual-wield pairing was never sniffed.

**Settling experiment:** Configure dual-wield pairing in the modded game
(two guns, same profile name, same team) under USBPcap. Capture the
pairing/merge sequence and the in-game downlink values sent to the
secondary gun while toggling the primary's trigger state. Compare
against the single-gun downlink values.

### OQ-16 — Other modes' status layouts (Lua-only)

**What is known:** Capture evidence covers FreeFrag, TeamFrag, and
Tag'n Shoot setups/games. The Lua-only status decodes for other modes —
Disarm bomb-digit fields, Capture energy field, TheWolf score-slot reuse,
Infection score-increment fields — have no capture confirmation. The
status stream is mode-dependent (different leading size byte and field
positions per mode family), so these layouts cannot be assumed from the
FreeFrag/TeamFrag models.

**Settling experiment:** Capture one game of each mode under USBPcap
with event markers (Disarm digit scans, Capture flag/energy events,
TheWolf transfers, Infection cures), and diff the status stream around
the markers to locate each field. Modes that need tkdrob banks (new
sounds) should be captured with the mod installed, noting the provenance.

## 2. Gun audio

### OQ-17 — 8,000 Hz sample rate: high-confidence but UNPROVEN

**What is known:** See [09-gun-audio.md](09-gun-audio.md) §3.3 for the
full evidence (speaking pace, clean transcription, hardware context) and
the inconclusive cross-check. The rate has never been compared against
the real gun speaker.

**Settling experiment (prepared, not run):** The ear-check package
(`ear_check.py` + prompt). Self-test first (must recover 8,000 / 11,025 /
16,000 Hz on simulated data). Then, per sound: play the decoded
reference through speakers, trigger the real gun, record the microphone,
and compare by duration ratio and spectrum scale, plus the operator's
hearing verdict. Six sounds: SD06 (gun on, no vest), SD01 (empty
magazine + trigger pull), SD13 (following ammo-pack prompt), SD02
(respawn after team-base scan), SD16 (end a game), SC01 (accepted RFID
scan). Even 2–3 sounds decide the rate. Playback + microphone only; no
writes to hardware.

### OQ-18 — SC/SW-to-sound-name mapping (except SC01/SC06 predictions)

**What is known:** SC01 ≈ SCAN_GOOD (strong, context substitution) and
SC06 ≈ BIP (plausible, duration-based) are predictions, not proofs.
SC02–SC05, SC07–SC10, and SW12/SW22/SW42/SW56 are unmapped. The
name-to-code mapping lives in the native engine, not in any file read.

**Settling experiment:** On the Windows machine, trigger each ByteCode
sound name in turn (SHOOT, HURT, RELOAD, RELOAD_CLIP, EMPTY, SCAN_BAD,
SCAN_GOOD, DEAD, RESPAWN, BIP, OK, START) in a test mode and record which
sound the gun speaker plays; compare each recording against the decoded
SC/SW files. Check the SC01 and SC06 predictions first. Combine with the
ear-check session (OQ-17) where triggers overlap.

### OQ-19 — Uncertain gun-prompt hearings

**What is known:** Five machine transcriptions carry uncertainty flags:
SD07, SD10, SD15, SD19, and SG05. SD10's reading is near-certain from
the SD09/SD11/SD12 pattern; the others have genuine alternatives.

**Settling experiment:** Listen to the decoded files and to the real gun
(ear-check session), and have a second listener confirm. No capture
needed.

### OQ-20 — DANI animation program semantics

**What is known:** DANI banks hold LED/gauge animation programs (13
entries, 407 bytes). The tkdrob Animations Guide documents editing rules
(start bytes, distances, trailer, MD5 footer) and a longer code list, but
the per-byte program semantics (which bytes drive which LED/gauge
behaviour, timing, sequencing) are not reverse-engineered. No host
command for LEDs exists — animation is gun-side, bytecode-driven.

**Settling experiment:** Observation only: correlate gun LED/gauge
behaviour with game events (reload, heal, hit, out, game over) on video,
and compare against the DANI entry bytes for the corresponding animation
names. Do not flash edited DANI banks to test hypotheses (see OQ-14).

### OQ-21 — Gun clock (`gunseconds`) units

**What is known:** The status report carries a gun clock field used as a
heartbeat; a stall of more than 5 seconds indicates vest disconnection.
Whether the field counts seconds, ticks, or another unit, and how it
behaves at game start, during countdown, and after rejoin, is not
established beyond the stall rule.

**Settling experiment:** Log the clock field against wall-clock time
during a full game (setup, countdown, play, end) and measure its
increment rate and resets. Correlate stalls with deliberate vest
disconnects.

## 3. Host behaviour, settings, and content

### OQ-22 — Vest-disconnect setting value mapping (0/1/2)

**What is known:** The setting takes values 0/1/2 with default 1. The
sources name an automatic trigger-disable mode and a manual leaderboard-
toggle mode but do not cleanly map all three values to behaviours.

**Settling experiment:** Re-read the vest-handling Lua in both the
original and the bundle with the setting values traced through every
branch, and (if the Lua does not settle it) run the game at each setting
value with a deliberate vest disconnect, observing whether the trigger
disables automatically, flags for manual action, or does nothing.

### OQ-23 — Out-of-range kick time: 10 s vs 30 s

**What is known:** The in-game text says 10 seconds; the printed manual
says 30 seconds. The Lua device-pinger values are in undocumented units.

**Settling experiment:** Take a paired gun out of UbiConnect range during
a game and time the interval to kick/warning at each candidate value,
noting on-screen warnings. Repeat to check consistency. Any replacement
must show a visible countdown and never kick silently, whichever value
proves correct.

### OQ-24 — Respawn Mode setting (`goption011`)

**What is known:** Present in settings data with value 0, commented out
of the options UI. Possible values and effect unknown.

**Settling experiment:** Search all Lua and ByteCode for the setting key
and any variable it feeds; if the search is empty, the setting is dead
in this build and should be recorded as such rather than resurrected.
Do not invent values.

### OQ-25 — StarterFrag exact scoring

**What is known:** The gun configuration sends assistance, ammunition,
and clips only — no lifepoints — so there is no frag/death mechanic.
Whether a hit scores 1 point and whether any bonus exists needs one
close read of the mode's RoundLoop module, which the sources did not
complete.

**Settling experiment:** Read the StarterFrag RoundLoop scoring code
(paraphrase, do not copy) and, if it does not settle the question, run a
StarterFrag game with marked hits and compare the scoreboard deltas.

### OQ-26 — Announcer triggers not fully pinned

**What is known:** (a) Two context-dependent reaction lines (priority 2,
play probability 0.25): exact trigger not pinned. (b) Two teammate-frag
reaction lines (priority 2): exact trigger not fully pinned. (c) The
shared-win line's use for ties: INFERRED, not fully pinned. (d) The
final-rankings callout family base file speaks a race-related line while
the family is computed from classId — mismatch unresolved. (e) Files
with no grounded trigger at all (listed in
[11-asset-inventory.md](11-asset-inventory.md) §4.1, including the
round-transition files and the race/Tag'n Shoot candidates). (f) The
INIT tutorial files have no Lua reference; attribution is content-based.

**Settling experiment:** For (a)–(c): trace the registration calls in the
relevant RoundLoop/Game Master modules and log which game events precede
each line in live play. For (d): check whether the base file is ever
addressed (classId 0 case?) or is dead. For (e): play the modes that the
content suggests (Tag'n Shoot, race-like flows) with audio logging and
correlate lines with events; check the native engine's sound calls if
accessible. For (f): run the Introduction tutorial and log the audio
sequence against the tutorial stages.

### OQ-27 — 5th/6th-team gun support

**What is known:** Gun firmware does not address teams 5/6 according to
the tkdrob bundle, which forces friendly fire on and needs custom T-Base
hardware for those teams. No sniffing proves or disproves gun-side
support for team values beyond 4.

**Settling experiment:** Send a configuration with team value 5 (and 6)
to a gun in a controlled replay (using the proven configuration format,
with the owner's approval), and observe the gun's team-dependent
behaviour (respawn-base acceptance, team-colour display, friendly-fire
handling). Alternatively, capture a 5-team game in the modded build and
inspect the configuration and status traffic. Do not ship 5–6 teams
until this is settled.

### OQ-28 — Player scale beyond 8 on the wire

**What is known:** 1.1.354 caps at 8 players; the 1.2.439-era video shows
16-player Team Frag and 32-player Capture; the tkdrob bundle advertises
32 with a code ceiling of 48. No capture demonstrates more than 4 guns
in one session. Whether the radio/host polling scales (timing, id
assignment, status throughput) is unknown.

**Settling experiment:** Staged scale test with the hardware available
(12+ guns): run games at 8, then above 8 in the build that permits it,
measuring poll cadence, reply rates, id assignment, and any failures.
Record the largest stable player count observed, not the largest
configured.

### OQ-29 — Version 1.2.439: provenance and installer availability

**What is known:** The June 2014 video shows 1.2.439 with community modes,
classes, 16-player/6-team support, and settings panels. Whether it is an
official Ubisoft update or a community-patched 1.1.354 is unknown, and
no 1.2.x installer has been located. The sniffing baseline is 1.1.354
(CONFIRMED on the Windows machine); this question is curiosity-only and
not blocking.

**Settling experiment:** One targeted search for a 1.2.x installer or
release notes (official support channels, forum release threads,
archive sources), plus a version-string comparison if any 1.2.x binary
is found. Do not install any 1.2.x build on the sniffing machine without
the owner's explicit say-so.

### OQ-30 — Online leaderboard

**What is known:** A web source says the game had an online leaderboard /
high-score component. No upload code was found in the Lua files read. It
may live in the native engine or a separate module. Local play does not
need it.

**Settling experiment:** Search the native binary's strings and network
imports for leaderboard/upload endpoints, and check the Lua for any
network or HTTP calls missed in the first pass. If nothing is found,
record the feature as unevidenced in this build.

### OQ-31 — Default volumes as experienced (audio pipeline scaling)

**What is known:** Internal volume levels are documented (music 5/8,
effects 2/8, Game Master 5/8, gun volume level 0 with Off/Low/Medium/High
choices), but perceived loudness depends on undocumented audio-pipeline
scaling.

**Settling experiment:** Measure output levels at each setting step
(host speakers for music/effects/announcer; gun speaker for gun volume)
with a sound-level meter or calibrated recording, and record the
per-step curve. Low priority.

### OQ-32 — UbiConnect range claim

**What is known:** Web sources inconsistently claim 1,000 ft / 300 m /
300 yards. Treat as a hardware claim, not a fact. The radio operates at
915.3–915.7 MHz per the FCC test report (radio layer only, irrelevant to
the USB layer).

**Settling experiment:** Outdoor line-of-sight range test: walk a paired
gun away from the UbiConnect until status polling fails or the
out-of-range warning appears, recording distance and behaviour. Repeat
indoors through walls for the realistic case.

### OQ-33 — Team derivation from a profile in the original game

**What is known:** The tkdrob bundle derives team from a stored gun
default, an icon-filename prefix, or a UI option. The original 1.1.354
mechanism for assigning a team from a profile (beyond the PlayersSetup
team picker) is not established in the sources, and the profile record
itself carries only name and icon.

**Settling experiment:** Re-read the original (non-bundle) PlayersSetup
and profile-decode Lua for team assignment, and observe team defaults in
the original game when a gun with a known stored profile is paired fresh.
Settle together with OQ-02's profile work.

### OQ-34 — Unidentified UI details in the walkthrough video

**What is known:** The colour-coded arrows in the Game List (blue, green,
orange) have undetermined meaning, and the device strips on the in-game
screen (labelled with paired identifiers) have unidentified purpose.
Sound Settings and Activity Settings panels are visible only at
contact-sheet resolution. These are UI curiosities with no wire
relevance.

**Settling experiment:** Re-extract sharp frames of the relevant video
segments, or re-run the same build and interact with the controls while
recording. Low priority; do not guess meanings.

### OQ-35 — French announcer transcription (optional)

**What is known:** 162 French files ship; none transcribed. The French
set is the same lines in French, with one more file than English (extra
file unidentified).

**Settling experiment:** Run the same local transcription pass used for
English over the French set, and identify the extra file by comparing
filename lists. Only if bilingual support is wanted.

## 4. Resolved — do not re-ask

The following were open questions in the sources and are now settled.
They are listed here so future work does not reopen them without new
evidence.

- **HID report packing (resolved).** Host writes 65 bytes (leading
  report-ID byte plus the message); 64 bytes go on the wire; trailing
  bytes are stale buffer residue. Ghidra pass + capture verification.
- **LED/rumble host control (closed by absence).** No host LED/rumble
  command exists in any of the 45 captures or in the Lua command table.
  LEDs/rumble are gun-side, bytecode-driven. Do not run a dedicated
  LED/rumble capture.
- **Hit-id model (resolved).** 5-slot shift register, most-recent-first,
  wire bytes 12–16 (TeamFrag) / 8–12 (FreeFrag), slots not cleared at
  game start. The interim "byte 12 not confirmed" verdict is superseded.
- **Health 5-vs-4 (resolved).** Carried-over damage from an aborted
  session, not a settings change.
- **RFID/medkit/respawn mechanisms (resolved as status-only).** No
  distinct USB messages exist for ammo, medkit, team-base, or respawn
  events; all are visible only as status-report field changes. Medkit
  restores to full health, not +1.
- **Game version baseline (resolved).** 1.1.354 on the sniffing machine.
- **Palette (resolved).** Measured hex values from texture pixels.
- **Bank container and codec identification (resolved to INFERRED, high
  confidence).** Container validated; IMA ADPCM high-nibble-first
  accepted on decoder evidence. Only the sample rate (OQ-17) and the
  SC/SW mapping (OQ-18) remain open.
- **Game-over sting identity (resolved).** SD16 default, SD07 override,
  confirmed on the wire.
- **Pairing ref validation (resolved by live replay).** The gun validates
  the reference in the revision request; stale references go unanswered.
- **HID access prompts (resolved).** No accessory or Input Monitoring
  prompt appears for the UbiConnect on the Mac; hidapi access just works.
- **Ghidra struct-layout micro-question (closed for reproduction).** A
  strict SDK reading of the capabilities struct disagrees with the
  observed offsets, but every behavioural check pins both report lengths
  to 65. No reproduction impact; flagged only for completeness.
- **Global settings presence in the original (resolved).** Playtime,
  beam power, friendly fire, launch mode, assistance, and swap are in
  the original Lua and are original, not modded — local files win over
  the printed manual's omissions.
- **Mode count for this build (resolved).** 7 modes + Introduction in
  1.1.354; extra names belong to the later/community lineage (see OQ-29
  for the remaining provenance question).

## Sources

- `BATTLE_TAG_MASTER.md` §10 (Q1–Q25 with statuses), §3 (remaining manual
  collection: ear-check, respawn precision, optional vest check).
- `sniffing-results/protocol_map.json` (merge notes; per-entry open
  notes for `0x02`, `0x06`, `0x03`/`0x04`, `0x85` absence, `0x86` field
  split, `0xC1` disconnected case, `RESPAWN` timer note, byte 17/18 and
  stream-04 notes, re-pairing stability).
- `GUN_BANK_DECODING.md` §7 (ear-check, SC mapping, DANI).
- `EAR_CHECK_PROMPT.md` (ear-check procedure).
- `TKDROB_RESEARCH.md` §6 (dangling references, 32/48, 5–6 teams,
  flashing).
- `GHIDRA_PASS_2026-11-10.md` (caveats; `0x85`; `0x22` upload binding).
- `GUI_SCREENSHOTS.md` §§10.2–10.3 (unresolved video details; version
  finding).
- `SPEEDUP_RESEARCH_2026-11-10.md` §§2–3, 8 (multi-hub discussion, Lua
  limits, manual-only items).
