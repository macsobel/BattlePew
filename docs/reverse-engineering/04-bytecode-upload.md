# 04 — Bytecode upload to the guns

Game version 1.1.354. This document describes how the original game programs each
T-Blaster with a mode's gun code before a match: the broadcast upload sequence,
its 250 ms pacing, its all-guns acknowledgement rule, the payload format, and the
traps that break guns when the discipline is skipped.

This is an adaptation of the upload specification verified against the original
Lua source and USB captures on 2026-11-13. Where this document and any other
handoff note disagree about upload timing or acknowledgement rules, this
document wins.

## Claim grades

- **CONFIRMED** — verified in USB captures and/or live replay on real hardware.
- **INFERRED** — original Lua source only, or a single observation.
- **UNVERIFIED** — open question; do not implement as fact.

Evidence is cited per claim: capture numbers refer to the USBPcap captures in
`sniffing-results/` and the session-2 bundle (`review-2026-11-11/`); Lua
citations name the original installer file so a reader with the installer can
verify. No Lua source is reproduced here; all logic is paraphrased.

Related docs: [config and settings](05-config-and-settings.md) (the per-gun
0x92 step that follows the upload), [match loop](06-match-loop.md) (countdown
and polling after config), [mode behavior](07-mode-behavior.md) (per-mode
payload sizes and behavior).

## 1. What the upload is

Before a match, the host sends the mode's gun program (bytecode) to every paired
gun over the UbiConnect. The upload runs in the `bytecode` activity state, after
pairing and before the per-gun config step. The original re-uploads on every
match setup, regardless of whether the mode changed. **CONFIRMED** — capture 16
(FreeFrag setup) and capture 17 (TeamFrag setup) both show the full sequence;
the minimal FreeFrag loop ran it live on real hardware on 2026-11-11.

Properties that define the upload:

- **Broadcast.** Every upload message is addressed to `0xFF` (all guns at once).
  There are no per-gun upload messages. **CONFIRMED** (captures 16/17).
- **One message at a time.** Exactly one message is in flight; the host waits
  for acknowledgement before building the next one. No pipelining.
  **CONFIRMED** (Lua state machine + capture timing, §3).
- **Flash address `0x00004000`.** Game code is always written at address
  `0x00004000`, big-endian `00 00 40 00`. Address `0x00000000` is the
  player-profile block, a different, unicast sequence — never use it for a mode
  upload. **CONFIRMED** — direct re-parse of capture 16 frames 61/67 on
  2026-11-12; agrees with the flash-memory address in
  `UTActivity.State.Bytecode.lua`. (An earlier `02 02 02 02` / address-0
  reading of those frames was a parse error and is superseded.)
- **Settings are not in the upload.** The uploaded bytes are byte-identical
  across different game settings (verified: TeamFrag uploads identical with
  ammo 9 vs 12, medkit on/off, different durations and launch modes). All
  per-match settings travel in the per-gun 0x92 config sent after the upload
  (see [05](05-config-and-settings.md)). If only settings change, a re-config
  is sufficient in principle; if the mode changes, re-upload.
  **CONFIRMED** (captures 20/21, 39/41/42 chunk-identity checks).

Authority order for this document: (1) the original Lua
`UTActivity.State.Bytecode.lua` (the upload state machine); (2) captures 16 and
17, re-measured 2026-11-13; (3) the live 2026-11-11 replay. The tkdrob
community mod's copy of the bytecode state file, the firmware-update path in
`ULUsbDevice.FlashMemoryManager.lua`, and Efeion's Java timings are NOT
authorities for this path — see §10.

## 2. The six rules (non-negotiable)

1. **Broadcast, one message at a time.** Every upload message goes to `0xFF`.
   Never send per-gun upload messages. Never pipeline: send exactly one
   message, then wait.
2. **250 ms minimum between sends — a floor, not a target.** No two upload
   sends may be less than 250 ms apart, however fast the acknowledgements
   arrive. Slower than 250 ms is safe. Faster is what breaks guns.
