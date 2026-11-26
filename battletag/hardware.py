"""Hardware & Simulator transport layer for UbiConnect (0x044F:0xB365).

RealHIDTransport uses the exact proven packet framing, sequence runner, pairing
announcement collection, bytecode upload, countdown echo check, and poll loop
from freefrag-minimal/freefrag_single_gun.py.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
import pathlib
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    import hid  # type: ignore
except ImportError:
    hid = None

from battletag.protocol import (
    AVATAR_NAMES,
    DecodedStatus,
    REPORT_LEN,
    build_config_packet,
    build_countdown_packet,
    build_gameover_packet,
    build_gameover_clear_packet,
    build_ping_packet,
    build_poll_packet,
    build_profile_block,
    build_vest_poll_packet,
    decode_0x84_profile,
    decode_0x95_status,
    pad64,
)

VID = 0x044F
PID = 0xB365

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "battletag" / "data"
MINIMAL_DIR = ROOT / "battle-tag-antigravity-handoff" / "freefrag-minimal"
MINIMAL_GUN_REFS = MINIMAL_DIR / "gun_refs.json"
def _default_logs_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return pathlib.Path.home() / "Library" / "Logs" / "BattlePew"
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
            return pathlib.Path(base) / "BattlePew" / "logs"
        xdg = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
        return pathlib.Path(xdg) / "BattlePew" / "logs"
    return ROOT / "logs"


LOGS_DIR = _default_logs_dir()


def load_mode_sequences() -> dict[str, Any]:
    path = DATA_DIR / "mode_sequences.json"
    return json.loads(path.read_text())


def detect_ubiconnect_readonly() -> dict[str, Any]:
    """Read-only USB enumeration check (never opens or writes to the device)."""
    if hid is None:
        return {"connected": False, "reason": "hidapi not installed"}
    try:
        matches = hid.enumerate(VID, PID)
    except Exception as exc:
        return {"connected": False, "reason": str(exc)}
    if not matches:
        return {"connected": False, "devices": []}
    return {
        "connected": True,
        "count": len(matches),
        "devices": [
            {
                "product": m.get("product_string") or "UbiConnect",
                "usage_page": f"0x{m.get('usage_page', 0):04X}",
                "usage": f"0x{m.get('usage', 0):04X}",
                "release": m.get("release_number", 0),
            }
            for m in matches
        ],
    }


@dataclass
class SimGunState:
    """In-memory simulated T-Blaster state for zero-hardware testing."""

    gun_num: int
    gun_id: int
    ref_hex: str
    name: str
    avatar: str
    # What the gun itself stores in flash (the 0x84 profile). Display edits
    # change name/avatar only; the stored profile changes only when a
    # profile write runs, mirroring the original's Associate editor.
    stored_name: str = ""
    stored_avatar: str = ""
    team_id: int = 1
    vest_connected: bool = True
    life_points: int = 5
    max_life: int = 5
    ammo: int = 9
    max_ammo: int = 9
    clips: int = 5
    max_clips: int = 5
    shots_fired: int = 0
    hits_received: int = 0
    phase_flag: int = 1
    hit_ids: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    last_rfid: str = ""
    led_color: str = "orange"
    # FreeForAll timer respawn: wall time the gun died (None while alive).
    dead_at: float | None = None
    # TeamFrag scan-then-timer respawn: wall time the wait ends (None
    # unless an own-base scan started a respawn wait).
    respawn_at: float | None = None
    # Set when the last bullet of a clip is fired with clips remaining:
    # the gun's next TIMER tick consumes a clip and refills the ammo.
    auto_reload_pending: bool = False
    # TagThenShoot course state: 0-based index of the next base to scan,
    # and whether the gun is in its shoot state awaiting a tower hit.
    tag_stage: int = 0
    awaiting_shot: bool = False


class SimulatorTransport:
    """Full fidelity in-memory simulator matching UbiConnect responses and gun VM behavior."""

    is_simulator: bool = True

    def __init__(self, num_guns: int = 2) -> None:
        self.guns: dict[int, SimGunState] = {}
        self.mode_name: str = "FreeFrag"
        self.settings: dict[str, Any] = {}
        self.upload_progress: float = 0.0
        # Cumulative per-gun UbiConnect tower hits (the sim's 0xC3
        # channel), mirroring RealHIDTransport.ubiconnect_hits. Cleared
        # by reset_for_mode every match.
        self.ubiconnect_hits: dict[int, int] = {}
        self.setup_number_of_guns(num_guns)

    def setup_number_of_guns(self, num_guns: int) -> None:
        defaults = [
            (1, 2, "021A02003044E901", "Finn", "Dragon", 1),
            (2, 3, "021A0200462DE901", "Player 2", "Shark", 2),
            (3, 4, "021A02007701E901", "Player 3", "Lion", 1),
            (4, 5, "021A02008802E901", "Player 4", "Eagle", 2),
            (5, 6, "021A02009903E901", "Player 5", "Bear", 3),
            (6, 7, "021A0200AA04E901", "Player 6", "Puma", 4),
            (7, 8, "021A0200BB05E901", "Player 7", "Panda", 3),
            (8, 9, "021A0200CC06E901", "Player 8", "Snake", 4),
        ]
        self.guns.clear()
        for i in range(max(1, min(8, num_guns))):
            gnum, gid, ref_hex, name, avatar, team_id = defaults[i]
            self.guns[gid] = SimGunState(
                gun_num=gnum,
                gun_id=gid,
                ref_hex=ref_hex,
                name=name,
                avatar=avatar,
                stored_name=name,
                stored_avatar=avatar,
                team_id=team_id,
            )

    def reset_for_mode(self, mode_name: str, settings: dict[str, Any], team_map: dict[int, int] | None = None) -> None:
        self.mode_name = mode_name
        self.settings = dict(settings)
        self.ubiconnect_hits = {}
        for gid, g in self.guns.items():
            if team_map and gid in team_map:
                g.team_id = team_map[gid]
            g.life_points = int(settings.get("lifePoints", 5))
            g.max_life = g.life_points
            g.ammo = 6 if mode_name == "OldFashionDuel" else int(settings.get("ammunitions", 9))
            g.max_ammo = g.ammo
            g.clips = 0 if mode_name == "OldFashionDuel" else int(settings.get("clips", 5))
            g.max_clips = g.clips
            g.shots_fired = 0
            g.hits_received = 0
            g.phase_flag = 1
            g.hit_ids = [0, 0, 0, 0, 0]
            g.last_rfid = ""
            g.led_color = "orange"
            g.dead_at = None
            g.respawn_at = None
            g.auto_reload_pending = False
            g.tag_stage = 0
            g.awaiting_shot = False

    def reset_race_course(self, gun_id: int) -> None:
        """Reset one sim gun's TagThenShoot course position for a new timed turn.

        The tag counter itself stays cumulative (the engine diffs it
        against its per-player baseline); only the course position —
        which base is next and whether a tower shot is awaited — resets,
        mirroring the per-turn countdown restart on real hardware.
        """
        g = self.guns.get(gun_id)
        if g is None:
            return
        g.tag_stage = 0
        g.awaiting_shot = False
        g.phase_flag = 1

    def read_gun_profile(self, gun_id: int) -> dict[str, Any] | None:
        """Return the sim gun's stored (flash) profile, as a 0x84 read would."""
        g = self.guns.get(gun_id)
        if g is None:
            return None
        return {"name": g.stored_name or g.name, "avatar": g.stored_avatar or g.avatar}

    def write_gun_profile(self, gun_id: int, name: str, avatar: str) -> tuple[bool, str]:
        """Burn name/avatar into the sim gun's stored profile (validates like the real write)."""
        g = self.guns.get(gun_id)
        if g is None:
            return False, f"Gun {gun_id:02x} is not a simulated gun."
        try:
            build_profile_block(name, avatar)
        except ValueError as exc:
            return False, str(exc)
        g.stored_name = name.strip()
        g.stored_avatar = next(a for a in AVATAR_NAMES if a.lower() == avatar.strip().lower())
        return True, "verified"

    def sim_fire_shot(self, shooter_gid: int, target_gid: int | None = None) -> dict[str, Any]:
        """Simulate a gun firing a shot, optionally hitting `target_gid`."""
        shooter = self.guns.get(shooter_gid)
        if not shooter:
            return {"ok": False, "error": "Unknown shooter gun_id"}

        if self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding") and shooter.life_points <= 0:
            return {"ok": False, "error": "Shooter is out of life points (needs respawn/medkit)"}
        if self.mode_name == "OldFashionDuel" or (
            self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding", "StarterFrag")
            and int(self.settings.get("ammoBox", 1)) != 0
        ):
            if shooter.ammo <= 0:
                if shooter.auto_reload_pending:
                    return {"ok": False, "error": "Reloading — the next tick refills the ammo."}
                if shooter.clips > 0:
                    # The gun auto-reloads on its next TIMER tick: arm
                    # the reload here; poll_gun performs the refill.
                    shooter.auto_reload_pending = True
                    return {"ok": False, "error": "Out of ammo — reloading on the next tick."}
                return {"ok": False, "error": "Out of ammo — scan an Ammo card (RF01/RF02)!"}
            shooter.ammo = max(0, shooter.ammo - 1)
            if shooter.ammo == 0 and shooter.clips > 0:
                # Firing the last bullet arms the gun's auto_reload.
                shooter.auto_reload_pending = True

        shooter.shots_fired += 1

        if target_gid is not None and target_gid in self.guns:
            target = self.guns[target_gid]
            if self.mode_name == "TagThenShoot":
                if shooter.awaiting_shot:
                    # Tower hit: the UbiConnect reports it and the gun
                    # returns to its tag state for the next base. The
                    # stage wraps after the last base: the course loops.
                    self.ubiconnect_hits[shooter_gid] = self.ubiconnect_hits.get(shooter_gid, 0) + 1
                    bases = max(1, int(self.settings.get("numberOfBase", 4)))
                    shooter.tag_stage = (shooter.tag_stage + 1) % bases
                    shooter.awaiting_shot = False
                    shooter.phase_flag = 1
                    return {"ok": True, "shot": True, "hit": True, "shooter": shooter_gid, "target": target_gid}
                # In the tag state a shot scores nothing.
                return {"ok": True, "shot": True, "hit": False, "shooter": shooter_gid, "target": target_gid}

            target.hits_received += 1
            target.hit_ids = [shooter_gid] + target.hit_ids[:4]
            if self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding"):
                if target.life_points > 0:
                    target.life_points = max(0, target.life_points - 1)
                    if target.life_points == 0 and (
                        self.mode_name == "FreeForAll"
                        or (self.mode_name == "TeamFrag" and int(self.settings.get("tBase", 1)) == 0)
                    ):
                        # The timer-only respawn clock starts at death.
                        target.dead_at = time.time()
            return {"ok": True, "shot": True, "hit": True, "shooter": shooter_gid, "target": target_gid}

        return {"ok": True, "shot": True, "hit": False, "shooter": shooter_gid}

    def sim_scan_rfid(self, gun_id: int, rfid_code: str) -> dict[str, Any]:
        """Simulate scanning an RFID card or T-Base (`RF01`..`RF09`) on `gun_id`."""
        gun = self.guns.get(gun_id)
        if not gun:
            return {"ok": False, "error": "Unknown gun_id"}
        gun.last_rfid = rfid_code
        if rfid_code in ("RF01", "RF02"):
            gun.clips = gun.max_clips
            gun.ammo = gun.max_ammo
            gun.auto_reload_pending = False
        elif rfid_code == "RF07":
            if int(self.settings.get("medkit", 1)) > 0 or self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding"):
                gun.life_points = gun.max_life
                gun.dead_at = None
                gun.respawn_at = None
        elif rfid_code in ("RF03", "RF04", "RF05", "RF06"):
            base_num = int(rfid_code[-1]) - 2
            if self.mode_name == "TeamFrag" and base_num == gun.team_id:
                if gun.life_points <= 0:
                    # Scan-then-timer respawn: the own-base scan starts
                    # the wait; the gun returns once respawn_time
                    # elapses (at once when respawn_time is 0).
                    wait = float(self.settings.get("respawnTime", 0))
                    if wait <= 0:
                        gun.life_points = gun.max_life
                        gun.ammo = gun.max_ammo
                        gun.clips = gun.max_clips
                    else:
                        gun.respawn_at = time.time() + wait
                else:
                    gun.life_points = gun.max_life
                    gun.ammo = gun.max_ammo
                    gun.clips = gun.max_clips
            elif self.mode_name == "TagThenShoot":
                self._tag_scan(gun, base_num)
        return {"ok": True, "gun_id": gun_id, "rfid_code": rfid_code}

    def _tag_scan(self, gun: SimGunState, base_num: int) -> None:
        """TagThenShoot scan: the gun enforces course order.

        Only the next base in sequence counts, and only while the gun
        is in its tag state. A correct scan raises the tag count and
        moves the gun to its shoot state (phase_flag 0); a wrong base,
        or a scan while awaiting a tower shot, scores nothing.
        """
        if gun.awaiting_shot:
            return
        if base_num != gun.tag_stage + 1:
            return
        gun.hits_received += 1
        gun.awaiting_shot = True
        gun.phase_flag = 0

    def poll_gun(self, gun_id: int, mode_name: str, downlink_word: int | None = None) -> DecodedStatus | None:
        g = self.guns.get(gun_id)
        if not g:
            return None
        now = time.time()
        # Timer-based auto-reload: the gun's TIMER tick consumes a clip
        # and refills the ammo after the last bullet was fired.
        if g.auto_reload_pending:
            if g.clips > 0:
                g.clips -= 1
                g.ammo = g.max_ammo
            g.auto_reload_pending = False
        # Timer-only respawn: the dead gun returns with full life/ammo once
        # respawn_time has elapsed (FreeForAll, or TeamFrag with T-Bases off).
        if (
            (mode_name == "FreeForAll" or (mode_name == "TeamFrag" and int(self.settings.get("tBase", 1)) == 0))
            and g.life_points <= 0
            and g.dead_at is not None
        ):
            if now - g.dead_at >= float(self.settings.get("respawnTime", 0)):
                g.life_points = g.max_life
                g.ammo = g.max_ammo
                g.clips = g.max_clips
                g.dead_at = None
        # TeamFrag scan-then-timer respawn: the wait the own-base scan
        # started has run out, so the gun returns.
        if mode_name == "TeamFrag" and g.respawn_at is not None and now >= g.respawn_at:
            g.life_points = g.max_life
            g.ammo = g.max_ammo
            g.clips = g.max_clips
            g.respawn_at = None
        if mode_name == "FreeFrag":
            words = [g.hits_received, g.shots_fired]
        elif mode_name == "TeamFrag":
            words = [g.life_points, g.ammo, g.clips, g.shots_fired]
        elif mode_name in ("FreeForAll", "LastManStanding"):
            words = [g.life_points, g.ammo, g.clips]
        elif mode_name == "StarterFrag":
            words = [g.hits_received, g.ammo, g.clips]
        elif mode_name == "OldFashionDuel":
            words = [g.ammo, g.hits_received]
        elif mode_name == "TagThenShoot":
            words = [g.hits_received, g.phase_flag]
        else:
            words = [g.hits_received, g.shots_fired]

        num_vars = len(words)
        word_count = 2 * num_vars + 1
        size = 6 + 2 * num_vars
        payload = (
            bytes([size, gun_id & 0xFF, 0x95, word_count])
            + b"".join((w & 0xFFFF).to_bytes(2, "big") for w in words)
            + bytes(g.hit_ids[:5])
            + bytes([0x00])
        )
        return decode_0x95_status(payload, mode_name)


