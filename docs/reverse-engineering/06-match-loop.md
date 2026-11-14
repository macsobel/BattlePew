# 06 — Match loop: polling, countdown, game-over, RFID, and vest checks

Game version 1.1.354. This document describes what happens after setup: the
0x95 poll loop that carries all in-game state, the countdown, game-over
frames, the hit shift register, the RFID mechanisms (ammo, medkit, team base,
respawn), manual launch, vest polling, the status byte map, and the
TagThenShoot specifics — including the unverified 0xC3 path.

Setup (upload + config) is covered in [04](04-bytecode-upload.md) and
[05](05-config-and-settings.md). Per-mode rules that consume this loop are in
[07](07-mode-behavior.md).

## Claim grades

- **CONFIRMED** — verified in USB captures and/or live replay on real hardware.
- **INFERRED** — original Lua source only, or a single observation.
- **UNVERIFIED** — open question; do not implement as fact.

Byte offsets are 0-based report offsets (byte 0 = size byte, byte 1 = gun id,
byte 2 = command), unless a Lua `arg[]` index is explicitly stated. No Lua
source is reproduced here; all logic is paraphrased, with file/function
citations for verification against the installer.

## 1. Loop overview

After config, the match proceeds:

1. **Vest wait.** The host polls harness (vest) status with 0xC1 while waiting
   for game start (§8).
2. **Countdown.** The host sends a 0x93 countdown per gun (§5). In manual
   launch, an A1/C2 ready phase precedes it (§7).
3. **Poll loop.** The host polls each gun with 0x95 about every 0.311 s per
   gun; every shot, hit, ammo change, health change, and RFID scan is visible
   only as a change in the 0x95 status replies (§2–§4). There are no separate
   event messages for trigger pulls, hits, ammo, or health.
4. **Game over.** At the end (host-side timer expiry, last player standing, or
   rounds complete), the host sends a 0x94 game-over frame per gun with a
   win/lose colour (§6).

The match timer is host-side: duration appears nowhere in the 0x92 config or
the 0x83 upload (captures 41/42, byte-identical to 39). **CONFIRMED**
(negative). The host owns all scoring derived from status deltas; the guns
own hit registration, ammo/health bookkeeping, RFID reads, sounds, lights,
and vibration (see §9: no host LED/rumble command exists).

## 2. 0x95 polling

### 2.1 Poll format

The poll is unicast, one per gun, in rotation:

| Form | Bytes | Meaning |
|---|---|---|
| Empty poll | `01 <id> 95 00` | No downlink gameplay data. The usual frag-mode form. |
| TagThenShoot poll | `03 <id> 95 01 00 <N>` | Carries the running hit count `N` down to the gun (§10). |

Construction (per `RoundLoop.lua`, poll send site): the message is built from
a size, the gun id, command 0x95, a count of gameplay data values, and the
gameplay data values themselves — **the poll carries downlink gameplay data**,
it is not a bare ping. In the empty form the count is 0; in the TagThenShoot
form the count is 1 and one big-endian 16-bit value (the hit count) follows.
**CONFIRMED** (Lua construction + captures 13/17 for the empty form, capture
25 for the TagThenShoot form).

Downlink values other than the TagThenShoot hit count are **UNVERIFIED** on
the 1.1.354 wire: the TeamFrag poll in capture 47 game 1 carried no downlink
data (`01 02 95 00` plus stale tail). In particular, the trigger-disable
downlink value described in tkdrob-era Lua (a gameplay-data value of 7
disabling the trigger on vest disconnect) has no 1.1.354 capture evidence —
see §8.3. Do not assume frag-mode polls carry downlink data.

### 2.2 Cadence

- **In-game poll:** each gun polled every ~0.311 s (capture 13, id-02 gaps
  0.310–0.313 s; capture 17 same). **CONFIRMED.**
- **Ping:** the broadcast ping `00 FF 05` is sent about every 6 s while idle
  (capture 13 frames 7–55, gaps 5.99–6.24 s; Lua rule: at most once per
  second, only after ≥ 5 s silence) and once per poll cycle in-game (~0.311 s).
  Per-gun ping replies read `00 <id> 05 01`. **CONFIRMED.** (Efeion's Java
  used a different size byte, `01 FF 05 00`, and different cadence figures —
  both contradicted by the captures; do not copy them.)
- **C1 vest poll:** ~0.25 s during setup, rare in-game (§8).
- Live-replay note: the proven minimal client polled at ~189 ms/gun and the
  guns handled it fine; the original's ~311 ms is the reference cadence, not a
  proven limit. Faster polling is **INFERRED** safe from that single live run,
  not from the original's behavior.

