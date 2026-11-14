# 02 — USB transport

How bytes move between the host and the UbiConnect: HID identity, report
format, hidapi usage, framing, the echo rule, and the read discipline that
live replay proved necessary. Message-level formats (pairing, config,
status) are in [pairing and registration](03-pairing-registration.md) and
the sibling protocol docs; the byte-level authority for every frame is
`sniffing-results/protocol_map.json`. Background and method are in
[overview and method](01-overview-and-method.md); evidence and replay
results are in [capture index and verification](12-capture-index-and-verification.md).

Claim grades (CONFIRMED / INFERRED / UNVERIFIED) and byte numbering
(zero-based within the 64-byte report, report-ID byte stripped) follow
[overview](01-overview-and-method.md) §0.

## 1. HID identity (CONFIRMED)

Live hidapi enumeration on Windows (`hid.enumerate(0x044F, 0xB365)`),
confirmed again by the Mac probe on 2026-11-11 (exactly one matching
device found):

| Fact | Value |
|---|---|
| VID / PID | `0x044F` (1103, Thrustmaster/Guillemot) / `0xB365` (45925) |
| Manufacturer / product | `Ubisoft` / `UbiConnect` (manufacturer string carries trailing spaces in the live read) |
| Release number | 256 (`0x0100`) |
| HID usage page / usage | `0xFF01` (65281, vendor-defined) / usage `0` |
| Interface | Interface `0`; one matching HID interface enumerated |
| Serial number | Empty |
| USB version | 1.10; one HID interface |

The UbiConnect is a USB **HID** device. It is not CDC/serial and not a COM
port. Enumerate by VID/PID and filter on `usage_page == 0xFF01`.

Minor conflict, resolved: a 2012 forum HID log recorded
`interface_number=-1`. The live hidapi read (interface 0) wins; the forum
value is a logging artefact of that tool, not a device property.

## 2. Report descriptor and endpoints (CONFIRMED)

Descriptor read live via hidapi `get_report_descriptor()` (hidapi 0.15.0),
34 bytes, decoded with usb-rea. Raw descriptor bytes:

`06 01 FF 09 00 A1 01 09 00 15 00 26 FF 00 75 08 95 40 81 02 09 00 15 00 26 FF 00 75 08 95 40 91 02 C0`

Decoded meaning:

| Report | Size | Notes |
|---|---|---|
| Input | 64 bytes | Vendor-defined usage `0x00`, 8 bits × 64 fields, logical range 0–255 |
| Output | 64 bytes | Same shape as Input |
| Feature | None declared | — |
| Report ID | None declared | Reports use implicit report ID `0`. This is why hidapi calls still need an explicit leading `0x00` byte — see §3. |

Endpoints (from the USB configuration descriptors in capture 00, frame 4):

| Endpoint | Direction | Type | Max packet size | bInterval |
|---|---|---|---|---|
| `0x85` | IN | Interrupt | 64 bytes | 1 |
| `0x05` | OUT | Interrupt | 64 bytes | 1 |

The Mac replay verified the descriptor matches between Windows and Mac
(34 bytes, 64-byte in/out, no Report ID item, no Feature report) — one of
the Phase 1 gate criteria.

## 3. hidapi: 65-byte writes and reads (CONFIRMED)

The descriptor declares 64 report-data bytes, but hidapi transfers carry a
leading report-ID byte:

- **Writes are 65 bytes:** a leading `0x00` report-ID byte followed by the
  64-byte message buffer. Sending a bare 64-byte report without the leading
  `0x00` shifts every byte by one and breaks framing.
- **Reads are 65 bytes:** the report-ID byte plus 64 data bytes; the ID is
  stripped before the message is interpreted, so all byte offsets in these
  docs are relative to the 64-byte message.

This packing was established by a Ghidra pass over the native executable
(as reported in the session-2 sniffing prompt and the evidence review —
INFERRED here as a secondhand summary, but CONFIRMED behaviourally: the
replay driver and `probe.py` both implement the 65-byte path, and live
replay on both Windows and Mac passed with it). The reported mechanism:
the Lua send routine copies the byte list verbatim into a 64-byte buffer
(max 64 elements), and the native write prepends the `0x00` byte; the
Windows HID driver strips the ID so 64 bytes go on the wire with the
message at byte 0. The same history explains Efeion's 64-bit Windows
failure: the write threw a "parameter is incorrect" error until a `0x00`
report-ID byte was prepended.

**hidapi, not pyusb/libusb** (CONFIRMED as the working choice on macOS).
macOS's kernel HID driver cannot be detached by libusb; hidapi uses native
IOKit. No kext, DriverKit, entitlement, or code-signing is needed for
local hidapi use. Setup notes from the live Mac runs:

