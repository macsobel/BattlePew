"""Shared arena core: player/event state and the ArenaEngine.

Mode-specific rules live in one module per mode under
:mod:`battletag.modes` (see :mod:`battletag.modes.base` for the
interface and :mod:`battletag.modes.registry` for discovery). This
module owns the shared core — pairing, vest sweeps, profiles,
global settings, the audio followup queue, the match clock, the
WebSocket snapshot — and delegates every mode decision to the
active mode object (``self._mode``). Public names are re-exported
by :mod:`battletag.modes` so existing imports keep working.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from battletag.audio import AudioCue, GameMasterAudio, audio_assets_ready
from battletag.hardware import RealHIDTransport, SimulatorTransport, detect_ubiconnect_readonly
from battletag.protocol import AVATAR_NAMES, RFID_CARDS, TEAM_INFO, DecodedStatus

from .registry import MODE_CATALOG, get_mode_class


# Tier 2 Modes (§8) listed for roadmap visibility after Tier 1
TIER2_PREVIEW: list[dict[str, str]] = [
    {"id": "Survivor", "title": "Survivor (Infection)", "origin": "Cut Ubisoft Mode (UASurvivor.lua)", "summary": "Asymmetric zombie/survivor infection where tagged survivors switch teams."},
    {"id": "TakeAndHold", "title": "Take & Hold (King of the Hill)", "origin": "Cut Ubisoft Mode (UATakeAndHold.lua)", "summary": "Scan the central T-Base to claim it; holding team ticks +1 pt/sec."},
    {"id": "CaptureTheFlag", "title": "Capture The Flag", "origin": "Cut Ubisoft Mode", "summary": "Scan enemy T-Base to steal flag and return to your home T-Base."},
    {"id": "VIP", "title": "VIP Escort", "origin": "Host Rule Mode", "summary": "One player per squad has 1 HP and no ammo; VIP tags count 3×."},
    {"id": "Juggernaut", "title": "Juggernaut (1 vs All)", "origin": "Host Rule Mode", "summary": "One boss with 20 HP vs hunters with 3 HP; killer becomes the new Juggernaut."},
    {"id": "Vampire", "title": "Vampire / Life Drain", "origin": "Host Rule Mode", "summary": "Health ticks down every 15s unless you land hits on opponents."},
]


@dataclass
class PlayerState:
    """Live state of a player/gun in the arena."""

    gun_num: int
    gun_id: int
    ref_hex: str
    name: str
    avatar: str
    team_id: int = 1
    paired: bool = True
    vest_ok: bool = True
    # False once the T-Blaster itself stops answering (powered off / out of
    # RF range) - distinct from vest_ok, which is the vest reporting in.
    gun_online: bool = True
    # Match stats
    score: int = 0
    hits_landed: int = 0
    hits_received: int = 0
    frags: int = 0
    deaths: int = 0
    # TagThenShoot splits its score into two disjoint channels: T-Base
    # tags (status word 0) and UbiConnect tower hits (0xC3 reports).
    # `frags` still mirrors the tower-hit total in that mode because the
    # poll downlink sends the running tower-hit count from it.
    tags: int = 0
    tower_hits: int = 0
    # Timed TagThenShoot turns, one entry per completed-or-forfeited
    # run: {"round": 1-based round, "time": seconds | None,
    # "completed": bool, "points": int, "tags": int, "tower_hits": int}.
    # `tags`/`tower_hits` above are the CURRENT run's live counters
    # (reset at each run start); `score` is the TOTAL across runs.
    # Ranking, winner LEDs, and every display derive best time and
    # totals from this history — never from a single-run field.
    run_history: list[dict[str, Any]] = field(default_factory=list)
    shots_fired: int = 0
    streak: int = 0
    best_streak: int = 0
    life_points: int = 5
    max_life: int = 5
    ammo: int = 9
    max_ammo: int = 9
    clips: int = 5
    max_clips: int = 5
    status_badge: str = "READY"  # READY | ACTIVE | OUT (SCAN BASE) | ELIMINATED | WINNER
    last_tagged_by: str = ""
    last_tagged_by_time: float = 0.0
    last_tagged_target: str = ""
    last_rfid: str = ""
    # Last time a 0x95 status report arrived from this gun (0 = none yet
    # this match). The match screen flags a gun whose reports stop
    # arriving mid-match as having lost signal.
    last_status_ts: float = 0.0
    # Last time ANY report arrived from this gun: a 0x95 poll reply, a
    # C1 vest reply, or the pairing handshake. Drives the lobby
    # quiet-timeout; unlike last_status_ts it is not reset per match.
    last_seen_ts: float = 0.0
    # Internal baseline for diffing 0x95 packets
    baseline_set: bool = False
    prev_hits_rec: int = 0
    prev_shots: int = 0
    prev_life: int = 5
    prev_ammo: int = 9
    prev_clips: int = 5
    prev_phase_flag: int = 1
    prev_slots: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    miss_streak: int = 0
    hits_dealt_to: dict[int, int] = field(default_factory=dict)
    hits_taken_from: dict[int, int] = field(default_factory=dict)
    _empty_ammo_warned: bool = False
    _last_empty_warn_ts: float = 0.0
    # True once the low-ammo ("Reload!") line has fired for the current
    # clip; cleared when ammo rises above 3 so it fires once per descent.
    _low_ammo_warned: bool = False

    @property
    def display_name(self) -> str:
        return (self.name or "").upper()

    @property
    def accuracy(self) -> float:
        if self.shots_fired <= 0:
            return 0.0
        return round(min(100.0, (self.hits_landed / self.shots_fired) * 100.0), 1)

    @property
    def best_run(self) -> dict[str, Any] | None:
        """The fastest finished run in run_history, or None (no finish)."""
        finished = [r for r in self.run_history if r.get("completed") and r.get("time") is not None]
        if not finished:
            return None
        return min(finished, key=lambda r: r["time"])

    @property
    def best_run_time(self) -> float | None:
        best = self.best_run
        return None if best is None else float(best["time"])

    @property
    def best_single_run_points(self) -> int:
        """Most points scored in any single recorded run (0 if none)."""
        return max((int(r.get("points", 0)) for r in self.run_history), default=0)

    def run_for_round(self, round_index: int) -> dict[str, Any] | None:
        """The recorded run for 1-based `round_index`, or None."""
        for r in self.run_history:
            if int(r.get("round", 0)) == round_index:
                return r
        return None

    def to_dict(self) -> dict[str, Any]:
        team = TEAM_INFO.get(self.team_id, TEAM_INFO[1])
        return {
            "gun_num": self.gun_num,
            "gun_id": self.gun_id,
            "ref_hex": self.ref_hex,
            "name": self.name,
            "display_name": self.display_name,
            "avatar": self.avatar,
            "avatar_url": f"/static/assets/textures/Avatars/256x/{self.avatar}.png",
            "avatar_80_url": f"/static/assets/textures/Avatars/80x/{self.avatar}.png",
            "avatar_out_url": f"/static/assets/textures/Avatars/80x/{self.avatar}_Out.png",
            "team_id": self.team_id,
            "team_name": team["name"],
            "team_short": team["short"],
            "team_color": team["color"],
            "team_slot_tex": team["slot_tex"],
            "paired": self.paired,
            "vest_ok": self.vest_ok,
            "gun_online": self.gun_online,
            "score": self.score,
            "hits_landed": self.hits_landed,
            "hits_received": self.hits_received,
            "frags": self.frags,
            "deaths": self.deaths,
            "tags": self.tags,
            "tower_hits": self.tower_hits,
            "run_history": [dict(r) for r in self.run_history],
            "best_run_time": self.best_run_time,
            "best_run": dict(self.best_run) if self.best_run is not None else None,
            "shots_fired": self.shots_fired,
            "accuracy": self.accuracy,
            "streak": self.streak,
            "best_streak": self.best_streak,
            "life_points": self.life_points,
            "max_life": self.max_life,
            "ammo": self.ammo,
            "max_ammo": self.max_ammo,
            "clips": self.clips,
            "max_clips": self.max_clips,
            "status_badge": self.status_badge,
            "last_tagged_by": self.last_tagged_by,
            "last_tagged_by_ago": round(time.time() - self.last_tagged_by_time, 1) if self.last_tagged_by_time else None,
            "last_tagged_target": self.last_tagged_target,
            "last_rfid": self.last_rfid,
            "hits_dealt_to": {str(k): v for k, v in self.hits_dealt_to.items()},
            "hits_taken_from": {str(k): v for k, v in self.hits_taken_from.items()},
            # True once a gun that answered this match goes quiet for
            # SIGNAL_LOST_TIMEOUT_S (the poll loop answers every cycle,
            # so 3 s is ~60 missed polls on hardware). Only meaningful
            # mid-match; the match screen gates the warning to the
            # PLAYING phase.
            "signal_lost": self.last_status_ts > 0 and (time.time() - self.last_status_ts) > SIGNAL_LOST_TIMEOUT_S,
        }


@dataclass
class EventEntry:
    """Kill-feed / arena event entry matching the E3 split-screen Events log."""

    id: int
    timestamp: float
    match_clock: str
    kind: str  # "hit" | "frag" | "respawn" | "medkit" | "ammo" | "lead" | "system" | "duel"
    text: str
    actor_name: str = ""
    actor_team: int = 1
    actor_gun_num: int = 0  # player number of the actor (0 = none/system)
    target_name: str = ""
    target_team: int = 2
    target_gun_num: int = 0  # player number of the target (0 = none/system)
    icon_url: str = "/static/assets/textures/Ui/Pictograms/64x/Hud_1.png"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "match_clock": self.match_clock,
            "kind": self.kind,
            "text": self.text,
            "actor_name": self.actor_name,
            "actor_team": self.actor_team,
            "actor_gun_num": self.actor_gun_num,
            "target_name": self.target_name,
            "target_team": self.target_team,
            "target_gun_num": self.target_gun_num,
            "icon_url": self.icon_url,
        }


# Global settings: apply to every mode until changed (see REPO_REVIEW §8).
# beam_power and gun_volume are read at 0x92 config time — the original reads
# gun volume from global game settings, and beam power describes the venue,
# not the mode. The audio volumes use the original 0-8 scale and are applied
# by the browser when it plays each cue.
# gun_volume uses the original's inverted scale: 16 = Off, 4 = Low, 1 = Medium,
# 0 = High (UTGame.Ui.Settings.Sound.lua).
DEFAULT_GLOBAL_SETTINGS: dict[str, int] = {
    "beam_power": 3,
    "gun_volume": 0,
    "music_volume": 5,
    "sfx_volume": 2,
    "announcer_volume": 5,
}

_GLOBAL_SETTING_RANGES: dict[str, tuple[int, int]] = {
    "beam_power": (1, 5),
    "gun_volume": (0, 16),
    "music_volume": (0, 8),
    "sfx_volume": (0, 8),
    "announcer_volume": (0, 8),
}

def get_user_data_dir(app_name: str = "BattlePew") -> Path:
    """Return the platform-appropriate user application data/config directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / app_name
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / app_name