### 2.3 Polling discipline

- **Active polling only.** Guns never send unsolicited status. The host must
  poll; passive listening sees nothing. **CONFIRMED** (captures + live
  replay: a passive-listening implementation fails after 120 s of silence).
- **Unanswered polls are normal.** Capture 16 gun 03's first poll went
  unanswered; the live 2026-11-11 run saw 4 unanswered of 1,254 polls
  (99.7% answered). Retry an unanswered poll on the next cycle; do not treat
  one miss as a disconnect. **CONFIRMED.**
- **Read until the right reply.** After sending a poll, read until a report
  with command 0x95 and the right gun id arrives; stale echoes from earlier
  messages may still be queued (§2.4). Drain before polling after a state
  change (e.g. after countdown).
- **Baseline snapshot required.** First-poll counters may already be nonzero
  (carried-over game state — e.g. capture 14 started mid-game with shots
  already fired; capture 10's hit-id slots carry the previous game's ids).
  Snapshot the first 0x95 report of a game as the baseline and score from
  deltas. **CONFIRMED** (captures 10/14/15/19/47).

### 2.4 The echo rule

Every host message is echoed back as an IN report, with the replying gun's id
substituted for the broadcast address `0xFF` on broadcast messages. **Never
treat the first report after a send as the reply.** Concretely:

- After a countdown send, the guns echo it in ~11 ms and then count down
  locally in silence — the echo is not a "countdown complete" message (none
  exists; see §5). **CONFIRMED** (captures 04/16 + live diagnosis).
- After a game-over send, each frame is echoed back as IN (capture 19 frames
  5757/5759). **CONFIRMED.**
- Echoes can linger in the HID queue and surface after later sends (e.g. a
  stale countdown echo appearing as `01 02` after a poll send). Always match
  replies on command + gun id + argument bytes, never on arrival order.
- The sources differ in wording on *what* echoes (the merge notes attribute
  echoes to the UbiConnect; the master document attributes them to the guns).
  The observable behavior — an IN report following the OUT, id substituted
  for `0xFF` — is the same either way and is what an implementer must handle.
  The originating hardware layer is **UNVERIFIED** and does not matter for
  decoding.

## 3. Status report byte map

The 0x95 reply is a 64-byte report. The size byte (byte 0) selects the layout:
`0x0A` for FreeFrag, `0x0E` for TeamFrag. **A decoder must key off the size
byte** — byte meanings are mode-dependent. TagThenShoot reuses bytes 5 and 7
with its own meanings (§10); its status size byte is not stated in the
sources (see §11).

Values are big-endian 16-bit pairs: the high byte stays `00` at these
magnitudes and the low byte moves. The single-byte descriptions in the
capture analyses (e.g. "byte 5 rises") refer to the low byte of the pair
starting at byte 4. The Lua `arg[]` view is 1-based over the argument bytes
(byte 3 onward), so `arg[k]` is report byte `k+2`: e.g. the Lua hit-count
pair `arg[2..3]` is report bytes 4–5.

### 3.1 FreeFrag layout (size `0x0A`) — CONFIRMED

Example first report (capture 08, stream 02, first 13 meaningful bytes):
`0A 02 95 07 00 00 00 00 00 00 00 00 00` (bytes beyond the meaningful prefix
in the full 24-byte dump are stale tail — see §3.3).

| Byte(s) | Field | Behavior |
|---|---|---|
| 0 | Size `0x0A` | Layout selector. |
| 1 | Gun id | Stream id (02/03/…). |
| 2 | Command `0x95` | — |
| 3 | Observed constant `07` | Meaning unknown (see §11). |
| 4–5 | Hits received (BE16) | Rises 1 per hit taken. Low byte is byte 5. |
| 6–7 | Shots fired (BE16) | Rises 1 per trigger pull, hit or miss. Low byte is byte 7. |
| 8–12 | Hit-id shift register (5 slots) | Most-recent-first; §4. |

Evidence: capture 08 (shots move byte 7 only), capture 09 (hits move byte 5
+ hit-id slots; shooter-side change precedes target-side by ~0.1 s), capture
10 (misses move byte 7 only; slots not cleared).

### 3.2 TeamFrag layout (size `0x0E`) — CONFIRMED

Example first report (capture 13, stream 02, first 17 bytes):
`0E 02 95 09 00 05 00 09 00 04 00 00 03 03 00 00 00`.

