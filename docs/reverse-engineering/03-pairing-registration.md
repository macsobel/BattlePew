# 03 — Pairing and registration

How a gun joins, stays registered, leaves, and rejoins. The headline
result, proven from the original Lua plus re-parsed captures: **one player
quitting never forced everyone else to re-pair.** There is no quit/unpair
message; a gun leaves only by going silent; mid-match, a silent gun is
never removed at all; the only "everyone re-pairs" case is losing the
UbiConnect itself. Transport framing, the echo rule, and read discipline
are in [USB transport](02-usb-transport.md); method and baseline are in
[overview and method](01-overview-and-method.md); capture evidence is in
[capture index and verification](12-capture-index-and-verification.md).

Claim grades (CONFIRMED / INFERRED / UNVERIFIED) and byte numbering
(zero-based within the 64-byte report) follow
[overview](01-overview-and-method.md) §0. Lua behaviour is paraphrased
throughout, with file and line citations; no Lua source is quoted.

## 1. Two different things both called "pairing" (CONFIRMED)

Confusing these layers is the source of most wrong assumptions:

1. **RF bonding (gun ↔ UbiConnect).** The gun stores the paired proxy's
   reference and includes it in every connection announcement. The proxy
   rejects announcements whose paired ref differs from its own, logging
   that the request came from an unpaired device, and ignores them
   (`ULUsbProxy.lua:391`). This bond persists in the gun across sessions
   (INFERRED from announcement contents and re-pairing behaviour). How
   the gun stores the bond, and whether/how it can be cleared gun-side,
   is UNVERIFIED — no Lua writes it. This bond is what the AddNewDevice
   "pairing" flow establishes.
2. **Host registration (gun ↔ running app).** On every connection the
   proxy accepts a `0x12` announcement, assigns an id/classId, and runs a
   revision + profile exchange. This happens on every app run and after
   every timeout-driven removal. It is cheap and automatic, and it is
   what "re-pairing" means inside a session.

## 2. Device references (refs)

A ref is an **8-byte device identifier** (CONFIRMED — announcement and ack
layouts). No real ref is shown anywhere in these docs. Every ref field
below is rendered as eight `RR` placeholder bytes. Any 8-byte sequence
labelled EXAMPLE is invented and must never be sent to hardware.

| Fact | Grade / evidence |
|---|---|
| Gun announcements carry two refs: the gun's own ref, then the paired proxy (UbiConnect) ref. | CONFIRMED — captures 02/03/45; `ULUsbProxy.lua:286-287` (16-byte argument = two 8-byte refs). |
| One byte position inside the ref distinguishes device class: guns carry `02` there, the UbiConnect carries `01`. That byte is a device-class byte, **not** a player id. | CONFIRMED — pairing analysis; the classId bug diagnosis (see §3.3) turned on this distinction. |
| The gun class constant is documented in `ULUsbProxy.lua:595-601` and is used to index registered devices by class. | CONFIRMED (constant exists in Lua). The constant is shared by all guns; it is not a device ref. |
| A gun's ref is stable across hosts: the same physical gun presented the same ref on Windows and on Mac replay, with the same decoded profile. | CONFIRMED — live replay 2026-11-11 (ref value itself withheld here). |
| Refs are the only stable physical identity. Protocol ids and player numbers are session-assigned (see §5) and must never be treated as physical identity. | CONFIRMED. |

Announcement frame (gun → host, repeated ~every 150 ms until acknowledged):

| Byte(s) | Value | Meaning |
|---|---|---|
| 0 | `10` | Size: 16 argument bytes |
| 1 | `00` | Source id `00` |
| 2 | `12` | Connection request |
| 3–10 | `RR RR RR RR RR RR RR RR` | Gun ref (8 bytes, placeholder) |
| 11–18 | `RR RR RR RR RR RR RR RR` | Paired proxy ref (8 bytes, placeholder) |

The announcement repeats until acked. Guns announce spontaneously: in
captures 02 and 45, announcements appear *before* any `0x13` pair message
(see §3.1). In one live run a gun's announcement repeated every ~150 ms
for 25+ s because it was never acknowledged — there is no gun-side
announcement timeout in the evidence; the only bound is the host code's
wait timeout.

## 3. The join handshake

Every join follows the same wire sequence (captures 02/03/45/47):

