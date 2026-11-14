# 12 — Capture index and verification

What each numbered capture proved, what live replay verified, where the
sources disagreed (and how each disagreement was resolved), and how to
re-derive the protocol from scratch. Wire formats are in
[USB transport](02-usb-transport.md) and
[pairing and registration](03-pairing-registration.md); method is in
[overview and method](01-overview-and-method.md).

Claim grades (CONFIRMED / INFERRED / UNVERIFIED) and byte numbering
(zero-based within the 64-byte report) follow
[overview](01-overview-and-method.md) §0. No capture file is reproduced
here — this doc is an inventory and evidence index only.

## 1. The corpus and how it was read

**Two sessions, one numbering sequence.** Session 1 (Nov 9–10, 2026):
21 captures, `00`–`20`, in `sniffing-results/`, with `protocol_map.json`,
`probe.py`, `FINDINGS.md`, `HANDOFF.md`, `capture_log.md`,
`hid_descriptor.txt`, a marker tool, and 12 marker CSVs. Session 2
(Nov 10–11): 24 more capture files, numbered `21`–`47` with gaps, in
`review-2026-11-11/` alongside copies of the session-1 files — 45
`.pcapng` files in total in the review bundle. Numbers `27`, `43`, and
`44` have no files: failed zero-packet attempts, nothing to recover.
Capture `30` exists but was discarded by instruction (see §2.2): raw
data only, no gameplay observation is relied on from it. Capture `14`
was aborted mid-session but remains partially reusable. All captures
were taken on the 1.1.354 baseline (see [overview](01-overview-and-method.md) §0);
session 2 ran with `medkitPack` on (off in session 1), which changed one
config word and enabled bases 1–4.

**Reading method.** Session 1 was reviewed with a small custom pcapng
parser (USBPCAP linktype 249, 64-byte HID payloads extracted after the
variable-length USBPcap header); every frame number quoted in the
session-1 docs was re-checked against the raw files and matched exactly.
Session 2 was reviewed the same way, with a pure-Python USBPcap parser
(`status_diff.py`: pcapng parsing, HID-report extraction, status-stream
separation, change events, prefix scans) — every frame number in the
review was checked against the raw `.pcapng`, not copied from summaries.
`usb-rea` was used for host→device traffic and background-traffic
separation; the `0x95` status stream was diffed directly with the
marker-aware differ, because the in-game signal is 1-byte changes in
polled IN reports. Only leading bytes up to the meaningful length were
treated as protocol; tails are stale buffer residue (see
[USB transport](02-usb-transport.md) §4).

**Marker discipline and caveats** (apply to every marker-aligned claim):

- Markers were keyboard timestamps written by `mark_shots.py` (keys for
  shot, reload, team-box scan, knockout, noise, quit) into timestamped
  CSVs. Marker-to-frame deltas of 0.07–0.27 s were measured in the
  marked-shot captures.
- A scan marker records an *attempt*, not proof the gun accepted the
  scan. Acceptance is proven only by the following status change (or its
  absence).
- Gun ids are session-assigned, not fixed physical identities; gun
  labels (A/B) in filenames and markers are only as good as the key
  pressed in the moment. Capture 05's physical gun was unlabeled despite
  its filename.
- Two documented marker errors: one keyboard mistake (ignored) and one
  mislabelled team-base scan (logged as a reload). In capture 19 a verbal
  "gun A fired" contradicted the markers and streams and was treated as
  a slip. Marker CSV schema drifted mid-session (early files lack the
  `event` column); any parser must tolerate both schemas.
- Timestamps in the session-1 docs are Central time (the Windows PC
  clock), not Aaron's Eastern time.
- Session-2 review caveats honoured throughout: capture 30 discarded,
  14 aborted, 18 unanalysed (in session 1), 27/43/44 zero-packet, 37
  missed its intended settings change.

## 2. Capture inventory

### 2.1 Session 1 — captures 00–20

Packet counts are the independent reviewer's parser counts (total
packets / 64-byte HID data packets). "Proved" means the frame-verified
conclusion; see §5 for readings later superseded.

