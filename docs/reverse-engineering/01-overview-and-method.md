# 01 — Overview and method

Reverse-engineering documentation for the Ubisoft Battle Tag (2010) T-Blaster
system. Goal: a human or AI implementer can recreate compatible host software
from these docs without redoing the USB sniffing. This doc covers what the
system is and how it was reverse-engineered. Wire details live in
[USB transport](02-usb-transport.md) and
[pairing and registration](03-pairing-registration.md); the evidence trail
lives in [capture index and verification](12-capture-index-and-verification.md).

## 0. Conventions used in all four docs

**Claim grades.** Every major claim carries one of these labels:

- **CONFIRMED** — verified in USB captures and/or live replay on real hardware.
- **INFERRED** — Lua source only, a single observation, or a secondhand summary
  (e.g. a Ghidra result reported in another doc, not re-verified here).
- **UNVERIFIED** — open question. Flagged inline so it can be collected into
  the planned `14-open-questions.md`. Nothing labelled UNVERIFIED may be
  implemented as if it were fact.

**Byte numbering.** Zero-based offsets within the 64-byte HID report, after
stripping the report-ID byte — the convention of the session-2 evidence review.
The Lua sources index message arguments 1-based (`arg[1]` is the first argument
byte, i.e. report byte 3 in a `[size][id][cmd][args]` frame). Where a Lua
argument index is cited, it is labelled as such; do not mix the two numberings.

**No verbatim source.** All Lua logic is paraphrased in the author's own words
with file and line citations. No original assets, screenshots, or capture
files are reproduced here. Device references (refs) are never shown: every ref
field is rendered with placeholders (`RR`), or as an explicitly marked
EXAMPLE. See [pairing and registration](03-pairing-registration.md) §1.

**Baseline version: 1.1.354** (CONFIRMED — main-menu version on Aaron's
Windows install, recorded in the session-1 findings; see §4). All captures,
all byte layouts, and all Lua citations describe this build unless stated
otherwise. A later build, 1.2.439, is known from a June 2014 video walkthrough
and already contained community modes, classes, and 16-player support — but
it was never sniffed, and nothing in these docs describes it. Do not mix
versions: if a future capture run uses another version, label it by version
and re-run the baseline before comparing bytes.

## 1. What Battle Tag was

Battle Tag was a real-life laser-tag system by Ubisoft, revealed at E3 2010
and released in November 2010 as a limited test release (Texas Toys "R" Us
stores and Canadian Zellers). Ubisoft stopped supporting it; by July 2012 it
told users it no longer supported the product. The original software is
Windows-only. The most common release is 1.1.354 (`BattleTag.exe`).

The game loop: players wear sensor vests, shoot each other with wired
T-Blasters, and a USB radio relay — the UbiConnect — links the guns to a PC
that acts as Game Master. The host programs the guns for the chosen mode,
tracks time and score, plays music and a voice announcer, and shows a live
leaderboard. RFID "T-Boxes" are scanned with the gun to reload, heal,
respawn, or score.

## 2. System components

| Component | Role | Key facts |
|---|---|---|
| **T-Blaster (gun)** | Player's weapon and RFID scanner | Trigger, mode button, RFID scanner, flash LED, dashboard showing health/ammo. Runs bytecode uploaded by the host at game setup (see §3). Speaks to the host only via the UbiConnect over RF. Stores a paired-proxy bond (see [pairing](03-pairing-registration.md) §1) and a player profile (name + icon) in flash. |
| **Sensor vest** | Hit detection | Wired to the gun. Four IR sensors: front, back, left/right shoulder. A hit is an IR event at the vest, reported to the host only through the gun's polled status stream — there is no separate hit message. Vest-connected state is checked with the 0xC1 harness poll. |
| **UbiConnect** | USB RF base station; the bridge between host and guns | USB HID device, VID `0x044F`, PID `0xB365` (see [USB transport](02-usb-transport.md)). The host's only path to the guns. Protocol id `01`. Also a shootable target in Tag'n Shoot and the tutorial ("shoot the UbiConnect"). Has an LED whose pulsing behaviour was used as a pairing observable during replay work (INFERRED). |
| **RFID T-Boxes** | Ammo Packs, Med-Kits, T-Bases / team blocks | Passive tags read by the gun itself. **No USB message identifies a physical box or its payload** (CONFIRMED — absence across all 45 captures; every RFID effect appears only as a change in the next polled status report). Team blocks exist for teams 1–4. The gun decides acceptance (e.g. swap-mode alternation, full-magazine rejection) and the host sees only the resulting status change. |
| **Host (PC/Mac)** | Game Master | Runs the game logic, owns the match timer and scoring, uploads bytecode and config, polls guns, plays audio, shows the leaderboard. In the original system this was a Windows PC; the replacement target is macOS. The host never talks to a gun except through the UbiConnect. |