3. **Re-send the current message every 250 ms until EVERY gun acknowledges
   it.** Do not advance when the first gun acknowledges, when most guns
   acknowledge, or when a timeout "feels long enough." Advance only when all
   paired guns have acknowledged the current message. A message that is never
   fully acknowledged is a failed upload (§8), not a cue to move on.
4. **After advancing, wait a full tick before the next message.** The original
   resets its 250 ms timer when it builds the next message, so the first send
   of the next step also waits. Do not send the next message immediately upon
   receiving the last acknowledgement. Pace every send, including the first
   attempt of a new step, against the previous send's timestamp.
5. **A 0x83 chunk is acknowledged only by a reply carrying the NEXT offset.**
   A reply whose offset bytes equal the offset just sent is not an
   acknowledgement — the gun has not confirmed the write. See §6 for the exact
   rule per command.
6. **A failed upload stops everything.** If any gun fails any step, the upload
   returns failure, the UI names the stuck gun(s), and the match must not
   proceed to config or countdown. The only recovery is a fresh upload from
   unlock (§8). Never report success on a partial upload.

## 3. Timing: the 250 ms timer and the measured oracle

### 3.1 The timer

The original's periodic update routine re-sends the current message when more
than 250,000 microseconds have elapsed since the timer was last reset, and the
timer is reset every time a new message is built — including on advance after
all acknowledgements. (Source: `UTActivity.State.Bytecode.lua`, `Update()` and
the message-build path. The 250,000 µs value is stated here because the exact
value is the fact.) Acknowledgements themselves arrive fast (~28–45 ms after
the send, the second gun ~15 ms after the first), so the gap between messages
is host pacing, not gun latency. An implementation that advances the instant
acknowledgements arrive runs at roughly 50–85 ms per message — 5–10× faster
than the original. **CONFIRMED** (Lua + capture measurement, 2026-11-13).

### 3.2 Measured gaps (test oracle)

OUT-to-OUT gaps between distinct messages, capture 16 (FreeFrag, 2 guns):

| Step transition | Gap |
|---|---|
| unlock → erase | 557 ms |
| erase → prepare | 544 ms |
| prepare → chunk 1 | 541 ms |
| chunk 1 → chunk 2 | 346 ms |
| chunk 2 → chunk 3 | 529 ms |
| chunk 3 → lock | 466 ms |

Capture 17 (TeamFrag, 2 guns, 20 chunks): chunk-to-chunk gaps 431–563 ms,
unlock → erase 689 ms, erase → prepare 359 ms. No distinct-message gap in
either capture is below 250 ms. The smallest observed gap anywhere is
250.3 ms — and that is a lock *retransmit* (same message), not a new step.
**CONFIRMED** (re-measured 2026-11-13).

Total upload durations (unlock send → first lock send):

| Mode | Messages | Lua floor (×250 ms) | Original measured |
|---|---|---|---|
| FreeFrag (120 bytes, 3 chunks) | 7 | 1.75 s | ~3.0 s |
| TeamFrag (1,152 bytes, 20 chunks) | 24 | 6.0 s | ~11.6 s |

**Test oracle:** if an upload finishes FreeFrag in under ~1.75 s or TeamFrag
in under ~6 s, it is violating the timer — stop and fix the pacing before
testing anything else. A correct implementation lands near the "measured"
column, not the floor. (The original is slower than the floor because of host
frame scheduling on top of the 250 ms timer; matching the floor exactly is
acceptable, matching the measured times is ideal. Exceeding 250 ms per message
is never wrong.)

### 3.3 Why the gap matters

The gun's bootloader needs the interval to commit each chunk to flash.
Overrunning it leaves a gun stuck mid-upload — it stops announcing/pairing
normally, and the only recovery observed has been re-flashing the gun with the
original program. The gun is not permanently damaged; it needs a clean re-flash
from unlock. **INFERRED** from observed behavior (guns stuck after fast
uploads, recovered by re-flash with the original game); no gun-side datasheet
was available to confirm the commit mechanism.

Upload timing with more than 2 guns was not measured in the cited captures
(captures 16/17 use 2 guns). The 250 ms floor is gun-count independent in the
Lua state machine — the interval must NOT shrink as guns are added (that is
the tkdrob trap, §10). **UNVERIFIED** for 4+ guns on the wire; see §11.