DEFAULT_GLOBAL_SETTINGS_PATH: Path = get_user_data_dir() / "global_settings.json"


# Consecutive silent C1 sweeps before a gun counts as vest-less in the lobby.
# One missed reply is normal RF behavior (a poll can go unanswered and be
# answered on the next cycle), so a single silence never flips the display.
VEST_SILENT_SWEEPS_TO_DISCONNECT = 2
# During a live match, guns are busy streaming 0x95/0x97 traffic and players
# move around, so transient C1 00 or missed C1 replies are much more common.
# Require 4 consecutive misses (~20 s at 5 s sweeps) before warning mid-game.
VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT = 4
# A gun that was acked never re-announces on its own, so dropping a gun that
# merely hit an RF lapse would strand it until a power cycle. Only a long
# lobby silence (~20 s of 2 s sweeps) is treated as "powered off"; before that
# the slot is kept and shown as offline. Never removed outside the LOBBY.
# This sweep-count removal is the backstop behind the 4 s quiet-timeout
# (LOBBY_QUIET_TIMEOUT_S), which normally fires first.
LOBBY_SILENT_SWEEPS_TO_REMOVE = 10
# Lobby quiet-timeout: the original's fast Pinger unregisters a gun after
# ~4 s of silence in setup (ping after 2 s, unregister after 4 s). The
# lobby C1 sweep runs every 2 s, so a live gun is heard from at least
# every ~2.5 s and 4 s leaves a full missed sweep of margin. Guns that
# hit this timeout drop from the roster with a "went quiet" notice; the
# sweep-count removal above stays as the backstop.
LOBBY_QUIET_TIMEOUT_S = 4.0
# Mid-match silence before a gun counts as not answering: polls run every
# cycle (~0.3 s per gun on hardware), so 10 s is ~30 missed polls.
MATCH_DISCONNECT_TIMEOUT_S = 10.0
# A gun that answered this match and then goes quiet for this long is
# flagged signal_lost on the scoreboard.
SIGNAL_LOST_TIMEOUT_S = 3.0