| Byte(s) | Field | Behavior |
|---|---|---|
| 0 | Size `0x0E` | Layout selector. |
| 1 | Gun id | Stream id. |
| 2 | Command `0x95` | — |
| 3 | Observed constant `09` | Meaning unknown (see §11). |
| 4–5 | Hit points (BE16) | Falls 1 per hit; 0 = knocked out; restored to full (config health) by team-base scan or medkit scan. Low byte is byte 5. |
| 6–7 | Ammo remaining (BE16) | Falls 1 per shot; reset to full clip by ammo-box scan or auto-reload at empty. Low byte is byte 7. |
| 8–9 | Clips / magazines (BE16) | Starts at config clips minus 1 in the captures (04 with clips = 5 — see note below); falls 1 on auto-reload at empty; restored by ammo-box reload. Low byte is byte 9. |
| 10–11 | Total shots fired (BE16) | Rises 1 per shot; never resets on reload. Low byte is byte 11. |
| 12–16 | Hit-id shift register (5 slots) | Most-recent-first; §4. |

Evidence: capture 13 (ammo/shots/clips), capture 19 (auto-reload: byte 7
`00`→`09` and byte 9 `04`→`03` at frame 2003; ammo-box reload: byte 9
`03`→`04` at frame 2983; byte 11 runs `00`→`0E` across the game), captures
15/19 (hit points fall + reset).

Clips note: with clips configured to 5, byte 9 starts at `04` in captures
13/15/19. The sources describe byte 9 as "magazines remaining" starting at 04
without explaining the off-by-one against the configured 5. Treat the
relationship as: byte 9 starts at (configured clips − 1) in the observed
captures, falls on auto-reload, and is restored to the start value by an
ammo-box scan. Whether the current magazine is excluded from the count, or
the value means "spare magazines," is **INFERRED** at most — do not present a
mechanism. The unlimited-ammo case (config 20) keeps ammo at `0014` and clips
at `0005` across 36 shots (capture 40) — a different clips behavior that
reinforces "do not over-model byte 9."

### 3.3 Bytes beyond the interpreted fields

- **Byte 17** reads `02` in all 2-gun 0x0E captures, `04` in 4-gun captures
  (46/47), and `00` in 0x0A streams — plausibly a gun/team count field, but
  that reading is a **HYPOTHESIS** (no counter-evidence, no proof of meaning).
- **Byte 21** reads `01` in every 0x0E stream; meaning unknown.
- **Byte 17/18 oscillation** appears only in capture 47 game 1 (4 guns, swap
  on): byte 17 flips `04`→`00` and byte 18 flips `00`→`10` simultaneously,
  reverting ~0.5 s later, irregularly every ~3–37 s, in streams 02/03/05 but
  never stream 04. It is not correlated with the flipping gun's own shots,
  with C1/C2/A1 traffic, or with vest status. Cause **UNVERIFIED**,
  non-blocking: the reference decoding reads only bytes 3–16. Do not chase it
  unless it ever affects gameplay.
- The sources also note a minor boundary curiosity: stream 04 byte 12 reads
  `05` at capture 47 game-2 start (frame 39483), with shots reset — leading
  hypotheses are an unmarked hit between games or a persisted slot value
  re-reported at game start. **UNVERIFIED**, minor; it does not affect the
  shift-register model.
- Where the meaningful prefix of a 0x95 reply ends is not pinned down in the
  sources: the size byte suggests 13 bytes (0x0A) / 17 bytes (0x0E), yet
  bytes 17–21 are discussed as (constant or oscillating) fields. Treat bytes
  beyond 16 as uninterpretted; see §11.

### 3.4 The Lua BE16 field map (INFERRED — reconcile with care)