## 4. The state machine (paraphrased from `UTActivity.State.Bytecode.lua`)

State held across the upload: the current command step, a lock/unlock flag for
the 0x86 steps, the next byte offset to send, the total message size, the
current message bytes, the timer, and one acknowledgement flag per paired gun,
reset to false every time a new message is built.

Begin:

- The current command is 0x86 with the lock flag cleared (unlock).
- The payload is built (TOC + bytecode + trailer, §5); the total size is the
  bytecode length plus 15 (TOC) plus 4 (trailer).
- The first message (unlock) is built, all acknowledgement flags are reset,
  and the timer is reset.

On receiving a reply from a gun:

- If the reply's command differs from the current command, ignore it.
- If that gun already acknowledged the current message, ignore the duplicate.
- If the current command is 0x83: read the first two argument bytes as a
  big-endian offset. The gun acknowledges only if that offset equals the
  state's offset — which was already advanced when the message was built, so
  it holds the NEXT offset, not the offset just sent. See §6.
- For any other command, any same-command reply from the gun acknowledges.
- If ALL paired guns have acknowledged, advance to the next command, build the
  next message, reset all acknowledgement flags, and reset the timer.

Advance order:

- 0x86 (unlock) → 0x81 (erase) → 0x82 (prepare) → 0x83 (chunk 1) → 0x83
  (chunk 2) → … → 0x83 (last chunk) → 0x86 (lock) → done (the original
  proceeds to matchmaking).

Periodic update (runs continuously):

- If more than 250 ms have elapsed since the timer was last reset, reset the
  timer and send the current message (broadcast) again.

Three properties are easy to get wrong:

- **The same message is re-sent, byte-identical, on every tick** until all guns
  acknowledge. There is no per-gun retransmit, no "send only to the guns still
  missing," and no backoff. Broadcast the identical message again.
- **The offset is advanced at message-build time, not at acknowledgement
  time.** When chunk N is built, the state's offset becomes the offset chunk
  N+1 will start at. That is why the acknowledgement test compares against the
  next offset, not the offset in the message just sent.
- **There is no give-up in the original.** The Lua re-sends forever until all
  guns acknowledge (or a gun is removed, which aborts to the menu — §8). A
  replacement must bound this (§8 gives attempt counts), but the bound must be
  generous: the original's posture is "never advance early," and a short
  timeout that advances anyway is worse than a long one that fails loudly.

**INFERRED** as a whole (Lua source), with the frame formats, gaps, and
acknowledgement behavior **CONFIRMED** by captures 16/17 and live replay.

## 5. Frame formats and payload construction

### 5.1 Message framing

Byte 0 of every message is the argument length: the number of bytes after the
`[size][address][command]` header (total meaningful length minus 3). Byte 1 is
the address (`0xFF` for all upload messages). Byte 2 is the command. The
remaining bytes are the arguments. Messages are carried in 64-byte HID
reports; only the leading meaningful bytes matter (§7).

| Step | Message (meaningful bytes) | Example (FreeFrag) |
|---|---|---|
| Unlock | `01 FF 86 00` | `01 FF 86 00` |
| Erase page | `04 FF 81 00 00 40 00` | `04 FF 81 00 00 40 00` |
| Prepare / write-init | `06 FF 82 00 00 40 00 <total size, BE16>` | `06 FF 82 00 00 40 00 00 78` (120 bytes) |
| Chunk | `<len+3> FF 83 <offset, BE16> <len> <data>` (len ≤ 58) | `3D FF 83 00 00 3A <58 bytes>` (first chunk) |
| Lock | `01 FF 86 01` | `01 FF 86 01` |

**CONFIRMED** — capture 16 frames 55–91 (FreeFrag) and capture 17 frames
55–215 (TeamFrag); Lua `FlashMemoryManager.lua` send sites agree on the
constructions, but the game-code path is governed by
`UTActivity.State.Bytecode.lua` (see trap 2, §10).