def _serialized(method):
    """Run a transport method under the instance I/O lock.

    The UbiConnect is a single half-duplex HID handle shared by the lobby
    auto-pair loop, manual pairing, bytecode upload, and the poll loop, which
    run on different threads. Single-shot replies (the 0x84 profile, the 0x04
    revision) are sent once: if two threads read concurrently, one can steal
    the other's reply and the pair step times out. Every HID operation must
    hold the lock for its whole duration so reads can never interleave. The
    lock is an RLock, so nested transport calls on the same thread are safe.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._io_lock:
            return method(self, *args, **kwargs)
    return wrapper


class RealHIDTransport:
    """Proven UbiConnect HID transport wrapping freefrag_single_gun.py verbatim."""

    is_simulator: bool = False

    # UTActivity.State.Bytecode.lua + ULUsb.lua (4 Hz USB poll + 250 ms send timer):
    # - CreateNextMessage() sets self.timer = now and waits 250 ms before first send.
    # - Same-step retry (when not all guns have ACKed yet) fires every 250 ms.
    # - When all guns ACK, ULUsb dispatches on its 250 ms tick, CreateNextMessage()
    #   resets self.timer = now, and Bytecode:Update() waits another 250 ms before
    #   sending the next step (~440 ms after the last gun's ACK, ~500 ms step-to-step).
    UPLOAD_INIT_DELAY_S = 0.25
    UPLOAD_RETRY_INTERVAL_S = 0.25
    UPLOAD_POST_ACK_DELAY_S = 0.44
    UPLOAD_MIN_GAP_S = 0.50
    UPLOAD_POST_LOCK_DELAY_S = 0.45
    UPLOAD_MAX_RETRIES = 16
    CONFIG_RETRY_INTERVAL_S = 0.25
    CONFIG_INTER_GUN_DELAY_S = 0.025

    def __init__(
        self,
        confirm_write: bool = False,
        logger: Callable[[str], None] | None = None,
        number_memory: dict[str, int] | None = None,
        ignored_refs: set[str] | None = None,
    ) -> None:
        self.confirm_write = confirm_write
        self.ext_logger = logger
        self.dev: Any = None
        self.sequences = load_mode_sequences()
        self.ubiconnect_hits: dict[int, int] = {}
        self.last_profile_replies: dict[int, bytes] = {}
        self._io_lock = threading.RLock()
        self._last_upload_send_ts: float = 0.0
        self._last_upload_ack_ts: float = 0.0
        self.upload_failed_guns: list[int] = []
        self._last_reack_ts: dict[str, float] = {}
        self.initialized: bool = False
        self.paired_by_ref: dict[str, dict[str, Any]] = {}
        self.vest_states: dict[int, bool] = {}
        # Ref -> player number remembered across quiet-drops, host
        # Removes, and UbiConnect-loss re-pairing within the session, so
        # a returning gun reclaims its number instead of taking the
        # lowest free one. The app passes the engine's dict so the
        # memory survives transport recreation; a standalone transport
        # keeps its own.
        self._ref_number_memory: dict[str, int] = (
            number_memory if number_memory is not None else {}
        )
        # Refs the host explicitly removed on the pairing page. The
        # auto-pair loop must not instantly re-add a still-announcing
        # removed gun; only a fresh pairing scan clears this list. The
        # app passes the engine's set so a Remove survives transport
        # recreation (UbiConnect replug); a standalone transport keeps
        # its own.
        self.ignored_refs: set[str] = ignored_refs if ignored_refs is not None else set()
        self.last_ping_ts: float = 0.0
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.log_path = LOGS_DIR / f"battletag_hw_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self._log_file: Any = None

    def log(self, text: str) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
        line = f"{stamp} {text}"
        print(line, flush=True)
        if self._log_file is None and self.confirm_write:
            try:
                self._log_file = open(self.log_path, "a", encoding="utf-8")
            except OSError:
                self._log_file = False
        if self._log_file:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                pass
        if self.ext_logger:
            self.ext_logger(text)

    def _require_write_permission(self) -> None:
        if not self.confirm_write:
            raise PermissionError(
                "Hardware write blocked: explicit user authorization and confirm_write=True are required."
            )

    @_serialized
    def open(self) -> None:
        self._require_write_permission()
        if hid is None:
            raise RuntimeError("hidapi is not installed in the active virtual environment.")
        devices = hid.enumerate(VID, PID)
        if not devices:
            raise RuntimeError(
                f"No device found for VID=0x{VID:04X} PID=0x{PID:04X}. Check USB cable."
            )
        self.dev = hid.device()
        self.dev.open_path(devices[0]["path"])
        self.log(f"Opened UbiConnect device: {devices[0].get('product_string', 'UbiConnect')}")

    @_serialized
    def close(self) -> None:
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    @_serialized
    def write_packet(self, raw: bytes) -> None:
        self._require_write_permission()
        if self.dev is None:
            raise RuntimeError("UbiConnect device is not open.")
        pkt = pad64(raw)
        self.dev.write(bytes([0x00]) + pkt)

    @_serialized
    def read_one(self, timeout_ms: int = 100) -> bytes | None:
        if self.dev is None:
            return None
        self.dev.set_nonblocking(True)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            data = self.dev.read(REPORT_LEN + 1, timeout_ms=100)
            if data:
                return bytes(data)
            time.sleep(0.005)
        return None

    @staticmethod
    def _matches_pairing_beacon_ref(payload: bytes, ref: str | None) -> bool:
        """True when payload is a 10 00 12 pairing beacon matching ref (or any when unset)."""
        if payload[:3] != bytes.fromhex("100012"):
            return False
        if ref is None:
            return True
        return len(payload) >= 11 and payload[3:11].hex().upper() == ref.upper()

    @_serialized
    def drain_queue(
        self,
        drain_ms: int = 500,
        last_ack_b: bytes | None = None,
        ref: str | None = None,
    ) -> tuple[int, int]:
        re_acks = 0
        drained = 0
        deadline = time.monotonic() + drain_ms / 1000.0
        self.dev.set_nonblocking(True)
        while time.monotonic() < deadline:
            chunk = self.dev.read(REPORT_LEN + 1, timeout_ms=100)
            if not chunk:
                continue
            raw = bytes(chunk)
            payload = raw[1:] if len(raw) == REPORT_LEN + 1 else raw
            drained += 1
            self.log(f"  drained queued IN: {payload.hex().upper()}")
            if (
                last_ack_b is not None
                and re_acks < 2
                and self._matches_pairing_beacon_ref(payload, ref)
            ):
                self.dev.write(bytes([0x00]) + last_ack_b)
                re_acks += 1
                self.log("  re-sent 0B ack")
        return drained, re_acks

    @_serialized
    def run_sequence(self, seq: dict[str, Any]) -> bool:
        """Exact sequence runner from freefrag_single_gun.py."""
        self._require_write_permission()
        ref: str | None = str(seq["ref"]).upper() if seq.get("ref") else None
        last_ack_b: bytes | None = None

        for i, step in enumerate(seq["steps"], 1):
            note = step.get("note", "")
            self.log(f"  step {i}/{len(seq['steps'])}: {note}")

            if "drain_ms" in step:
                self.drain_queue(step.get("drain_ms", 500), last_ack_b, ref=ref)
                continue

            if "wait_for_prefix" in step:
                prefix = bytes.fromhex(step["wait_for_prefix"])
                data = None
                deadline = time.monotonic() + step.get("wait_ms", 5000) / 1000.0
                self.dev.set_nonblocking(True)
                while time.monotonic() < deadline:
                    chunk = self.dev.read(REPORT_LEN + 1, timeout_ms=100)
                    if chunk:
                        raw = bytes(chunk)
                        payload = raw[1:] if len(raw) == REPORT_LEN + 1 else raw
                        if payload[: len(prefix)] == prefix:
                            # With a locked ref (pairing a specific detected gun) only
                            # that gun's announcement counts: another gun in pairing
                            # mode must not hijack the ack, or it gets this gun's id.
                            if seq.get("lock_ref") and ref and "capture_ref_bytes" in step:
                                a, b = step["capture_ref_bytes"]
                                if payload[a:b].hex().upper() != ref:
                                    continue
                            data = payload
                            break
                if data is None:
                    self.log(f"  FAIL: no IN report starting with {step['wait_for_prefix']}")
                    return False
                if "capture_ref_bytes" in step and not seq.get("lock_ref"):
                    a, b = step["capture_ref_bytes"]
                    ref = data[a:b].hex().upper()
                continue

            payload_hex = step["send"]
            if "{{ref}}" in payload_hex or "{{ref6}}" in payload_hex:
                if ref is None:
                    self.log("  FAIL: send needs {{ref}} but no ref captured")
                    return False
                payload_hex = payload_hex.replace("{{ref}}", ref)
                payload_hex = payload_hex.replace("{{ref6}}", ref[4:])

            payload = bytes.fromhex(payload_hex)
            if len(payload) != REPORT_LEN:
                # The proven client failed any sequence step whose payload was
                # not exactly 64 bytes: a short report is a broken template,
                # never a valid message. Fail loudly instead of sending it.
                self.log(f"  FAIL: step payload is {len(payload)} bytes, must be exactly {REPORT_LEN}")
                return False
            if payload.hex().upper().startswith(("0BFF12", "0AFF12")):
                last_ack_b = payload

            self.log(f"  OUT {payload.hex().upper()}")
            self.dev.write(bytes([0x00]) + payload)

            expect = step.get("expect_prefix")
            wait_ms = step.get("wait_ms", 1000)
            if expect is None:
                time.sleep(min(wait_ms, 500) / 1000.0)
                continue

            want = bytes.fromhex(expect)
            deadline = time.monotonic() + wait_ms / 1000.0
            matched = None
            re_acks = 0
            while time.monotonic() < deadline:
                reply = self.read_one(100)
                if reply is None:
                    continue
                payload_reply = reply[1:] if len(reply) == REPORT_LEN + 1 else reply
                if payload_reply[: len(want)] == want:
                    matched = payload_reply
                    break
                if (
                    last_ack_b is not None
                    and re_acks < 2
                    and self._matches_pairing_beacon_ref(payload_reply, ref)
                ):
                    self.dev.write(bytes([0x00]) + last_ack_b)
                    re_acks += 1

            if matched is None:
                self.log(f"  FAIL: no reply starting with {expect} within {wait_ms} ms")
                return False
            self.log(f"  IN  {matched.hex().upper()}")
            if len(matched) >= 3 and matched[2] == 0x84:
                self.last_profile_replies[matched[1]] = matched

        return True

    @_serialized
    def send_and_expect(
        self,
        payload: bytes,
        expect_prefix: str | None = None,
        wait_ms: int = 1000,
    ) -> tuple[bool, bytes | None]:
        """Exact send_and_expect from freefrag_single_gun.py."""
        self._require_write_permission()
        pkt = pad64(payload)
        self.log(f"OUT {pkt.hex().upper()}")
        self.dev.write(bytes([0x00]) + pkt)
        if expect_prefix is None:
            time.sleep(min(wait_ms, 500) / 1000.0)
            return True, None
        want = bytes.fromhex(expect_prefix)
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            reply = self.read_one(100)
            if reply is None:
                continue
            payload_reply = reply[1:] if len(reply) == REPORT_LEN + 1 else reply
            if payload_reply[: len(want)] == want:
                self.log(f"IN  {payload_reply.hex().upper()}")
                return True, payload_reply
        self.log(f"FAIL: no reply starting with {expect_prefix} within {wait_ms} ms")
        return False, None

    def load_ref_mapping(self) -> dict[str, dict[str, Any]]:
        ref_map: dict[str, dict[str, Any]] = {}
        if MINIMAL_GUN_REFS.exists():
            try:
                cfg = json.loads(MINIMAL_GUN_REFS.read_text())
                for ref_hex, info in cfg.get("ref_to_player", {}).items():
                    ref_map[ref_hex.upper()] = info
            except Exception as exc:
                self.log(f"WARNING: Failed to read {MINIMAL_GUN_REFS}: {exc}")
        return ref_map

    @_serialized
    def ensure_initialized(self) -> bool:
        if self.initialized:
            return True
        self._require_write_permission()
        self.log("=== INIT ===")
        init_seq = self.sequences["init"]
        if not self.run_sequence(init_seq):
            self.log("FAIL: init sequence")
            return False
        self.initialized = True
        self.log("INIT: complete")
        return True

    def choose_player_number(
        self,
        ref_hex: str,
        ref_map: dict[str, dict[str, Any]],
        used_pids: set[int],
        target_pid: int | None = None,
    ) -> int:
        """Pick the player number for a pairing gun.

        Precedence: an explicit target, then the saved gun_refs.json
        mapping, then the number this ref held earlier in the session
        (a gun returning after a quiet-drop, a host Remove, or a
        UbiConnect loss reclaims its number), then the lowest free
        number. A returning gun never reshuffles other players'
        numbers: a remembered number is used only while it is free.
        """
        if target_pid is not None and target_pid not in used_pids:
            return target_pid
        saved = ref_map.get(ref_hex, {})
        if "player_id" in saved and int(saved["player_id"]) not in used_pids:
            return int(saved["player_id"])
        remembered = self._ref_number_memory.get(ref_hex)
        if remembered is not None and remembered not in used_pids:
            return remembered
        pid = 1
        while pid in used_pids:
            pid += 1
        return pid

    @_serialized
    def pair_single_gun(
        self,
        ref_hex: str,
        ref_bytes: bytes,
        target_pid: int | None = None,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """Pair a single detected gun and register its decoded profile."""
        self._require_write_permission()
        self.ensure_initialized()

        ref_map = self.load_ref_mapping()
        used_pids = {info["gun_num"] for info in self.paired_by_ref.values()}
        pid = self.choose_player_number(ref_hex, ref_map, used_pids, target_pid)
        name = ref_map.get(ref_hex, {}).get("name") or f"Player {pid}"

        gun_id = 0x01 + pid
        class_id = pid
        self.log(f"=== PAIR GUN id {gun_id:02x}, classId {class_id:02x} (ref {ref_hex}) ===")

        pair_template = self.sequences["pair"]
        pair_seq: dict[str, Any] = {
            "name": f"pair gun {gun_id:02x}",
            "capture": pair_template.get("capture"),
            "ref": ref_hex,
            "lock_ref": True,
            "steps": [],
        }
        gun_id_hex = f"{gun_id:02X}"
        class_id_hex = f"{class_id:02X}"

        for step in pair_template["steps"]:
            new_step = dict(step)
            if "send" in new_step:
                send_hex = new_step["send"]
                if "0AFF12020101" in send_hex:
                    send_hex = send_hex.replace("0AFF12020101", f"0AFF12{gun_id_hex}{class_id_hex}01")
                if "0BFF12020101" in send_hex:
                    send_hex = send_hex.replace("0BFF12020101", f"0BFF12{gun_id_hex}{class_id_hex}01")
                if "000204020101" in send_hex:
                    send_hex = send_hex.replace(
                        "000204020101", f"000{gun_id_hex[-1:]}04{gun_id_hex}{class_id_hex}01"
                    )
                elif "000204" in send_hex:
                    send_hex = send_hex.replace("000204", f"000{gun_id_hex[-1:]}")
                    send_hex = send_hex.replace("0204", f"04{gun_id_hex}")
                    if f"{gun_id_hex}0101" not in send_hex:
                        send_hex = send_hex.replace("020101", f"{gun_id_hex}{class_id_hex}01")
                if "0502" in send_hex and "84" in send_hex:
                    send_hex = send_hex.replace("0502", f"05{gun_id_hex}")
                new_step["send"] = send_hex

            if "expect_prefix" in new_step and new_step["expect_prefix"]:
                prefix = new_step["expect_prefix"]
                if prefix.upper().startswith("0402"):
                    new_step["expect_prefix"] = f"04{gun_id_hex}"
                elif prefix.upper().startswith("3502"):
                    new_step["expect_prefix"] = f"35{gun_id_hex}"
            pair_seq["steps"].append(new_step)

        # Split the pairing sequence into the ack phase (steps 1-6 ending after
        # 0A/0B acks + settle drain), the revision check (step 7, 00 <id> 04 ->
        # 04 <id>), and the profile read (step 8, 05 <id> 84 -> 35 <id>). Once
        # 0A/0B acks go out, the gun says "OK", adopts gun_id, and stops
        # sending 10 00 12 announcements; retrying from step 1 would stall for
        # 30s and strand an already-acked gun off-screen, so retry revision and
        # profile directly instead.
        rev_idx = next(
            (
                idx
                for idx, st in enumerate(pair_seq["steps"])
                if str(st.get("expect_prefix", "")).upper().startswith("04")
            ),
            len(pair_seq["steps"]) - 1,
        )
        ack_seq = dict(pair_seq, steps=pair_seq["steps"][:rev_idx])
        rev_seq = {
            "name": f"revision gun {gun_id:02x}",
            "capture": pair_seq.get("capture"),
            "ref": ref_hex,
            "lock_ref": True,
            "steps": [pair_seq["steps"][rev_idx]],
        }
        profile_steps = pair_seq["steps"][rev_idx + 1 :]

        if ack_seq["steps"] and not self.run_sequence(ack_seq):
            self.log(f"FAIL: pair gun {gun_id:02x}")
            return None

        rev_ok = False
        for rev_attempt in (1, 2, 3):
            if self.run_sequence(rev_seq):
                rev_ok = True
                break
            self.log(f"  revision attempt {rev_attempt} failed for gun {gun_id:02x}, retrying revision")

        prof_ok = False
        if profile_steps:
            profile_seq = {
                "name": f"profile gun {gun_id:02x}",
                "capture": pair_seq.get("capture"),
                "ref": ref_hex,
                "lock_ref": True,
                "steps": profile_steps,
            }
            for prof_attempt in (1, 2):
                if self.run_sequence(profile_seq):
                    prof_ok = True
                    break
                self.log(f"  profile attempt {prof_attempt} failed for gun {gun_id:02x}, retrying profile read")
            if not prof_ok:
                self.log(f"  WARNING: profile read failed for gun {gun_id:02x}; using default name/avatar")

        if not (rev_ok or prof_ok):
            self.log(f"FAIL: pair gun {gun_id:02x}")
            return None

        profile_name = name
        profile_avatar = AVATAR_NAMES[(class_id - 1) % len(AVATAR_NAMES)]
        raw_prof = self.last_profile_replies.get(gun_id)
        if raw_prof is not None:
            decoded_prof = decode_0x84_profile(raw_prof)
            if decoded_prof.get("name"):
                profile_name = decoded_prof["name"]
            if decoded_prof.get("avatar"):
                profile_avatar = decoded_prof["avatar"]

        info = {
            "gun_num": class_id,
            "gun_id": gun_id,
            "ref_hex": ref_hex,
            "name": profile_name,
            "avatar": profile_avatar,
        }
        self.paired_by_ref[ref_hex] = info
        # Remember the number for this ref: if the gun later goes quiet,
        # is removed by the host, or must re-register after a UbiConnect
        # loss, it reclaims this number on return.
        self._ref_number_memory[ref_hex] = class_id
        if progress_cb:
            progress_cb(info)
        self.log(f"PAIR: gun {gun_id:02x} complete (Player {class_id}: {profile_name} [{profile_avatar}])")
        return info

    @_serialized
    def check_for_new_guns_in_pairing(self, timeout_ms: int = 150) -> list[dict[str, Any]]:
        """Non-blocking check for new 10 00 12 pairing announcements while in Lobby.

        Automatically pairs any newly detected gun and returns list of newly paired guns.
        """
        if self.dev is None:
            return []
        if not self.initialized:
            self.ensure_initialized()
        data = self.read_one(timeout_ms=timeout_ms)
        if not data or len(data) < 11:
            return []
        b = data[1:] if len(data) == REPORT_LEN + 1 else data
        if len(b) >= 11 and b[0] == 0x10 and b[1] == 0x00 and b[2] == 0x12:
            ref_bytes = bytes(b[3:11])
            ref_hex = "".join(f"{x:02X}" for x in ref_bytes)
            if ref_hex in self.ignored_refs:
                # Host-removed: the gun keeps announcing, but the host
                # chose to leave it out. Stay silent — no ack, no
                # re-pair — until a fresh pairing scan clears the
                # ignore list.
                return []
            if ref_hex in self.paired_by_ref:
                # A paired gun re-announcing means it never accepted its ack
                # (guns stop announcing once acked). Re-send ack B, as the
                # original proxy does for already-mapped devices, rate-limited
                # so a chatty gun cannot flood the channel.
                now = time.monotonic()
                if self.confirm_write and now - self._last_reack_ts.get(ref_hex, 0.0) >= 2.0:
                    self._last_reack_ts[ref_hex] = now
                    info = self.paired_by_ref[ref_hex]
                    ack_b = pad64(
                        bytes.fromhex(f"0BFF12{info['gun_id']:02X}{info['gun_num']:02X}01") + ref_bytes
                    )
                    self.write_packet(ack_b)
                    self.log(f"  re-sent 0B ack to already-paired gun {info['gun_id']:02x}")
                return []
            self.log(f"Auto-detected new gun in pairing mode: ref {ref_hex}")
            info = self.pair_single_gun(ref_hex, ref_bytes)
            if info:
                return [info]
        return []

    @_serialized
    def unpair_all(self) -> None:
        self.paired_by_ref.clear()
        self.last_profile_replies.clear()
        self._last_reack_ts.clear()
        # A full reset re-admits host-removed guns on the next scan, but
        # the ref->number memory survives: re-pairing the same guns
        # keeps their numbers stable.
        self.ignored_refs.clear()
        self.log("Cleared all paired guns.")

    @_serialized
    def unpair_gun(self, ref_hex: str, ignore: bool = False) -> dict[str, Any] | None:
        """Drop one gun's pairing, host-side only (no wire message exists).

        Only this gun is touched; other pairings and their numbers are
        unchanged. The ref->number memory is kept, so the same gun
        reclaims its number if it pairs again. With ignore=True the ref
        joins the ignore list: the auto-pair loop will not re-add the
        still-announcing gun until a fresh pairing scan clears the
        list. Returns the removed pairing info, or None if not paired.
        """
        info = self.paired_by_ref.pop(ref_hex, None)
        self._last_reack_ts.pop(ref_hex, None)
        if info is not None:
            self.last_profile_replies.pop(info["gun_id"], None)
            self.log(
                f"Unpaired gun {info['gun_id']:02x} (Player {info['gun_num']})"
                + ("; ignored until next pairing scan" if ignore else "")
            )
        if ignore:
            self.ignored_refs.add(ref_hex)
        return info

    def clear_ignored_refs(self) -> None:
        """A fresh pairing scan starts: host-removed guns may pair again."""
        self.ignored_refs.clear()

    @_serialized
    def init_and_pair(
        self,
        expected_guns: int = 2,
        pair_timeout_s: float = 12.0,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Run init.json and pair guns using the exact freefrag_single_gun.py handshake."""
        self._require_write_permission()
        if not self.ensure_initialized():
            return []

        self.log(f"=== COLLECT PAIRING ANNOUNCEMENTS (up to {pair_timeout_s:.0f}s) ===")
        gun_refs: dict[str, bytes] = {}
        start_time = time.time()
        last_new_ref_ts = start_time
        while (time.time() - start_time) < pair_timeout_s:
            try:
                data = self.dev.read(REPORT_LEN + 1, timeout_ms=500)
                if not data:
                    time.sleep(0.01)
                else:
                    b = bytes(data[1:]) if len(data) == REPORT_LEN + 1 else bytes(data)
                    if len(b) >= 11 and b[0] == 0x10 and b[1] == 0x00 and b[2] == 0x12:
                        ref_bytes = bytes(b[3:11])
                        ref_hex = "".join(f"{x:02X}" for x in ref_bytes)
                        if (
                            ref_hex not in gun_refs
                            and ref_hex not in self.paired_by_ref
                            and ref_hex not in self.ignored_refs
                        ):
                            self.log(f"  Saw pairing announcement, ref {ref_hex}")
                            gun_refs[ref_hex] = ref_bytes
                            last_new_ref_ts = time.time()
                            if len(self.paired_by_ref) + len(gun_refs) >= expected_guns:
                                self.log(f"All {expected_guns} guns detected!")
                                break
            except Exception:
                pass
            # Quiet-period exit: guns in pairing mode announce every ~150 ms,
            # so 2 s without a NEW announcement means every gun that is on has
            # been seen. Without this the scan always burns the full timeout
            # when fewer than `expected_guns` are present. When no guns at all
            # are known yet, keep waiting the full timeout for the user to
            # power one on.
            if (gun_refs or self.paired_by_ref) and (time.time() - last_new_ref_ts) > 2.0:
                self.log("Pairing scan: no new announcements for 2s, proceeding.")
                break

        for ref_hex, ref_bytes in gun_refs.items():
            self.pair_single_gun(ref_hex, ref_bytes, progress_cb=progress_cb)

        return sorted(list(self.paired_by_ref.values()), key=lambda g: g["gun_num"])

    @_serialized
    def vest_check(self, expect_gids: set[int] | None = None) -> int:
        """Vest check: count guns whose T-Vest reports connected.

        Only a real C1 reply (`01 <id> C1 <arg>`, arg 1 = vest connected)
        counts. Echoes of the broadcast poll itself (`00 <id> C1`) and
        unrelated reports are not vest confirmations. Results are recorded in
        `vest_states` keyed by gun id; guns that never reply keep no entry —
        the caller decides what silence means (see ArenaEngine
        .apply_vest_states). When `expect_gids` is given, the collect window
        ends as soon as every expected gun has answered, so periodic sweeps
        stay cheap (about 0.25 s when all guns answer); otherwise the full
        window is used.
        """
        self._require_write_permission()
        self.vest_states.clear()
        self.log("VEST CHECK: sending broadcast...")
        self.write_packet(build_vest_poll_packet())
        time.sleep(0.2)
        self.dev.set_nonblocking(True)
        vests_found = 0
        seen: set[int] = set()
        replied: set[int] = set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            chunk = self.dev.read(REPORT_LEN + 1, timeout_ms=100)
            if not chunk:
                time.sleep(0.01)
            else:
                raw = bytes(chunk)
                payload = raw[1:] if len(raw) == REPORT_LEN + 1 else raw
                self.log(f"  vest reply: {payload.hex().upper()}")
                if len(payload) >= 4 and payload[0] == 0x01 and payload[2] == 0xC1:
                    gid = payload[1]
                    connected = payload[3] == 0x01
                    self.vest_states[gid] = connected
                    replied.add(gid)
                    if connected and gid not in seen:
                        seen.add(gid)
                        vests_found += 1
            if expect_gids and expect_gids <= replied:
                break
        self.log(f"VEST CHECK: {vests_found} vest(s) connected")
        return vests_found

    def _pace_upload_send(self, *, is_retry: bool = False) -> None:
        """Sleep until the original game's timer allows the next upload packet.

        - Same-step retries fire at `UPLOAD_RETRY_INTERVAL_S` (250 ms) from the
          previous send, matching UTActivity.State.Bytecode:Update().
        - Advancing to a new step waits until BOTH `UPLOAD_MIN_GAP_S` (500 ms)
          since the previous step send AND `UPLOAD_POST_ACK_DELAY_S` (440 ms)
          since the previous step's final gun ACK, matching the 4 Hz ULUsb
          dispatch + 250 ms CreateNextMessage timer reset in the original game.
        """
        now = time.monotonic()
        if is_retry:
            target = (self._last_upload_send_ts + self.UPLOAD_RETRY_INTERVAL_S) if self._last_upload_send_ts > 0 else now
        else:
            targets = [now]
            if self._last_upload_send_ts > 0:
                targets.append(self._last_upload_send_ts + self.UPLOAD_MIN_GAP_S)
            if self._last_upload_ack_ts > 0:
                targets.append(self._last_upload_ack_ts + self.UPLOAD_POST_ACK_DELAY_S)
            target = max(targets)
        if target > now:
            time.sleep(target - now)
        self._last_upload_send_ts = time.monotonic()

    @_serialized
    def _broadcast_step_and_wait_all(
        self,
        raw_pkt: bytes,
        gun_ids: list[int],
        expected_cmd: int,
        expected_offset: int | None = None,
        expected_byte3: int | None = None,
        label: str = "",
        retry_ms: int | None = None,
        max_attempts: int | None = None,
    ) -> bool:
        """Broadcast a flash step (0x86/0x81/0x82/0x83) and wait for every paired gun's RF ACK.

        Matches UTActivity.State.Bytecode.lua + ULUsb.lua: the current message
        is re-sent on a 250 ms tick until ALL guns ACK, and the transition to
        the next step respects the post-ACK / step-to-step quiet window. On
        exhaustion the guns still pending are recorded in `upload_failed_guns`
        and the step returns False — the caller must stop, not proceed on a
        partial upload.
        """
        self._require_write_permission()
        attempts = self.UPLOAD_MAX_RETRIES if max_attempts is None else max_attempts
        wait_ms = int(self.UPLOAD_RETRY_INTERVAL_S * 1000) if retry_ms is None else retry_ms
        pkt = pad64(raw_pkt)
        pending = set(gun_ids)
        for attempt in range(1, attempts + 1):
            self._pace_upload_send(is_retry=(attempt > 1))
            self.log(f"OUT {pkt.hex().upper()} [{label} try {attempt}/{attempts}, pending={[f'{g:02x}' for g in sorted(pending)]}]")
            self.dev.write(bytes([0x00]) + pkt)
            self._last_upload_send_ts = time.monotonic()
            deadline = time.monotonic() + wait_ms / 1000.0
            while time.monotonic() < deadline and pending:
                rep = self.read_one(timeout_ms=60)
                if rep is None:
                    continue
                b = rep[1:] if len(rep) == REPORT_LEN + 1 else rep
                self.log(f"IN  {b.hex().upper()}")
                if len(b) >= 3 and b[1] in pending and b[2] == expected_cmd:
                    if expected_offset is not None:
                        if len(b) >= 5 and int.from_bytes(b[3:5], "big") >= expected_offset:
                            pending.discard(b[1])
                    elif expected_byte3 is not None:
                        if len(b) >= 4 and b[3] == expected_byte3:
                            pending.discard(b[1])
                    else:
                        pending.discard(b[1])
            if not pending:
                self._last_upload_ack_ts = time.monotonic()
                # Drain any duplicate/trailing RF acks for 40 ms so the half-duplex RF channel is clear
                quiet_deadline = time.monotonic() + 0.04
                while time.monotonic() < quiet_deadline:
                    extra = self.read_one(timeout_ms=20)
                    if extra is not None:
                        eb = extra[1:] if len(extra) == REPORT_LEN + 1 else extra
                        self.log(f"IN  {eb.hex().upper()} (extra)")
                return True
        self.upload_failed_guns = sorted(pending)
        self.log(f"WARN: {label} missing ACK from guns {[f'{g:02x}' for g in sorted(pending)]}")
        return False

    @_serialized
    def upload_mode_bytecode(
        self,
        mode_name: str,
        gun_ids: list[int],
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Upload mode bytecode (unlock, erase, prepare, chunks, lock) waiting for all guns' RF ACKs.

        Returns True only when EVERY gun ACKed EVERY step. On any failure the
        upload stops at the failed step, the guns still pending are recorded
        in `upload_failed_guns`, and False is returned — the caller must not
        proceed to config or countdown. A gun that missed a chunk is left in
        bootloader mode and needs a fresh upload from unlock (the retry path),
        never a match start.
        """
        self._require_write_permission()
        uploads = self.sequences["uploads"]
        if mode_name not in uploads:
            raise ValueError(f"Unknown mode for bytecode upload: {mode_name}")
        up = uploads[mode_name]
        chunks = up["chunks"]
        total_chunks = len(chunks)
        total_steps = total_chunks + 4
        self.log(f"UPLOAD: {mode_name} bytecode ({total_chunks} chunks) to guns {[f'{g:02x}' for g in gun_ids]}")
        self.upload_failed_guns = []
        self._last_upload_send_ts = 0.0
        self._last_upload_ack_ts = 0.0

        self.drain_stale_echoes(0.15)
        if self.UPLOAD_INIT_DELAY_S > 0:
            time.sleep(self.UPLOAD_INIT_DELAY_S)

        if not self._broadcast_step_and_wait_all(
            bytes.fromhex(up["unlock"]),
            gun_ids,
            expected_cmd=0x86,
            expected_byte3=0x00,
            label="0x86 unlock",
        ):
            self.log("UPLOAD: FAILED at 0x86 unlock")
            return False
        if progress_cb:
            progress_cb(1, total_steps)

        # Erase is the slowest flash operation: allow 24 ticks (6 s) before
        # giving up, matching the original's never-give-up posture closely
        # enough to ride out a slow erase without hanging forever.
        if not self._broadcast_step_and_wait_all(
            bytes.fromhex(up["erase"]),
            gun_ids,
            expected_cmd=0x81,
            label="0x81 erase",
            max_attempts=24,
        ):
            self.log("UPLOAD: FAILED at 0x81 erase")
            return False
        if progress_cb:
            progress_cb(2, total_steps)

        if not self._broadcast_step_and_wait_all(
            bytes.fromhex(up["prepare"]),
            gun_ids,
            expected_cmd=0x82,
            label="0x82 prepare",
        ):
            self.log("UPLOAD: FAILED at 0x82 prepare")
            return False
        if progress_cb:
            progress_cb(3, total_steps)

        for i, chunk_info in enumerate(chunks):
            pkt = bytes.fromhex(chunk_info["message_hex"])
            next_offset = int(chunk_info["offset"]) + int(chunk_info["length"])
            if not self._broadcast_step_and_wait_all(
                pkt,
                gun_ids,
                expected_cmd=0x83,
                expected_offset=next_offset,
                label=f"0x83 chunk {i + 1}/{total_chunks} (next_offset=0x{next_offset:04X})",
            ):
                self.log(f"UPLOAD: FAILED at 0x83 chunk {i + 1}/{total_chunks}")
                return False
            if progress_cb:
                progress_cb(4 + i, total_steps)

        if not self._broadcast_step_and_wait_all(
            bytes.fromhex(up["lock"]),
            gun_ids,
            expected_cmd=0x86,
            expected_byte3=0x01,
            label="0x86 lock",
        ):
            self.log("UPLOAD: FAILED at 0x86 lock")
            return False
        if progress_cb:
            progress_cb(total_steps, total_steps)

        if self.UPLOAD_POST_LOCK_DELAY_S > 0:
            time.sleep(self.UPLOAD_POST_LOCK_DELAY_S)

        self.log("UPLOAD: complete")
        return True

    @_serialized
    def configure_gun(
        self,
        gun_id: int,
        mode_name: str,
        settings: dict[str, Any],
        team_id: int = 1,
        volume: int = 0,
        beam_power: int = 3,
    ) -> bool:
        """Send 0x92 config to a gun and wait for its `<len> <gid> 92` echo (retrying up to 4 times).

        `volume` and `beam_power` come from the global settings store: the
        original reads gun volume from global game settings, and beam power
        describes the venue, not the mode.
        """
        pkt = build_config_packet(gun_id, mode_name, settings, team_id=team_id, volume=volume, beam_power=beam_power)
        expect_prefix = f"{pkt[0]:02x}{gun_id:02x}92"
        retry_ms = int(self.CONFIG_RETRY_INTERVAL_S * 1000)
        if self.CONFIG_INTER_GUN_DELAY_S > 0:
            time.sleep(self.CONFIG_INTER_GUN_DELAY_S)
        for attempt in range(1, 5):
            attempt_start = time.monotonic()
            ok, reply = self.send_and_expect(pkt, expect_prefix=expect_prefix, wait_ms=retry_ms)
            if ok and reply is not None:
                self.log(f"Config 0x92 echo received from gun {gun_id:02x} (try {attempt})")
                return True
            remaining = self.CONFIG_RETRY_INTERVAL_S - (time.monotonic() - attempt_start)
            if remaining > 0 and attempt < 4:
                time.sleep(remaining)
        self.log(f"WARN: no 0x92 echo from gun {gun_id:02x} after 4 attempts")
        return False

    @_serialized
    def read_gun_profile(self, gun_id: int, attempts: int = 4) -> dict[str, Any] | None:
        """Read a gun's stored profile with the 0x84 request (address 0x11, 48 bytes).

        Mirrors ULUsbDevice.State.Profile: the request is re-sent until the
        gun answers. Returns the decoded {"name", "avatar", ...} or None.
        """
        req = bytes([0x05, gun_id, 0x84, 0x00, 0x00, 0x00, 0x11, 0x30])
        for _ in range(max(1, attempts)):
            self.write_packet(req)
            # The gun echoes the request before answering; keep consuming
            # 0x84 reports until one decodes to a real profile.
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                reply = self.wait_for_report(gun_id, 0x84, timeout_ms=100)
                if reply is None:
                    continue
                decoded = decode_0x84_profile(reply)
                if decoded.get("name"):
                    self.last_profile_replies[gun_id] = reply
                    for info in self.paired_by_ref.values():
                        if info.get("gun_id") == gun_id:
                            info["name"] = decoded["name"]
                            info["avatar"] = decoded["avatar"]
                    return decoded
        return None

    @_serialized
    def write_gun_profile(self, gun_id: int, name: str, avatar: str) -> tuple[bool, str]:
        """Burn name/avatar into one gun's flash, exactly as UTGame.Ui.Associate does.

        Unicast sequence to the gun (250 ms resend-until-ACK per step, the
        original's timer): unlock 0x86 -> erase page 0x81 -> write-init
        0x82 -> single 0x83 chunk carrying the whole PRFL block -> lock
        0x86. ACK matching keys on the command byte only, as the Lua does
        (the reply arg bytes for these steps were never captured). The
        write is verified by re-reading the profile afterwards; a
        mismatch fails loudly instead of claiming success.
        """
        self._require_write_permission()
        try:
            block = build_profile_block(name, avatar)
        except ValueError as exc:
            return False, str(exc)
        canonical_avatar = next(a for a in AVATAR_NAMES if a.lower() == avatar.strip().lower())
        # The shared step helper records failures in upload_failed_guns;
        # a profile write must not pollute the upload failure list.
        saved_failed = list(self.upload_failed_guns)
        steps = [
            (bytes([0x01, gun_id, 0x86, 0x00]), 0x86, "unlock"),
            (bytes([0x04, gun_id, 0x81, 0x00, 0x00, 0x00, 0x00]), 0x81, "erase"),
            (bytes([0x06, gun_id, 0x82, 0x00, 0x00, 0x00, 0x00, 0x00, len(block)]), 0x82, "write-init"),
            (bytes([3 + len(block), gun_id, 0x83, 0x00, 0x00, len(block)]) + block, 0x83, "write"),
            (bytes([0x01, gun_id, 0x86, 0x01]), 0x86, "lock"),
        ]
        try:
            for pkt, expected_cmd, label in steps:
                ok = self._broadcast_step_and_wait_all(
                    pkt, [gun_id], expected_cmd, label=f"profile {label}"
                )
                if not ok:
                    self.log(f"PROFILE WRITE: FAILED at {label} for gun {gun_id:02x}")
                    return False, f"Gun did not acknowledge the {label} step."
        finally:
            self.upload_failed_guns = saved_failed
        verified = self.read_gun_profile(gun_id)
        if verified is None:
            return False, "Write finished but the gun's profile could not be read back."
        if verified.get("name") != name.strip() or verified.get("avatar") != canonical_avatar:
            return (
                False,
                f"Write verification mismatch: gun reports "
                f"{verified.get('name')!r} / {verified.get('avatar')!r}.",
            )
        self.log(f"PROFILE WRITE: gun {gun_id:02x} now stores {name.strip()!r} [{canonical_avatar}]")
        return True, "verified"

    @_serialized
    def start_countdown(self, gun_ids: list[int], seconds: int = 10) -> bool:
        """Send countdown to each gun and wait for `01 <id> 93` echo."""
        for gid in gun_ids:
            pkt = build_countdown_packet(gid, seconds=seconds)
            ok = False
            for attempt in range(1, 4):
                ok_try, reply = self.send_and_expect(pkt, expect_prefix=f"01{gid:02x}93", wait_ms=400)
                if ok_try and reply is not None:
                    self.log(f"Countdown echo received from gun {gid:02x} (try {attempt})")
                    ok = True
                    break
            if not ok:
                self.log(f"FAIL: countdown for gun {gid:02x} - no echo")
                return False
        return True

    @_serialized
    def poll_manual_trigger(self, gun_id: int) -> bool:
        """Send 0xC2 trigger query to gun_id; return True if trigger was pulled (arg[1] == 2)."""
        pkt = bytes([0x00, gun_id & 0xFF, 0xC2])
        ok, reply = self.send_and_expect(pkt, expect_prefix=f"01{gun_id:02x}c2", wait_ms=180)
        if ok and reply is not None and len(reply) >= 4 and reply[3] == 2:
            self.log(f"MANUAL LAUNCH: trigger pulled on gun {gun_id:02x}")
            return True
        return False

    @_serialized
    def send_manual_launch_prompt(self, gun_id: int) -> None:
        """Play the SD17 prompt sound on Player 1's T-Blaster while waiting for manual launch."""
        pkt = bytes([0x04, gun_id & 0xFF, 0xA1, 0x53, 0x44, 0x31, 0x37])
        self.send_and_expect(pkt, expect_prefix=None, wait_ms=80)

    @_serialized
    def send_ping(self) -> None:
        self.send_and_expect(build_ping_packet(), expect_prefix=None, wait_ms=100)

    @_serialized
    def drain_stale_echoes(self, duration_s: float = 0.5) -> int:
        self.dev.set_nonblocking(True)
        drained = 0
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            chunk = self.dev.read(REPORT_LEN + 1, timeout_ms=50)
            if chunk:
                raw = bytes(chunk)
                payload = raw[1:] if len(raw) == REPORT_LEN + 1 else raw
                self.log(f"  drained stale IN: {payload.hex().upper()}")
                drained += 1
            else:
                time.sleep(0.01)
        self.log(f"Drained {drained} stale report(s)")
        return drained

    @_serialized
    def wait_for_report(self, gun_id: int, cmd: int, timeout_ms: int = 500) -> bytes | None:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            rep = self.read_one(timeout_ms=100)
            if rep is None:
                continue
            b = rep[1:] if len(rep) == REPORT_LEN + 1 else rep
            self.log(f"IN  {b.hex().upper()}")
            if len(b) >= 5 and b[2] == 0xC3:
                shooter_gid = b[4]
                self.ubiconnect_hits[shooter_gid] = self.ubiconnect_hits.get(shooter_gid, 0) + 1
                self.log(f"  UbiConnect IR hit (0xC3) from gun {shooter_gid:02x}")
            if len(b) >= 3 and b[1] == gun_id and b[2] == cmd:
                return b
        return None

    @_serialized
    def poll_gun(self, gun_id: int, mode_name: str, downlink_word: int | None = None) -> DecodedStatus | None:
        raw_pkt = build_poll_packet(gun_id, mode_name=mode_name, downlink_word=downlink_word)
        pkt = pad64(raw_pkt)
        self.log(f"OUT {pkt.hex().upper()}")
        self.write_packet(raw_pkt)
        reply = self.wait_for_report(gun_id, 0x95, timeout_ms=350)
        if reply is None:
            return None
        decoded = decode_0x95_status(reply, mode_name=mode_name)
        self._log_status_capture(gun_id, mode_name, reply, decoded)
        return decoded

    def _log_status_capture(self, gun_id: int, mode_name: str, reply: bytes, decoded: DecodedStatus | None) -> None:
        """Capture log for the vest-disconnect / gun-clock investigation.

        Writes one `STATUS95` line whenever a gun's reported words change, and
        otherwise a heartbeat line at most once per second per gun, so a clock
        that stalls (unchanged words across heartbeats) is visible without
        flooding the log. `words` are the BE16 send-variables in order; in
        modes with 4+ vars the 4th is the Lua `gunseconds` (arg[8..9]).
        """
        if decoded is None:
            return
        cache = self.__dict__.setdefault("_status_capture", {})
        now = time.monotonic()
        words = list(decoded.send_words)
        prev = cache.get(gun_id)
        if prev is not None and prev[0] == words and now - prev[1] < 1.0:
            return
        changed = prev is None or prev[0] != words
        cache[gun_id] = (words, now)
        n = len(reply)
        self.log(
            f"STATUS95 gun={gun_id:02x} mode={mode_name} words={words} "
            f"hit_ids={list(decoded.hit_ids)} {'CHANGED' if changed else 'heartbeat'} "
            f"raw={bytes(reply[: min(n, 3 + reply[0])]).hex().upper()}"
        )

    @_serialized
    def send_gameover_clear(self, gun_ids: list[int]) -> None:
        """Release every gun from its end-of-round state (winner/loser LED blink)."""
        self._require_write_permission()
        for gid in gun_ids:
            self.log(f"GAME-OVER CLEAR: gun {gid:02x}")
            self.send_and_expect(build_gameover_clear_packet(gid), expect_prefix=None, wait_ms=100)

    @_serialized
    def send_game_over(self, outcomes: dict[int, bool], mode_name: str = "FreeFrag") -> None:
        for gid, won in outcomes.items():
            pkt = build_gameover_packet(gid, won=won, mode_name=mode_name)
            self.send_and_expect(pkt, expect_prefix=None, wait_ms=100)
