"""Game Mode definitions, scoring rules, and ArenaEngine state machine."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from battletag.audio import AudioCue, GameMasterAudio, audio_assets_ready
from battletag.hardware import RealHIDTransport, SimulatorTransport, detect_ubiconnect_readonly
from battletag.protocol import (
    AVATAR_NAMES,
    DecodedStatus,
    RFID_CARDS,
    TEAM_INFO,
    count_new_shooters,
)

# Original Main.loc kill-feed templates (ingame001-021)
KILL_VERBS = [
    "tagged",
    "blasted",
    "zapped",
    "ambushed",
    "outgunned",
    "sniped",
    "locked onto",
]

FRAG_VERBS = [
    "eliminated",
    "knocked out",
    "fragged",
    "neutralized",
    "dominated",
]

# All 7 Tier 1 modes in implementation order: FreeFrag -> TeamFrag -> rest
MODE_CATALOG: dict[str, dict[str, Any]] = {
    "FreeFrag": {
        "id": "FreeFrag",
        "title": "Free Frag",
        "subtitle": "Instant Action · Infinite Ammo · No Reloads",
        "tier": 1,
        "badge": "Arcade Action",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeFrag.png",
        "banner": "/static/assets/textures/Packages/FreeFrag/Ui/Loading_FreeFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeFrag.png",
        "description": "Pure laser tag action with zero setup cards required. Infinite life and infinite ammo — every hit landed scores +1 point. Highest score when the timer hits 0:00 wins!",
        "bullets": [
            "Infinite life & infinite ammo",
            "No setup boxes required",
            "+1 pt per hit · Highest score wins",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "scoreLimit": 0,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "scoreLimit", "label": "Hit Limit (0 = Timed)", "choices": [0, 10, 20, 30, 50]},
        ],
    },
    "TeamFrag": {
        "id": "TeamFrag",
        "title": "Team Frag",
        "subtitle": "2–4 Teams · Life Points · T-Base Respawn",
        "tier": 1,
        "badge": "Team Battle",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": True,
        "needs_accessories": True,
        "accessories": ["ammo", "tbase", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TeamFrag.png",
        "banner": "/static/assets/textures/Packages/TeamFrag/Ui/Loading_TeamFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TeamFrag.png",
        "description": "Squad up into Red, Blue, Yellow, or Green teams! Every hit scores +1 point and depletes enemy Life Points; knocking a player to 0 HP scores a +3 Frag bonus. Eliminated players scan their team's T-Base (RF03–RF06) to respawn!",
        "bullets": [
            "Red, Blue, Yellow & Green teams",
            "+1 pt per hit · +3 pt Frag bonus",
            "Scan team T-Base to respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 5,
            "swap": 0,
            "respawnTime": 0,
            "teamFrag": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 3, 5, 6]},
            {"key": "teamFrag", "label": "Friendly Fire", "choices": [0, 1]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    },
    "FreeForAll": {
        "id": "FreeForAll",
        "title": "Free For All",
        "subtitle": "Every Player for Themselves · Life + Clips + Respawn",
        "tier": 1,
        "badge": "Free For All",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo", "tbase", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeForAll.png",
        "banner": "/static/assets/textures/Packages/FreeForAll/Ui/Loading_FreeForAll.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeForAll.png",
        "description": "Every player for themselves! 2–8 players fight solo with full Life Points, Ammo clips, and T-Base respawn. Score +1 per hit landed and +3 bonus per Frag.",
        "bullets": [
            "Every player for themselves",
            "+1 pt per hit · +3 pt Frag bonus",
            "Limited life & ammo · T-Base respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "respawnTime": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5, 6]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    },
    "LastManStanding": {
        "id": "LastManStanding",
        "title": "Last Man Standing",
        "subtitle": "Survival Elimination · No Respawns · Final Survivor Wins",
        "tier": 1,
        "badge": "Elimination",
        # The original has no timer: the match ends when one player is
        # left alive. No duration setting exists for this mode.
        "timed": False,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/LastManStanding.png",
        "banner": "/static/assets/textures/Packages/LastManStanding/Ui/Loading_LastManStanding.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_LastManStanding.png",
        "description": "High-stakes survival! Players have limited Life Points and NO T-Base respawns. Once your HP hits 0, you are eliminated — the last player standing takes victory!",
        "bullets": [
            "Limited Life Points & ammo",
            "No T-Base respawns",
            "Last player standing wins",
        ],
        "defaults": {
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "assist": 1,
            "medkit": 0,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Starting Lives", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
        ],
    },
    "OldFashionDuel": {
        "id": "OldFashionDuel",
        "title": "Wild West Duel",
        "subtitle": "2-Player Showdown · 5 Rounds · 6 Bullets per Round",
        "tier": 1,
        "badge": "Quickdraw",
        # The original runs purely on rounds with no timer at all.
        "timed": False,
        "min_players": 2,
        "max_players": 2,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/OldFashionDuel.png",
        "banner": "/static/assets/textures/Packages/OldFashionDuel/Ui/Loading_OldFashionDuel.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_OldFashionDuel.png",
        "description": "Classic 2-player showdown across 5 tense rounds! Point your T-Blaster down during the 5–8s standoff; when the announcer yells DRAW, fire first — you have 6 bullets per round. Hitting your opponent before the signal forfeits the round!",
        "bullets": [
            "1v1 quickdraw standoff",
            "6 bullets per round · Fire on DRAW",
            "Hitting early forfeits the round",
        ],
        "defaults": {
            "countdownSeconds": 5,
            "rounds": 5,
        },
        "options": [
            {"key": "rounds", "label": "Number of Rounds", "choices": [3, 5, 7]},
        ],
    },
    "StarterFrag": {
        "id": "StarterFrag",
        "title": "Starter Frag",
        "subtitle": "Infinite Life · Reloads with Ammo Cards",
        "tier": 1,
        "badge": "Card Reload",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/StarterFrag.png",
        "banner": "/static/assets/textures/Packages/StarterFrag/Ui/Loading_StarterFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_StarterFrag.png",
        "description": "Bridges Free Frag and full tactical modes: players have infinite Life Points, but limited ammunition that must be reloaded by scanning physical Ammo Boxes!",
        "bullets": [
            "Infinite Life Points",
            "Limited ammo · Scan Ammo Box to reload",
            "+1 pt per hit · Highest score wins",
        ],
        # The original fixes all three settings (playtime 5 min, ammo
        # 12, clips 3) with no UI options; only the launch mode varies.
        "defaults": {
            "durationSeconds": 300,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "ammunitions": 12,
            "clips": 3,
            "assist": 1,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
        ],
    },
    "TagThenShoot": {
        "id": "TagThenShoot",
        "title": "Tag'n Shoot",
        "subtitle": "Timed turns: pull the trigger, run the course, beat the clock",
        "tier": 1,
        "badge": "Solo Course",
        "timed": True,
        "min_players": 1,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["tbase"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TagNShoot.png",
        "banner": "/static/assets/textures/Packages/TagThenShoot/Ui/Loading_TagNShoot.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TagNShoot.png",
        "description": "Solo timed turns! Each player pulls the trigger to start their run, scans the T-Bases in order, and shoots the UbiConnect tower after each one. Fastest finished course wins — points still count if the 60-second cap runs out!",
        "bullets": [
            "Each player runs the course solo, in turn",
            "Pull the trigger to start your run",
            "Fastest finished course wins (60 s cap)",
        ],
        "defaults": {
            "durationSeconds": 60,
            "countdownSeconds": 3,
            "gameLaunch": 1,
            "numberOfBase": 4,
            "assist": 1,
        },
        "options": [
            # Fixed 60 s in the original: no duration option. Bases are
            # 2, or 4 with the medkit pack.
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "numberOfBase", "label": "T-Bases Required", "choices": [2, 4]},
        ],
    },
}

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
    # Timed TagThenShoot turns: the finished run's elapsed seconds, or
    # None when the run never completed (cap expiry). Set at turn end;
    # reset every match. Ranking, winner LEDs, and the results screen
    # all read these, so they ride in to_dict with the other stats.
    run_time: float | None = None
    run_completed: bool = False
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
            "run_time": self.run_time,
            "run_completed": self.run_completed,
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
            "subphase": "INTRO",  # INTRO | WAIT | DRAW | ROUND_END
            "can_shoot": False,
            "next_transition_at": 0.0,
            "round_scores": {},
            # Per-round results, oldest first: {"round": n,
            # "winner_gun_num": g | None} — None means the round was a draw.
            "round_results": [],
        }
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
        # tick_race_turns for the WAIT_TRIGGER -> COUNTDOWN -> RUNNING
        # -> TURN_END -> DONE machine.
        self.race_state: dict[str, Any] = {}
        # Best finished run per gun id across matches in this server
        # run: {gun_id: {"time": seconds, "name": display name}}.
        # In-memory only; no persistent personal-best storage yet.
        self.race_session_best: dict[int, dict[str, Any]] = {}
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
        self.audio.play_ui("GunDetect.wav", subtitle=f"{self.expected_hardware_guns} T-Blasters configured")

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
        self.mode_name = mode_name
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
        return bool(MODE_CATALOG.get(self.mode_name, {}).get("timed", True))

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
        if not self._is_timed_mode():
            return None
        if self.mode_name == "TagThenShoot":
            # Timed turns: the clock is the active run's cap, not the
            # match. Outside a live run it shows the full cap, so the
            # 60 s warning stays skipped and the match never ends on
            # this clock — the turn machine owns the cap.
            rs = self.race_state
            if self.phase == "PLAYING" and rs and rs.get("turn_phase") == "RUNNING":
                return max(0.0, rs.get("turn_ends_at", 0.0) - time.time())
            if self.phase == "GAME_OVER":
                return 0.0
            return float(self.match_duration_s)
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
            elif kind == "duel_can_shoot":
                # Only arm shooting while the round is still in DRAW;
                # a hit that already resolved the round must not
                # re-arm it.
                if self.duel_state.get("subphase") == "DRAW":
                    self.duel_state["can_shoot"] = True

    def _record_duel_round_result(self, winner: PlayerState | None) -> None:
        """Record one finished duel round for the round display.

        `winner` is the round-winning player, or None for a draw.
        Totals in round_scores and the per-round history in
        round_results stay in step.
        """
        rnd = int(self.duel_state.get("round", 0))
        if winner is not None:
            scores = self.duel_state.setdefault("round_scores", {})
            scores[winner.gun_id] = scores.get(winner.gun_id, 0) + 1
        self.duel_state.setdefault("round_results", []).append(
            {"round": rnd, "winner_gun_num": winner.gun_num if winner else None}
        )

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
        self.score_history = [
            {"t": 0.0, "scores": {str(p.gun_num): 0 for p in self.players.values()}}
        ]
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
        max_ammo = 6 if self.mode_name == "OldFashionDuel" else int(self.settings.get("ammunitions", 9))
        max_clips = 0 if self.mode_name == "OldFashionDuel" else int(self.settings.get("clips", 3))

        for p in self.players.values():
            p.score = 0
            p.hits_landed = 0
            p.hits_received = 0
            p.frags = 0
            p.deaths = 0
            p.tags = 0
            p.tower_hits = 0
            p.run_time = None
            p.run_completed = False
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

        if self.mode_name == "OldFashionDuel":
            self.duel_state = {
                "round": 0,
                "max_rounds": int(self.settings.get("rounds", 5)),
                "phase_code": 2,
                "subphase": "INTRO",
                "can_shoot": False,
                "next_transition_at": time.time() + 3.0,
                "round_scores": {gid: 0 for gid in self.players},
                "round_results": [],
            }

        if self.mode_name == "TagThenShoot":
            order = sorted(self.players.keys(), key=lambda gid: self.players[gid].gun_num)
            first = order[0] if order else None
            self.race_state = {
                "turn_order": order,
                "turn_index": 0,
                "turn_phase": "WAIT_TRIGGER" if order else "DONE",
                "active_gun_id": first,
                # False until a trigger poll observes the trigger
                # released, so a trigger held over from the previous
                # turn cannot auto-release the next wait.
                "trigger_armed": False,
                "countdown_seconds": 0,
                "countdown_remaining": 0,
                "countdown_ends_at": 0.0,
                "countdown_sent": True,
                "next_number": 0,
                "next_number_at": 0.0,
                "turn_started_at": 0.0,
                "turn_ends_at": 0.0,
                "beat_ends_at": 0.0,
                "elapsed": 0.0,
                "turn_cap_s": int(self.settings.get("durationSeconds", 60)),
                "bases": max(1, int(self.settings.get("numberOfBase", 4))),
            }
            if first is not None:
                fp = self.players[first]
                self.add_event(
                    "system",
                    f"{fp.display_name} is up first — pull the trigger to start the run!",
                )
        else:
            self.race_state = {}

    def tick_mode_timers(self) -> None:
        """Advance OldFashionDuel round subphases (`INTRO`/`ROUND_END` phase=2 -> `WAIT` phase=1 -> `DRAW`)."""
        if self.phase != "PLAYING" or self.mode_name != "OldFashionDuel":
            return
        now = time.time()
        sub = self.duel_state.get("subphase", "INTRO")
        if now >= self.duel_state.get("next_transition_at", 0.0):
            if sub in ("INTRO", "ROUND_END"):
                next_rnd = int(self.duel_state.get("round", 0)) + 1
                max_rnd = int(self.duel_state.get("max_rounds", 5))
                if next_rnd > max_rnd:
                    self.duel_state["subphase"] = "COMPLETE"
                    return
                self.duel_state["round"] = next_rnd
                self.duel_state["phase_code"] = 1
                self.duel_state["subphase"] = "WAIT"
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = now + random.uniform(5.0, 8.0)
                for p in self.players.values():
                    p.ammo = 6
                    p.prev_ammo = 6
                if self.use_simulator:
                    for sg in self.sim.guns.values():
                        sg.ammo = 6
                # Round 1 opens with the setup intros (OW_DUEL_01/02),
                # which the original plays once at PlayersSetup; later
                # rounds use the per-round position calls (03/04/05).
                self.audio.trigger_pool(
                    "duel_setup" if next_rnd == 1 else "duel_prepare", force=True
                )
            elif sub == "WAIT":
                self.duel_state["subphase"] = "DRAW"
                self.duel_state["phase_code"] = 1
                # Shooting opens when the DRAW line finishes, not when
                # it starts: the original sets canShoot on completion
                # of the Go line. No completion callback exists, so
                # arm shooting at the end of the host's modeled line
                # window (the pool's active_until, ~2.4 s, covers both
                # DRAW recordings); fall back to 1 s when no cue
                # emitted (assets absent).
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = now + 8.0
                cue = self.audio.trigger_pool("duel_draw", force=True)
                if cue is not None:
                    delay = max(0.1, self.audio.active_until - now)
                else:
                    delay = 1.0
                self._schedule_audio_followup(delay, "duel_can_shoot")
            elif sub == "DRAW":
                # Timeout without a hit -> round draw
                self.duel_state["subphase"] = "ROUND_END"
                self.duel_state["phase_code"] = 2
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = now + 3.5
                self._record_duel_round_result(None)
                self.add_event("duel", f"Round {self.duel_state['round']} ended in a draw!")
                self.audio.trigger_pool("duel_drawn", force=True)

    def _race_run_active(self, player: PlayerState) -> bool:
        """True when `player` is the runner of a live timed turn."""
        if self.mode_name != "TagThenShoot":
            return False
        rs = self.race_state
        return bool(rs) and rs.get("turn_phase") == "RUNNING" and rs.get("active_gun_id") == player.gun_id

    def tick_race_turns(self, now: float | None = None) -> None:
        """Advance the TagThenShoot timed-turn machine.

        WAIT_TRIGGER (the active player pulls the trigger; mandatory,
        one pull per turn) -> COUNTDOWN (halved, as the match-level
        manual launch halves it) -> RUNNING (one course; the cap ends
        an unfinished run) -> TURN_END (3 s beat) -> the next turn's
        WAIT_TRIGGER, or DONE once every player has run. Called once
        per match-loop cycle, next to tick_mode_timers; the hardware
        trigger poll and the per-turn countdown command live in the
        loop, because they need blocking HID calls this sync tick
        cannot make.
        """
        if self.phase != "PLAYING" or self.mode_name != "TagThenShoot":
            return
        rs = self.race_state
        if not rs:
            return
        now = time.time() if now is None else now
        phase = rs.get("turn_phase")
        if phase == "WAIT_TRIGGER":
            if self._turn_triggered:
                self._turn_triggered = False
                cd = max(1, int(self.settings.get("countdownSeconds", 3)) // 2)
                rs["turn_phase"] = "COUNTDOWN"
                rs["countdown_seconds"] = cd
                rs["countdown_remaining"] = cd
                rs["countdown_ends_at"] = now + cd
                rs["countdown_sent"] = False
                rs["next_number"] = cd
                rs["next_number_at"] = now
                self.countdown_remaining = cd
                self.audio.trigger_pool("countdown_intro", force=True)
        elif phase == "COUNTDOWN":
            if rs.get("next_number", 0) >= 1 and now >= rs.get("next_number_at", 0.0):
                self.audio.play_countdown_number(rs["next_number"])
                self.countdown_remaining = rs["next_number"]
                rs["countdown_remaining"] = rs["next_number"]
                rs["next_number"] -= 1
                rs["next_number_at"] = now + 1.0
            if now >= rs.get("countdown_ends_at", 0.0):
                self._start_race_run(now)
        elif phase == "RUNNING":
            rs["elapsed"] = max(0.0, now - rs.get("turn_started_at", now))
            if now >= rs.get("turn_ends_at", 0.0):
                self._complete_race_turn(completed=False, now=now)
        elif phase == "TURN_END":
            if now >= rs.get("beat_ends_at", 0.0):
                self._advance_race_turn(now)

    def _start_race_run(self, now: float) -> None:
        """Enter RUNNING for the active player's turn: fresh run counters, a reset course, re-armed time warnings."""
        rs = self.race_state
        gid = rs.get("active_gun_id")
        player = self.players.get(gid) if gid is not None else None
        rs["turn_phase"] = "RUNNING"
        rs["turn_started_at"] = now
        rs["turn_ends_at"] = now + float(rs.get("turn_cap_s", 60))
        rs["elapsed"] = 0.0
        self.countdown_remaining = 0
        rs["countdown_remaining"] = 0
        if player is not None:
            player.tags = 0
            player.tower_hits = 0
            player.score = 0
            player.run_time = None
            player.run_completed = False
        if self.use_simulator and gid is not None:
            self.sim.reset_race_course(gid)
        self.audio.reset_match_timers()
        self.audio.trigger_pool("game_start", force=True)
        if player is not None:
            self.add_event("system", f"{player.display_name} started the run — go!")

    def _complete_race_turn(self, completed: bool, now: float | None = None) -> None:
        """Record the active run's result and open the TURN_END beat.

        Called from the ingest paths when the runner finishes the
        course, and from the tick when the cap expires. No-op unless a
        run is live, so a late tower report cannot re-complete a turn.
        """
        rs = self.race_state
        if not rs or rs.get("turn_phase") != "RUNNING":
            return
        now = time.time() if now is None else now
        gid = rs.get("active_gun_id")
        player = self.players.get(gid) if gid is not None else None
        elapsed = max(0.0, now - rs.get("turn_started_at", now))
        rs["turn_phase"] = "TURN_END"
        rs["beat_ends_at"] = now + 3.0
        rs["elapsed"] = elapsed
        if player is None:
            return
        player.run_completed = completed
        if not completed:
            player.run_time = None
            self.add_event(
                "system",
                f"{player.display_name} ran out of time — {player.score} points.",
            )
            return
        player.run_time = elapsed
        others = [
            p.run_time
            for p in self.players.values()
            if p.gun_id != gid and p.run_completed and p.run_time is not None
        ]
        is_match_best = not others or elapsed < min(others)
        prev_session = self.race_session_best.get(gid)
        is_session_best = prev_session is None or elapsed < prev_session["time"]
        if is_session_best:
            self.race_session_best[gid] = {"time": elapsed, "name": player.display_name}
        if is_match_best and is_session_best:
            tail = " — fastest so far, and a new session best!"
        elif is_match_best:
            tail = " — fastest so far!"
        elif is_session_best:
            tail = " — a new session best!"
        else:
            tail = ""
        self.add_event(
            "system",
            f"{player.display_name} finished the course in {elapsed:.1f}s!{tail}",
        )
        cue = self.audio.trigger_pool(
            "race_finish",
            custom_subtitle=f"{player.display_name} finished in {elapsed:.1f}s!",
            force=True,
        )
        if is_match_best or is_session_best:
            if cue is not None:
                delay = max(0.1, self.audio.active_until - now) + 0.25
            else:
                delay = 0.1
            self._schedule_audio_followup(
                delay,
                "pool",
                pool_key="race_new_best",
                subtitle=f"{player.display_name} — new best time!",
                force=True,
            )

    def _advance_race_turn(self, now: float) -> None:
        """Move to the next player's WAIT_TRIGGER, or DONE after the last turn."""
        rs = self.race_state
        rs["turn_index"] = int(rs.get("turn_index", 0)) + 1
        order = rs.get("turn_order", [])
        if rs["turn_index"] >= len(order):
            rs["turn_phase"] = "DONE"
            rs["active_gun_id"] = None
            return
        gid = order[rs["turn_index"]]
        rs["active_gun_id"] = gid
        rs["turn_phase"] = "WAIT_TRIGGER"
        rs["trigger_armed"] = False
        rs["elapsed"] = 0.0
        self._turn_triggered = False
        player = self.players.get(gid)
        if player is not None:
            self.add_event(
                "system",
                f"{player.display_name} is up — pull the trigger to start the run!",
            )

    def ingest_ubiconnect_hits(self, hits_by_gid: dict[int, int]) -> None:
        """Process 0xC3 IR shots received directly by the UbiConnect tower in TagThenShoot.

        Tower hits are one of the mode's two scoring channels (+1 each);
        the other is T-Base tags from status word 0. `player.frags` is
        kept in step with the tower-hit total because the poll downlink
        sends the running tower-hit count from it.
        """
        if self.mode_name != "TagThenShoot":
            return
        for gid, total_hits in hits_by_gid.items():
            player = self.players.get(gid)
            if not player:
                continue
            if total_hits > player.frags:
                delta = total_hits - player.frags
                player.frags = total_hits
                # Only the active runner scores during a timed turn.
                # The frags mirror above still advances for every gun,
                # so a stray tower shot during someone else's turn
                # never leaks into a later turn as phantom credit.
                if not self._race_run_active(player):
                    continue
                player.tower_hits += delta
                player.score += delta
                player.hits_landed += delta
                player.streak += delta
                player.best_streak = max(player.best_streak, player.streak)
                self.add_event(
                    "frag",
                    f"{player.display_name} shot the UbiConnect tower! (+{delta} pt)",
                    actor=player,
                    icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                )
                self.audio.trigger_pool("tower_hit", custom_subtitle=f"{player.display_name} hit the UbiConnect tower!")
                self._check_lead_change()
                # The final tower shot finishes the run: all bases
                # tagged and every tower shot landed.
                bases = max(1, int(self.settings.get("numberOfBase", 4)))
                if player.tags >= bases and player.tower_hits >= bases:
                    self._complete_race_turn(completed=True)

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
            if player.miss_streak >= 5 and now - self._last_miss_taunt_ts >= 9.0:
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

        if status.ammo is not None:
            if status.ammo > player.prev_ammo and (status.clips or 0) >= player.prev_clips:
                player.last_rfid = "RF01"
                self.add_event(
                    "ammo",
                    f"{player.display_name} scanned an Ammo Card — clips reloaded!",
                    actor=player,
                    icon_url=RFID_CARDS["RF01"]["icon"],
                )
            player.ammo = status.ammo
            player.prev_ammo = status.ammo
            finite_ammo_mode = self.mode_name in ("TeamFrag", "FreeForAll", "StarterFrag", "LastManStanding")
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

        # 3. Mode-specific hit & frag resolution
        if self.mode_name in ("FreeFrag", "StarterFrag"):
            curr_hits = status.hits_received or 0
            delta = curr_hits - player.prev_hits_rec
            if delta > 0:
                player.hits_received += delta
                player.streak = 0
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                for sgid in shooters:
                    shooter = self.players.get(sgid)
                    if shooter:
                        self._record_hit(shooter, player)
            player.prev_hits_rec = curr_hits

        elif self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding"):
            curr_life = status.life_points if status.life_points is not None else player.life_points
            if curr_life < player.prev_life:
                delta = player.prev_life - curr_life
                player.hits_received += delta
                player.life_points = curr_life
                player.streak = 0
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                # "Medic!" when life drops to exactly 1.
                if curr_life == 1 and player.prev_life > 1:
                    self.audio.trigger_pool("low_life")
                if self.mode_name == "LastManStanding":
                    self._record_lms_hits(shooters, player, curr_life)
                else:
                    for i, sgid in enumerate(shooters):
                        shooter = self.players.get(sgid)
                        if shooter:
                            # The shift register is most-recent-first,
                            # so shooters[0] landed the killing blow.
                            is_frag = (curr_life == 0) and (i == 0)
                            self._record_hit(shooter, player, is_frag=is_frag)
                if curr_life == 0:
                    player.deaths += 1
                    if self.mode_name == "LastManStanding":
                        player.status_badge = "ELIMINATED"
                    else:
                        player.status_badge = "SCAN T-BASE"
            elif curr_life > player.prev_life:
                was_zero = player.prev_life == 0
                player.life_points = curr_life
                player.status_badge = "ACTIVE"
                if was_zero:
                    if self.mode_name == "TeamFrag":
                        team_rfid = TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_code"]
                        player.last_rfid = team_rfid
                        self.add_event(
                            "respawn",
                            f"{player.display_name} scanned {TEAM_INFO.get(player.team_id, TEAM_INFO[1])['name']} T-Base & respawned!",
                            actor=player,
                            icon_url=TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_tex"],
                        )
                    else:
                        # FreeForAll respawns are timer-only (no scan is
                        # involved); LastManStanding has no respawn at
                        # all, so a restore there is only ever a medkit
                        # edge case — neutral wording for both.
                        self.add_event(
                            "respawn",
                            f"{player.display_name} is back in the game!",
                            actor=player,
                        )
                    if self.mode_name in ("TeamFrag", "FreeForAll"):
                        self.audio.trigger_pool("respawn")
                else:
                    player.last_rfid = "RF07"
                    self.add_event(
                        "medkit",
                        f"{player.display_name} scanned a Medkit (+HP)!",
                        actor=player,
                        icon_url=RFID_CARDS["RF07"]["icon"],
                    )
                    self.audio.trigger_pool("medkit_used")
            player.prev_life = curr_life

        elif self.mode_name == "OldFashionDuel":
            curr_hits = status.hits_received or 0
            if curr_hits > player.prev_hits_rec and self.duel_state.get("phase_code", 2) <= 1:
                delta = curr_hits - player.prev_hits_rec
                player.hits_received += delta
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                shooter = self.players.get(shooters[0]) if shooters else None
                if shooter:
                    if self.duel_state.get("can_shoot", False):
                        shooter.score += 1
                        shooter.hits_landed += 1
                        shooter.frags += 1
                        self.add_event(
                            "frag",
                            f"Round {self.duel_state.get('round', 1)}: {shooter.display_name} outdrew {player.display_name}! (+1 pt)",
                            actor=shooter,
                            target=player,
                        )
                        self._record_duel_round_result(shooter)
                        self._announce_duel_round_win(shooter)
                    else:
                        # Early hit before DRAW signal -> victim wins the round!
                        # (The forfeit triggers on a hit landing, not on a
                        # shot fired: an early miss only spends ammo.)
                        player.score += 1
                        self._record_duel_round_result(player)
                        self.add_event(
                            "duel",
                            f"Round {self.duel_state.get('round', 1)}: {shooter.display_name} fired before DRAW! Point awarded to {player.display_name}!",
                            actor=player,
                            target=shooter,
                        )
                        self.audio.trigger_pool("duel_cheat", force=True)
                else:
                    # A hit with no identifiable shooter is a draw in the
                    # original: GLOBAL_118, round over.
                    self._record_duel_round_result(None)
                    self.audio.trigger_pool("duel_drawn", force=True)
                self.duel_state["subphase"] = "ROUND_END"
                self.duel_state["phase_code"] = 2
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = time.time() + 4.0
                self._check_lead_change()
            elif (
                self.duel_state.get("phase_code", 2) <= 1
                and len(self.players) >= 2
                and all(p.ammo == 0 for p in self.players.values())
            ):
                self.duel_state["subphase"] = "ROUND_END"
                self.duel_state["phase_code"] = 2
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = time.time() + 4.0
                self._record_duel_round_result(None)
                self.add_event("duel", f"Round {self.duel_state.get('round', 1)}: Both duelists missed — Draw!")
                self.audio.trigger_pool("duel_drawn", force=True)
            player.prev_hits_rec = curr_hits

        elif self.mode_name == "TagThenShoot":
            # Status word 0 is the T-Base tag count (the gun's only SEND
            # var); tower hits arrive separately via 0xC3. Both score
            # +1, tracked and displayed as separate stats. The second
            # status word is not read: the original's RoundLoop never
            # looks at it and its meaning on real hardware is unknown.
            curr_tags = status.hits_received or 0
            if curr_tags > player.prev_hits_rec:
                delta = curr_tags - player.prev_hits_rec
                # Only the active runner scores during a timed turn;
                # the baseline below still advances for every gun, so
                # a stray scan during someone else's turn never counts
                # toward the stray gun's own run.
                if self._race_run_active(player):
                    player.tags += delta
                    player.score += delta
                    bases = max(1, int(self.settings.get("numberOfBase", 4)))
                    base_num = ((player.tags - 1) % bases) + 1
                    if delta == 1:
                        text = f"{player.display_name} tagged T-Base {base_num}! (+1 pt)"
                    else:
                        text = f"{player.display_name} tagged {delta} T-Bases! (+{delta} pt)"
                    self.add_event(
                        "hit",
                        text,
                        actor=player,
                        icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                    )
                    # Base-name callout (T-Base 1-4), then a confirmation
                    # line once the callout finishes. The gun enforces scan
                    # order, so the tag count modulo the base count is the
                    # stage of the base just tagged.
                    cue = self.audio.play_announcer_file(
                        f"DLG_GM_GLOBAL_{72 + base_num - 1:02d}.wav",
                        f"T-Base {base_num}!",
                        priority=2,
                        duration=1.5,
                    )
                    if cue is not None:
                        delay = max(0.1, self.audio.active_until - time.time()) + 0.25
                    else:
                        delay = 0.1
                    self._schedule_audio_followup(
                        delay,
                        "pool",
                        pool_key="tagnshoot_base",
                        subtitle=f"{player.display_name} tagged T-Base {base_num}!",
                        force=False,
                    )
                    self._check_lead_change()
                    # Safety net: a batched report can carry the last
                    # tag together with an already-landed final tower
                    # shot. Normally the tower path completes the run.
                    if player.tags >= bases and player.tower_hits >= bases:
                        self._complete_race_turn(completed=True)
            player.prev_hits_rec = curr_tags
            player.prev_phase_flag = status.phase_flag if status.phase_flag is not None else 1

        player.prev_slots = list(status.hit_ids)

    def _announce_duel_round_win(self, shooter: PlayerState) -> None:
        """Announce a duel round win as the original does: the winner's
        player-number callout (Set A, GLOBAL_54+classId), then GLOBAL_119
        "Wins the round!" once the callout finishes. The followup is
        scheduled at the end of the host's modeled callout window (the
        recordings run ~1.2 s); see _pending_audio."""
        subtitle = f"{shooter.display_name} wins Round {self.duel_state.get('round', 1)}!"
        if 1 <= shooter.gun_num <= 16:
            cue = self.audio.play_announcer_file(
                f"DLG_GM_GLOBAL_{54 + shooter.gun_num:02d}.wav",
                f"Player {shooter.gun_num}!",
                priority=3,
                duration=1.4,
            )
            if cue is not None:
                delay = max(0.1, self.audio.active_until - time.time()) + 0.25
            else:
                delay = 0.1
            self._schedule_audio_followup(
                delay, "pool", pool_key="duel_round_win", subtitle=subtitle, force=True
            )
        else:
            self.audio.trigger_pool("duel_round_win", custom_subtitle=subtitle, force=True)

    def _record_lms_hits(self, shooters: list[int], victim: PlayerState, curr_life: int) -> None:
        """Score LastManStanding hits by the original's survival formula.

        Hits and kills award no points — only counters. While alive a
        player's score is life + #players, recomputed when their life
        drops; on elimination it becomes #players - #alive, set once at
        the moment of elimination. Healing never raises the score: the
        recompute lives in the life-decrease branch only, as in the
        original's RoundLoop (a quirk, preserved).
        """
        num_players = len(self.players)
        for sgid in shooters:
            shooter = self.players.get(sgid)
            if not shooter:
                continue
            self.last_action_ts = time.time()
            shooter.miss_streak = 0
            shooter.hits_landed += 1
            shooter.last_tagged_target = victim.display_name
            victim.last_tagged_by = shooter.display_name
            victim.last_tagged_by_time = time.time()
            shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
            victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1
            verb = random.choice(KILL_VERBS)
            self.add_event(
                "hit",
                f"{shooter.display_name} {verb} {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            if curr_life > 0:
                self.audio.trigger_pool("hit_scored")
        if curr_life > 0:
            victim.score = victim.life_points + num_players
        else:
            alive = [p for p in self.players.values() if p.life_points > 0]
            victim.score = num_players - len(alive)
            killer = self.players.get(shooters[0]) if shooters else None
            if killer is not None:
                killer.frags += 1
                fverb = random.choice(FRAG_VERBS)
                self.add_event(
                    "frag",
                    f"ELIMINATED! {killer.display_name} {fverb} {victim.display_name}",
                    actor=killer,
                    target=victim,
                )
            else:
                self.add_event(
                    "frag",
                    f"ELIMINATED! {victim.display_name} is out!",
                    actor=victim,
                )
            self.audio.play_lms_elimination(
                len(alive),
                subtitle=f"{victim.display_name} is eliminated! {len(alive)} left!",
            )
        self._check_lead_change()

    def _record_hit(self, shooter: PlayerState, victim: PlayerState, is_frag: bool = False) -> None:
        self.last_action_ts = time.time()
        shooter.miss_streak = 0
        # Check friendly fire in TeamFrag
        if (
            self.mode_name == "TeamFrag"
            and shooter.team_id == victim.team_id
            and shooter.gun_id != victim.gun_id
        ):
            self.add_event(
                "hit",
                f"Friendly fire! {shooter.display_name} hit teammate {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            self.audio.trigger_pool("friendly_fire")
            return

        shooter.hits_landed += 1
        shooter.score += 1
        shooter.streak += 1
        shooter.best_streak = max(shooter.best_streak, shooter.streak)
        shooter.last_tagged_target = victim.display_name
        victim.last_tagged_by = shooter.display_name
        victim.last_tagged_by_time = time.time()
        shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
        victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1

        verb = random.choice(KILL_VERBS)
        self.add_event(
            "hit",
            f"{shooter.display_name} {verb} {victim.display_name} (+1 pt)",
            actor=shooter,
            target=victim,
        )

        if is_frag:
            shooter.frags += 1
            shooter.score += 3
            fverb = random.choice(FRAG_VERBS)
            self.add_event(
                "frag",
                f"FRAG! {shooter.display_name} {fverb} {victim.display_name} (+3 bonus)",
                actor=shooter,
                target=victim,
            )
            self.audio.trigger_pool("frag_scored", custom_subtitle=f"{shooter.display_name} fragged {victim.display_name}!")
        elif shooter.streak == 5:
            self.audio.trigger_pool("streak_5", custom_subtitle=f"{shooter.display_name} is on a 5-hit streak!")
        elif shooter.streak == 3:
            self.audio.trigger_pool("streak_3", custom_subtitle=f"{shooter.display_name} — 3-hit streak!")
        else:
            self.audio.trigger_pool("hit_scored")

        self._check_lead_change()

    def _check_lead_change(self) -> None:
        # Duel has no lead concept: rounds are announced by the
        # round-win callout + GLOBAL_119, and a generic lead-change
        # line would interrupt that sequence.
        if self.mode_name == "OldFashionDuel":
            return
        # Timed TagThenShoot turns have no lead concept either: players
        # run solo, one at a time, and a points lead mid-run says
        # nothing about who is fastest. Run results are announced at
        # completion instead.
        if self.mode_name == "TagThenShoot":
            return
        if self.mode_name == "TeamFrag":
            standings = self.get_team_standings()
            if len(standings) >= 2 and standings[0]["score"] > standings[1]["score"]:
                lead_key = f"team_{standings[0]['team_id']}"
                if lead_key != self.current_leader_key:
                    self.current_leader_key = lead_key
                    t_id = standings[0]["team_id"]
                    pool_map = {1: "lead_red", 2: "lead_blue", 3: "lead_yellow", 4: "lead_green"}
                    self.audio.trigger_pool(pool_map.get(t_id, "lead_red"))
        else:
            ranked = self.get_sorted_players()
            if len(ranked) >= 2 and ranked[0].score > ranked[1].score:
                lead_key = f"player_{ranked[0].gun_id}"
                if lead_key != self.current_leader_key:
                    self.current_leader_key = lead_key
                    self.audio.trigger_pool("lead_change_ffa", custom_subtitle=f"{ranked[0].display_name} takes the lead!")

    def get_sorted_players(self) -> list[PlayerState]:
        if self.mode_name == "LastManStanding":
            return sorted(
                self.players.values(),
                key=lambda p: (p.life_points > 0, p.life_points, p.score, p.hits_landed, -p.deaths),
                reverse=True,
            )
        if self.mode_name == "TagThenShoot":
            # Timed turns: finished runs rank by elapsed time, fastest
            # first; unfinished runs (cap expiry, or not yet run) rank
            # after them by points, then tags, then gun number.
            def race_key(p: PlayerState) -> tuple[int, float, int, int, int]:
                if p.run_completed and p.run_time is not None:
                    return (0, p.run_time, 0, 0, p.gun_num)
                return (1, 0.0, -p.score, -p.tags, p.gun_num)

            return sorted(self.players.values(), key=race_key)
        return sorted(
            self.players.values(),
            key=lambda p: (p.score, p.hits_landed, -p.hits_received, p.accuracy),
            reverse=True,
        )

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
        """
        if not self.players:
            return {}
        if self.mode_name == "LastManStanding":
            # Survival decides: every player still alive gets winner
            # LEDs regardless of score, so the sole survivor is never
            # shown red behind an eliminated player's higher score. If
            # somehow nobody is alive, fall back to the score rule.
            alive = {gid for gid, p in self.players.items() if p.life_points > 0}
            if alive:
                return {gid: gid in alive for gid in self.players}
        if self.mode_name == "TeamFrag":
            teams = self.get_team_standings()
            if not teams:
                return {gid: False for gid in self.players}
            top_score = teams[0]["score"]
            top_teams = [t["team_id"] for t in teams if t["score"] == top_score]
            is_tie = len(top_teams) > 1
            return {
                gid: (not is_tie and p.team_id == top_teams[0])
                for gid, p in self.players.items()
            }

        if self.mode_name == "TagThenShoot":
            # Timed turns: the fastest finished run wins. An exact time
            # tie wins nobody (same rule as the solo score tie below).
            # When nobody finished, fall through to the score rule.
            completed = [
                p for p in self.players.values() if p.run_completed and p.run_time is not None
            ]
            if completed:
                best = min(p.run_time for p in completed)
                winners = [p.gun_id for p in completed if p.run_time == best]
                is_tie = len(winners) > 1
                return {
                    gid: (not is_tie and gid == winners[0]) for gid in self.players
                }

        ranked = self.get_sorted_players()
        top_score = ranked[0].score
        winners = [p.gun_id for p in ranked if p.score == top_score]
        is_tie = len(winners) > 1
        return {
            gid: (not is_tie and gid == winners[0])
            for gid in self.players
        }

    def check_idle_announcer(self) -> None:
        """Fire idle banter when no shots/hits happen for a stretch during PLAYING."""
        if self.phase != "PLAYING" or self.mode_name == "OldFashionDuel":
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
        the prompt. Two rules, following the original's per-mode
        behavior (its Pinger is protected mid-match, so a dropout ends
        the match only through the host-facing prompt):
        - OldFashionDuel / TagThenShoot: any player going silent
          prompts at once — these modes need every gun answering.
        - Other modes: prompt once at most one gun is still answering
          after the disconnect timeout.
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

        if self.mode_name in ("OldFashionDuel", "TagThenShoot"):
            silent = [p for p in self.players.values() if silent_for(p, SIGNAL_LOST_TIMEOUT_S)]
            if silent:
                return {
                    "reason": "mode_critical",
                    "silent_gun_nums": [p.gun_num for p in silent],
                    "silent_names": [p.display_name for p in silent],
                    "answering_count": len(self.players) - len(silent),
                    "total_count": len(self.players),
                }
            return None
        silent = [p for p in self.players.values() if silent_for(p, MATCH_DISCONNECT_TIMEOUT_S)]
        answering_count = len(self.players) - len(silent)
        if len(self.players) >= 2 and answering_count <= 1:
            return {
                "reason": "last_gun",
                "silent_gun_nums": [p.gun_num for p in silent],
                "silent_names": [p.display_name for p in silent],
                "answering_count": answering_count,
                "total_count": len(self.players),
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
            and self.mode_name != "TagThenShoot"
            and (self.get_remaining_seconds() or 0.0) <= 0
        ):
            return True
        score_limit = int(self.settings.get("scoreLimit", 0))
        if score_limit > 0 and any(p.score >= score_limit for p in self.players.values()):
            return True
        if self.mode_name == "OldFashionDuel" and self.duel_state.get("subphase") == "COMPLETE":
            return True
        if self.mode_name == "TagThenShoot" and self.race_state.get("turn_phase") == "DONE":
            return True
        if self.mode_name == "LastManStanding" and len(self.players) >= 2:
            alive = [p for p in self.players.values() if p.life_points > 0]
            if len(alive) <= 1:
                return True
        return False

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
            "duel_state": self.duel_state if self.mode_name == "OldFashionDuel" else None,
            "race_state": self.race_state if self.mode_name == "TagThenShoot" else None,
            "race_session_best": (
                {str(k): dict(v) for k, v in self.race_session_best.items()}
                if self.mode_name == "TagThenShoot"
                else None
            ),
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