### 2.1 Links between components

- **Gun ↔ vest:** wired. The vest's sensors feed the gun; the gun's own clock
  (`gunseconds`, carried in the status stream) stalls when the vest is
  disconnected, which is how the host detects vest loss. Vest loss is not gun
  removal — see [pairing](03-pairing-registration.md) §11.
- **Gun ↔ UbiConnect:** RF. The gun announces itself repeatedly until the
  host acknowledges it; all game traffic is host-polled after that. The RF
  layer itself (frequencies, modulation) was not reverse-engineered and is
  out of scope: the host sees only HID reports.
- **Gun ↔ vest (opponent):** IR. One gun's IR emitter hits another player's
  vest sensors. Range behaviour is set by the beam-power config value (1–5);
  the actual IR pulse/power mapping is UNVERIFIED.
- **Gun ↔ RFID tag:** the gun's scanner reads the tag directly. The tag
  format (Mifare Ultralight family, per the abandoned btbase project) is
  gun↔tag only and is not needed to reproduce host software.
- **Host ↔ UbiConnect:** USB HID, 64-byte interrupt reports. The entire
  protocol in these docs lives at this link. See
  [USB transport](02-usb-transport.md).

### 2.2 Hardware Aaron owns (inventory, 2026-11-09)

At least 12 T-Blasters and 12 vests; many ammo RFID boxes; team RFID boxes
for teams 1, 2, 3, and 4; multiple medboxes; multiple T-bases. The 12-gun
scale matters: the 1.1.354 build caps at 8 players per activity, so the
hardware exceeds the baseline build's cap. Do not design a replacement that
assumes 8 is a hardware limit.

**Single-T-base-per-computer constraint** (Aaron, 2026-11-09): the original
game worked with a single T-base per computer; multiple distant T-bases
would have required running the software on multiple computers. Deferred as
not worth pursuing. Modes that use multiple bases as capture points must
work with the bases in range of one UbiConnect / one computer unless the
multi-computer idea is revisited.

### 2.3 Original game reference (summary)

The 1.1.354 build ships 7 modes plus an Introduction tutorial: Free For All,
Free Frag, Last Man Standing, Wild West Duel (1v1, 5 rounds), Starter Frag,
Tag'n Shoot (the only solo mode), and Team Frag. Mode rules, settings, and
the announcer system are documented from the extracted Lua and are outside
the scope of these four wire-protocol docs, except where a mode changes the
bytes on the wire (config layouts, status-stream formats, countdown and
game-over variants) — those wire differences are recorded in the protocol
map and the capture index. Scoring shared by the frag modes: hit +1 point,
frag +3 points (from the mode RoundLoop sources and the localisation
strings). The match timer is host-side only: duration and launch settings
appear nowhere in the config or bytecode upload (CONFIRMED — captures 41/42
byte-identical to 39; see the capture index).

## 3. The protocol at a glance

The host↔gun protocol, as established by the captures, is deliberately
small. Everything below is CONFIRMED unless noted; frame-level evidence is
in [capture index](12-capture-index-and-verification.md) and the byte-level
authority is `sniffing-results/protocol_map.json` (14/14 messages confirmed).

1. **Framing.** Messages sit at the head of a 64-byte HID report as
   `[size][id][cmd][args]`; `size` is the argument byte count; the command
   byte is at report byte 2. Only the leading bytes are meaningful; the tail
   is stale buffer residue. One exception: the 0x12 pair acks use the first
   byte (`0A`/`0B`) as a new/re-ack discriminator, not a size.
2. **Pairing.** Guns announce (`0x12`) until acknowledged; the host assigns
   a protocol id and a classId (the player number, `id = classId + 1`),
   then runs a revision exchange (`0x04`) and a profile read (`0x84`).
   See [pairing](03-pairing-registration.md).
3. **Setup.** The host uploads mode bytecode over the flash commands
   (`0x86` unlock, `0x81` erase at `0x4000`, `0x82` write header,
   `0x83` chunks, `0x86` lock), then sends per-gun config (`0x92`).
   Settings live in `0x92`, never in the bytecode: the upload is
   byte-identical across settings changes.