- `pip install hidapi` in a virtual environment. System pip is blocked by
  the managed Python environment (`externally-managed-environment`); the
  Mac replay and the minimal game loop both ran from a temporary venv.
  The build must do the same (venv, not system pip). Python 3.11+.
- **No macOS prompts appear.** The pre-run expectation was a one-time
  Apple Silicon "Allow accessory to connect?" prompt, with Input Monitoring
  expected not to be required for a pure vendor-page device. The live Mac
  replay closed both questions: neither prompt appeared; hidapi just works
  for the UbiConnect.
- `probe.py` (the reference probe CLI) is Mac-portable by construction:
  stdlib plus `hid` only, no Windows-only imports, no hard-coded paths or
  COM ports. Its write subcommand requires an explicit confirmation flag
  and exactly 64 payload bytes, prepends the report-ID byte, and never
  pads or synthesises payloads. Its read loop reads 65-byte buffers with
  timestamped hex dumps.

## 4. Message framing inside the report (CONFIRMED)

Every message sits at the head of the 64-byte report:

```
byte:  0      1     2      3 ...
field: [size] [id]  [cmd]  [args...]
```

- `size` (byte 0) is the argument byte count: the number of bytes after
  the `[size][id][cmd]` header that belong to the message.
- `id` (byte 1) is the destination/source device id (see §5).
- `cmd` (byte 2) is the command byte (`0x12`, `0x92`, `0x95`, …).
- Only the leading `3 + size` bytes are protocol. **The tail is stale
  buffer residue, not protocol.** Evidence: in one capture the ping,
  `0x86`, `0x81`, and `0x82` messages all share the identical tail, and
  104 pings carry only 3 distinct tails. A decoder must ignore everything
  past the meaningful length, and a differ must compare only meaningful
  prefixes. (One nuance: the revision request is an exception in practice
  — bytes past its short prefix carry a ref the gun validates. See
  [pairing](03-pairing-registration.md) §4.3 and the conflict note in
  [capture index](12-capture-index-and-verification.md) §5. Do not
  generalise from that exception to other messages.)
- Maximum message length is 64 bytes (the Lua byte list is copied verbatim
  into a 64-byte buffer — INFERRED from the reported Ghidra finding). The
  longest observed messages are the `0x83` bytecode chunks, which fill the
  whole report (61 meaningful bytes: 58 data bytes plus header).

**Framing exception — command `0x12`.** For the pair acks, the first byte
is a discriminator, not a size: `0A` means a new pair request received,
`0B` means already received (re-ack). Both forms carry the same 14-byte
layout. See [pairing](03-pairing-registration.md) §3. The `0x12`
announcement from the gun does follow the size convention (`0x10` = 16
argument bytes = two 8-byte refs).

## 5. Addressing (CONFIRMED)

| Id byte | Meaning |
|---|---|
| `0x00` | The proxy itself. Used as the destination of the `0x13` pair start/stop and the `0x01` init poll, and as the source id of gun announcements. |
| `0x01` | The UbiConnect. Its revision reply carries id `01`; gun ids start at `02`. |
| `0x02`–`0xFE` | Guns, assigned lowest-free from 2 at registration (see [pairing](03-pairing-registration.md) §5). Session-assigned, not fixed physical identities — the same gun can get a different id in another session or announcement order. |
| `0xFF` | Broadcast. Ping (`0x05`), vest poll (`0xC1`), flash commands (`0x81`/`0x82`/`0x83`/`0x86`), and pair acks (`0x12`) are addressed to `0xFF`. Echoes of broadcast messages come back per-gun with the gun's id substituted for `0xFF` (see §6). |

## 6. The echo rule (CONFIRMED — the single most important trap)

**Every host message is echoed back.** After a host send, IN reports
carrying the same message bytes arrive — for broadcast (`0xFF`) messages,
one echo per gun with that gun's id substituted for `0xFF`. Consequences,
all proven by captures and by live-replay failures:

- **Never treat the first report after a send as the reply.** The first
  report is usually the echo. Await code must read until it finds a report
  matching the expected command (and id), skipping everything else.
- The countdown echo arrives in ~11 ms; the guns then count down locally
  in silence for 10 s. Early minimal-loop code mistook the echo for a
  "countdown complete" message (which does not exist) and either proceeded
  early or timed out. Correct behaviour: send the countdown, discard the
  echo, wait the countdown duration, then poll.
- A stale countdown echo still sitting in the queue was misread as a poll
  reply (`01 02` prefix) in another early run. Correct behaviour: drain
  before polling, then read until `cmd == 0x95` with the right id (see §7).
- Game-over messages are echoed back as IN reports with identical bytes
  (capture 19, frames 5753/5755 OUT, 5757/5759 IN).