The Lua status decoding described in the master document (from the tkdrob
mod's Lua, stated to match the original Lua) reads big-endian pairs from the
argument bytes: hit count, ammunitions, clips-or-score (mode-dependent reuse
of the same slot), a gun-seconds clock used as a heartbeat, and — for
later/community modes only — Disarm bomb digits and Capture energy in the
later pairs. The pair structure and the mode-dependent slot reuse agree with
the capture layouts above. Two cautions:

- The gun-seconds heartbeat slot (argument pair 8–9, report bytes 10–11) is
  the *total shots* slot in the TeamFrag capture layout (§3.2). Either the
  slot is reused differently across modes/builds, or the heartbeat reading
  belongs to a different mode family. The sources do not resolve this.
  **UNVERIFIED** — do not implement vest-disconnect detection from bytes
  10–11 in 1.1.354 frag modes (see §8.3).
- The Disarm/Capture pairs describe modes that do not exist in the 1.1.354
  build. They are not part of the 1.1.354 wire behavior in this document.

For 1.1.354 FreeFrag/TeamFrag decoding, the capture-derived tables in §3.1–§3.2
win over the general Lua map wherever they differ.

## 4. The 5-slot hit shift register — CONFIRMED

Hit ids live at report bytes 12–16 (TeamFrag) / 8–12 (FreeFrag): five slots,
most-recent-first. Each hit shifts the array right and inserts the shooter's
id at slot 0 (byte 12 / byte 8). The most recent shooter is always slot 0.

Decisive evidence: capture 47 game 2, stream 04, frame 40003 — one hit
changed byte 12 `05`→`02` AND byte 13 `00`→`05` simultaneously (insert +
shift in a single hit). Subsequent hits shifted `02` right through bytes
14/15/16 (frames 44025/45921/47399/54623). Capture 22, with slots starting
empty, shows writes starting at byte 12 (frames 2884/2954/3024/3108).
Captures 15/19/23/32/34 are all consistent once pre-filled slots are accounted
for. This **supersedes** the Nov 10 analysis (Models A/B: bytes 12–16 vs
14–16), which observed writes only at bytes 14–16 because slots 12–13 were
pre-filled with the opponent's id at game start.

Rules for implementers:

- **Slots are NOT cleared at game start.** Capture 10 (a new game, shot
  counter reset) starts with bytes 8–9 already filled from game 09; capture
  47 game 2 starts with slot 0 = `05`. Snapshot the first report as the
  baseline; a slot value alone does not prove a hit happened in this game.
  Only *changes* (insert + shift) prove a hit.
- **Hits landed are derived, not reported.** A gun's status reports hits it
  *received*. Hits a gun *landed* equal the opponents' hits-received deltas.
  The live 2026-11-11 run caught this inversion in practice: comparing
  hits-received deltas crowned the gun hit most; the correct winner is the
  gun whose opponents' hits-received rose most (see §6.3). **CONFIRMED**
  (live run + shift-register reading).
- Scoring use (TeamFrag): 1 point per hit, 3 points plus a frag when the
  victim's life reaches 0, attributed to slot 0 (most recent shooter). This
  matches the Lua frag scoring (hit +1, frag +3) and the Java reference's
  status handling, both corroborated by the captures. **CONFIRMED** as the
  scoring derivation; the host computes it from status deltas.

## 5. Countdown (0x93)

Format: `01 <id> 93 <N>`, unicast, one per gun. Byte 3 is the countdown value
in seconds. (Source: `BeginMatch.lua`, countdown send site.)

| Countdown value | When | Evidence |
|---|---|---|
| `0A` (10) | Auto launch (default) | Capture 16 frames 233/235/237/239; capture 17 frames 1379–1385 |
| `05` (5) | Manual launch | Capture 42 frames 2887/2891 (after the trigger-ready phase, §7) |
| `03` (3) | TagThenShoot | Capture 25 frames 921/4185 |

**CONFIRMED.** The 4th byte is the countdown value; any tail after it is
stale. The Lua countdown duration is 10 s for Auto and halved to 5 s for
Manual — consistent with the captured values.

Behavior after sending:

- **No countdown-complete message exists.** The guns echo the countdown in
  ~11 ms, then count down locally in silence. The host waits the countdown
  duration and then polls: the first poll lands +10.1–10.4 s after the
  countdown in the captures. An implementation that waits for a reply
  mistakes the echo for completion (or times out). **CONFIRMED** (captures
  04/16 + live diagnosis; the proven client sleeps 10 s with one broadcast
  ping at ~5 s, then polls).
- A poll sent mid-countdown gets no reply (a live-run bug: a poll ~3.2 s
  after the countdown received zero replies). Wait the full countdown before
  polling. **CONFIRMED** (live run).
- The first poll after countdown may still go unanswered (capture 16 gun 03);
  retry next cycle (§2.3).

## 6. Game over (0x94)

### 6.1 Format and colours

Format: `06 <id> 94 01 <colour> 53 44 31 36` (SD16 form) or
`06 <id> 94 01 <colour> 53 44 30 37` (SD07 form). The `53 44 …` bytes are
ASCII `SD16` / `SD07` — the game-over sting reference from the Lua
`gameoverSnd` value. They are stale tail data after the 5 meaningful bytes
(`06 <id> 94 01 <colour>`), not part of the message. (Sources:
`EndMatch.lua` and `FinalRankings.lua`, game-over send sites.)

Colour: `01` = won / green, `02` = lost / red. **CONFIRMED** in TeamFrag
(capture 19 frames 5753/5755: gun 02 colour 02, gun 03 colour 01, each echoed
back as IN). Capture 42 shows the SD16 form for both guns. Send each gun its
correct final colour **once**. (The Java reference's two-pass sequence —
red to winners first, then green — is an apparent sequencing bug in that
code; the captures show one correct colour per gun. Do not copy the
two-pass behavior.)

### 6.2 SD codes by mode

| Mode | Game-over sting | Evidence / grade |
|---|---|---|
| TeamFrag, FreeFrag | SD16 ("Game over!") | **CONFIRMED** (captures 19/42; capture 05 FreeFrag) |
| TagThenShoot | SD07 ("Finished!") | **CONFIRMED** (captures 25/26/28) |
| OldFashionDuel | SD07 (override, as TagThenShoot) | **INFERRED** (Lua override + decoded bank transcript; no Duel capture exists) |
| FreeForAll, LastManStanding, StarterFrag | SD16 (default) | **INFERRED** (default `gameoverSnd`; no captures for these modes in the sources) |

SD16/SD07 are gun-bank sound references, not host audio files. The gun plays
the sting itself when it receives the game-over frame; the host's announcer
lines are separate.

### 6.3 Winner determination and the tie question

The winner is the player (or team) with the most points derived from status
deltas — for FreeFrag, most hits *landed* (opponents' hits-received deltas),
not most hits received (§4). The live 2026-11-11 run verified this: gun 03
landed 36 hits and was correctly declared winner with green; gun 02 received
36 and got red. **CONFIRMED** (live run).

**Tie behavior is UNVERIFIED in the original.** Capture 05 (FreeFrag) shows
both guns receiving colour `01`, but the sources do not state that game's
outcome, so it cannot be read as the original's tie behavior. The proven
minimal client sends both red on a tie — a replacement choice, not a
capture-verified original behavior. Do not present either as the original's
tie rule.

### 6.4 Clear form (Lua-only)

The Lua also defines a clear form, `06 <id> 94 00 00 00 00 00 00`
(`EndMatch.lua`, `FinalRankings.lua`). It was **not observed** in any capture
in the sources. **INFERRED** (Lua-only). Do not depend on it without a
capture; its effect (clearing the game-over display) is unproven on the wire.

## 7. RFID mechanisms: no distinct USB messages

General principle, **CONFIRMED** across all RFID event types: the gun reads
the RFID tag itself; the PC sees only the resulting state change in the next
0x95 status report. Prefix-window checks (±5 s around scan markers) show only
the 0x95 poll and the 0x05 ping — no distinct RFID message exists for any box
type. No USB message identifies a physical box or its payload: two physical
medkit boxes (or ammo boxes) are indistinguishable on USB; only accepted vs
rejected scans differ, by whether the status changes. **CONFIRMED**
(negative).

### 7.1 Ammo-box scan (RFID_AMMO) — CONFIRMED

Visible only as the ammo reset in the shooter's next status report: byte 7
resets to the full clip value (09 with ammo 9). Lags: +0.64 s / +0.74 s after
the reload markers in capture 13 (frames 2305/3987). Clips behavior: a reload
while ammo remains does not change byte 9; an ammo-box reload after an
auto-reload restores byte 9 (capture 19 frame 2983, `03`→`04`). A scan with a
full magazine changes nothing (capture 12). In swap mode, refills happen only
on accepted scans (capture 47: ammo refills `04`→`06` only on accepted scans,
frames 10325/12169/13571).

### 7.2 Medkit scan (RFID_MEDKIT) — CONFIRMED

Visible only as the health restore in the next status report: byte 5 restores
to FULL health (5 or 7 per config), not +1. Capture 22 frame 3948: byte 5
`01`→`05` (+0.66 s after the scan marker); capture 23 frame 2347: same
(+0.85 s). Capture 47 (swap mode): restores `06`→`07` (frame 42833) and
`05`→`07` (frame 48125). Repeated-box attempts produce no change (capture 47
frame 54623), matching the reported failure sounds and the accepted-scans-only
rule. Note: the scanned player's life reached 1, not 0, in captures 22/23 —
the "knocked out" description in the session notes does not match the
captured value. Medkit restore does not require knockout.

### 7.3 Team-base scan and respawn (RFID_TEAM_BASE / RESPAWN) — CONFIRMED mechanism, timer UNVERIFIED

Visible only as the health reset in the target's next status report: byte 5
`00`→`05`, ~3.0–3.3 s after the scan (capture 15 frame 3937, +3.05 s; capture
19 frame 4837, +3.29 s). Session 2 confirms the same mechanism (capture 32
frame 6835; capture 34 frame 2383). The ~3 s lag is the gun-to-PC propagation
of the scan.

Respawn = the team-base scan restoring health. **No automatic return was
observed in any capture**: capture 32 shows a 107.1 s gap between knockout
(frame 2035) and restore (frame 6835) with the user confirming no automatic
return and a team-base scan before the return. Capture 34 shows an 8.221 s
marker-to-restore interval against a respawn setting of 5 — inconsistent with
a simple 5 s scan→restore timer, but the marker is approximate (keyboard +
polling lag), so the timer's exact role is **UNVERIFIED** (hypothesis only).
The discriminating test defined in the sources (respawn 0/5/10, precise scan
markers) was not run. What the respawn config value times — scan→restore
delay, post-scan lockout, or something else — is an open question (see §11).

Tension to preserve: the FreeForAll Lua describes three respawn routes (shoot
the UbiConnect, tag a T-Base, or wait the set respawn time). That description
is **INFERRED** (Lua + manual glossary, FreeForAll only) and has no wire
confirmation; the TeamFrag captures show scan-gated respawn with no automatic
return. Do not generalize either observation to the other mode. TeamFrag's
rule (death → scan your own team's T-Base, matching the gun's configured team)
is Lua-sourced and consistent with the captures' team-base restores, but the
"own team only" restriction was not isolated on the wire (no wrong-team-base
capture is in the sources for TeamFrag) — treat the restriction as
**INFERRED**.

### 7.4 Swap (alternate boxes)

When swap is on, scanning the same T-Box twice in a row is rejected — the
player must alternate boxes. On the wire this appears as accepted scans
changing status and repeated-box attempts changing nothing (capture 47 ammo
and medkit sequences). The gun's rejection cue is a sound (`SCAN_BAD` in the
bytecode vocabulary); the rejection sound itself is user-reported, not in any
capture. Swap setting: 0x92 swap word (§2 of [05](05-config-and-settings.md)).
**CONFIRMED** as a status-level behavior (capture 47); the per-box tracking
inside the gun is **INFERRED** (bytecode logic).

## 8. Manual launch (A1 / C2)

Manual launch (default in TagThenShoot; selectable elsewhere) replaces the
straight 10 s countdown with a ready phase: players press the trigger when
ready, then a 5 s countdown runs. Wire behavior, **CONFIRMED as Lua-sourced
and capture-consistent** (review verdict; not frame-proven to the same depth
as the poll loop):

- The host sends `04 <id> A1 53 44 31 37` (SD17 — the gun prompt "Press the
  trigger to start the game") every 21st poll (Lua `ManualLaunch.lua`, prompt
  send site). Capture 42 shows A1 at frames 2329/2539/2743, before the
  trigger marker.
- The host sends `00 <id> C2` (button-state poll) about every 250 ms (Lua
  `ManualLaunch.lua`, poll sites; capture 47 shows C2 polls from frame 2675).
- Inbound 0xC2 replies: argument value 2 begins the match; value 1 changes
  team (Lua `PlayersManagement.lua`, dispatch sites). The exact inbound frame
  layout beyond the argument value is not detailed in the sources.
- After the trigger marker, the host sends the 5 s countdowns
  (`01 <id> 93 05`, capture 42 frames 2887/2891) and the match starts.

Grade honestly: the A1/C2 presence and sequencing are capture-consistent and
Lua-grounded, but the review labels this "CONFIRMED (source) / capture-
consistent" rather than frame-proven end-to-end. The trigger-ready semantics
(press trigger = ready) come from the design research (tips text + Lua
countdown halving). Implement the A1/C2 exchange as described, but flag the
inbound C2 argument semantics as the least-verified part of this section.

## 9. Vest polling (0xC1) and vest-disconnect handling

### 9.1 The C1 harness check — CONFIRMED (connected case)

- Host poll: `00 FF C1` (broadcast, size byte 0). Sent every 250 ms after
  config is done, while waiting for game start (Lua `PlayersSetup.lua`, poll
  sites). Observed 191× in capture 17 (frames 233+, ~0.26 s intervals) and
  17× in capture 16. Rare in-game (6 polls in capture 13).
- Reply: `01 <id> C1 01` — argument value 1 means the harness/vest is
  connected. Verified for all four guns in capture 47 game 1 (frames
  1207–39445) and capture 46. **CONFIRMED.**
- **The disconnected case (argument 0) has never been captured.** It is
  **UNVERIFIED** (optional, non-blocking): a client can treat 1 as connected
  and anything else as not-connected without it, but the 0 value itself is
  Lua-only.
- Size-byte caution: the official poll is `00 FF C1`; Efeion's Java sends
  `01 FF C1 00`. Use the official form. **CONFIRMED** difference.

### 9.2 No host LED/rumble command — closed by absence

No host LED/rumble command exists in any of the 45 captures (00–47),
including knockouts (15/19/32/34) and heavy hit traffic (22/23), and no such
command exists in the Lua command table. The gun's sounds, lights, and
vibration are driven by its own bytecode (the 0x83 upload). **CONFIRMED**
(negative). Do not run a dedicated LED/rumble capture; there is no host
command to find. Do not invent a downlink for lights or rumble.

### 9.3 Vest-disconnect anti-cheat — tkdrob-era design, NOT 1.1.354-verified

The master document describes a vest-disconnect design from tkdrob-era Lua:
watch the gun-seconds clock in the status stream; if it stalls for more than
5 s, consider the vest disconnected and disable the trigger via a downlink
gameplay-data value; manual leaderboard toggle; excluded modes (FreeFrag,
Infection, and later/community modes); a prevent-launch setting blocking
match start while a gun is disconnected. **None of this is verified against
1.1.354 captures in the sources.** The heartbeat slot it relies on conflicts
with the TeamFrag shots slot (§3.4), and no 1.1.354 capture shows a
trigger-disable downlink (§2.1). Treat the whole anti-cheat design as
**INFERRED** (later/community Lua) and out of scope for a faithful 1.1.354
reproduction unless new captures verify it. The C1 check in §9.1 is the only
vest-related behavior with 1.1.354 wire evidence.

## 10. TagThenShoot specifics

TagThenShoot (Tag'n Shoot) is the solo/objective mode: tag T-Bases in order,
then shoot the UbiConnect. Its wire behavior differs from the frag modes and
is **CONFIRMED** by captures 24–28, except for the 0xC3 path (§10.1).

- **Status bytes:** byte 5 = hits scored; byte 7 = phase flag (1 = scan
  phase, 0 = shoot phase). Every accepted T-Base scan coincides with byte 7
  `01`→`00` (capture 25: frames 1525/2507/3727; capture 26: frames 817/3061;
  capture 28: frames 1617/2411/2943/3275). After an accepted UbiConnect hit,
  byte 5 rises and byte 7 returns `00`→`01` (capture 25: frames
  1947/2781/4591/5235/5633; capture 26: frame 2113; capture 28: frames
  2211/2743/3105).
- **Byte 5 resets to `00` after the 4th hit completes the round** (capture 25
  frame 6859). The round-completion reset is capture-observed; its exact
  trigger condition beyond "4th hit" (e.g. interaction with the bases count
  setting) is not detailed in the sources.
- **Poll downlink carries the hit count:** `03 <id> 95 01 00 <N>`, with `N`
  incrementing per accepted hit (capture 25: id 02 `00`→`01`→`02` at frames
  1935/2769; id 03 `00`→`04` at frames 4579/5223/5621/6847). The host pushes
  the running count down to the gun; the gun's phase/score state follows the
  poll.
- **Wrong-base attempts change nothing.** Capture 28's wrong-base attempt
  (marker 2) produced no status change for ~11.7 s until the correct base-1
  scan. The rejection sound is user-reported only, not in the capture. The
  byte-7 transition does not identify *which* base was scanned — the same
  transition occurs for all four bases. There is no per-base USB signal.
- **Misses and pre-countdown triggers change nothing.** Capture 26's
  deliberate miss produced no change; the hit did (poll change frame 2101,
  status frame 2113). Pre-countdown trigger markers in captures 25/26/28
  show no status change (confirmed non-shots).
- **Countdown is `01 <id> 93 03`** (capture 25 frames 921/4185). **Game-over
  is the SD07 form** (§6.2). **Config is the 2-value form** (numberOfBase,
  assist — see [05](05-config-and-settings.md)).
- **Launch defaults to Manual** (the only mode where it does). The manual
  ready phase (§8) applies.

### 10.1 The 0xC3 tower-hit path — UNVERIFIED on real hardware (caveat)

The TagThenShoot Lua (`UATagThenShoot.State.RoundLoop.lua`) handles command
`0xC3` as a UbiConnect hit, and the announcer lines for UbiConnect hits are
registered against that command. **No 0xC3 frame appears in any capture
discussed in the sources, and the protocol map contains no 0xC3 entry.**
Whether a UbiConnect hit arrives as a distinct inbound 0xC3 message, is
inferred by the host from the byte-5/byte-7 status transitions, or is
signaled some other way, is **UNVERIFIED** on real hardware.

Implementers must NOT depend on receiving a distinct 0xC3 message: the
capture-verified TagThenShoot hit signal is the status transition (byte 5
rises, byte 7 returns to `01`) plus the poll-downlink count increment. Treat
any 0xC3 handling as Lua-only behavior awaiting hardware verification. This
caveat applies wherever 0xC3 is mentioned in these docs, including the
Introduction tutorial's "Shoot the UbiConnect" stage (see
[07](07-mode-behavior.md)) and the FreeForAll "shoot the UbiConnect" respawn
route (§7.3), both of which presume the same unobserved mechanism.

## 11. Open questions

- **Status byte 3** (`07` in 0x0A, `09` in 0x0E): observed constant per mode,
  meaning not established in any source. **UNVERIFIED.**
- **Meaningful prefix length of 0x95 replies** vs the byte 17/21 fields (§3.3).
  The size byte and the field discussion point in different directions.
  **UNVERIFIED.**
- **Byte 17/18 oscillation** (capture 47 game 1 only): cause unknown,
  non-blocking. **UNVERIFIED.**
- **Respawn timer semantics**: what the respawn config value times. Needs the
  0/5/10 precise-marker test. **UNVERIFIED** (hypothesis only).
- **0xC3 UbiConnect-hit path**: Lua-only, no capture evidence. **UNVERIFIED**
  — see §10.1. (Flagged for collection into the open-questions doc.)
- **TagThenShoot status size byte**: the sources give byte offsets 5/7 but
  never state the status report's size byte for this mode. **UNVERIFIED.**
- **C1 disconnected reply** (argument 0): never captured. **UNVERIFIED**
  (optional, non-blocking).
- **0x94 clear form**: Lua-only, never observed. **UNVERIFIED.**
- **Tie colour rule**: no original tie game-over is characterized in the
  sources. **UNVERIFIED.**
- **Clips byte 9 off-by-one** (starts at configured clips − 1; unlimited-ammo
  behavior differs): mechanism unmodeled. **INFERRED** at most.
- **Status layouts for FreeForAll, LastManStanding, StarterFrag, Duel**: no
  captures exist in the sources. Do not assume the 0x0E layout for them.
  **UNVERIFIED.**
- **Inbound C2 frame layout** beyond the argument value (2 = begin, 1 = change
  team): not detailed in the sources. **UNVERIFIED** detail.
- **Gun-seconds heartbeat / vest-disconnect design**: tkdrob-era only, slot
  conflict unresolved (§3.4, §9.3). **UNVERIFIED** for 1.1.354.

## Sources

- `sniffing-results/protocol_map.json` — `0x95_status_poll`, `0x93_countdown`,
  `0xC1_harness_check`, `GAME_END`, `TRIGGER_PULL`, `IR_HIT`, `RFID_AMMO`,
  `RFID_MEDKIT`, `RFID_TEAM_BASE`, `HEALTH_UPDATE`, `AMMO_UPDATE`, `RESPAWN`,
  `LED_RUMBLE` entries. Byte-level authority; the 5-slot shift-register model
  and 0x95 layouts here follow the map.
- `STATUS_DIFF_RESULTS.md` — marker-aligned 0x95 diffs (captures 08/09/10/
  13/15/19), ammo/clips/shots semantics, respawn lags, health 5-vs-4
  resolution. Its hit-id Models A/B and "no medkit event" statements are
  superseded by the session-2 review (see §4, §7.2).
- `REVIEW_2026-11-11.md` §a3–a7/a9/a10/a14 (medkit, respawn, shift register,
  TagThenShoot, game-over, cadence, byte 17/18), §b (confirmed/hypothesis/
  open table), §c (Java comparison — framing, downlink form, endMatch bug),
  §e (remaining experiments), §f step 5 (minimal loop sequence).
- `BATTLE_TAG_MASTER.md` §3 items 38–42 (live loop: echo rule, countdown
  silence, poll discipline, winner inversion fix), §4.3 (Lua status map,
  downlink values), §4.4 (vest-disconnect design — tkdrob-era, see §9.3),
  §5.3/§5.7 (flow, SD16/SD07).
- `GAME_DESIGN_RESEARCH.md` §1.6 (TagThenShoot flow, 0xC3), §2–§3 (launch
  modes, countdown durations, kill feed), §5.3 (announcer triggers incl.
  0xC3 registration).
- `ANTIGRAVITY_UPLOAD_SPEC.md` §9 (post-upload boundaries: config, countdown,
  poll loop).