4. **Countdown and play.** The host sends a countdown (`0x93`), waits the
   countdown out in silence (there is no countdown-complete message), then
   polls each gun (`0x95`) about every 0.311 s. The poll carries optional
   downlink gameplay data; the reply carries the entire game state (shots,
   hits, ammo, health, clips, hit-id history). There are no separate
   trigger, hit, RFID, health, or ammo messages — all of those are byte
   changes in the polled status reply.
5. **End.** The host sends game-over (`0x94`) with a win/lose colour per gun.
6. **Background traffic.** A broadcast ping (`0x05`), a vest/harness poll
   (`0xC1`, mainly during setup), and flash-manager traffic (`0x86`/`0x06`)
   at startup. There is no host LED/rumble command: gun lights and rumble
   are driven by the gun's own uploaded bytecode (CONFIRMED by absence
   across all 45 captures).

## 4. How the reverse engineering was done

Three legs, in dependency order. The work ran 2026-11-09 to 2026-11-11 for
the sniffing and replay phases, with the pairing/unpairing analysis
completed 2026-11-13.

### 4.1 Leg 1 — USB sniffing on Windows (USBPcap + Wireshark)

USB sniffing does not work on macOS (no libpcap USB support), so all
captures were taken on Aaron's Windows machine, where the original game
runs, using USBPcap + Wireshark with the filter `usb.idVendor == 0x044f`.
(Linux usbmon was noted as an alternative; it was not used.) The sniffing
was done by GitHub Copilot on Windows (VS Code, Agent mode) following
written prompts; its job was sniffing, documenting, and proving the
protocol only — it did not build game, UI, or macOS code.

Method, per capture:

- **One action per capture, labelled.** Files are numbered (`00_idle`,
  `01_plug_launch`, `02_pair_gun1`, …) with a `capture_log.md` recording
  timestamps, frame numbers, UbiConnect light behaviour, and spoken gun
  prompts. Session 1 produced 21 captures (00–20); session 2 added 24
  more (21–47 with gaps — see the capture index). Captures were kept
  short: start, do the action, stop. Long idle captures were the tedious
  part of session 1 and were avoided in session 2.
- **Marker discipline.** A keyboard marker tool (`mark_shots.py`) wrote
  timestamped markers (shot, reload, team-box scan, knockout) to CSV files
  while capturing, so a physical action could be aligned to a frame range.
  Marker-to-frame deltas of 0.07–0.27 s were measured in the marked-shot
  captures, showing small, consistent human-timing error. Caveats: a scan
  marker records an *attempt*, not proof the gun accepted the scan; gun
  labels (A/B) are only as good as the key pressed in the moment; two
  documented marker errors exist (a keyboard mistake and a mislabelled
  team-base scan). See the capture index §1.
- **Baseline first.** Each session verified the baseline before capturing:
  the game launches, sees the UbiConnect, pairs at least 2 guns, and runs
  a FreeFrag game. The main-menu version was recorded as 1.1.354 in both
  sessions. Session 2 additionally re-verified the baseline with
  `medkitPack` turned on (it was off for all of session 1), which changed
  one config word and enabled bases 1–4.
- **Descriptor dump.** The HID report descriptor was read live via hidapi
  (`get_report_descriptor`) and saved as `hid_descriptor.txt`: 34 bytes,
  64-byte in/out reports, no Report ID item, no Feature report. Endpoint
  descriptors came from capture 00 frame 4.
- **Diffing, not guessing.** Captures were diffed per action with `usb-rea`
  (useful for host→device traffic and for separating the background `0x05`
  ping) and with a purpose-built marker-aware differ over the polled `0x95`
  status stream (`status_diff.py`), because the in-game signal is 1-byte
  changes in polled IN reports, which `usb-rea` does not model. A pure-Python
  USBPcap (linktype 249) parser extracted the 64-byte HID payloads after the
  variable-length USBPcap header. Every frame number quoted in the reviews
  was re-checked against the raw `.pcapng` files rather than copied from
  summaries. Bytes were marked confirmed / inferred / unknown with the
  frame numbers that prove them; the original `protocol_map.json` shipped
  with all 14 messages `unknown` and zero filled-in bytes rather than
  invent anything.

Safety rules applied throughout (hard rules from the sniffing prompts):
do not flash gun or UbiConnect firmware, do not write custom 8051 gun
firmware, do not run the original updater's firmware-update path, do not
modify the installed game files (work in a separate folder), and do not
replay (write to hardware) without Aaron's explicit in-session go-ahead.
Aaron's decision, recorded 2026-11-09: no custom gun firmware, ever — brick
risk. Sniffing and replaying normal game packets only.