Sources word the mechanism two ways — the session-2 prompt says host
messages "are echoed back by the UbiConnect as IN reports (OUT then IN,
same bytes)", while the minimal-loop diagnosis says "every host message is
echoed back by each gun (id substituted for FF)". The observable behaviour
(per-gun echoes with id substitution for broadcasts) is CONFIRMED; which
physical component generates the echo (UbiConnect relay vs gun RF reply)
is UNVERIFIED and does not matter to an implementer: handle echoes by
matching, never by position in the queue.

Related: **guns never send unsolicited status.** The `0x95` status report
arrives only in reply to a poll. Passive listening yields nothing; the
host must poll actively. (The exception is the `0x12` announcement, which
a gun repeats spontaneously until acknowledged — see
[pairing](03-pairing-registration.md) §2. Announcements are the only
spontaneous gun traffic in the captures.)

## 7. Read discipline and drain behaviour (CONFIRMED, with an open design question)

The HID input queue buffers reports, including stale repeats. The replay
driver that passed on both hosts used this discipline:

- **Open once.** One shared HID handle for the whole session (the replay
  driver is an open-once write/read driver). Do not open/close per message.
- **Drain while awaiting.** While waiting for an expected reply, consume
  and discard queued reports that do not match, logging the skipped
  reports. The driver gained an explicit drain step (`drain_ms`) before
  the revision request: discard queued reports, re-acknowledging `0x12`
  repeats as they are discarded (see below).
- **Re-ack stale `0x12` repeats.** A gun's repeated announcement
  (`10 00 12 …`) can arrive stale, in the same millisecond as a later
  request — capture 02 frame 166 shows the original game itself re-acking
  with the `0B` form at frame 169. The driver reproduces this: on a
  repeated `10 00 12` while awaiting a reply, re-send the last `0B` ack,
  at most 2 per wait. Observed drain counts: 6 stale `10 00 12` repeats
  discarded in the passing Windows pair run and 6 in the passing Mac pair
  run; 7 in one failed run. The drain is necessary hygiene, but it was
  **not** the fix for the stale-ref failure: a control run with
  drain-style reading, 2 re-acks, and a 2 s wait still got no revision
  reply with a stale ref, while the live ref was answered at once. Do not
  credit (or blame) the drain for ref-validation behaviour.
- **Drain before polling after a phase change.** After sending the
  countdown, drain first, then read until the expected `0x95` reply.
  After the countdown, the first poll goes out ~10.1–10.4 s after the
  countdown send in the original game's captures.
- **Retry unanswered polls next cycle.** A poll can go unanswered without
  anything being wrong: capture 16's first poll to gun `03` (frame 251)
  went unanswered, and the passing 118.5 s loop had 4 unanswered polls out
  of 1,254 (99.7% reply rate). Never treat one missed reply as a
  disconnect; never invent mock status to fill the gap.
- **Baseline, don't assume zero.** The first `0x95` report of a game can
  already carry nonzero counters and leftover hit-id slots from a previous
  game. Snapshot the first report as the baseline and decode deltas.

**Open design question (UNVERIFIED — no reader-thread/lock design in the
evidence).** The allowed sources describe a single-threaded, synchronous
driver (send, then read-until-match) and say nothing about a background
reader thread, a mutex around the handle, or how concurrent poll/pair
activity should serialise access to the one shared queue. Any such design
is an implementation choice, not a reverse-engineered fact, and is flagged
for the planned open-questions doc. What the evidence does constrain: all
reads consume one ordered queue; any design that lets two consumers race
for reports, or that assumes the next report belongs to the last send,
will misattribute echoes and stale repeats. Note also that "lock" in the
Lua sources means the *proxy lock gate* (whether new guns may register),
not a thread lock — see [pairing](03-pairing-registration.md) §6. Do not
confuse the two.

## 8. Idle and startup behaviour (CONFIRMED)

- **Idle is silent.** Capture 00 (30 s, game closed): descriptor/config
  packets only, zero HID reports. The Mac probe's 10 s idle read was
  likewise silent. Expect no unsolicited reports from an idle UbiConnect.
- **Opening the device sends no interrupt report** — only file/descriptor
  queries (INFERRED from the reported init-sequence analysis). The first
  traffic is Lua-driven: the `0x01` init poll, then the init reply, the
  `0x12` init ack, the revision exchange, then steady-state ping/vest
  traffic. See [pairing](03-pairing-registration.md) §4.1 for the
  frame-verified startup sequence.
- **Init poll shape.** The host sends `01 00 01 FF` four times (~25 ms
  apart in capture 01), each answered in ~2 ms by an `0A 00 01 FF …`
  reply. The Lua resends the poll every tick until the device answers
  (INFERRED — Lua resend behaviour, consistent with the four observed
  sends).