| Step | Direction | Frame (meaningful bytes) | Meaning |
|---|---|---|---|
| 1 | Gun → host | `10 00 12` + gun ref + proxy ref | Connection request; repeats until acked |
| 2 | Host → all | `0A FF 12 <id> <classId> 01` + gun ref | New-device ack (first registration) |
| 3 | Host → all | `0B FF 12 <id> <classId> 01` + gun ref | Re-ack (repeated announcement hits the already-mapped branch) |
| 4 | Host → gun | `00 <id> 04` (+ ref tail — see §4.3) | Revision request, re-sent every tick until answered |
| 5 | Gun → host | `04 <id> 04 00 02 00 00` | Revision reply |
| 6 | Host → gun | `05 <id> 84 00 00 00 11 30` | Profile request (flash address `0x11`), re-sent every tick until answered |
| 7 | Gun → host | `<size> <id> 84 …` | Profile reply (name + icon) |

Lua: `ULUsbProxy.lua:277-396` (the `0x12` processor),
`ULUsbDevice.State.Boot.lua`, `ULUsbDevice.State.Revision.lua:50` (resend
loop `:106-112`), `ULUsbDevice.State.Profile.lua:44-45` (resend loop
`:71-77`), `ULUsbDevice.lua:142-276` (the `0x04`/`0x84` processors).

Capture proof: capture 45, gun C announces frames 127–141, `0A` ack at
frame 147, `0B` at frame 151; gun D announces frames 177–187, `0A` at
frame 199, `0B` at frame 201. Capture 02: `0A` frame 163, `0B` frames
165/169, revision frames 171/173, profile frames 175/177 decoding to a
name and icon (see §4.4).

### 3.1 The 0x13 pair start/stop is NOT part of ordinary joining (CONFIRMED)

| Message | Bytes |
|---|---|
| Pair start | `01 00 13 01` |
| Pair stop | `01 00 13 00` |

- The `0x13` messages are sent by exactly **one** place in all original
  Lua: the AddNewDevice pairing screen
  (`UTGame.Ui.Settings.AddNewDevice.Pairing`, lines 43, 60, 82). They are
  addressed to `0x00` (the proxy itself).
- **No Lua processor handles `0x13`.** Its firmware/C++ effect is
  UNVERIFIED.
- The lobby (PlayersManagement) **never** sends `0x13`; it only unlocks
  the proxy (§6) and relies on already-bonded guns announcing
  spontaneously.
- `0x13` is optional for re-registration. Proof: in capture 47, a gun
  re-registers at frames 37659–37669 (announcements 37631–37657, `0A`
  37659, `0B` 37661, revision 37663/65, profile 37667/69) with **no `0x13`
  anywhere nearby** — the last `0x13` in the whole capture is frame 389.
  The gun announced on its own and was accepted because the proxy was
  unlocked.
- Where `0x13` does appear (captures 02/03/45), the order is: start,
  stop, then `0A` ack, then `0B` ack(s). Capture 02: start frame 159,
  stop frame 161, `0A` frame 163, `0B` frames 165/169. Capture 03: same
  pattern at frames 151/155/157/159. A replacement that sends `0x13`
  before the acks matches the AddNewDevice flow; a replacement that
  omits it in the lobby matches the lobby flow. Either way, do not treat
  `0x13` as a precondition for accepting an announcement.

### 3.2 Ack A (0A) vs ack B (0B) (CONFIRMED layout; semantics INFERRED)

Both forms are 14 bytes with identical layout after the first byte:

| Byte(s) | Value | Meaning |
|---|---|---|
| 0 | `0A` or `0B` | Discriminator: `0A` = new pair request received; `0B` = already received (re-ack). Not a size byte — see [USB transport](02-usb-transport.md) §4. |
| 1 | `FF` | Broadcast |
| 2 | `12` | Pair ack |
| 3 | `<id>` | Assigned protocol id (from 2) |
| 4 | `<classId>` | Assigned classId = player number (from 1) |
| 5 | `01` | Proxy protocol id |
| 6–13 | `RR …` (8 bytes) | Gun ref (placeholder) |

Lua: the `0B` form is constructed in `ULUsbProxy.lua:312`, the `0A` form
in `ULUsbProxy.lua:386`. The protocol map records the semantics of the two
forms beyond the Lua construction as unclassified; the pairing analysis
attributes `0A` to first registration and `0B` to the already-mapped
branch (a repeated announcement for a device already in the id map).
Treat the branch attribution as INFERRED; the byte layouts and the
ordering (`0A` before `0B`, with `0B` repeated) are CONFIRMED.

On a stale repeated announcement while awaiting a later reply, the
original game re-sends the `0B` ack (capture 02 frame 169, after a repeat
at frame 166). The proven replay driver reproduces this with at most 2
re-acks per wait — see [USB transport](02-usb-transport.md) §7.

### 3.3 The classId trap: substitute BOTH id and classId (CONFIRMED)