class ArenaEngine:
    """Central Battle Tag match coordinator and state machine."""

    @property
    def mode_name(self) -> str:
        """Active mode id. Assigning it swaps the mode object too, so
        ``mode_name`` and ``_mode`` can never disagree — including for
        direct assignment (tests and the app both do this)."""
        return self._mode_name

    @mode_name.setter
    def mode_name(self, value: str) -> None:
        self._mode_name = value
        # Mode objects are stateless; every mode-specific decision
        # goes through this object, with match state passed as ctx.
        self._mode = get_mode_class(value)()

    def __init__(
        self,
        notify_cb: Callable[[dict[str, Any]], None] | None = None,
        settings_path: Path | None = DEFAULT_GLOBAL_SETTINGS_PATH,
    ) -> None:
        self.notify_cb = notify_cb
        self._settings_path = settings_path
        self.use_simulator: bool = True
        self.hardware_write_confirmed: bool = False
        self.sim = SimulatorTransport(num_guns=2)
        self.hw: RealHIDTransport | None = None
        self.audio = GameMasterAudio(emit_cb=self._on_audio_cue, local_play=False)

        self.mode_name: str = "FreeFrag"
        self.settings: dict[str, Any] = dict(MODE_CATALOG["FreeFrag"]["defaults"])
        self.global_settings: dict[str, int] = self._load_global_settings()
        self.phase: str = "LOBBY"  # LOBBY | PAIRING | VEST_CHECK | UPLOADING | COUNTDOWN | PLAYING | GAME_OVER
        self.bytecode_ready: bool = False
        self.auto_pair_active: bool = True
        # The Arena Master wizard step the main screen is showing (1 = Pair
        # Guns, 2 = Mode & Settings), reported by the frontend. Names and
        # avatars are editable only on step 1 — the pairing page — from
        # either interface, mirroring the original's Player Management
        # editor; going back to step 1 re-opens editing.
        self.wizard_step: int = 1
        # Per-gun profile-write progress for the roster/HUD: gun_num ->
        # {"state": queued|writing|saved|failed, "detail": str}. Updated by
        # the profile-write worker; the UIs render from the snapshot.
        self.profile_write_status: dict[int, dict[str, Any]] = {}
        self.upload_progress: dict[str, int] = {"current": 0, "total": 3}
        self.last_upload_failed_guns: list[int] = []
        # Gun ids whose 0x92 config was never acknowledged after a
        # successful bytecode upload. The host retries just the config
        # for these guns, or removes one and continues with the rest —
        # a single silent gun never nukes the whole launch.
        self.config_failed_guns: list[int] = []
        # True once the current mode's bytecode upload succeeded for the
        # current roster, even if 0x92 config is still pending for some
        # guns. Cleared whenever the roster changes.
        self.bytecode_uploaded: bool = False
        # Ref -> player number remembered across quiet-drops, host
        # Removes, and UbiConnect-loss re-pairing. Shared with the
        # hardware transport so the memory survives transport
        # recreation; see RealHIDTransport.choose_player_number.
        self.ref_number_memory: dict[str, int] = {}
        # Refs the host removed on the pairing page. Shared with the
        # hardware transport for the same reason: a Remove survives a
        # UbiConnect replug; only a fresh pairing scan clears it.
        self.ignored_refs: set[str] = set()
        # Pairing/liveness notes for the pairing page ("went quiet",
        # host Removes, UbiConnect transitions). Separate from the
        # match feed: lobby happenings stay visible in setup without
        # leaking into the next match's kill-feed.
        self.lobby_notices: list[dict[str, Any]] = []
        # Gun numbers whose name/avatar the user explicitly set (roster or
        # phone HUD). Only explicit choices override the gun's stored profile
        # when hardware re-pairs; simulator defaults never win over it.
        self._explicit_profiles: set[int] = set()

        self.players: dict[int, PlayerState] = {}
        self.events: list[EventEntry] = []
        self._event_seq: int = 0
        # Consecutive C1 sweeps each gun id has stayed silent for. Used by
        # apply_vest_states so a single dropped reply never marks a vest
        # disconnected; reset whenever the gun answers a sweep.
        self._vest_miss_counts: dict[int, int] = {}
        self.match_start_ts: float = 0.0
        # Set when a match ends so the elapsed clock on timerless modes
        # freezes at the final time instead of keep counting in GAME_OVER.
        self.match_end_ts: float = 0.0
        self.match_duration_s: int = int(self.settings.get("durationSeconds", 300))
        self.countdown_remaining: int = 0
        self.current_leader_key: str = ""
        self.duel_state: dict[str, Any] = {
            "round": 0,
            "max_rounds": 5,
            "phase_code": 2,  # 2 = between rounds (reload 6 bullets), 0/1 = active standoff/draw
            "subphase": "INTRO",  # INTRO | WAIT | DRAW | ROUND_END | WAIT_TRIGGER | COMPLETE
            "can_shoot": False,
            "next_transition_at": 0.0,
            "round_scores": {},
            # Per-round results, oldest first: {"round": n,
            # "winner_gun_num": g | None, "fault_gun_num": g | None}.
            "round_results": [],
            "last_result_text": "",
            "last_winner_gun_num": None,
            "trigger_armed": False,
        }
        self._duel_round_triggered: bool = False
        # Deferred actions the match loop fires when their time comes:
        # sequenced announcer followups (a second line that must start
        # after the first finishes) and the duel can_shoot flip, which
        # the original sets on completion of the DRAW line. Each entry
        # is {"fire_at": float, "kind": str, ...}; see
        # _schedule_audio_followup / tick_pending_audio. The browser
        # reports no playback-completion events, so every delay here is
        # the host's modeled line length, not a true completion callback.
        self._pending_audio: list[dict[str, Any]] = []
        self.expected_hardware_guns: int = 2
        self.match_history: list[dict[str, Any]] = []
        self.score_history: list[dict[str, Any]] = []
        self.last_action_ts: float = 0.0
        self._last_idle_banter_ts: float = 0.0
        self._last_miss_taunt_ts: float = 0.0
        self._last_reload_reminder_ts: float = 0.0
        self._manual_launch_triggered: bool = False
        # Timed TagThenShoot turns: set when the player whose turn it is
        # pulls the trigger (API endpoint in the simulator, hardware
        # trigger poll on real guns); consumed by tick_race_turns when
        # the wait releases. Separate from _manual_launch_triggered,
        # which releases the match-level wait other modes use.
        self._turn_triggered: bool = False
        # Timed-turn machine state (TagThenShoot only; {} otherwise).
        # Shape is built by reset_stats_for_new_match; see
        # tick_race_turns for the ROUND_INTRO -> WAIT_TRIGGER/COUNTDOWN
        # -> RUNNING -> TURN_END -> DONE machine, looped per round.
        self.race_state: dict[str, Any] = {}
        # Race roster/order, editable only in setup (LOBBY) for
        # TagThenShoot: gun numbers in race order + benched gun
        # numbers. Default: every paired gun, gun-number order, all
        # in. New guns append to the end of the order; removed guns
        # drop out of both lists (sync_race_roster). Frozen into
        # race_state.turn_order at match start; benching never
        # unpairs a gun.
        self.race_roster: dict[str, list[int]] = {"order": [], "bench": []}
        # Best finished run per (gun id, base count) across matches in
        # this server run: {(gun_id, bases): {"time": seconds,
        # "name": display name}}. Keyed by bases so a 2-base time can
        # never overwrite a 4-base best. In-memory only.
        self.race_session_best: dict[tuple[int, int], dict[str, Any]] = {}
        self.results_revealed: bool = False
        self._impatient_timestamps: dict[int, list[float]] = {}
        self._impatient_last_global_ts: float = 0.0
        self._sync_players_from_sim()

    def _on_audio_cue(self, cue: AudioCue) -> None:
        if self.notify_cb:
            self.notify_cb({"type": "audio", "cue": cue.to_dict()})

    def _sync_players_from_sim(self) -> None:
        existing_profiles = {
            p.gun_num: (p.name, p.avatar, p.team_id) for p in self.players.values()
        }
        self.players.clear()
        for gid, sg in sorted(self.sim.guns.items(), key=lambda item: item[1].gun_num):
            saved = existing_profiles.get(sg.gun_num)
            name = saved[0] if saved else sg.name
            avatar = saved[1] if saved else sg.avatar
            team_id = saved[2] if saved else sg.team_id
            sg.name = name
            sg.avatar = avatar
            sg.team_id = team_id
            self.players[gid] = PlayerState(
                gun_num=sg.gun_num,
                gun_id=gid,
                ref_hex=sg.ref_hex,
                name=name,
                avatar=avatar,
                team_id=team_id,
                paired=True,
                vest_ok=sg.vest_connected,
            )

    def set_num_simulated_guns(self, count: int) -> None:
        self.expected_hardware_guns = max(1, min(8, int(count)))
        self.sim.setup_number_of_guns(self.expected_hardware_guns)
        self._sync_players_from_sim()
        self.sync_race_roster()
        self.audio.play_ui("GunDetect.wav", subtitle=f"{self.expected_hardware_guns} T-Blasters configured")

    def sync_race_roster(self) -> dict[str, list[int]]:
        """Reconcile the race roster with the currently paired guns.

        Guns that left drop out of both lists; newly paired guns
        append to the end of the race order (gun-number order among
        themselves) so an existing order is never disturbed. Called
        lazily from the snapshot too, so every roster mutation site
        (pairing, quiet-drops, host Removes, unpair-all) stays
        consistent without each one remembering to call it.
        """
        paired = sorted(p.gun_num for p in self.players.values())
        paired_set = set(paired)
        order = [n for n in self.race_roster.get("order", []) if n in paired_set]
        bench = [n for n in self.race_roster.get("bench", []) if n in paired_set]
        seen = set(order) | set(bench)
        order.extend(n for n in paired if n not in seen)
        self.race_roster = {"order": order, "bench": bench}
        return {"order": list(order), "bench": list(bench)}

    def race_roster_snapshot(self) -> dict[str, list[int]]:
        """The roster as the UIs render it (always reconciled)."""
        return self.sync_race_roster()


    def set_race_roster(
        self, order: list[int], bench: list[int]
    ) -> tuple[bool, str]:
        """Replace the full race roster (TV drag-and-drop / X / re-add)."""
        return self._mode.set_roster(self, order, bench)

    def toggle_race_roster(self, gun_num: int, in_race: bool) -> tuple[bool, str]:
        """Opt one player in or out of the race (phone HUD)."""
        return self._mode.toggle_roster(self, gun_num, in_race)

    def race_roster_players(self) -> list[PlayerState]:
        """Paired players in race order (rostered racers only)."""
        self.sync_race_roster()
        by_num = {p.gun_num: p for p in self.players.values()}
        return [by_num[n] for n in self.race_roster["order"] if n in by_num]

    def race_rosters_gun_ids(self) -> list[int]:
        """Gun ids in race order (rostered racers only)."""
        return [p.gun_id for p in self.race_roster_players()]

    def add_lobby_notice(self, text: str) -> None:
        """Note a pairing/liveness event for the pairing page.

        Newest first, capped at 5. Unlike engine.events, notices are
        never cleared by a match start — they describe the lobby, not
        the match.
        """
        self.lobby_notices.insert(0, {"text": text, "timestamp": time.time()})
        self.lobby_notices = self.lobby_notices[:5]

    def sync_paired_hardware_guns(self, paired_list: list[dict[str, Any]]) -> None:
        """Populate `self.players` with 1..8 physically paired T-Blasters.

        Name and avatar come from the gun's stored profile unless the user
        explicitly set them (roster or phone HUD) — a simulator default must
        never stick over the real stored profile on re-pair. Team assignment
        is always the user's choice, so a previously assigned team is kept.
        Every synced gun was just heard from (pair handshake), so its
        last-seen clock starts now, and its ref->number mapping is
        remembered for a future return. A changed roster invalidates
        any completed upload/config state: the guns must be re-primed.
        """
        saved_by_num = {
            p.gun_num: (p.name, p.avatar, p.team_id) for p in self.players.values()
        }
        self.players.clear()
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        now = time.time()
        for ginfo in sorted(paired_list, key=lambda g: g["gun_num"]):
            gnum = int(ginfo["gun_num"])
            gid = int(ginfo["gun_id"])
            saved = saved_by_num.get(gnum)
            explicit = gnum in self._explicit_profiles
            default_team = ((gnum - 1) % 2) + 1
            name = saved[0] if (saved and explicit) else (ginfo.get("name") or f"Player {gnum}")
            avatar = saved[1] if (saved and explicit) else (ginfo.get("avatar") or AVATAR_NAMES[(gnum - 1) % len(AVATAR_NAMES)])
            team_id = saved[2] if saved else default_team
            ref_hex = ginfo.get("ref_hex", "")
            if ref_hex:
                self.ref_number_memory[ref_hex] = gnum
            self.players[gid] = PlayerState(
                gun_num=gnum,
                gun_id=gid,
                ref_hex=ref_hex,
                name=name,
                avatar=avatar,
                team_id=team_id,
                paired=True,
                vest_ok=True,
                last_seen_ts=now,
            )

    def apply_vest_states(self, states: dict[int, bool]) -> list[tuple[PlayerState, bool]]:
        """Fold one C1 vest sweep into player state.

        In the lobby, an explicit C1 reply sets vest_ok immediately and a
        silent gun is marked vest-less after VEST_SILENT_SWEEPS_TO_DISCONNECT
        consecutive silent sweeps. During an active match (phase != 'LOBBY'),
        both C1 00 replies and silent sweeps require
        VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT consecutive misses before
        flipping vest_ok to False, so transient mid-game glitches do not
        flash vest warnings. Any C1 01 reply restores vest_ok immediately.
        """
        in_match = self.phase != "LOBBY"
        miss_threshold = (
            VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT if in_match else VEST_SILENT_SWEEPS_TO_DISCONNECT
        )
        changed: list[tuple[PlayerState, bool]] = []
        now = time.time()
        for gid, player in self.players.items():
            if gid in states:
                # A C1 reply is a report from the gun: it was heard from.
                player.last_seen_ts = now
                player.gun_online = True
                if states[gid]:
                    self._vest_miss_counts[gid] = 0
                    new_ok = True
                else:
                    if in_match:
                        self._vest_miss_counts[gid] = self._vest_miss_counts.get(gid, 0) + 1
                        if self._vest_miss_counts[gid] < miss_threshold:
                            continue
                    else:
                        self._vest_miss_counts[gid] = 0
                    new_ok = False
            else:
                self._vest_miss_counts[gid] = self._vest_miss_counts.get(gid, 0) + 1
                if self._vest_miss_counts[gid] < miss_threshold:
                    continue
                player.gun_online = False
                new_ok = False
            if new_ok != player.vest_ok:
                player.vest_ok = new_ok
                changed.append((player, new_ok))
        for gid in list(self._vest_miss_counts):
            if gid not in self.players:
                del self._vest_miss_counts[gid]
        return changed

    def remove_quiet_lobby_players(
        self,
        now: float | None = None,
        timeout_s: float = LOBBY_QUIET_TIMEOUT_S,
    ) -> list[PlayerState]:
        """Drop lobby players unheard from for longer than `timeout_s`.

        Mirrors the original's fast Pinger: in setup a gun silent >4 s
        is treated as gone. Only the quiet guns leave — other players
        keep their numbers — and the ref->number memory remembers the
        dropped guns so a returning gun reclaims its number. Never
        removes outside LOBBY, and never removes a gun never heard
        from (last_seen 0): there is no silence to measure. Each
        removal leaves a "went quiet" lobby notice. The caller unbinds
        the transport-side pairing for each removed gun.
        """
        if self.phase != "LOBBY":
            return []
        now = time.time() if now is None else now
        removed: list[PlayerState] = []
        for gid in list(self.players):
            player = self.players[gid]
            if player.last_seen_ts <= 0:
                continue
            if now - player.last_seen_ts > timeout_s:
                removed.append(self.players.pop(gid))
                self._vest_miss_counts.pop(gid, None)
                self.add_lobby_notice(
                    f"Player {player.gun_num} ({player.display_name}) went quiet "
                    "and was removed from the roster."
                )
        return removed

    def remove_player(self, gun_num: int) -> PlayerState | None:
        """Host Remove: drop one player before a match, nothing else.

        LOBBY only — the pairing page is a setup screen, and a match
        never loses a player to a host action. Removes the player,
        clears vest-miss counts and any stale profile-write status,
        and leaves every other player (and their numbers) untouched.
        The transport-side unbind + ignore-listing is the caller's job
        (app layer); the ref->number memory keeps the number for the
        gun's return. Remaining guns keep their uploaded code/config —
        removal never forces a re-upload — and if the removed gun was
        the last one holding up a config retry, the rest are ready.
        Returns the removed player, or None when not allowed/found.
        """
        if self.phase != "LOBBY":
            return None
        gid = next((g for g, p in self.players.items() if p.gun_num == gun_num), None)
        if gid is None:
            return None
        player = self.players.pop(gid)
        self._vest_miss_counts.pop(gid, None)
        self.profile_write_status.pop(gun_num, None)
        if gid in self.config_failed_guns:
            self.config_failed_guns.remove(gid)
            if not self.config_failed_guns and self.bytecode_uploaded:
                self.bytecode_ready = True
        self.add_lobby_notice(
            f"Player {player.gun_num} ({player.display_name}) was removed by "
            "the host. Run a pairing scan to add the gun back."
        )
        return player

    def remove_silent_lobby_players(
        self,
        max_silent_sweeps: int = LOBBY_SILENT_SWEEPS_TO_REMOVE,
    ) -> list[PlayerState]:
        """Remove T-Blasters that have stayed silent across consecutive lobby sweeps.

        Balances temporary RF drops (tolerating up to max_silent_sweeps - 1
        missed C1 sweeps) against powered-off T-Blasters lingering on the lobby
        roster. Never removes players outside the LOBBY phase.
        """
        if self.phase != "LOBBY":
            return []
        removed: list[PlayerState] = []
        for gid in list(self.players):
            if self._vest_miss_counts.get(gid, 0) >= max_silent_sweeps:
                player = self.players.pop(gid)
                self._vest_miss_counts.pop(gid, None)
                removed.append(player)
        return removed

    def select_mode(self, mode_name: str, custom_settings: dict[str, Any] | None = None) -> None:
        if mode_name not in MODE_CATALOG:
            raise ValueError(f"Unknown mode: {mode_name}")
        self.mode_name = mode_name  # setter swaps self._mode too
        self.settings = dict(MODE_CATALOG[mode_name]["defaults"])
        if custom_settings:
            self.settings.update(custom_settings)
        self.match_duration_s = int(self.settings.get("durationSeconds", 300))
        self.bytecode_ready = False
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        self.audio.play_ui("Validation.wav")
        self.audio.play_mode_intro(mode_name)

    def update_settings(self, patch: dict[str, Any]) -> None:
        for k, v in patch.items():
            self.settings[k] = int(v) if isinstance(v, (int, float, str)) and str(v).isdigit() else v
        self.match_duration_s = int(self.settings.get("durationSeconds", 300))
        self.bytecode_ready = False
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        self.audio.play_ui("ChangeValue.wav")

    def _load_global_settings(self) -> dict[str, int]:
        """Load persisted global settings from disk, falling back to defaults."""
        loaded = dict(DEFAULT_GLOBAL_SETTINGS)
        if self._settings_path is None or not self._settings_path.exists():
            return loaded
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return loaded
        if not isinstance(raw, dict):
            return loaded
        for key, value in raw.items():
            if key not in DEFAULT_GLOBAL_SETTINGS:
                continue
            try:
                lo, hi = _GLOBAL_SETTING_RANGES[key]
                loaded[key] = max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                continue
        return loaded

    def _save_global_settings(self) -> None:
        """Persist global settings to disk so they survive server restarts."""
        if self._settings_path is None:
            return
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._settings_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.global_settings, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(self._settings_path)
        except OSError:
            pass

    def update_global_settings(self, patch: dict[str, Any]) -> dict[str, int]:
        """Apply global-setting changes, clamped to each setting's valid range.

        Beam power and gun volume are baked into the 0x92 config sent at
        upload time, so changing either one forces a fresh upload + config
        before the next match. Audio volumes apply immediately (the browser
        scales each cue) and do not invalidate the upload.
        """
        for key, value in patch.items():
            if key not in DEFAULT_GLOBAL_SETTINGS:
                continue
            lo, hi = _GLOBAL_SETTING_RANGES[key]
            self.global_settings[key] = max(lo, min(hi, int(value)))
        if any(k in patch for k in ("beam_power", "gun_volume")):
            self.bytecode_ready = False
            self.bytecode_uploaded = False
            self.config_failed_guns = []
        self._save_global_settings()
        self.audio.play_ui("ChangeValue.wav")
        return dict(self.global_settings)

    IMPATIENT_MAX_PRESSES = 10
    IMPATIENT_WINDOW_S = 120.0
    IMPATIENT_GLOBAL_COOLDOWN_S = 2.5

    def get_impatient_status(self, now: float | None = None) -> dict[str, dict[str, int]]:
        """Return per-player remaining impatient taps and cooldown reset seconds."""
        t = time.time() if now is None else now
        out: dict[str, dict[str, int]] = {}
        for p in self.players.values():
            ts = [x for x in self._impatient_timestamps.get(p.gun_num, []) if t - x < self.IMPATIENT_WINDOW_S]
            self._impatient_timestamps[p.gun_num] = ts
            used = len(ts)
            remaining = max(0, self.IMPATIENT_MAX_PRESSES - used)
            reset_in = int(math.ceil(self.IMPATIENT_WINDOW_S - (t - ts[0]))) if (used >= self.IMPATIENT_MAX_PRESSES and ts) else 0
            out[str(p.gun_num)] = {
                "remaining": remaining,
                "limit": self.IMPATIENT_MAX_PRESSES,
                "reset_in": max(0, reset_in),
            }
        return out

    def trigger_impatient(self, gun_num: int, now: float | None = None) -> dict[str, Any]:
        """Play a pre-game jeer on behalf of an impatient player, with rate limiting."""
        t = time.time() if now is None else now
        player = next((p for p in self.players.values() if p.gun_num == gun_num), None)
        if player is None:
            return {"ok": False, "reason": "unknown_player", "status_code": 404}
        if self.phase not in ("LOBBY", "WAIT_TRIGGER"):
            return {"ok": False, "reason": "invalid_phase", "status_code": 400}

        ts = [x for x in self._impatient_timestamps.get(gun_num, []) if t - x < self.IMPATIENT_WINDOW_S]
        self._impatient_timestamps[gun_num] = ts
        if len(ts) >= self.IMPATIENT_MAX_PRESSES:
            retry_after = max(1, int(math.ceil(self.IMPATIENT_WINDOW_S - (t - ts[0]))))
            return {
                "ok": False,
                "reason": "rate_limit",
                "retry_after": retry_after,
                "remaining": 0,
                "status_code": 429,
            }

        since_global = t - self._impatient_last_global_ts
        if since_global < self.IMPATIENT_GLOBAL_COOLDOWN_S:
            retry_after = max(1, int(math.ceil(self.IMPATIENT_GLOBAL_COOLDOWN_S - since_global)))
            return {
                "ok": False,
                "reason": "global_cooldown",
                "retry_after": retry_after,
                "remaining": self.IMPATIENT_MAX_PRESSES - len(ts),
                "status_code": 429,
            }

        ts.append(t)
        self._impatient_last_global_ts = t
        self.audio.trigger_pool("impatient", force=True)
        return {
            "ok": True,
            "gun_num": player.gun_num,
            "name": player.display_name,
            "avatar": player.avatar,
            "remaining": self.IMPATIENT_MAX_PRESSES - len(ts),
        }

    def set_wizard_step(self, step: int) -> int:
        """Record the wizard step the main screen is showing (1 or 2)."""
        self.wizard_step = 1 if int(step) < 1 else (2 if int(step) > 2 else int(step))
        return self.wizard_step

    def profiles_editable(self) -> bool:
        """Whether names/avatars may change right now, from either interface.

        Only in setup phases (LOBBY / GAME_OVER) AND while the Arena Master
        is on the pairing page (wizard step 1). Once the user moves past
        pairing, editing stays closed until they come back to step 1 —
        the same shape as the team lock after game selection, one screen
        earlier, because a name/avatar save writes to the gun's flash.
        """
        return self.phase in ("LOBBY", "GAME_OVER") and self.wizard_step == 1

    def profiles_locked_reason(self) -> str:
        """Human-readable reason profiles are not editable ('' when editable)."""
        if self.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
            return "Profiles are locked while a match is running."
        if self.wizard_step != 1:
            return "Names and avatars can only be changed from the pairing screen."
        return ""

    def update_player_profile(
        self,
        gun_num: int,
        name: str | None = None,
        avatar: str | None = None,
        team_id: int | None = None,
    ) -> PlayerState | None:
        """Apply a name/avatar/team change, enforcing the phase locks.

        This is the display-update primitive. Name/avatar changes reach it
        from the profile-write worker AFTER the new values have been
        written to the gun's flash (a save in either interface writes to
        the gun; the display follows the gun, never the other way round).
        The /api/profile endpoint itself accepts team changes only.
        The team IS baked into the gun's 0x92 config, so:
        - During UPLOADING / COUNTDOWN / PLAYING every profile change is
          rejected (ValueError) so the scoreboard cannot change mid-game.
        - In setup (LOBBY / GAME_OVER) name/avatar are freely editable.
        - A team change in setup is allowed but invalidates the upload: the
          guns still hold the old team in their config, so the next match
          must re-upload and re-configure before it starts.
        """
        if self.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
            raise ValueError("Profiles are locked while a match is running.")
        target = next((p for p in self.players.values() if p.gun_num == gun_num), None)
        if not target:
            return None
        if name is not None:
            clean = name.strip()[:14]
            if clean:
                target.name = clean
                self._explicit_profiles.add(gun_num)
        if avatar is not None and avatar in AVATAR_NAMES:
            target.avatar = avatar
            self._explicit_profiles.add(gun_num)
        if team_id is not None and int(team_id) in TEAM_INFO:
            if int(team_id) != target.team_id:
                self.bytecode_ready = False
                self.bytecode_uploaded = False
                self.config_failed_guns = []
            target.team_id = int(team_id)
        if target.gun_id in self.sim.guns:
            sg = self.sim.guns[target.gun_id]
            sg.name = target.name
            sg.avatar = target.avatar
            sg.team_id = target.team_id
        self.audio.play_ui("Validation.wav")
        return target

    def mark_profile_stored(self, gun_num: int) -> None:
        """Drop the host-side override after a successful gun profile write.

        The gun now stores exactly what the roster shows, so a future
        re-pairing reads the new profile back from the gun itself — the
        explicit override is no longer needed to protect it.
        """
        self._explicit_profiles.discard(gun_num)

    def _is_timed_mode(self) -> bool:
        """Whether the current mode runs against a match clock.

        OldFashionDuel and LastManStanding are timerless in the
        original (rounds / last-alive endings); every other mode is
        timed. Timerless modes show elapsed time, get no time
        warnings, and never end on the clock.
        """
        return self._mode.timed

    def get_elapsed_seconds(self) -> float:
        if self.match_start_ts <= 0:
            return 0.0
        end = self.match_end_ts if (self.phase == "GAME_OVER" and self.match_end_ts > 0) else time.time()
        return max(0.0, end - self.match_start_ts)

    def format_clock(self) -> str:
        if not self._is_timed_mode():
            elapsed = self.get_elapsed_seconds()
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60
            return f"{mins:02d}:{secs:02d}"
        rem = self.get_remaining_seconds()
        if rem is None:
            return "00:00"
        mins = int(rem) // 60
        secs = int(rem) % 60
        return f"{mins:02d}:{secs:02d}"

    def get_remaining_seconds(self) -> float | None:
        """Seconds left on the match clock, or None for timerless modes."""
        handled, remaining = self._mode.clock_remaining(self)
        if handled:
            return remaining
        if not self._is_timed_mode():
            return None
        if self.phase == "PLAYING":
            elapsed = time.time() - self.match_start_ts
            return max(0.0, self.match_duration_s - elapsed)
        if self.phase == "GAME_OVER":
            return 0.0
        return float(self.match_duration_s)

    def _schedule_audio_followup(self, delay_s: float, kind: str, **payload: Any) -> None:
        """Queue a deferred action for tick_pending_audio to fire.

        Kinds: "pool" (payload: pool_key, subtitle, force),
        "file" (payload: filename, subtitle, priority, duration),
        "duel_can_shoot" (no payload). Delays are modeled line
        lengths — see _pending_audio.

        Race followups may carry "race_run" (the race turn_index at
        queue time): tick_pending_audio drops them unless that run
        is still RUNNING, so a prompt queued for a run that capped
        or was skipped never sounds into the next run.
        """
        entry: dict[str, Any] = {"fire_at": time.time() + max(0.0, delay_s), "kind": kind}
        entry.update(payload)
        self._pending_audio.append(entry)

    def tick_pending_audio(self, now: float | None = None) -> None:
        """Fire due deferred actions. Called once per match-loop cycle."""
        if not self._pending_audio:
            return
        now = time.time() if now is None else now
        due, rest = [], []
        for entry in self._pending_audio:
            (due if entry["fire_at"] <= now else rest).append(entry)
        self._pending_audio = rest
        if self.phase != "PLAYING":
            return
        for entry in due:
            kind = entry["kind"]
            if "race_run" in entry and not self._mode.followup_still_live(self, entry):
                continue
            if kind == "pool":
                self.audio.trigger_pool(
                    entry["pool_key"],
                    custom_subtitle=entry.get("subtitle"),
                    force=bool(entry.get("force", False)),
                )
            elif kind == "file":
                self.audio.play_announcer_file(
                    entry["filename"],
                    entry.get("subtitle", ""),
                    priority=int(entry.get("priority", 0)),
                    duration=float(entry.get("duration", 2.0)),
                )
            else:
                # Other kinds belong to the active mode (the
                # duel's "duel_can_shoot" flip). Unknown kinds
                # are ignored, as before.
                self._mode.on_followup(self, entry)


    def add_event(
        self,
        kind: str,
        text: str,
        actor: PlayerState | None = None,
        target: PlayerState | None = None,
        icon_url: str | None = None,
    ) -> EventEntry:
        # Guarantee all player names appearing in event texts are shown in ALL CAPS
        for p in sorted(self.players.values(), key=lambda pl: len(pl.name or ""), reverse=True):
            if p.name and p.name.strip():
                text = re.sub(re.escape(p.name.strip()), p.name.strip().upper(), text, flags=re.IGNORECASE)

        if icon_url is None:
            if actor is not None and 1 <= actor.gun_num <= 19:
                icon_url = f"/static/assets/textures/Ui/Pictograms/64x/Hud_{actor.gun_num}.png"
            else:
                icon_url = "/static/assets/textures/Ui/Pictograms/64x/Hud_1.png"
        self._event_seq += 1
        entry = EventEntry(
            id=self._event_seq,
            timestamp=time.time(),
            match_clock=self.format_clock(),
            kind=kind,
            text=text,
            actor_name=actor.name if actor else "",
            actor_team=actor.team_id if actor else 1,
            actor_gun_num=actor.gun_num if actor else 0,
            target_name=target.name if target else "",
            target_team=target.team_id if target else 2,
            target_gun_num=target.gun_num if target else 0,
            icon_url=icon_url,
        )
        self.events.insert(0, entry)
        if len(self.events) > 60:
            self.events = self.events[:60]
        return entry

    def reset_stats_for_new_match(self) -> None:
        self.events.clear()
        self._event_seq = 0
        self.current_leader_key = ""
        now = time.time()
        self.last_action_ts = now
        self._last_idle_banter_ts = now
        self._last_miss_taunt_ts = 0.0
        self._last_reload_reminder_ts = 0.0
        self._manual_launch_triggered = False
        self._turn_triggered = False
        self.results_revealed = False
        self.match_end_ts = 0.0
        self._pending_audio = []
        self.audio.reset_match_timers()
        team_map = {gid: p.team_id for gid, p in self.players.items()}
        self.sim.reset_for_mode(self.mode_name, self.settings, team_map=team_map)
        # The 0xC3 tower-hit counter on the hardware transport is
        # cumulative across the transport's lifetime; clear it every
        # match or the first poll of the next TagThenShoot match
        # credits phantom hits equal to the previous match's total.
        if self.hw is not None:
            self.hw.ubiconnect_hits.clear()

        max_life = int(self.settings.get("lifePoints", 5))
        max_ammo, max_clips = self._mode.ammo_caps(self)
        num_players = len(self.players)

        for p in self.players.values():
            p.score = self._mode.initial_score(self, p, max_life, num_players)
            p.hits_landed = 0
            p.hits_received = 0
            p.frags = 0
            p.deaths = 0
            p.tags = 0
            p.tower_hits = 0
            p.run_history = []
            p.shots_fired = 0
            p.streak = 0
            p.best_streak = 0
            p.hits_dealt_to.clear()
            p.hits_taken_from.clear()
            p.life_points = max_life
            p.max_life = max_life
            p.ammo = max_ammo
            p.max_ammo = max_ammo
            p.clips = max_clips
            p.max_clips = max_clips
            p.status_badge = "ACTIVE"
            p.miss_streak = 0
            p._empty_ammo_warned = False
            p._last_empty_warn_ts = 0.0
            p._low_ammo_warned = False
            p.last_tagged_by = ""
            p.last_tagged_by_time = 0.0
            p.last_tagged_target = ""
            p.last_rfid = ""
            p.last_status_ts = 0.0
            p.baseline_set = False
            p.prev_slots = [0, 0, 0, 0, 0]

        self.score_history = [
            {"t": 0.0, "scores": {str(p.gun_num): p.score for p in self.players.values()}}
        ]

        self._mode.on_match_start(self)


    def tick_mode_timers(self) -> None:
        """Advance the active mode's round/timer machine once per cycle."""
        self._mode.on_timer_tick(self)


    # Round announcement lines, shared with the duel (PR #15): the same
    # GLOBAL_114/115/116 files, with the duel's modeled durations.
    # GLOBAL_113 "NEXT GAME!" stays unwired everywhere.

    # Player-number callout duration: the duel's GLOBAL_54+gun_num
    # files ("Player 1!".."Player 16!"). Modeled duration covers the
    # longest line in the set (GLOBAL_67 "Player 13", 1.83 s) so the
    # callout window never overlaps the countdown numbers.


    def tick_race_turns(self, now: float | None = None) -> None:
        """Advance the active mode's turn machine once per cycle."""
        self._mode.on_turn_tick(self, now)


    def skip_race_run(self, now: float | None = None) -> tuple[bool, str]:
        """Host skip: forfeit the current race run and move on."""
        return self._mode.skip_run(self, now)

    def finalize_race_on_stop(self, now: float | None = None) -> bool:
        """Finalize mode state when the match is stopped (the race
        snapshots a live run so its points are not lost)."""
        return self._mode.on_match_end(self, now)

    def race_next_gun_id(self) -> int | None:
        """The following racer's gun id (UP NEXT tag), or None."""
        return self._mode.race_next_gun_id(self)


    def race_ranked_players(self) -> list[PlayerState]:
        """Rostered racers in race rank order (benched excluded)."""
        return self._mode.race_ranked_players(self)

    def race_winners(self) -> list[PlayerState]:
        """Race winners under the shared-win rules."""
        return self._mode.race_winners(self)


    def ingest_ubiconnect_hits(self, hits_by_gid: dict[int, int]) -> None:
        """Process 0xC3 IR shots received directly by the UbiConnect
        tower, per the active mode's rules (only the race scores
        them)."""
        self._mode.on_tower_hits(self, hits_by_gid)

    def ingest_status_report(self, status: DecodedStatus) -> None:
        """Process a single gun's 0x95 status report and update scores, streaks, and kill-feed."""
        player = self.players.get(status.gun_id)
        if not player:
            return
        now = time.time()
        player.last_status_ts = now
        player.last_seen_ts = now

        # 1. Establish t=0 baseline so residual shift-register values are ignored
        if not player.baseline_set:
            player.baseline_set = True
            player.prev_hits_rec = status.hits_received or 0
            player.prev_shots = status.shots_fired or 0
            player.prev_life = status.life_points if status.life_points is not None else player.max_life
            player.prev_ammo = status.ammo if status.ammo is not None else player.max_ammo
            player.prev_clips = status.clips if status.clips is not None else player.max_clips
            player.prev_phase_flag = status.phase_flag if status.phase_flag is not None else 1
            player.prev_slots = list(status.hit_ids)
            return

        # 2. Update shots fired
        shots_delta = 0
        if status.shots_fired is not None:
            if status.shots_fired >= player.prev_shots:
                shots_delta = status.shots_fired - player.prev_shots
                player.shots_fired += shots_delta
            player.prev_shots = status.shots_fired
        elif status.ammo is not None:
            # Derive shots from ammo drop in modes that don't send nb_shot directly
            if status.clips == player.prev_clips and status.ammo < player.prev_ammo:
                shots_delta = player.prev_ammo - status.ammo
                player.shots_fired += shots_delta

        if shots_delta > 0:
            now = time.time()
            self.last_action_ts = now
            player.miss_streak += shots_delta
            # The original's blind/aim taunt fires after repeated
            # misses; the threshold is 5, matching its blind rule.
            # Gated off in TagThenShoot: tower shots are not misses,
            # and tower hits never reset the streak, so the taunt
            # would fire mid-run (worse across multiple rounds).
            if (
                self._mode.miss_taunt_enabled
                and player.miss_streak >= 5
                and now - self._last_miss_taunt_ts >= 9.0
            ):
                player.miss_streak = 0
                self._last_miss_taunt_ts = now
                self.audio.trigger_pool(
                    "miss_taunt",
                    custom_subtitle=f"{player.display_name} — need some help aiming?",
                )

        # Update live ammo & clips readout
        if status.clips is not None:
            player.clips = status.clips
            player.prev_clips = status.clips

        ammo_dropped = False
        if status.ammo is not None:
            ammo_dropped = status.ammo < player.prev_ammo
            if (
                self._mode.ammo_card_events
                and status.ammo > player.prev_ammo
                and (status.clips or 0) >= player.prev_clips
            ):
                player.last_rfid = "RF01"
                self.add_event(
                    "ammo",
                    f"{player.display_name} scanned an Ammo Card — clips reloaded!",
                    actor=player,
                    icon_url=RFID_CARDS["RF01"]["icon"],
                )
            player.ammo = status.ammo
            player.prev_ammo = status.ammo
            finite_ammo_mode = self._mode.finite_ammo
            # The original's low-ammo event fires when ammo reaches
            # exactly 3, once per descent (a separate trigger from the
            # empty-ammo reminder below).
            if finite_ammo_mode and status.ammo == 3 and not player._low_ammo_warned:
                player._low_ammo_warned = True
                self.audio.trigger_pool("reload_reminder")
            elif status.ammo > 3:
                player._low_ammo_warned = False
            if finite_ammo_mode and player.ammo == 0 and player.clips == 0:
                now = time.time()
                if (
                    (not player._empty_ammo_warned or now - player._last_empty_warn_ts >= 25.0)
                    and now - self._last_reload_reminder_ts >= 25.0
                    and now - self.match_start_ts >= 5.0
                ):
                    player._empty_ammo_warned = True
                    player._last_empty_warn_ts = now
                    self._last_reload_reminder_ts = now
                    self.audio.trigger_pool("reload_reminder")
            elif player.ammo > 0 or player.clips > 0:
                player._empty_ammo_warned = False

        # Mode-specific hit & frag resolution lives in the mode.
        self._mode.on_status_report(self, player, status, ammo_dropped)

        player.prev_slots = list(status.hit_ids)


    def _record_hit(self, shooter: PlayerState, victim: PlayerState, is_frag: bool = False) -> None:
        """Score one landed hit through the active mode's rules."""
        self._mode.record_hit(self, shooter, victim, is_frag=is_frag)

    def _check_lead_change(self) -> None:
        """Fire a lead-change line when the leader changes, per the
        active mode's rules (team standings, player ranking, or no
        lead concept at all)."""
        self._mode.on_lead_check(self)

    def get_sorted_players(self) -> list[PlayerState]:
        """Players in scoreboard rank order, per the active mode."""
        return self._mode.sort_players(self, list(self.players.values()))

    def get_team_standings(self) -> list[dict[str, Any]]:
        active_teams: dict[int, dict[str, Any]] = {}
        for p in self.players.values():
            tid = p.team_id
            if tid not in active_teams:
                tinfo = TEAM_INFO.get(tid, TEAM_INFO[1])
                active_teams[tid] = {
                    "team_id": tid,
                    "name": tinfo["name"],
                    "short": tinfo["short"],
                    "color": tinfo["color"],
                    "emblem_tex": tinfo["emblem_tex"],
                    "leader_tex": tinfo["leader_tex"],
                    "slot_tex": tinfo["slot_tex"],
                    "score": 0,
                    "hits_landed": 0,
                    "frags": 0,
                    "players": [],
                }
            active_teams[tid]["score"] += p.score
            active_teams[tid]["hits_landed"] += p.hits_landed
            active_teams[tid]["frags"] += p.frags
            active_teams[tid]["players"].append(p.to_dict())

        return sorted(
            active_teams.values(),
            key=lambda t: (t["score"], t["hits_landed"], t["frags"]),
            reverse=True,
        )

    def compute_gameover_outcomes(self) -> dict[int, bool]:
        """Compute per-gun `won: bool` for 0x94 game-over packets.

        Per freefrag_single_gun.py:
        - Outright top scorer(s) get `True` (green LED `0x01`).
        - Ties at the top or losers get `False` (red LED `0x02`).
        Each mode defines its own winner rule; see the mode modules.
        """
        if not self.players:
            return {}
        return self._mode.gameover_outcomes(self)

    def check_idle_announcer(self) -> None:
        """Fire idle banter when no shots/hits happen for a stretch during PLAYING."""
        # Duel is excluded (its standoff silence is the game), and so
        # is the race: tags do not count as action, so banter would
        # fire during trigger waits and slow walks between bases.
        if self.phase != "PLAYING" or not self._mode.idle_banter_enabled:
            return
        now = time.time()
        remaining = self.get_remaining_seconds()
        if remaining is not None and remaining <= 12:
            return
        if now - self.last_action_ts >= 8.0 and now - self._last_idle_banter_ts >= 10.0:
            self._last_idle_banter_ts = now
            self.audio.trigger_pool("idle_banter")

    def get_disconnect_prompt(self, now: float | None = None) -> dict[str, Any] | None:
        """Mid-match disconnect prompt, or None while the match looks healthy.

        Never auto-removes and never auto-ends: the host decides, from
        the prompt. Two policies, following the original's per-mode
        behavior (its Pinger is protected mid-match, so a dropout ends
        the match only through the host-facing prompt):
        - "mode_critical" (duel, race): any scoped player going silent
          prompts at once — these modes need every gun answering.
        - "last_gun" (other modes): prompt once at most one gun is
          still answering after the disconnect timeout.
        A gun counts as silent when it answered this match and then
        went quiet past the threshold, or never answered at all once
        the match is older than the threshold — so the prompt never
        flashes in the first seconds while polls are still starting.
        Silent players' scores are frozen by construction: scores only
        change when a status report arrives.
        """
        if self.phase != "PLAYING" or not self.players:
            return None
        now = time.time() if now is None else now
        match_age = now - self.match_start_ts if self.match_start_ts > 0 else 0.0

        def silent_for(player: PlayerState, threshold: float) -> bool:
            if player.last_status_ts > 0:
                return (now - player.last_status_ts) > threshold
            return match_age > threshold

        scoped = self._mode.disconnect_players(self)
        if self._mode.disconnect_policy == "mode_critical":
            silent = [p for p in scoped if silent_for(p, SIGNAL_LOST_TIMEOUT_S)]
            if silent:
                return {
                    "reason": "mode_critical",
                    "silent_gun_nums": [p.gun_num for p in silent],
                    "silent_names": [p.display_name for p in silent],
                    "answering_count": len(scoped) - len(silent),
                    "total_count": len(scoped),
                }
            return None
        silent = [p for p in scoped if silent_for(p, MATCH_DISCONNECT_TIMEOUT_S)]
        answering_count = len(scoped) - len(silent)
        if len(scoped) >= 2 and answering_count <= 1:
            return {
                "reason": "last_gun",
                "silent_gun_nums": [p.gun_num for p in silent],
                "silent_names": [p.display_name for p in silent],
                "answering_count": answering_count,
                "total_count": len(scoped),
            }
        return None

    def check_auto_end_conditions(self) -> bool:
        """Return True if the match should transition to GAME_OVER."""
        if self.phase != "PLAYING":
            return False
        # Timerless modes (duel, LMS) never end on the clock; the
        # original has no stalemate rule for them either. TagThenShoot
        # is excluded too: its cap belongs to each timed turn, enforced
        # by the turn machine, not to the match.
        if (
            self._is_timed_mode()
            and self._mode.match_clock_ends_match
            and (self.get_remaining_seconds() or 0.0) <= 0
        ):
            return True
        score_limit = int(self.settings.get("scoreLimit", 0))
        if score_limit > 0 and any(p.score >= score_limit for p in self.players.values()):
            return True
        return self._mode.check_end_condition(self)

    def record_score_snapshot(self, force: bool = False) -> None:
        """Record a (t, scores) sample when any score changes or when forced at match end."""
        if not self.players:
            return
        elapsed = round(max(0.0, time.time() - self.match_start_ts), 1) if self.match_start_ts > 0 else 0.0
        scores = {str(p.gun_num): p.score for p in self.players.values()}
        if self.score_history:
            last = self.score_history[-1]
            if not force and last["scores"] == scores:
                return
            if abs(elapsed - last["t"]) < 0.1:
                last["scores"] = scores
                return
        self.score_history.append({"t": elapsed, "scores": scores})

    def get_snapshot(self, base_url: str = "http://localhost:3466") -> dict[str, Any]:
        now = time.time()
        if self.phase == "PLAYING":
            self.record_score_snapshot()

        ranked = [p.to_dict() for p in self.get_sorted_players()]
        teams = self.get_team_standings() if MODE_CATALOG[self.mode_name]["teams"] else []
        remaining = self.get_remaining_seconds()
        # Display contract: the active mode supplies the state
        # slice its screens need (duel strip, race turns, session
        # bests); the snapshot merges it verbatim.
        display = self._mode.display_state(self)

        return {
            "phase": self.phase,
            "wizard_step": self.wizard_step,
            "profile_write_status": {str(k): dict(v) for k, v in self.profile_write_status.items()},
            "bytecode_ready": self.bytecode_ready,
            "auto_pair_active": self.auto_pair_active,
            "use_simulator": self.use_simulator,
            "audio_assets_ready": audio_assets_ready(),
            "hardware_write_confirmed": self.hardware_write_confirmed,
            "usb_status": detect_ubiconnect_readonly(),
            "mode": MODE_CATALOG[self.mode_name],
            "settings": self.settings,
            "global_settings": dict(self.global_settings),
            "upload_failed_guns": list(self.last_upload_failed_guns),
            "clock": self.format_clock(),
            # None for timerless modes (duel, LMS): the clock counts up.
            "remaining_seconds": None if remaining is None else round(remaining, 1),
            "countdown_remaining": self.countdown_remaining,
            "upload_progress": self.upload_progress,
            "players": ranked,
            "teams": teams,
            "events": [e.to_dict() for e in self.events[:25]],
            "disconnect_prompt": self.get_disconnect_prompt(now),
            "lobby_notices": list(self.lobby_notices),
            "config_retry_pending": [
                self.players[g].gun_num for g in self.config_failed_guns if g in self.players
            ],
            "impatient_status": self.get_impatient_status(now),
            "duel_state": display["duel_state"],
            "race_state": display["race_state"],
            "race_roster": self.race_roster_snapshot(),
            "race_session_best": display["race_session_best"],
            "avatars": AVATAR_NAMES,
            "team_catalog": list(TEAM_INFO.values()),
            "rfid_catalog": list(RFID_CARDS.values()),
            "mode_catalog": list(MODE_CATALOG.values()),
            "tier2_preview": TIER2_PREVIEW,
            "base_url": base_url,
            "match_history": self.match_history[-5:],
            "score_history": list(self.score_history),
            "results_revealed": self.results_revealed,
        }