## 9. Measured cadences (CONFIRMED — captures win over nominal values)

| Traffic | Measured cadence | Evidence |
|---|---|---|
| `0x95` status poll, in-game | ~0.311 s per gun | Capture 13 (id-`02` gaps 0.310–0.313 s); capture 17 same |
| `0x05` broadcast ping, idle | ~6 s (gaps 5.99–6.24 s) | Capture 13, frames 7–55 |
| `0x05` ping, in-game | Once per poll cycle (~0.311 s) | Capture 13, frames 313+ |
| `0xC1` vest poll, setup | ~0.25 s (gaps 0.23–0.28 s) | Capture 17, 191 polls |
| `0xC1` vest poll, in-game | Rare (6 polls in capture 13) | Capture 13 |
| `0x95` reply latency | 14–29 ms | Live minimal-loop logs |
| Countdown echo latency | ~11 ms | Captures 04/16 analysis |
| Gun announcement repeat | ~150 ms until acknowledged | Captures 02/03/45; live logs (25+ s of repeats in one run where the gun was never acked) |

Notes and conflicts:

- The Lua RoundLoop nominally cycles faster than the measured poll
  cadence (a 100 ms figure appears in the pairing analysis for the
  RoundLoop update, and a 30,000–100,000 µs cycle setting exists in the
  test settings). The wire measurement (~0.311 s per gun) is what an
  implementer should reproduce; the nominal Lua cycle is INFERRED and
  does not match the wire. Do not "correct" the measured cadence toward
  the Lua figure.
- Efeion's Java figures (2 s ping delay, 100 ms in-game status cadence)
  are contradicted by the captures. Do not use them.
- Tolerance observation (not a recommendation): one passing live loop
  polled at ~189 ms/gun instead of ~311 ms and the guns handled it fine.
  Faster polling is tolerated; it is not the original behaviour.
- **Upload chunk pacing is UNVERIFIED.** No chunk-to-chunk gap was
  measured from the captures in the allowed sources. The Java project's
  500 ms inter-chunk figure is source-only; the tkdrob mod's
  player-count-scaled interval is MOD behaviour, not original. The
  original Lua re-sends config and upload steps on a 250 ms cadence
  (see [pairing](03-pairing-registration.md) §8.3 for the config loop) —
  but that is a resend cadence for unacknowledged steps, not a measured
  chunk gap. Flagged for the open-questions doc.

## 10. Failure modes observed (CONFIRMED unless noted)

- **Unanswered polls are normal.** See §7. Retry next cycle; do not
  disconnect on one miss.
- **Stale `0x12` repeats queue up.** See §7. Drain + re-ack; at most 2
  re-acks per wait in the proven driver.
- **Occasional HID read error mid-game.** One live run reported an
  intermittent HID "read error" (possible connection drop). The exact
  exception and game time were never captured in the allowed sources —
  UNVERIFIED as a diagnosable failure mode. A replacement should log the
  exact exception and game time if it recurs.
- **A gun that never announces is a gun problem, not a code problem.**
  Five consecutive live runs in which one gun's ref never appeared (and
  one run in which nothing announced at all) were traced to the guns not
  being in pairing mode when the code looked — not to a timeout, the
  drain, or a code race. A refuted "≈31 s gun-side pairing window" theory
  came out of this episode: sequential pairing takes ~4 s total and the
  30 s figure was the code's wait timeout, not gun behaviour. If a gun
  stops announcing, re-running the original Battle Tag game once resets
  it (observed 2026-11-11: both guns announced afterwards). Test a silent
  gun alone before blaming the host code.
- **Efeion's Win64 pairing failure** (write error fixed by the `0x00`
  prefix, pair-accept flag still never set) is historical context, not a
  live failure mode of the proven driver. It is recorded here because any
  new hidapi implementation that forgets the report-ID byte will
  reproduce its first half.

## Sources

Grounded in: `BATTLE_TAG_MASTER.md` §§1.4, 3 (items 30, 34–38, 42), 4.1,
4.5–4.6, 4.9; `REVIEW_2026-11-11.md` §§a1, a10, b (rows 1–3, 24–25), c
(Java comparison), f; `SNIFFING_REVIEW_2026-11-10.md` §§2–3, 5;
`WINDOWS_SNIFFING_PROMPT_V2.md` (HID packing, init sequence, identity);
`sniffing-results/protocol_map.json` (`device`, `hid_descriptor`,
`usb_configuration`, `merge_notes`) and `PROTOCOL_MAP_MERGE.md`;
`sniffing-results/hid_descriptor.txt` (descriptor bytes and endpoints,
as cited by the protocol map).