`classId` **is** the player number, and `id = classId + 1`. The pairing
ack carries id at byte 3 **and** classId at byte 4, and both must be
substituted per gun. A minimal-loop build copied gun 1's ack template and
substituted only the id, so both guns received classId `01` and both
displays showed "Player 1". The fix — substituting classId in acks A/B
and in the revision request — was verified live: gun id `02`/classId `01`
acked as `0A FF 12 02 01 01 …`, gun id `03`/classId `02` as
`0A FF 12 03 02 01 …`, and the displays showed 1 and 2.

Capture 45 is the independent proof: gun C acked `0A FF 12 04 03 01 …`
(id 04, classId 03), gun D acked `0A FF 12 05 04 01 …` (id 05,
classId 04). Lua: classIds are assigned sequentially from 1
(`ULUsbProxy.lua:443-445`); the player display name combines profile and
classId (`UTActivity.lua:168`); the winner callout is computed from
classId (`FinalRankings.lua:89`).

Refuted in the same diagnosis: the `02` byte inside gun refs is a
device-class byte, not a player id (§2); the config message has no player
field (its bytes 3/4 are volume/beam-power). The display showing game
state rather than a number after the countdown is normal behaviour.

## 4. Revision and profile

### 4.1 Startup (UbiConnect) sequence (CONFIRMED, frame-verified)

Before any gun pairs, the host runs the same machinery against the
UbiConnect itself (capture 01):

| Frame(s) | Direction | Bytes | Meaning |
|---|---|---|---|
| 5/9/13/17 | Host → device | `01 00 01 FF` | Init poll, ~25 ms apart |
| 7/11/15/19 | Device → host | `0A 00 01 FF …` | Init reply, ~2 ms after each poll |
| 21 | Host → device | `0A 00 12 01 01` + proxy ref | `0x12` init ack (13 bytes; byte-exact Lua construction, `ULUsbProxy.lua:263`) |
| 23 | Host → device | `00 01 04` | Revision request to the UbiConnect (id `01`) |
| 25 | Device → host | `04 01 04 00 01 00 00` | Revision reply |
| 27–37 | Host → device | Alternating `01 FF 86 00` / `00 FF 06` | Flash-manager / connect traffic (see §4.5) |

### 4.2 Revision exchange (CONFIRMED bytes; meaning UNVERIFIED)

- Request: `00 <id> 04`, re-sent every tick until answered (Lua Revision
  state resend loop). See §4.3 for the ref tail the live replay proved
  necessary.
- Reply: `04 <id> 04 00 <02|01> 00 00`. Guns reply with the `02` variant
  (capture 02 frame 173; capture 45 frames 155/205); the UbiConnect
  replies with the `01` variant (capture 01 frame 25). The Lua revision
  processor decodes a big-endian 32-bit revision (guns `0x00020000`,
  UbiConnect `0x00010000`, per `ULUsbDevice.lua`). **What the numbers
  denote (firmware version or something else) is UNVERIFIED.**
- The Efeion Java project's comment that the revision request received no
  response is CONTRADICTED by the captures. The likely cause is Java's
  double-read bug discarding the reply (see
  [overview](01-overview-and-method.md) §5). Do not copy that behaviour.
- A related inbound message, `0x03` (device reference message, stored by
  the Lua `0x03` processor), is Lua-only: no capture frame isolates it.
  INFERRED at best; do not implement behaviour around it.

### 4.3 Revision-request ref validation — read this before implementing (CONFIRMED, with a source conflict)

The protocol map and the evidence review record the revision request by
its 3-byte prefix, `00 <id> 04`. Live replay proved that is not the whole
story:

- Two live pair runs failed at the revision step (6/7) although the acks
  had been accepted (the gun said "OK" audibly both times). The revision
  request in those runs carried a **stale capture ref** in bytes 6–13 —
  residue from the ack in the game's shared buffer.