| # | File | Pkts / HID | What it proved |
|---|---|---|---|
| 00 | `00_idle` | 6 / 0 | Idle baseline: 30 s, game closed, descriptor/config packets only, **zero HID reports**. Endpoint descriptors at frame 4 (IN `0x85` / OUT `0x05`, 64-byte, `bInterval` 1). |
| 01 | `01_plug_launch` | 38 / 17 | Complete startup sequence, frame-verified: init poll ×4, init replies, `0x12` init ack (frame 21), revision request/reply (frames 23/25), alternating `0x86`/`0x06` (frames 27–37). See [pairing](03-pairing-registration.md) §4.1. |
| 02 | `02_pair_gun1` | 274 / 137 | Full gun-pairing sequence: announcements (frames 151–157), `0x13` start/stop (159/161), `0A`/`0B` acks (163/165/169), revision (171/173), profile request/reply (175/177, decoding to a name + `dog.tga`). Source of the replay pair sequence. |
| 03 | `03_pair_gun2` | 298 / 149 | Same pairing pattern for the second gun (frames 151–169), profile reply decoding to the same name/icon as capture 02. |
| 04 | `04_freefrag_start` | 1,532 / 763 | FreeFrag game start; `0x95` polling begins at frame 181. Countdown-echo timing source (echo ~11 ms, first poll +10.1–10.4 s after countdown). |
| 05 | `05_gun1_trigger_no_hit` | 2,706 / 1,350 | 4 trigger pulls → stream-`03` status byte 7 `00`→`04` (frames 313, 635, 859, 1055). Game-end messages at frames 2687–2693 (both guns colour `01`). Interrupted early (71.8 s of a planned 120 s); physical gun unlabeled — treat gun identity as unproven. |
| 06 | `06_gunA_single_shot` | 5,422 / 2,708 | Single shot: stream-`02` byte 7 `00`→`01` at frame 897; other stream constant. |
| 07 | `07_gunB_single_shot` | 3,340 / 1,667 | Single shot: stream-`03` byte 7 `00`→`01` at frame 599. |
| 08 | `08_marked_shots` | 3,546 / 1,770 | 4 marked shots; each followed 0.07–0.27 s later by exactly one byte-7 increment in the matching stream (frames 979, 1557, 2057, 2551). Trigger = status-counter change, no distinct message. |
| 09 | `09_marked_hits` | 3,126 / 1,560 | 4 marked hits: shooter byte 7 + target byte 5 + shooter-id bytes 8, then 9 (frames 949, 1519, 2079, 2607). Hit = target status change, no distinct message. |
| 10 | `10_marked_misses` | 2,668 / 1,331 | 4 marked misses: only the shooter's byte 7 moves. First reports already carry leftover hit-id slots from capture 09 — slots are NOT cleared at game start (supersedes the session-1 review's "counters reset" reading; see §5). |
| 11 | `11_reload` | 3,210 / 1,602 | Null capture: both `0A` streams have exactly 1 distinct payload each. The mode does not allow ammo boxes; nothing changed. |
| 12 | `12_ammo_box` | 6,750 / 3,372 | Full-ammo box scans: both `0E` streams exactly 1 distinct payload each. Inconclusive as documented — scanning with a full magazine changes nothing observable. |
| 13 | `13_shots_then_reload` | 4,642 / 2,318 | `0E` mode: byte 7 (ammo) `09`→`06` over 3 shots, byte 11 (shots) `00`→`03`, reload resets byte 7 to `09` (frames 1143/1311/1451/2305 and 3303/3499/3695/3987). Poll cadence source (~0.311 s) and idle-ping source (~6 s). |
| 14 | `14_aborted_restart` | 6,620 / 3,307 | Aborted; markers do not line up. Partially reusable: started mid-game (first `0x95` at frame 9 already shows 3 shots fired) and its `0x92` config (frame 947) has health 5 — resolving the health 5-vs-4 discrepancy as carried-over damage, not a setting. |
| 15 | `15_knockout_team_box` | 6,540 / 3,267 | Target byte 5 `04`→`00` over 4 hits (frames 1447–1839; shots 1 and 4 confirmed misses by the user), team-base scan restores byte 5 to `05` at frame 3937 (~3 s later). Respawn = team-base scan, no distinct message. |
| 16 | `16_freefrag_setup` | 3,892 / 1,943 | FreeFrag setup: flash sequence (`0x86` unlock frame 55, `0x81` erase frame 61, `0x82` prepare frame 67), 120-byte `0x83` upload (2×58 + 4, frames 73–85), 6-byte `0x92` config, countdown frames 233–239. Gun `03`'s first poll (frame 251) went unanswered — unanswered polls are normal. C1 polls: 17. |
| 17 | `17_teamfrag_setup` | 3,678 / 1,836 | TeamFrag setup: 20-chunk `0x83` upload totalling 1,152 data bytes (frames 73–187), 24-byte `0x92` config per gun, countdown frames 1379–1385. C1 polls: 191 (~0.25 s cadence). Baseline for the ammo 9-vs-12 diff (capture 20). |
| 18 | `18_teamfrag_steady_unanalyzed` | 4,166 / 2,080 | Steady-state `0E` traffic only (`0x95` polls + `0x05` pings, ~297 each). Nothing new; usable as a second steady-state sample. |
| 19 | `19_knockout_team1_box` | 5,784 / 2,889 | Ammo `09`→`00` over 9 shots, auto-reload to `09`, clips byte 9 `04`→`03`→`04` (auto-reload drops a clip, ammo-box reload restores it); target byte 5 `05`→`00`, team-base-1 scan restores `05`; game-end colours at frames 5753–5759 (gun `02` colour `02`/red, gun `03` colour `01`/green, SD16), echoed back as IN reports. |
| 20 | `20_teamfrag_ammo12_setup` | 2,884 / 1,439 | **Cleanest result in session 1.** All 20 `0x83` chunks byte-identical to capture 17; the *only* differing bytes are byte 9 of the per-gun `0x92` messages (`09`→`0C`, ammo 12). Proves settings live in `0x92`, not the bytecode upload. |