Worked example — the complete FreeFrag upload, exactly as captured (capture
16, frames 55–91; total size 120 = `0x0078`). Chunk data bytes are summarized,
not reproduced, except for the trailer chunk:

```text
OUT 01 FF 86 00                                  unlock
OUT 04 FF 81 00 00 40 00                         erase page at 0x4000
OUT 06 FF 82 00 00 40 00 00 78                   prepare, 120 bytes
OUT 3D FF 83 00 00 3A <58 bytes: TOC + bytecode start>   chunk 1, offset 0
OUT 3D FF 83 00 3A 3A <58 bytes>                 chunk 2, offset 0x3A (58)
OUT 07 FF 83 00 74 04 22 04 19 86                chunk 3, offset 0x74 (116), 4 bytes (magic trailer)
OUT 01 FF 86 01                                  lock
```

Note the last chunk: its data is the 4-byte magic trailer `22 04 19 86`
alone, because 116 + 4 = 120. Chunking is purely mechanical (§5.3): split the
payload into 58-byte pieces; the last piece is whatever remains, even if tiny.

### 5.2 Payload: TOC + bytecode + trailer

The payload is built once per mode, before the upload starts: TOC (15 bytes) +
bytecode + magic trailer (4 bytes). Total size = bytecode length + 15 + 4.

TOC (15 bytes), as built by `UTActivity.State.Bytecode.lua`, `Begin()`:

| Bytes | Content |
|---|---|
| `43 4F 44 45` | ASCII `CODE` |
| `01` | Number field, value 1 |
| `44 46 4C 54` | ASCII `DFLT` (entry) |
| `0D 00` | Offset field, as stored (byte `0D` then byte `00`) |
| 4 bytes | Bytecode length, little-endian, written TWICE |