### 4.2 Leg 2 — Reading the original Lua source

The installer (523 MB, dated Dec 28, 2010, still hosted on Ubisoft's
support site in Nov 2026) extracts with `msiextract` to 1,150 files
(319 MB). The PC-side game logic ships as **215 plaintext `.lua` files**
— no decompilation was needed for the application layer. The Lua was the
shortcut that made the capture analysis tractable:

- The Lua constructs every host→device message as a literal byte list, so
  the command table (message shapes, field order, resend behaviour, state
  machines) could be read directly and then checked against captures.
  Where Lua and capture agree, the claim is CONFIRMED; where only Lua
  speaks, it is INFERRED at best.
- The `0x95` status field layout was read from the mode RoundLoop sources
  before the captures were diffed, giving the differ its hypotheses.
- Pairing, lock/whitelist, Pinger, and removal behaviour were established
  by grep-verified enumeration of call sites across the whole original
  tree (e.g. the only caller of the unregister routine, the only callers
  of the proxy unlock routine) — see
  [pairing](03-pairing-registration.md). That enumeration is what makes
  negative claims ("no quit message exists") trustworthy: they rest on
  exhaustive search, not on not having looked.
- A Ghidra pass over the native executable (reported secondhand in the
  session-2 prompt and the evidence review — INFERRED here, since the
  Ghidra report itself is not among these docs' sources) established the
  HID packing rules (65-byte writes, stale tails) and that the native code
  originates no commands — it is a pure transport, and everything on the
  wire comes from Lua. Two native-only commands (`0x22` firmware data,
  `0x85` bank/revision check) were reported from native code but never
  observed on the wire in any capture; they are UNVERIFIED as wire
  behaviour and the firmware path must not be used.

**Provenance trap — tkdrob is not the original.** The tkdrob community mod
(2016–2018, a Lua delta overlay on 1.1.354) was read as a cross-check and
as a design reference, but its behaviour must never be presented as
original behaviour. Differences that matter for these docs: tkdrob adds
reconnect/unregister settings and an 80 s in-game unregister threshold
that do not exist in the original Lua; it changes upload pacing to scale
with player count (the original re-sends on a fixed 250 ms cadence); and
it raises the USB update frame rate. Worse, several features long
attributed to tkdrob (community modes, classes, 16-player support) were
already present in the 1.2.439 build shown in a June 2014 video, two years
before tkdrob's first commit — so "present in tkdrob's bundle" proves
neither originality nor invention. Where these docs mention tkdrob, it is
only as a contrast, labelled MOD.

### 4.3 Leg 3 — Live replay verification on real hardware

Captures and Lua together produce a model; replay proves the model. The
order was deliberate: Windows replay first (where ground truth was
recorded), then Mac replay, then a minimal game loop. Each write step
required Aaron's explicit session-specific approval.

- **Replay driver.** A small open-once write/read driver (`replay.py`)
  replayed exact 64-byte payloads extracted from captures 01 (init) and 02
  (pair), with per-step PASS/FAIL assertions, dry-run mode, and a
  write-confirmation gate. It was first proven against a simulated device
  (stub-hid), including a ref-validating stub that reproduced a live
  failure exactly (see below).
- **Windows gate (2026-11-11).** Init replay passed 3/3. Pair replay
  failed twice at the revision step (6/7 both times) before passing 8/8 —
  and the failures were the finding: the gun validates the device ref in
  the revision request, so a stale capture ref is ignored while the live
  ref is answered immediately. The drain/read discipline that fixed the
  first failure is documented in [USB transport](02-usb-transport.md) §6.
  After the replay work, Aaron re-opened the original game and the gun
  paired normally — the replay left hardware and game state intact.
- **Mac gate (2026-11-11).** The same sequences on Aaron's Mac via hidapi
  in a temporary venv: probe found exactly one UbiConnect, descriptor
  matched Windows, 10 s idle read silent, init 3/3, pair 8/8, same live ref
  and same decoded profile as Windows (proving the ref is per-gun and
  stable across hosts). Neither the expected "Allow accessory to connect?"
  prompt nor an Input Monitoring request appeared — both open questions
  closed by the live run.
- **Minimal FreeFrag loop (2026-11-11).** A 2-gun terminal-scoreboard loop
  (pair, upload, config, countdown, polling, shots, hits, game-over,
  correct winner) ran live on the Mac and passed, including a marked-shot
  test with both vests on and a 120 s round at 99.25% poll-reply rate.
  Full numbers, caveats, and the bugs found along the way (classId
  substitution, inverted winner logic, a refuted "pairing window" theory)
  are in [capture index](12-capture-index-and-verification.md) §4.

Phase 1 (protocol reproduction) was declared complete on 2026-11-11 on the
strength of these three legs. The rule for anything built on top: every new
feature is proven the same way — a live run against the real guns with a
log, verified against the captures. If hardware does not respond as the
captures say it should, stop and report; do not work around it by guessing
bytes.

## 5. Prior reverse-engineering projects

No Python, Rust, Go, or Node project for this hardware exists, and no
published packet-byte documentation, Wireshark capture, or datasheet
existed before this work. Four abandoned projects were used as references:

| Project | What it gave | Traps — do not copy |
|---|---|---|
| **Unofficial BattleTag (Efeion)** — Java + C, hidapi, 2012–2015, GPLv3. The only known code that ever spoke HID to a UbiConnect. | Message framing, pairing commands, config/countdown/game-over/status layouts. FreeFrag and TeamFrag reportedly worked. Used as a Rosetta Stone: translate its framing and pairing state machine, do not re-run the Java. | Pairing was broken on 64-bit Windows (fixed only by prepending the `0x00` report-ID byte, and the pair-accept flag was still never set). Its read routine reads twice and discards the first result — a real bug that likely explains its "revision request got no response" comment, which the captures contradict. Its ping (`01 FF 05 00`) and vest poll (`01 FF C1 00`) differ from the official bytes by the size byte. Its `endMatch` sends the wrong colour sequence. Its TeamFrag config defaults are the author's choices, not the game's. Its writes send short buffers as-is; the official game always writes full 64-byte reports. |
| **btasm (perotinus)** — C/Yacc/Lex, 2012–2015 | Gun-bytecode compiler and unfinished spec. Confirms guns run bytecode sent from the PC at game start, exchanging CONFIG/SEND/RECEIVE variables whose counts must match exactly. | Gun-side only; nothing about PC↔UbiConnect framing. |
| **btbase / BTBaseUtil (perotinus)** — C, libnfc, Qt | T-Bases are Mifare Ultralight / Ultralight C; the only fully documented on-air tag format. | Gun↔tag only, not PC↔UbiConnect. Not needed for host software. |
| **tkdrob Battle-Tag mod** — Lua / bytecode, 2016–2018, MIT (own files) | Application-level message semantics, including the `0x95` field map; mode designs adopted as Tier-2 reference designs. | No USB/HID code at all. Windows-only mod on top of the original game. See the provenance trap in §4.2: reconnect settings, 80 s unregister, scaled upload pacing, and raised frame rate are MOD behaviour, not original. |

## 6. What is still unknown

The following were still open after the Nov 9–11 work and are flagged
UNVERIFIED wherever they appear in these docs (full list belongs in the
planned `14-open-questions.md`):

- The firmware/C++ effect of the `0x13` pair start/stop message (no Lua
  processor handles it) and how the gun stores its paired-proxy bond.
- What the revision numbers denote (bytes confirmed, meaning not
  established) and whether the disconnected-vest C1 reply value is `00`
  (never captured — all observed replies are `01`).
- Respawn-timer semantics: what the respawn config value actually times
  (scan→restore delay vs something else). A precise 0/5/10 test with
  scan-instant markers was specified but not run.
- The 8 kHz sample rate of the decoded gun sounds (high-confidence, not
  proven — the ear-check was specified but never run) and the remaining
  sound-code-to-name mappings.
- The byte 17/18 oscillation seen only in capture 47 game 1 (non-blocking;
  the client ignores those bytes), one unexplained byte at a game boundary
  in capture 47 game 2, and the out-of-range kick time (in-game text says
  10 s, printed manual says 30 s).
- Whether a rejoined gun's profile-checked state persists across a
  restart, and whether the whitelist-restore code path ever executes
  (static analysis says it would error; no runtime proof either way).

## Sources

Grounded in: `BATTLE_TAG_MASTER.md` (read fully); `PAIRING_UNPAIRING_REVIEW.md`;
`REVIEW_2026-11-11.md`; `SNIFFING_REVIEW_2026-11-10.md`;
`WINDOWS_SNIFFING_PROMPT_V2.md`; `sniffing-results/protocol_map.json` and
`sniffing-results/PROTOCOL_MAP_MERGE.md`. Where a source doc and the protocol
map disagree, the map wins; conflicts found are logged in
[capture index](12-capture-index-and-verification.md) §5.