### 2.2 Session 2 — captures 21–47

No independent packet counts were published for session 2 in the allowed
sources, so none are given. Settings accumulate across the one-setting
experiments (e.g. capture 33 already carries respawn 5 and medkit 1);
only the targeted byte changes in each step, so the diffs are clean.

| # | File | What it proved |
|---|---|---|
| 21 | `21_teamfrag_medkit_on_setup` | Baseline re-verified with `medkitPack` on: medkit config word `0000`→`0001` (frames 203/207). `0x83` chunks byte-identical to capture 20 (22 sends for 20 unique chunks = 2 retransmits). |
| 22 | `22_medkit_scan` | Medkit scan: B's life `05`→`01` over four hits, then `01`→`05` at frame 3948 (+0.66 s after the scan marker), byte 5 only. No distinct medkit message in ±5 s windows (only `0x95` polls + `0x05` pings). Restore is to **full** health, not +1. B reached life 1, not 0 — the "knocked out" description does not match the captured value. |
| 23 | `23_second_medkit_scan` | Repeats capture 22: `01`→`05` at frame 2347 (+0.85 s). Medkit = status-only mechanism, confirmed. |
| 24 | `24_tagnshoot_setup` | Tag'n Shoot config: only 2 values, `07 02 92 00 03 02 0004 0001` (frames 121/125) — numberOfBase 4 (`2 + 2 × medkitPack`, pack on) and assist 1. |
| 25 | `25_tagnshoot_tbase_scans` | Tag'n Shoot decoded: T-base scans coincide with status byte 7 `01`→`00`; after an accepted UbiConnect hit, byte 5 rises and byte 7 returns `00`→`01`; byte 5 resets after the 4th hit (frame 6859); the poll downlink carries the running hit count. Countdown `01 <id> 93 03` (frames 921/4185). Game-over uses SD07, not SD16 (frames 3815/7097). Pre-countdown trigger markers show no status change (non-shots). |
| 26 | `26_tagnshoot_miss_hit` | Deliberate miss: no change. Hit: poll change frame 2101, status frame 2113. Game-over SD07 (frame 3403). |
| 27 | *(no file)* | Failed zero-packet attempt. Nothing to recover. |
| 28 | `28_tagnshoot_wrong_base_retry` | Wrong-base attempt: no status change for ~11.7 s until the correct base-1 scan's byte-7 change (frame 1617). The rejection sound is user-reported only, not in the capture. The byte-7 change does not identify which base was scanned. Game-over SD07 (frame 3377). |
| 29 | `29_teamfrag_respawn_setup` | One-setting experiment: respawn config word `0005` (frames 333/337). |
| 30 | `30_teamfrag_timed_respawn` | **DISCARDED** by instruction; retained as raw data only. Prefix summary shows steady-state only (pings, polls, C1, one countdown at frames 371–377, game-end at frames 5345–5351) and no config upload. No gameplay observation from it is relied on. Its setup (capture 29) is unaffected. |
| 31 | `31_teamfrag_respawn_setup_repaired` | Repaired respawn setup: respawn word `0005` (frames 235/239). |
| 32 | `32_teamfrag_timed_respawn_retry` | B's health `05`→`00` at frame 2035, `00`→`05` at frame 6835, 107.1 s later. No automatic return; the user confirmed B scanned team base 2 before returning. Scan unmarked, so no scan-to-return time is measurable. Respawn is scan-gated, never automatic. |
| 33 | `33_teamfrag_power5_setup` | One-setting experiment: beam-power byte `03`→`05` (frames 199/203). |
| 34 | `34_teamfrag_marked_base_respawn` | B at `00` (frame 1113), restored `00`→`05` at frame 2383, 8.221 s after the approximate scan marker (keyboard + polling lag — not exact). Inconsistent with a simple 5 s scan→restore timer, but the marker is approximate: respawn-timer semantics stay UNVERIFIED (see §6). |
| 35 | `35_teamfrag_health7_setup` | One-setting experiment: health word `0007` (frames 313/317). |
| 36 | `36_teamfrag_clips6_setup` | One-setting experiment: clips word `0006` (frames 189/193). |
| 37 | `37_teamfrag_friendlyfire_on_setup` | Null capture: **no** `0x92`/`0x83`/`0x86`/`0x81`/`0x82` reports at all (verified by prefix scan) — it missed its intended settings change. The retry is capture 38. |
| 38 | `38_teamfrag_friendlyfire_on_setup_retry` | Friendly fire word `0001` (frames 193/197); assist `0000` in the later upload of the same capture (frames 1409/1413). |
| 39 | `39_teamfrag_swap_on_setup` | Swap word `0001` (frames 211/215); unlimited ammo `0014` (= 20) in the later upload (frames 2105/2109). |
| 40 | `40_teamfrag_unlimited_ammo_shots` | 36 marked shots: shots counter `0000`→`0024` (frame 2317), ammo stays `0014`, clips stay `0005`. Supports unlimited firing for at least 36 shots, not an absolute guarantee. |
| 41 | `41_teamfrag_duration10_setup` | Null result: config and all 20 chunks byte-identical to capture 39's later upload (frames 187/191). Duration is host-side, nowhere in `0x92`/`0x83`. |
| 42 | `42_teamfrag_manual_launch_setup` | Manual launch: `0xA1` SD17 prompts (frames 2329/2539/2743, every 21st poll per Lua), `0xC2` button-state polls, then `93 05` countdowns (frames 2887/2891) after the trigger marker. Config/chunks identical to capture 41 — launch mode is host-side. Game-over SD16 (frames 9143/9145). |
| 43–44 | *(no files)* | Failed zero-packet attempts. Nothing to recover. |
| 45 | `45_pair_guns_C_D_retry` | Pairing of two more guns: gun C = id `04`/classId `03`, profile decoding to a name + `eagle.tga` (frames 141–159); gun D = id `05`/classId `04`, name + `cat.tga` (frames 177–209). Independent proof that `id = classId + 1` and that ids follow pairing order. |
| 46 | `46_teamfrag_four_teams_setup` | Four teams: team words `0001`/`0002`/`0003`/`0004` for ids `02`/`03`/`04`/`05` (frames 367/371/375/379). One marked shot per gun (frames 5311/5809/6229/6727). Status byte 17 = `04` in all four streams (vs `02` in 2-gun captures — plausibly a gun/team count field, INFERRED). |
| 47 | `47_reverse_reconnect_swap_boxes` | Reverse re-pairing: D paired first takes id `04` (frames 347/349), C second takes id `05` (frames 391/393) — the reverse of capture 46, verified by marked shots. Full config (health 7, ammo 6, clips 6, swap 1, respawn 5, FF 1, assist 0, medkit 1, teams 1–4; frames 1163–1175). Ammo refills only on accepted scans (frames 10325/12169/13571); medkit restores to 7 (frames 42833/44909/48125); repeated-box attempts produce no change. **Decisive hit-id frame:** game 2 frame 40003 — one hit changes byte 12 `05`→`02` AND byte 13 `00`→`05`, proving the 5-slot shift register. Late re-registration without `0x13` (frames 37631–37669). Byte 17/18 oscillation in game 1 (UNVERIFIED cause, non-blocking) and one unexplained byte at the game-2 start (stream-`04` byte 12 = `05` at frame 39483). |

