"""Game modes for BattlePew: one module per mode, discovered by the registry.

This package replaces the former ``battletag/modes.py`` module. The
shared arena core lives in :mod:`battletag.modes.engine`
(:class:`ArenaEngine`, :class:`PlayerState`), each mode's rules live
in its own module behind the :class:`GameMode` interface (see
:mod:`battletag.modes.base`), and
:mod:`battletag.modes.registry` discovers the mode modules and
builds ``MODE_CATALOG``.

Everything the old module exported is re-exported here, so
``from battletag.modes import ArenaEngine, MODE_CATALOG`` and friends
keep working unchanged.
"""

import sys  # noqa: F401  (re-exported: tests monkeypatch battletag.modes.sys)

from .base import FRAG_VERBS, KILL_VERBS, GameMode
from .duel import DUEL_FIRE_GRACE_S
from .engine import (
    AVATAR_NAMES,
    DEFAULT_GLOBAL_SETTINGS,
    DEFAULT_GLOBAL_SETTINGS_PATH,
    LOBBY_QUIET_TIMEOUT_S,
    LOBBY_SILENT_SWEEPS_TO_REMOVE,
    MATCH_DISCONNECT_TIMEOUT_S,
    RFID_CARDS,
    SIGNAL_LOST_TIMEOUT_S,
    TEAM_INFO,
    TIER2_PREVIEW,
    VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT,
    VEST_SILENT_SWEEPS_TO_DISCONNECT,
    ArenaEngine,
    AudioCue,
    DecodedStatus,
    EventEntry,
    GameMasterAudio,
    PlayerState,
    RealHIDTransport,
    SimulatorTransport,
    audio_assets_ready,
    detect_ubiconnect_readonly,
    get_user_data_dir,
)
from .registry import MODE_CATALOG, MODE_CLASSES, get_mode_class
from battletag.protocol import count_new_shooters

__all__ = [
    "ArenaEngine",
    "AudioCue",
    "AVATAR_NAMES",
    "count_new_shooters",
    "DecodedStatus",
    "DEFAULT_GLOBAL_SETTINGS",
    "DEFAULT_GLOBAL_SETTINGS_PATH",
    "DUEL_FIRE_GRACE_S",
    "EventEntry",
    "FRAG_VERBS",
    "GameMasterAudio",
    "GameMode",
    "get_mode_class",
    "get_user_data_dir",
    "KILL_VERBS",
    "LOBBY_QUIET_TIMEOUT_S",
    "LOBBY_SILENT_SWEEPS_TO_REMOVE",
    "MATCH_DISCONNECT_TIMEOUT_S",
    "MODE_CATALOG",
    "MODE_CLASSES",
    "PlayerState",
    "RealHIDTransport",
    "RFID_CARDS",
    "SIGNAL_LOST_TIMEOUT_S",
    "SimulatorTransport",
    "TEAM_INFO",
    "TIER2_PREVIEW",
    "VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT",
    "VEST_SILENT_SWEEPS_TO_DISCONNECT",
    "audio_assets_ready",
    "detect_ubiconnect_readonly",
    "sys",
]