Example: FreeFrag bytecode length 101 = `0x0065`, so the TOC ends
`65 00 65 00` and the full TOC is
`43 4F 44 45 01 44 46 4C 54 0D 00 65 00 65 00`. TeamFrag bytecode length
1,133 = `0x046D`, so its TOC ends `6D 04 6D 04`. The length in the TOC is
little-endian and duplicated; the length in the 0x82 prepare message is
big-endian and single. Do not confuse the two. **CONFIRMED** (capture 16/17
first-chunk data + Lua; the Java reference's TOC description agrees).

Magic trailer (4 bytes), appended after the bytecode: `22 04 19 86`.
**CONFIRMED** (capture 16 frame 85; capture 17 final chunk).

### 5.3 Chunking

Split the payload into consecutive pieces of at most 58 bytes. Chunk `i`
starts at offset `58*i` (the last chunk starts at its own offset and carries
`total size − offset` bytes). The `offset` field in each chunk header is that
chunk's start offset, big-endian. The `len` field is that chunk's data length.
The size byte is `len + 3`.

### 5.4 Per-mode sizes and chunk counts

Use the capture bytes, not the installer Lua tables, for two modes. For five
modes the installer bytecode tables match the captures; for TeamFrag and
TagThenShoot they do not, and the captures are ground truth for what the guns
receive. The verified per-mode uploads live in the replacement repo's data
file `battletag/data/mode_sequences.json` (`uploads` key, with `unlock`,
`erase`, `prepare`, `lock`, and `chunks[]` carrying `offset`, `length`,
`message_hex`, and `data_hex`). Use those bytes verbatim.

| Mode | Total bytes | Chunks | Last chunk (derived) | Installer Lua table matches capture? |
|---|---|---|---|---|
| FreeFrag | 120 | 3 | 4 bytes | Yes (101-byte table) |
| TeamFrag | 1,152 | 20 | 50 bytes | **No** — Lua table is 1,110 bytes; capture is 1,152. Content diverges at byte 164. Use capture bytes (capture 17). |
| TagThenShoot | 439 | 8 | 33 bytes | **No** — Lua table is 390 bytes; capture is 439. Use capture bytes (capture 24). |
| FreeForAll | 877 | 16 | 7 bytes | Yes (858-byte table) |
| LastManStanding | 801 | 14 | 47 bytes | Yes (782-byte table) |
| OldFashionDuel | 225 | 4 | 51 bytes | Yes (206-byte table) |
| StarterFrag | 409 | 8 | 3 bytes | Yes (390-byte table) |

Total bytes and chunk counts are **CONFIRMED** (captures 16/17/24 and the
verified data file; TeamFrag's 1,152 corrects two earlier miscounts — see
§12). Last-chunk sizes other than FreeFrag (4) and TeamFrag (50) are
arithmetic remainders of the confirmed totals over 58-byte chunks. The
TeamFrag divergence point (byte 164) is **INFERRED** from the upload-spec
comparison of table vs capture; no finer divergence analysis is in the
sources. Full payload byte arrays are deliberately not reproduced in these
docs; see the data file above.

## 6. Acknowledgement matching — what counts as an ACK, per command

Replies arrive as HID IN reports. Byte 0 is the argument-length byte, and in
replies it does not always equal the value that was sent — **do not match on
byte 0**. Byte 1 is the replying gun's id. Byte 2 is the command. Bytes 3+
are the arguments. Only replies from guns in the current upload set count;
ignore replies from any other id, and ignore reports addressed to `0xFF`.

| Step | ACK test (all must hold) | Captured reply example |
|---|---|---|
| 0x86 unlock | byte 1 = gun id, byte 2 = `0x86`, byte 3 = `0x00` | `01 02 86 00 …`, `01 03 86 00 …` |
| 0x81 erase | byte 1 = gun id, byte 2 = `0x81` | `04 02 81 00 00 40 00 …` |
| 0x82 prepare | byte 1 = gun id, byte 2 = `0x82` | `04 02 82 00 00 40 00 00 78 …` (reply size byte is `04`, not the sent `06` — another reason not to match byte 0) |
| 0x83 chunk | byte 1 = gun id, byte 2 = `0x83`, **bytes 3:5 (BE16) == next offset** (this chunk's start offset + this chunk's length = the offset the next chunk will use) | Chunk at offset `0x0000`, len `0x3A` → reply `02 02 83 00 3A 3A …`. Final chunk (offset `0x0074`, len `0x04`) → reply `02 02 83 00 78 04 …` (`0x0078` = 120 = total size). |
| 0x86 lock | byte 1 = gun id, byte 2 = `0x86`, byte 3 = `0x01` | `01 02 86 01 …`, `01 03 86 01 …` |

Notes:

- **The 0x83 offset rule is the whole game.** The Lua test is exact equality
  against the next offset (advanced at build time, §4). A reply carrying the
  offset just sent (e.g. `0x0000` for the first chunk) means the gun parroted
  the message; it has NOT confirmed the write. Accepting command-byte-only
  matching for 0x83 is a known live failure: on 2026-11-12 the profile-write
  path (same 0x83 mechanism, unicast) accepted such a reply, sent the lock
  ~40 ms later, and nothing was written — the gun kept its old data. The gun
  was unharmed, but the write silently did nothing. Do not repeat that bug in
  the broadcast path. **CONFIRMED** (Lua rule + capture reply offsets + live
  failure).
- A greater-than-or-equal offset test is equivalent in correct operation (a
  gun never legitimately reports beyond the next offset, and stale replies
  from earlier chunks report smaller offsets and are rejected by both tests).
  Either test is acceptable; exact equality is the original. What is not
  acceptable is no offset test at all.
- **The byte-3 test for 0x86** (unlock `0x00` vs lock `0x01`) is
  capture-verified and distinguishes the two 0x86 steps, which otherwise share
  a command byte. The Lua does not test it (it relies on command state plus
  the lock flag), but at the HID level the byte-3 test is correct and cheap.
  Keep it.
- **Tails are meaningless.** Reply reports carry stale residue after the
  meaningful bytes (e.g. lock replies echo the previous chunk's tail
  `74 04 22 04 19 86`). Never match on full-frame equality, and never require
  a specific tail. Match only the bytes in the table above.
- **Duplicate ACKs are normal.** A gun may acknowledge the same message more
  than once across retransmits. Once a gun is marked acknowledged for the
  current message, further replies from it for that message are ignored. When
  the next message is built, all flags reset.
- **Echo caution.** Host messages are echoed back as IN reports (see
  [06](06-match-loop.md) for the echo rule). An echo carries the sent offset,
  not the next offset, so the 0x83 offset test also rejects echoes. For other
  commands, echo handling must still distinguish a same-command echo from a
  genuine acknowledgement — match on the replying gun's id and the argument
  bytes in the table, not on mere arrival of a same-command report.

## 7. HID framing

- Transport is USB HID via hidapi (`hid_write` / `hid_read`). Device: VID
  `0x044F`, PID `0xB365`, usage page `0xFF01`. Do not use pyusb/libusb on
  macOS. **CONFIRMED** (descriptor + live replay).
- Every write is **65 bytes**: a `0x00` report-ID byte prepended to a 64-byte
  report. The driver strips the report ID; the gun sees 64 bytes.
- Every message is padded to 64 bytes with **`0x00`**. Do not copy the residue
  tails seen in captures (e.g. `09 00 05` or previous-message bytes) — those
  are stale buffer contents from the original host, not protocol. Meaningful
  prefix + zero padding, always.
- Every read must tolerate the report-ID byte on input the same way (strip
  byte 0 if the read returns 65 bytes).
- The UbiConnect is half-duplex RF behind one HID handle: see §8 for the
  serialization requirement. There is exactly one reader.

## 8. Bus ownership, retries, and failure handling

**One operation owns the bus.** During the upload, no other code may read from
or write to the HID handle: no auto-pair loop, no polling loop, no pings, no
profile reads, no second upload. Concurrent readers are a proven
pairing-breaker: a reply consumed by the wrong reader looks exactly like a
timeout to the reader waiting for it. **CONFIRMED** (live experience; the
reference implementation serializes the whole upload under one lock with the
lobby auto-pair loop paused).

**Before the first upload message**, drain stale inbound reports briefly
(~150 ms) so leftover announcements/echoes from pairing cannot be mistaken for
upload acknowledgements.

**Retry budget (a replacement necessity — the original never gives up):**

| Step | Attempts (250 ms apart) | Total wait |
|---|---|---|
| Unlock, prepare, each chunk, lock | 12 | 3 s |
| Erase | 24 | 6 s (flash erase is the slowest operation) |

These budgets are floors, not ideals: if a step exhausts its budget with guns
still pending, that is a failure, full stop. The budgets themselves are a
replacement-implementation choice, **INFERRED** as reasonable from the
original's never-give-up posture — they are not values from the original game.

**On failure:**

1. Stop at the failed step. Do not send the remaining steps. Do not proceed to
   config, countdown, or match start.
2. Record exactly which guns are still pending, and surface them by player
   number/name in the UI (e.g. "Upload failed at chunk 7/20 — no ACK from
   Player 3").
3. Offer exactly two actions: **Retry upload** (restart from unlock, for all
   guns in the match — a gun that missed a chunk is in bootloader state and
   needs the full sequence from the beginning; never resume mid-upload) or
   **Back to pairing**.
4. Never set any "ready" flag on a partial upload. Never let a countdown
   start.

**If a gun disappears mid-upload** (stops replying entirely, or is powered
off), the original aborts the whole activity back to the menu with a popup
(`OnDeviceRemoved` in the Lua activity handling). The replacement equivalent
is the failure handling above, with the missing gun named. Do not hang, do not
spin forever, and do not silently drop the gun from the match.

**Lock retransmits:** the original sends the lock 4× in the captures (frames
91–109 in capture 16, 250–331 ms apart) even though both guns acknowledge the
first lock. That is an artifact of the original's asynchronous state
transition (the update loop keeps firing until the state actually changes to
matchmaking), not a requirement. Repeat-until-acknowledged, then stop, is
correct. Do not hard-code "send lock 4 times," and do not implement the lock
as several independent lock rounds with separate acknowledgement waits.
**CONFIRMED** (capture 16 frame pattern + state-machine reading).

## 9. Boundaries: before and after the upload

**Before:** all guns for the match are paired (pairing handshake complete,
profile read, stable player numbers assigned). The upload set is exactly the
paired guns in the match. Do not upload to unpaired guns; do not pair during
an upload.

**After — config (per gun, unicast, NOT broadcast):** once the lock is
acknowledged by all guns, send each gun its 0x92 config. Config is unicast
(addressed to each gun's id in turn), carries the team assignment and all
settings, and is a separate step with its own acknowledgement discipline — see
[05](05-config-and-settings.md). Config must not start until the upload has
fully succeeded, and a config failure must also fail loudly: no silent partial
config, no countdown on an unconfigured gun. Team is baked into the config;
changing teams after config requires returning to setup.

**After config:** countdown, then the poll loop — see
[06](06-match-loop.md).

**Not part of the upload:** the player-profile write (name/icon stored on the
gun) is a separate, unicast sequence to flash address `0x00000000` (unlock →
erase → write-init → one 0x83 chunk → lock, same 250 ms resend discipline,
same offset-acknowledgement rule), triggered at profile-edit time, never
during a mode upload. The mode upload carries no profile data. Do not merge
the two sequences. The profile-write sequence is **INFERRED** (Lua
`UTGame.Ui.Associate.lua`; never observed on the wire — no capture covers a
profile edit); it is documented here only as a boundary.

## 10. Traps — things that look right and are wrong

1. **tkdrob's fast timer (most likely cause of fast-upload bugs).** The tkdrob
   community mod's copy of the bytecode state file replaces the fixed 250 ms
   interval with a player-count-scaled interval: 21,000 µs plus 16,000 µs per
   player — 53 ms for 2 players, 85 ms for 4, 149 ms for 8. The comment in
   that file still says "send msg each 250ms"; the code does not do that.
   This is a community-mod speedup, **never the original's behavior**, and it
   must never be presented as original. If any code computes an upload
   interval from the player count, or uses an interval under 250 ms, delete it
   and use the fixed 250 ms floor in §2. Do not "tune" the interval down
   because acknowledgements arrive quickly — the acknowledgement speed is
   irrelevant; the gap is for the gun's flash write.
2. **FlashMemoryManager is the firmware path.**
   `ULUsbDevice.FlashMemoryManager.lua` implements staged flash operations
   used only by the Revision states (firmware update). Its chunk/offset
   bookkeeping differs from the game-code path. Game-code upload uses
   `UTActivity.State.Bytecode.lua` only. Do not import FlashMemoryManager logic
   into the game upload, and do not run the firmware-update path at all (no
   custom gun firmware, no updater flows — brick risk).
3. **The proven minimal client's upload is fire-and-forget — do not copy its
   timing.** The early single-gun FreeFrag client's upload routine sends each
   step with no acknowledgement wait and ~150 ms spacing, and it happened to
   work live for FreeFrag with 2 guns. It has no verification, no retry, and
   no failure signal; it proves the *bytes*, not the *discipline*. The
   20-chunk TeamFrag upload and larger gun counts are exactly where
   fire-and-forget drops a chunk silently. The acknowledgement-gated
   implementation supersedes it.
4. **Efeion's 500 ms is safe, but it is not the original's timer.** The Java
   project's upload routine waits 500 ms between chunks. That is slower than
   the original's 250 ms floor and therefore harmless — but cite the
   Lua/captures, not the Java, as authority, and do not copy Java's other
   upload quirks (its revision-reply handling is contradicted by the
   captures).
5. **"Advance on first ACK" / "advance on timeout."** Both produce a
   fast-looking upload and a gun in bootloader state. The rule is
   all-guns-acknowledged or failure. There is no third option.
6. **Sending the next message immediately when the last ACK arrives.** Even
   with correct acknowledgement gating, this halves the gap (ACKs arrive in
   ~40 ms; the next message must still wait for the 250 ms tick). Pace every
   send against the previous send. This single mistake accounts for most of
   the 3–5× speedup seen in pre-fix replacement code.
7. **Copying capture tails / matching full frames.** Capture reports contain
   stale residue after the meaningful bytes. Pad with zeros; match only
   meaningful bytes (§6–§7).
8. **Rebuilding payloads from installer Lua tables for TeamFrag/TagThenShoot.**
   See §5.4: the tables disagree with what the original game actually
   uploaded. Use the capture bytes in `battletag/data/mode_sequences.json`.

## 11. Verification checklist

Log every upload send and reply with millisecond timestamps (OUT/IN hex + step
label + pending set). Then:

- **Timing assertion, FreeFrag, 2 guns:** no two OUT sends less than 250 ms
  apart; total unlock→lock ≥ 1.75 s. Expect ~3 s.
- **Timing assertion, TeamFrag, 2 guns:** same 250 ms floor; total
  unlock→lock ≥ 6 s. Expect ~11–12 s.
- **ACK gating:** with 2 guns, confirm the log shows an acknowledgement from
  *each* gun id for *each* step before the next step's first send. Confirm
  0x83 acknowledgements carry the next offset (e.g. chunk 1 reply offset
  `0x003A`).
- **Retransmit behavior:** briefly shield/power off one gun mid-upload and
  confirm the current message repeats every ~250 ms, the upload does not
  advance, and after the retry budget it fails loudly naming that gun — with
  no config/countdown following.
- **Retry recovery:** after a failed upload, retry from unlock succeeds with
  both guns, and the match then plays normally (polls answer, shots register).
- **4+ guns:** repeat FreeFrag with 4 guns; confirm all four acknowledge each
  step and the upload still respects the 250 ms floor (the interval must NOT
  shrink with gun count — that is the tkdrob trap).
- **No cross-talk:** confirm no pair/poll/ping/config traffic appears in the
  log between the first unlock send and the final lock acknowledgement.
- **Regression guard:** keep a test that fails if any upload send gap is under
  250 ms in a scripted run, and a test that a 0x83 reply carrying the *sent*
  offset does not advance the upload (the 2026-11-12 profile-write failure,
  generalized).

## 12. Open questions

- **Upload behavior with 4+ guns on the wire.** No cited capture measures
  upload timing or acknowledgement behavior with more than 2 guns. The Lua
  floor is gun-count independent, but multi-gun retransmit/ACK interleaving is
  **UNVERIFIED**. (Flagged for collection into the open-questions doc.)
- **Gun-side flash commit mechanism.** The 250 ms requirement is established
  behaviorally (fast uploads strand guns; clean re-flash recovers them), but
  no gun-side documentation confirms what the interval is for internally.
  **INFERRED**, not proven at the firmware level.
- **0x86 repeated form.** After the upload, a longer 0x86 form appears in the
  captures (e.g. `01 FF 86 01` followed by additional bytes that look like a
  length echo). The exact field split is unknown in the protocol map.
  **UNVERIFIED**; it does not affect the upload sequence in §5, which uses
  the 4-byte forms.
- **TeamFrag divergence detail.** The installer table and the capture diverge
  at byte 164 (per the upload spec), but no source characterizes *how* the
  bytes differ beyond the size difference (1,110 vs 1,152). **UNVERIFIED**
  detail; the capture bytes are authoritative regardless.

## Sources

- `ANTIGRAVITY_UPLOAD_SPEC.md` (upload state machine, timing re-measurement,
  ACK rules, traps) — primary.
- `sniffing-results/protocol_map.json` — frame formats (0x81/0x82/0x83/0x86),
  TeamFrag 1,152-byte correction, profile-write boundary. Supersedes the
  Nov 10 merge note's "1,155 bytes" (which conflated the last chunk's message
  size 53 with its data length 50) and the earlier FINDINGS "about 1,097
  bytes."
- `REVIEW_2026-11-11.md` §a8 (flash sequence frame verification), §d
  (1,152-byte arithmetic correction).
- `BATTLE_TAG_MASTER.md` §3 items 30–42 (live replay verification, echo rule,
  winner-fix run), §4.6 (Phase 1 gate).
- Original Lua (installer, plaintext): `UTActivity.State.Bytecode.lua`
  (state machine, timer, ACK rules), `FlashMemoryManager.lua` (frame
  constructions only — firmware path, see trap 2).