### 2.3 What the corpus closed, in one view

After session 2, the merged protocol map's 9 confirmed / 3 inferred /
2 unknown became **14/14 confirmed**: the 3 inferred (RFID_AMMO,
RFID_TEAM_BASE, RESPAWN) were confirmed as `0x95`-status-only
mechanisms; RFID_MEDKIT was confirmed the same way; LED_RUMBLE was
closed by absence (no host LED/rumble command in any of the 45 captures,
including knockouts and heavy hit traffic, and none in the Lua table —
gun lights/sounds are bytecode-driven). The hit-id model was resolved as
a 5-slot shift register. Init/pairing/revision/profile were verified
frame-by-frame. The `0x92` config field map was confirmed byte-exact in
every one-setting experiment. Duration and launch were confirmed
host-side. Remaining manual collection is small and specific — see §6.

## 3. The protocol map and its history

`sniffing-results/protocol_map.json` is the byte-level authority. Schema:
`device`, `hid_descriptor`, `usb_configuration`, `messages[]` (14 entries:
name, direction, report_id, length, `bytes_hex`, fields, evidence pcap,
confidence), plus `game_version` (`1.1.354`), `merge_notes`, and
`additional_commands` (12 further commands: `0x02`, `0x03`/`0x04`,
`0x05`, `0x06`, `0x81`, `0x82`, `0x83`, `0x83` profile write, `0x84`,
`0x86`, `0x93`, `0x95`, `0xC1` — documented so they are not lost).

Confidence rules, applied strictly: `confirmed` = capture frame evidence
exists, or Lua AND capture agree; `inferred` = Lua-only or a single
capture observation; `unknown` = neither. An empty `bytes_hex` means **no
distinct message bytes exist** for that event (it rides on the `0x95`
status stream) — not "not yet found". `report_id` is 0 everywhere
(implicit; the descriptor declares no Report ID item).

History:

1. **Original map** (session 1, backed up as
   `protocol_map_original.json`): all 14 messages `unknown`, zero
   filled-in bytes. Honest but stale — the decoded findings in
   `FINDINGS.md` had never been transferred into it.
2. **Nov 10 merge** (`PROTOCOL_MAP_MERGE.md`): Lua command table +
   findings + sniffing review + status-stream differ results merged in:
   9 confirmed / 3 inferred / 2 unknown. The merge resolved the
   `0x86`/`0x81`/`0x82` flash commands, the C1 harness meaning, the full
   `0x92` layout, the health 5-vs-4 question, and the `0x94` format.