- A third run, identical except that the request carried the **live**
  ref (taken from the gun's own announcement) in bytes 6–13, was answered
  at once. A control run (drain-style reading + 2 re-acks + 2 s wait,
  stale ref) still got no reply, isolating the live ref — not the drain —
  as the decisive variable. (One caveat, with no supporting evidence: in
  the failed run the re-acks came after the revision request, in the
  passing run before it; a state machine in which a post-request ack
  suppresses the reply is possible in principle but unevidenced.)
- **Implementer rule: the revision request must carry the live gun ref at
  bytes 6–13.** The proven template is the 14-byte form
  `00 <id> 04 <id> <classId> 01` + gun ref, with id, classId, and ref all
  substituted per gun (see §3.3). Whether bytes 3–5 (id/classId/`01`)
  are themselves validated, or only bytes 6–13, is UNVERIFIED — the
  evidence establishes ref validation, not the validation of the
  intervening bytes. Flagged for the open-questions doc.

This is recorded as a source conflict in
[capture index](12-capture-index-and-verification.md) §5: the map's
truncated prefix is correct as far as it goes, but an implementer who
sends only 3 meaningful bytes with a zeroed or stale tail will reproduce
the live failure. Trust the live replay for the tail.

The pair driver also substituted the live ref into the profile request
(see §4.4), but the allowed sources do not specify the tail positions
involved there. That detail is UNVERIFIED; do not guess byte positions
for the profile-request tail.

### 4.4 Profile request and reply (CONFIRMED)

- Request: `05 <id> 84 00 00 00 11 30` — size `05`, gun id, command
  `0x84`, flash address `0x00000011` big-endian, size `0x30` (48).
  Re-sent every tick until answered (Lua Profile state resend loop).
  Observed at capture 02 frame 175 and capture 45 frames 157/207.
  Lua: `Profile.lua:45`; reply decoding in `ULUsbDevice.lua:229-255`.
- Reply: `<size> <id> 84 …`, variable size depending on the profile
  contents. It decodes to a player profile: a length-prefixed name,
  then a length-prefixed icon filename. Decoded examples from captures:
  a profile reply decoding to a name with icon `dog.tga` (capture 02
  frame 177, same profile again in capture 03), one decoding to a name
  with icon `eagle.tga`, and one with icon `cat.tga` (capture 45 frames
  159/209). Live replay decoded a profile to name and icon `snake.tga`
  on both Windows and Mac. The Java parser's name/icon decoding is exact;
  Java's hardcoded icon (`Shark.tga`) is wrong — real icons differ per
  profile.
- One decoding nuance (INFERRED): the profile-block description in the
  protocol map says the icon length counts the icon *stem* (without the
  `.tga` suffix) while the bytes that follow carry the full filename
  including `.tga`. That description comes from the Lua-only write path
  (§4.5), so treat the stem-length reading of the *reply* as INFERRED
  until a reply is decoded byte-by-byte against it. The name-length /
  name / icon-length / icon structure itself is CONFIRMED.

### 4.5 Profile writing (INFERRED — Lua only, never observed on the wire)

The profile the `0x84` read returns is written by a flash sequence
described in the profile-editor Lua (`UTGame.Ui.Associate`): confirming an
edited profile flashes it immediately (not at mode-upload time). In
outline, paraphrased: unlock the gun's flash, erase the page at address
`0x00000000`, send a write header for that address with the block length,
send the block in a single `0x83` chunk, then lock — unicast to the gun
id, each message re-sent every 250 ms until a reply carrying the same
command arrives. The block is a 17-byte table-of-contents header
(identifying marker, profile number, revision, data marker, offset and
length) followed by the record at offset `0x11` (name length + name +
icon-stem length + full icon filename), which is why the `0x84` read
requests address `0x11`. The protocol map notes 12 writable icons.

Grade: INFERRED throughout. No capture covers a profile edit, and all
captured `0x82` frames target the bytecode address `0x4000`, not address
0. Do not present the write sequence as capture-verified, and do not
reproduce the map's worked example bytes as if they were observed. The
known correction stands: **profile writes use address `0x00000000`**,
while bytecode uploads use `0x4000` — do not conflate the two.

### 4.6 Startup 0x86/0x06 traffic (INFERRED)

After the revision exchange, capture 01 shows alternating
`01 FF 86 00` / `00 FF 06` (frames 27–37). The `0x86` form is a
flash-manager command (see the protocol map); `0x06` (`00 FF 06`) is
Lua-only beyond its send site in the connected-state handling, and its
purpose is UNVERIFIED. The Java project's reading of `0x06` as a
reset/disconnect signal is source-only with no observed gun-disconnect
effect. Do not implement behaviour around `0x06`.

## 5. Id vs classId assignment (CONFIRMED)

- For a genuinely new registration, the proxy assigns the **lowest free**
  protocol id from 2 to 254 (`ULUsbProxy.lua:363-376`) and the lowest free
  classId from 1 within the gun class (`ULUsbProxy.lua:443-448`).
- **There is no ref→player memory in the original.** The same physical gun
  can get a different number depending on announcement order.
- Capture proof: capture 46 assigns two later guns to id/classId `04/03`
  and `05/04`; capture 47 assigns the **reverse** (the second gun paired
  first, taking `04/03` at frames 347/349, the first taking `05/04` at
  frames 391/393), confirmed by the `0x92` team words and marked shots.
  Capture 47's late re-registration reused id `04` only because that slot
  happened to be free.
- A stable ref→player mapping (a known ref always reclaiming its number)
  is a *replacement improvement*, not original behaviour. It is recorded
  here only so an implementer does not mistake it for something the
  original did: the original had no such memory, and ids were
  re-assigned in announcement order after every UbiConnect loss or app
  restart (§8).

## 6. Lock and whitelist (CONFIRMED by call-site enumeration)

The proxy gate decides whether a *new* device may register. ("Lock" here
is the Lua proxy gate, not a thread lock.)

| Fact | Evidence |
|---|---|
| The proxy starts **locked**: it does not accept incoming connection requests. | `ULUsbProxy.lua:43-48` |
| A new device is accepted only if the proxy is unlocked **or** the device is whitelisted. | `ULUsbProxy.lua:299` |
| The only `Unlock()` callers in the whole original tree are PlayersManagement `Begin`, AddNewDevice Pairing, and the Introduction UI. | Grep-verified enumeration |
| `Lock()` is called only by PlayersManagement `End`, the PlayersManagement UI firmware-required path, and AddNewDevice Pairing. | Grep-verified enumeration |
| The whitelist is populated **only** by AddNewDevice Pairing (single entry) and the flash-memory manager's Stage 7 reboot (before sending the `00 FF 06` reboot message). | Grep of whitelist callers |
| The whitelist is cleared by the Revision state's `End` and by AddNewDevice close. | Grep-verified |
| **No whitelist population happens during normal gameplay.** The whitelisted-rejoin path never applies to ordinary joins/rejoins. | Consequence of the above |

**Consequence — lobby-only joining (CONFIRMED).** After leaving the
lobby, the proxy stays locked through config → upload → matchmaking →
round, until the next lobby visit. A brand-new (never-registered) gun
cannot join mid-activity. Only already-registered devices get re-acks:
the already-mapped branch of the `0x12` processor does not check the lock
(`ULUsbProxy.lua:306-336`).

**Lobby reuse — no re-pair between matches (CONFIRMED).** PlayersManagement
`Begin` resets the activity's players/teams/matches but re-creates players
from already-registered devices in the proxy's per-class device table
(`UTActivity.State.PlayersManagement.lua:108-138`), restoring teams from
a saved team backup (`UTActivity.lua:214-229`). Registered guns keep the
same device objects, ids, and classIds across matches within one app run.

**Whitelist-restore latent bug (INFERRED — static analysis, unreachable
in practice, no runtime proof).** The whitelist store keeps the device
reference as a *string* (the reference's formatted form), while the
restore branch reads radio-protocol fields off the stored value as if it
were a device object — yielding nothing, which would then trip the
registration routine's requirement that the protocol id be a number.
Likely unreachable: AddNewDevice clears its entry on close, and
firmware-reboot rejoins normally hit the already-mapped branch (the
device is still registered). **Do not present id restoration via the
whitelist as working behaviour, and do not rely on it.** The correct
stable-identity mechanism in a replacement is a ref→number map (§5), not
this branch.

Two dead ends, grep-verified: **no `GameSettings` / `reconnect` settings
exist in the original 1.1.354 Lua** — a whole-tree grep found only two
comments mentioning "reconnect". Any "reconnect 1 or 3" behaviour is
tkdrob-only (see §10). And the locale string warning that unknown
T-Blasters are trying to connect has no Lua caller — dead text.

## 7. The Pinger: silence → ping → unregister (CONFIRMED)

The Pinger (`ULUsbDevice.Pinger.lua`) is the **only** removal mechanism.
It tracks per-device silence and, outside protected mode, pings and then
unregisters silent devices.

- **Any** message from a gun resets its silence timer (the device message
  processor zeroes the timeout on every message, `ULUsbDevice.lua:126`).
- At or past the high threshold, the Pinger unregisters that device as
  timed out and returns — only one device per update
  (`ULUsbDevice.Pinger.lua:105-113`).
- At or past the low threshold, it broadcasts the ping `00 FF 05`,
  rate-limited (see below).
- The Pinger starts suspended and resumes on first gun registration
  (`ULUsbProxy.lua:455-467`).

### 7.1 Timings and modes (CONFIRMED — all reset call sites grep-verified)

| Phase | Ping after (lo) / unregister after (hi) / ping interval | Protected mode? |
|---|---|---|
| Default (idle, Introduction) | 5 s / 10 s / 1 s | No |
| PlayersManagement, PlayersSetup, Bytecode | 2 s / 4 s / 0.5 s | No |
| Generic RoundLoop | 2 s / 10 s / 0.5 s | **Yes — Pinger does nothing** |
| OldFashionDuel RoundLoop | 2 s / 8 s / 0.5 s, then 2 s / 10 s / 0.5 s | **Yes, after the second reset** |

The underlying Lua values are in microseconds (5,000,000 / 10,000,000 /
1,000,000 for the defaults); the seconds above are the same values
converted. The ping rule as stated in the Pinger sources: at most one
ping per second (per the interval) and only after at least 5 s of silence
under default timings. Measured wire cadence differs by phase — ~6 s
while idle, once per poll cycle in-game — because the RoundLoop sends its
own per-cycle ping while the Pinger is off (see §7.3). Both statements
are true; do not force them into one number.

### 7.2 Protected mode (CONFIRMED mechanism; one INFERRED consequence)

In protected mode the Pinger returns immediately and does *nothing at
all*: no timer accumulation, no ping, no unregister
(`ULUsbDevice.Pinger.lua:86`). Since the Pinger is the only caller of the
unregister routine, **no timeout removal can occur during a protected
round**. The further consequence — that the round-removal handlers are
effectively unreachable in the generic case — is INFERRED: no capture
shows a mid-match timeout, and the handlers could still fire if a removal
somehow occurred (e.g. after the round end restores default timings with
freshly zeroed timers).

### 7.3 Ping frame (CONFIRMED)

Broadcast ping: `00 FF 05` (3 meaningful bytes; an observed 4th byte is
stale tail). Per-gun reply: `00 <id> 05 01`. The official ping differs
from Efeion's Java ping (`01 FF 05 00`) in the size byte — use the
official form. The generic RoundLoop's update sends one `00 FF 05` ping
per poll cycle with a code comment explaining that the ping keeps
inactive guns connected precisely because the Pinger no longer runs
during the round (paraphrased from `UTActivity.State.RoundLoop.lua:164`).
The RoundLoop also polls `0x95` round-robin (nominally every 100 ms per
player in the Lua; measured wire cadence ~0.311 s per gun — captures win;
see [USB transport](02-usb-transport.md) §9).

## 8. How a gun leaves — silence is the only signal (CONFIRMED)

### 8.1 There is no quit/unpair message (CONFIRMED by exhaustive grep)

- The **only** caller of the unregister routine in the entire original
  Lua tree is the Pinger (§7).
- There is no explicit unpair/kick/remove-device wire message, no
  gun-side "quit" message, and no host-side "drop player" command.
- The unregister routine (`ULUsbProxy.lua:512-556`) removes **only that
  one device** from the class and id tables, invokes the device-removed
  event, and deletes the device object. Other devices are untouched.

A player "quitting" therefore means exactly one of: powering the gun off,
walking out of RF range, or the battery dying. Power-off vs out-of-range
vs dead battery are **indistinguishable on the wire** — all are silence
(INFERRED; no Lua distinguishes causes). A replacement UI must say
"silent" / "not responding", never "powered off".

### 8.2 What removal does globally (CONFIRMED)

Event ordering: delegates run in ascending priority order
(`UTEvent.lua:66,74,86-95`); the activity subscribes the global
device-removed handler at priority 10 (`UTActivity.lua:134`), so
state-specific handlers (priority 1) run **before** it.

The global handler (`UTActivity.lua:186-210`): if the device has an owner
player, it unbinds the device from that player and marks the player's
heap data as disconnected. **The player entity remains in the activity's
player and match lists** — only the binding is cleared. (A debug-only
block replaces the name with the "disconnected" locale string.) The bind
routine (`UTPlayer.lua:70-90`) also *steals*: binding a device that
already has a player owner unbinds that player first.

### 8.3 Per-phase behaviour when one gun leaves

| Phase | Behaviour when one gun goes silent | Grade |
|---|---|---|
| **Lobby (PlayersManagement)** | The state handler deletes that player: removes the player entity from its team and the player list, invokes the player-removed event, unbinds, deletes. Pinger is fast here (2 s/4 s), so a powered-off gun disappears from the roster within ~4 s. **Other players keep their devices, ids, and teams.** The accept button re-checks minimum player count / non-empty teams. Manual removal exists but **only for players with no gun bound** (slot menu "Delete", gated on having no bound gun device) — covering guest/created players and gun-less slots. **A player with a bound gun cannot be manually removed**; the host's only recourse is to power the gun off and wait ~4 s for the timeout. | CONFIRMED (`UTActivity.State.PlayersManagement.lua:374-390`, `:267-308`; `Ui/UIPlayerSlot.lua:174-182`) |
| **Config (PlayersSetup)** | Disconnect is **silently ignored**. The state has no removal handler of its own, and the UI removal handlers in all three variants are entirely commented out (with a note saying to stop carrying about disconnection here) — the handler is still registered but the body is dead code. The disabled code would have shown a "T-Blaster is disconnected" failure popup and gone to title. Actual behaviour: the `0x92` config loop (re-sent every 250 ms) **skips unbound guns**, so config completes short a player; the global handler unbinds the departed gun and nothing else happens. Exception: single-category games (shared guns) with **no gun at all** show a "No T-Blaster is available" failure and go to title. | CONFIRMED (`UTActivity.State.PlayersSetup.lua:213-272`, `:89-111`; commented handlers in the three PlayersSetup UI files) |
| **Upload (Bytecode)** | The handler shows an "Update failed" popup (update-failure strings reused for disconnect) whose button goes to activity end + game title, and sets a stop-sending flag. Quirks (code-derived): the flag is **never read** by the update loop, so sends continue every 250 ms until the user clicks the popup button; the all-acknowledged check **skips unbound guns**, so the remaining guns can still complete the upload and advance while the popup is up — a race (INFERRED, no runtime proof). Pinger is fast (2 s/4 s), so a silent gun trips this within ~4 s. Net effect: the others' upload can complete, but the UI flow heads to title once acknowledged. **The other guns do not need to re-pair** — they stay registered and the lobby reuses them (§6). | CONFIRMED handler; race INFERRED (`UTActivity.State.Bytecode.lua:183-223`, `:312-326`, `:251-260`) |
| **Mid-match (RoundLoop)** | **Nothing happens; the match continues.** Protected mode (§7.2) means the Pinger cannot unregister anyone; a powered-off gun stays bound with frozen status and unanswered polls. The removal handler counts remaining bound devices and acts **only if** the match can be stopped and **≤1 device remains**, showing a "Only one device is connected!" stop popup with a button to end the match, then disabling itself. If ≥2 remain, it does nothing. Mode overrides: Tag'n Shoot ends the match if the *current* player's gun is removed; OldFashionDuel ends if *either* of the 2 players' guns is removed; FreeFrag, TeamFrag, FreeForAll, LastManStanding, and StarterFrag inherit the generic behaviour. Introduction (standalone, default Pinger timings, no protected mode) restarts from the beginning on any removal. | CONFIRMED mechanism (Lua only — **no capture** shows a mid-match timeout/removal or rejoin; mark any retelling accordingly) |
| **Matchmaking** | Matches are built once from the activity's players with no filtering of unbound players — disconnected players stay in the match player list. Developer comments in the matchmaking state ask what happens when guns disconnect and whether incompatible/corrupted matches are discarded; the questions are unresolved in the code. | CONFIRMED (`UTActivity.State.Matchmaking.lua:36-37`, `:73-187`) |

Other removal-adjacent paths (CONFIRMED): removal during the Revision /
firmware flow fails or suspends the flash task (firmware update is out of
scope for any replacement — brick risk — but the pattern matters: removal
fails the flash, not the other guns); the profile editor polls each
update and shows a dismiss-only "Device Disconnected" failure if the
player's device binding disappears.

## 9. Rejoining (CONFIRMED + INFERRED)

### 9.1 Same gun, still registered — seamless re-ack, same number (CONFIRMED)

If the gun re-announces while its device object still exists in the id
map, the already-mapped branch re-acks with `0B FF 12` carrying the
**same id/classId**. No re-registration, no revision/profile re-run. This
is the common brief-dropout case.

### 9.2 Same gun, after unregister — new registration, number NOT guaranteed (CONFIRMED)

After unregister, the gun is a stranger. If it re-announces while the
proxy is **unlocked** (lobby), it gets a fresh registration with the
lowest free id/classId (§5) — which may or may not be its old number,
depending on who else took slots meanwhile. If the proxy is **locked**
(config/upload/match), its `0x12` is ignored entirely — **it cannot
rejoin the same match, period**. Capture 47 is the live proof: the late
re-registration got its old id again only because that slot was free,
and it happened in an unlocked phase with no `0x13` involved (§3.1).

### 9.3 A different gun taking the departed player's place (CONFIRMED)

- **Same match:** impossible. The proxy is locked, and the only path
  binding a new device to a player entity runs in PlayersManagement. A
  new gun cannot inherit the departed player's team/score binding
  mid-match.
- **Next match:** the new gun registers in the lobby as a new player
  (lowest-free id/classId) and is a fresh player entity. It does not
  inherit the old player's profile, team, or score. Team assignment is
  smallest-team via the team lookup in the create-player routine
  (`UTActivity.State.PlayersManagement.lua:189-263`), capped at the
  8-player maximum.

### 9.4 Revision/profile state on rejoin (partially UNVERIFIED)

The whitelisted-restart path clears the revision fields but does **not**
clear the profile-checked flag (`ULUsbProxy.lua:318-331`). Whether a
rejoined gun's profile check persists across a restart is UNVERIFIED —
and moot in gameplay, since the whitelist is empty there (§6).

## 10. UbiConnect loss: the only "everyone re-pairs" case (CONFIRMED)

Unplug of the active proxy handle deletes the proxy object and clears the
proxy/connected state (`ULUsb.lua:165-178`); replug creates a **fresh**
proxy with empty device tables (`ULUsb.State.Disconnected.lua:44`). The
proxy destructor sends the shutdown message `00 00 02` if initialised
(`ULUsbProxy.lua:93-104`) — Lua-only, no capture of a clean shutdown
exists, so INFERRED as wire behaviour. **Every** gun must then
re-announce and re-register, with ids assigned in announcement order
(lowest-free) — players may get different numbers than before. App
restart has the same effect. This is the only case where one event forces
everyone to re-pair, and it is caused by UbiConnect loss, never by one
gun quitting.

## 11. Vest disconnect is NOT gun removal

A stalled `gunseconds` clock in the `0x95` status stream indicates vest
loss and is handled by disabling the trigger (via the downlink gameplay
data) — it does not unregister the gun and must not be conflated with
removal. The full vest-disconnect settings (a three-way disconnect
setting, a prevent-launch gate, manual leaderboard toggle, 5 s stall
rule) as described in the master document's connection-state-machine
section come from the tkdrob-era sources; the boundary between original
and mod behaviour for vest handling is not fully separated in the allowed
sources, so the settings-level detail is INFERRED and flagged for the
open-questions doc. What is CONFIRMED here is the separation itself:
vest loss ≠ gun removal, and only the Pinger removes guns (§7–§8).

## 12. tkdrob contrast (MOD — never present as original behaviour)

The tkdrob mod changes the pairing model substantially. Recorded only so
an implementer does not import it by mistake:

- Adds settings for reconnect, unregister, and protected mode, and
  changes the `0x12` acceptance gate to admit whitelisted devices
  mid-game when reconnect allows it; one reconnect value is consumed
  after a single use.
- The Pinger gains a timed-out flag, a disconnect sound, and an **80 s**
  threshold while in-game, with unregister gated by settings. **The 80 s
  figure is MOD behaviour.** The original Pinger never unregisters
  mid-match at all (protected mode, §7.2) and uses 10 s / 4 s thresholds
  outside rounds (§7.1). Do not present 80 s as original.
- Keeps the same whitelist storage but comments out the whitelist-restore
  and whitelisted dispatch/restart branches; every new registration is
  whitelisted and always gets the lowest-free id; rejoin sets
  game-data-block/rejoin flags and resets the timeout.
- Its upload-timeout popup goes to the player-management screen rather
  than the title screen, and its RoundLoop has reconnect handling.
- It changes upload timing to scale with player count and raises the USB
  update frame rate. **Do not copy the timing change** — the original
  uses the fixed 250 ms resend cadence (§8.3).

A replacement should follow the **original** model in this doc, with
deliberate, labelled improvements (stable ref→number mapping, visible
silence state) — not tkdrob's reconnect machinery.

## Sources

Grounded in: `PAIRING_UNPAIRING_REVIEW.md` (primary — Lua file:line
citations and re-parsed captures 02/03/45/46/47 throughout);
`BATTLE_TAG_MASTER.md` §§3 (items 30, 35–37, 40–42), 4.3–4.4, 4.6;
`REVIEW_2026-11-11.md` §§a1, a10, b (rows 1–9, 24), c (Java comparison);
`SNIFFING_REVIEW_2026-11-10.md` §5 (pairing bytes present in 02/03);
`sniffing-results/protocol_map.json` (INIT_HANDSHAKE, PAIR_REQUEST,
PAIR_ACCEPT, `0x03`/`0x04` revision, `0x05` ping, `0x84` profile request,
`0x83` profile write, `0x02` shutdown) and `PROTOCOL_MAP_MERGE.md`.
Where the master document's tkdrob-sourced connection-state-machine
section (§4.4) and the pairing review disagree (reconnect settings, 80 s
unregister), the pairing review's grep-verified original-tree enumeration
wins — see [capture index](12-capture-index-and-verification.md) §5.