3. **Nov 11 reconciliation** (session-2 review): 14/14 confirmed;
   TeamFrag upload corrected to 1,152 data bytes; hit-id model resolved
   to the shift register; all confidences upgraded.
4. **Nov 12 corrections** (recorded in the map's field notes): the
   `0x81`/`0x82` flash address re-parsed as `0x4000` from capture 16
   frames 61/67 (superseding parse errors), and the Lua-only profile
   write path added.

One staleness note: the map's `merge_notes` still say no write was ever
attempted from `probe.py`. That was true at merge time (Nov 10) and is
superseded by the Nov 11 replay work in §4 — do not read it as current.

## 4. Replay verification results

### 4.1 Simulated-device tests (stub-hid, no hardware)

- Driver v2 (drain + re-ack): pair 7/7, resume 2/2, init regression 3/3
  against a stub injecting stale `0x12` repeats. An earlier failure run
  was a test-stub padding bug (replies 45–51 bytes, not 64), fixed in the
  stub, not the driver.
- Driver v3 (live-ref substitution + drain): against a ref-validating
  stub, a stale-ref revision request FAILS exactly like the live retry,
  while the new pair passes 8/8, resume 2/2, init 3/3. The stub
  reproduced the live failure before the live fix was attempted.

### 4.2 Windows replay (2026-11-11, live hardware)

| Run | Result | What it established |
|---|---|---|
| Init replay | **PASS 3/3** (12:24 UTC) | UbiConnect answered the init poll + revision request exactly as captured. |
| Pair run 1 | Stopped 6/7 | A stale `10 00 12` repeat arrived in the same millisecond as the revision request (same behaviour as capture 02 frame 166). Gun said "OK" audibly — pairing was accepted; the driver was too strict, not the protocol. Fix: drain + re-ack, plus a resume sequence (revision + profile only) for the already-paired gun. |
| Pair run 2 (fixed driver) | Stopped 6/7 | 7 stale repeats drained, 2 re-acks, no revision reply in 2 s. Same gun, "OK" both times: the acks were accepted and the gun stopped requesting, but the revision request was ignored. Root cause: the request carried a stale capture ref; the gun validates the ref at bytes 6–13 (see [pairing](03-pairing-registration.md) §4.3). |
| Pair run 3 (live ref) | **PASS 8/8** (12:42 UTC) | Revision request with the live ref answered at once (`04 02 04 00 02 00 00`); profile reply decoded to a name + `snake.tga`; drain discarded 6 stale repeats. Gun said "OK" and showed player 1 (protocol id `02` = player 1; id `01` is the UbiConnect). Windows gate PASSED. |
| Original-game check | Gun paired fine | Aaron re-opened the original game after all replay work: normal pairing/play intact. Windows phase fully closed. |

### 4.3 Mac replay (2026-11-11, live hardware)

Probe found exactly one UbiConnect; descriptor 34 bytes, 64-byte in/out
reports, no Report ID / Feature report; 10 s idle read silent; **no
"Allow accessory to connect?" prompt and no Input Monitoring request**
— both open questions closed. All three dry runs passed. **Init PASS
3/3; pair PASS 8/8.** The live ref matched the Windows live ref
(per-gun, stable across hosts) and the profile decoded identically.
Drain discarded 6 stale repeats, 2 re-acks sent. hidapi ran from a
temporary venv (system pip blocked). The resume sequence was not run —
not needed, since pair passed. Phase 1 gate PASSED on both hosts.

### 4.4 Minimal FreeFrag loop (2026-11-11, live hardware, 2 guns)

| Run | Result |
|---|---|
| Diagnostic phase | Two reported blockers were both code bugs, diagnosed from captures 04/16: no countdown-complete message exists (echo in ~11 ms, then 10 s silence, first poll +10.1–10.4 s); the poll works and the reported `01 02` was a stale countdown echo. Fixes: sleep-based countdown with one broadcast ping at ~5 s, drain-then-read-until-`0x95` polling, retry unanswered polls next cycle, baseline + delta decoding. |
| Passing loop (log `freefrag_20261111_153511.log`) | Init + pair both guns, upload 120 bytes (58+58+4), config, countdown with ping, **118.5 s active polling: 1,254 polls sent, 1,250 status replies (99.7%; 4 unanswered), 14–29 ms replies**, scoreboard every 2 s, game-over once per gun (tie, both red), RESULT: PASS, exit 0. Caveats: **no shots were fired** (0–0 tie) and only 1 vest was connected — the marked-shot test was still untested. Poll rate ~189 ms/gun (faster than the game's ~311 ms); guns handled it fine. |
| Marked-shot test | **PASSED** (as reported in the session summary: a poll example shows hits=3, shots=3 for both guns; 3 shots each way with both vests on; shots from one gun register as hits on the other vest). The pairing regression blocking it was resolved by running the original game once to reset both guns. The "≈31 s gun-side pairing window" theory from the regression analysis was refuted from 5 logs: the guns were not in pairing mode when the code looked (one gun's ref never appeared; in one run nothing announced at all), sequential pairing takes ~4 s, and the 30 s figure was the code's wait timeout. A real code bug was found in the same review (a wait-phase set that could never exceed 1 element, always burning the full wait). |
| ClassId fix run (`freefrag_20261111_191132.log`) | Both-guns-show-"Player 1" bug fixed by substituting classId in acks/revision (see [pairing](03-pairing-registration.md) §3.3); displays showed 1 and 2. Same run exposed the inverted-winner bug: gun `02` fired 10 / received 3, gun `03` fired 3 / received 10, and the code declared gun `03` (it compared hits *received*). |
| Crashed run (`…193321.log`) | `NameError` at the pairing-loop call (stale variable name after a refactor) before any pairing message was sent — no gun state to reset. Collection saw both refs in 0.1 s and the stable ref→number mapping loaded correctly. |
| Final verified run (`freefrag_20261111_194057.log`, 7,052 lines) | **RESULT: PASS.** One-word fix only (proven by diffing against the pre-fix backup: exactly one line differs). Init + pair both guns, both vests connected, upload + config + 10 s countdown, **120 s round, 605 polls/gun, 1,201/1,210 replies (99.25%), zero errors/exceptions/FAILs**. Final: gun `02` fired 9 / received 36 / landed 7; gun `03` fired 36 / received 7 / landed 36. **Winner: gun `03` (36 hits landed) — CORRECT.** Game-over colours correct (winner green, loser red). Stable ref→number mapping assigned the same numbers in three consecutive runs. |

**Trust the logs, not the summaries.** The session docs repeatedly
overstated results and misattributed logs — e.g. "340+ polls / 0
unanswered / 100%" vs the actual 1,254 / 4 / 99.7%, and "3 chunks of
40 bytes" vs the actual 58+58+4. Every number in §4.4 above was verified
against a log or a re-parsed capture, and the summary errors are
corrected inline. The same rule applies to future work: verify from the
primary artefact.

### 4.5 Phase 1 exit criteria (all met, 2026-11-11)

Descriptor matches Windows↔Mac; protocol map covers all 14 messages with
evidence; Mac `probe.py` replays init/pair; uplink + downlink behaviour
replayed (exceeded by the full loop); live `hid_open` on the Mac
confirmed with no prompts; findings recorded with works/unknowns/replay
steps/failures. Remaining opens were small and non-blocking: ear-check,
respawn-timer precision, vest-disconnected C1, optional French announcer
set, 1.2.x curiosity.

## 5. Source conflicts found and how they were resolved

Rule applied throughout: **the current protocol map wins over older
source docs; re-parsed captures win over summaries; live replay wins over
both for behaviour it directly tested.** Each conflict below states the
resolution used in these docs.

| # | Conflict | Resolution |
|---|---|---|
| C1 | **Hit-id model.** Nov 10 merge + session-2 prompt: TeamFrag hits write bytes 14/15/16 only; bytes 12–13 never change; Efeion's "byte 12" NOT confirmed. Session-2 review: 5-slot shift register, bytes 12–16, most-recent-first, decisive frame 47 game 2 frame 40003. | Review + current map win. Bytes 12–13 do change once pre-filled slots are accounted for; the shift-register model explains all observations. The Nov 10 note is superseded. |
| C2 | **TeamFrag upload size.** Findings: "about 1,097 bytes". Nov 10 merge: 1,155 bytes (19×58 + 53). Session-2 review + current map: **1,152 data bytes** (19×58 + 50). | 1,152 wins. The merge conflated the last chunk's message size (53) with its data length (50). The substantive conclusion (bytecode invariant across settings) is unaffected. |
| C3 | **Flash address/size.** Merge doc: `0x81` address bytes "look like stale buffer content"; `0x82` address `00000000`, size `0x0400`. Current map (direct re-parse, Nov 12): `0x81`/`0x82` address **`0x4000`**, `0x82` size `0x78` (120, FreeFrag). | Current map wins (capture 16 frames 61/67 re-parsed; agrees with the Lua flash-memory address). The earlier readings were parse errors. |
| C4 | **`0x82` length field.** Current map: `length` 10, but its own `bytes_hex` is 9 bytes, matching the Lua construction (3 header + 4 address + 2 size). | 9-byte form wins. The map's length field appears erroneous; flagged here rather than silently corrected. |
| C5 | **Revision reply.** Efeion Java comment: revision request received no response. Captures: replies exist (capture 01 frame 25; capture 02 frame 173; capture 45 frames 155/205). | Captures win. Likely cause: Java's double-read bug discarding the reply. Do not copy Java's behaviour. |
| C6 | **Ping / vest-poll size byte.** Java: `01 FF 05 00`, `01 FF C1 00`. Official captures: `00 FF 05`, `00 FF C1`. | Official bytes win. |
| C7 | **Reconnect / 80 s unregister.** Master doc §§4.3–4.4 (tkdrob-sourced): whitelisted rejoin when `reconnect` is 1 or 3; unregister after 80 s. Pairing review (grep over the original tree): no `GameSettings`/`reconnect` in original 1.1.354 Lua; original Pinger uses 5 s/10 s and 2 s/4 s timings with protected mode. | Pairing review wins. Reconnect settings and the 80 s threshold are MOD behaviour, never original. See [pairing](03-pairing-registration.md) §§6–7, 12. |
| C8 | **Revision-request form.** Map + review record the 3-byte prefix `00 <id> 04`. Live replay proved bytes 6–13 must carry the live gun ref (stale ref ignored twice, live ref answered at once). | Both, combined: send the 14-byte form with the live ref (see [pairing](03-pairing-registration.md) §4.3). The map's prefix is correct as far as it goes; the tail is load-bearing. |
| C9 | **Capture 10 / first-report state.** Session-1 review: "counters reset to 0 in the new game". Differ + session-2 review: hit-id slots NOT cleared; first reports carry leftovers. Minimal-loop diagnosis: first-poll counters already nonzero. | Later analyses win. Neither counters nor slots may be assumed zero; snapshot the first report as the baseline and decode deltas. |
| C10 | **Game-over SD bytes.** Map note: `53 44 31 36` ("SD16") is stale tail, not part of the message. Lua construction includes the sting bytes in the sent list, and Tag'n Shoot sends SD07 instead of SD16 — variation by mode proves the bytes are sent deliberately. | Lua + captures win. The `0x94` form is 9 bytes including the sting selector (SD16 default, SD07 Tag'n Shoot/Duel override). The map's "stale tail" note is inconsistent with the SD07 variation and is not followed. |
| C11 | **FreeFrag tie colour.** Capture 05: both guns colour `01`. Live minimal loop: tie → both red (`02`). | UNVERIFIED — left open. Capture 05's game state (whether it was a tie) is not established in the allowed sources, and the loop's tie rule is an implementer choice, not captured original behaviour. Flagged for the open-questions doc. Do not implement either as confirmed original behaviour. |
| C12 | **Poll cadence.** Lua nominal: 100 ms RoundLoop cycle. Measured wire cadence: ~0.311 s per gun. | Measured cadence wins for implementation. |
| C13 | **Descriptor interface number.** 2012 forum log: -1. Live hidapi read: interface 0. | Live read wins. |
| C14 | **`0x22` / `0x85`.** Session-2 prompt instructed adding them to the map's additional commands. The current map omits them; the session-2 review lists them as native-code-only, never observed on the wire. | Not wire-confirmed. Omitted from the wire spec; firmware paths must not be used. |
| C15 | **Init sequence.** Session-2 prompt summary: init → `0x85`/`0x84` revision exchange. Frame-verified sequence: `0x04` revision exchange + `0x84` profile exchange, no `0x85` observed. | Frame-verified sequence wins (capture 01, frames 5–37). |
| C16 | **`0x82` rendering.** Session-2 review §a8 renders the prepare message as 8 bytes (`06 FF 82 00 00 40 00 78`). Map `bytes_hex` is 9 bytes (`06 FF 82 00 00 40 00 00 78`); size `06` = 6 args (4 address + 2 size). | Map's 9-byte form wins; the review rendering drops a zero byte. |
| C17 | **Status offsets notation.** Minimal-loop prompt: FreeFrag hits BE16 bytes 4–5, shots bytes 6–7. Reviews: byte 5 (hits), byte 7 (shots). | Same values, different notation: the review numbers are the low bytes of the big-endian 16-bit fields. These docs use zero-based report bytes consistently. |
| C18 | **Session-doc summaries vs logs.** Copilot docs claimed "340+ polls / 0 unanswered / 100%", "3 chunks of 40 bytes", and a "≈31 s pairing window". Logs and re-parsed captures show 1,254 / 4 / 99.7%, 58+58+4, and no such window. | Logs + captures win, always. See §4.4. |

## 6. How to re-derive (and what not to repeat)

### 6.1 Re-derivation checklist

An implementer redoing this work from scratch, in order:

1. **Get the baseline.** Install/run the original game (record the
   main-menu version; do not mix versions), confirm it sees the
   UbiConnect, pairs 2 guns, and runs a FreeFrag game. If the baseline
   fails, stop — do not sniff a broken setup.
2. **Read the Lua first.** Extract the installer, find the plaintext
   Lua, and read the command constructions (proxy, device states,
   RoundLoop, setup states) before capturing. The Lua gives hypotheses;
   captures give verdicts. Keep file:line citations for every Lua claim.
3. **Dump the descriptor.** hidapi enumerate + `get_report_descriptor`;
   save the descriptor and endpoint details. Confirm 64-byte reports, no
   Report ID, no Feature.
4. **Capture labelled, one action per file**, with the marker tool
   running, on Windows via USBPcap (`usb.idVendor == 0x044f`). Keep
   captures short. Log timestamps, frame numbers, light behaviour, and
   spoken prompts. Never flash firmware, never modify the installed game,
   never replay without explicit approval.
5. **Parse and diff.** Parse USBPcap (linktype 249) to 64-byte HID
   payloads. Diff host→device traffic per action; diff the `0x95`
   status stream with a marker-aware differ (the signal is 1-byte
   changes in polled replies). Compare only meaningful prefixes; ignore
   stale tails. Run one-setting experiments (change exactly one setting
   between two setups, diff the `0x92`) — the method that proved the
   config layout.
6. **Write the map with evidence.** Every byte cites a capture + frame
   number or a Lua/Ghidra source. Use the confidence rules in §3. Empty
   bytes mean "no distinct message", never "not yet found". Never invent
   a byte.
7. **Replay Windows-first, then Mac.** Replay init, then pair, then one
   uplink + one downlink, with per-step assertions and a write gate.
   Expect echoes and stale repeats; drain and read-until-match. If a
   replay fails, diff the exact bytes sent against the capture before
   theorising — and check the ref tail (see C8).
8. **Build the minimal loop last.** Pair, upload, config, countdown,
   poll, marked shots, game-over. Verify from logs, not summaries.
   Baseline + delta decoding; retry unanswered polls; never mock status.

### 6.2 Do NOT repeat (settled questions, captures 00–47)

Init/pairing/revision/profile; the `0x92` config field map (TeamFrag 9
values, Tag'n Shoot 2 values); `0x83` bytecode invariance across
settings; medkit, ammo-box, and team-base scans as status-only
mechanisms; T-Base 1–4 scans in Tag'n Shoot (the status change does not
identify which base was scanned); wrong-base retry (USB: no change);
automatic return (does not exist); four teams / 4-gun setup; reverse
re-pairing / id assignment; swap-box alternation (no USB box identity
exists to learn more from); unlimited ammo (≥36 shots); duration/launch
as host-side; game-over colours / SD16 vs SD07; hit-id offsets and
ordering; ping/poll/C1 cadence; LED/rumble host commands (closed by
absence — do not run a dedicated LED/rumble capture); capture 30
(discarded); captures 27/43/44 (zero-packet, nothing to recover).

### 6.3 Remaining manual collection (still open after Nov 11)

1. **Ear-check** (10 minutes, no capture): play the decoded gun WAVs
   next to the real gun's spoken prompts and confirm pitch/pace match.
   The only remaining check on the 8 kHz rate and the decode itself.
2. **Respawn-timer precision** (one short session, three tiny games):
   TeamFrag, health 5, respawn 0, then 5, then 10. Knock one gun out,
   scan its team base with a marker pressed at the scan instant, record
   the restore frame. Separates "timer scales with the setting" from
   "restore is immediate after scan regardless".
3. **(Optional, non-blocking) Vest-disconnected C1 reply:** one short
   capture with a vest unplugged during setup, to confirm the reply arg
   reads `00` for that gun. All observed replies are `01`. Skip if any
   trouble — a client can treat `01` as connected and anything else as
   not-connected without this.
4. **(Only if it ever matters) Byte 17/18 oscillation:** a long 4-gun
   game with Swap ON vs Swap OFF, noting per gun which streams flip. Do
   not run speculatively — the client ignores bytes 17–18.

### 6.4 Open questions carried forward (UNVERIFIED — for `14-open-questions.md`)

The `0x13` firmware effect and gun-side bond storage; revision-number
meaning; disconnected-vest C1 value; respawn-timer semantics; 8 kHz
ear-check and remaining sound mappings; byte 17/18 oscillation and the
capture-47 game-2 boundary byte; out-of-range kick time (10 s vs 30 s);
rejoined-gun profile-checked persistence; whether the whitelist-restore
branch ever executes; the Bytecode `stopSending` race; upload chunk
pacing; reader-thread/lock discipline for a concurrent implementation;
revision-request bytes 3–5 validation and profile-request tail
positions; FreeFrag tie colour; the `0x06` purpose; `0x03`/`0x22`/`0x85`
as wire behaviour.

## Sources

Grounded in: `SNIFFING_REVIEW_2026-11-10.md` (§§1–9, packet counts and
frame verifications for captures 00–20); `REVIEW_2026-11-11.md` (§§a–f,
frame-verified verdicts for captures 21–47, corrections, remaining
collection, shortest path); `BATTLE_TAG_MASTER.md` (§3 items 12–13,
28–30, 34–42, §§4.5–4.6, 4.9, changelog); `PAIRING_UNPAIRING_REVIEW.md`
(re-parsed captures 02/03/45/46/47); `WINDOWS_SNIFFING_PROMPT_V2.md`
(session-2 method and checklist); `sniffing-results/protocol_map.json`
(14/14 map, merge notes) and `sniffing-results/PROTOCOL_MAP_MERGE.md`
(merge history). Capture filenames and file counts verified against the
`sniffing-results/` (21 files) and `review-2026-11-11/` (45 files)
directories. Where sources disagreed, §5 states the resolution.
