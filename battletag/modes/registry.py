"""Mode registry: discovers mode modules, builds the mode catalog.

Discovery is automatic: every module in this package that defines a
``MODE_CLASS`` (a :class:`GameMode` subclass) and an ``ORDER`` is a
mode. Adding a mode means adding its file; removing a mode means
deleting it — no central list to edit. Catalog order is the modules'
``ORDER`` values (ties broken by mode id), which preserves the
wizard's mode order exactly.

Discovery runs once, at import time, and fails loudly on duplicate
mode ids, duplicate orders, missing metadata, or a ``MODE_CLASS``
that is not a :class:`GameMode` subclass — a broken mode module must
never boot half-registered.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any

from battletag.modes.base import GameMode

# Package modules that are infrastructure, not modes. Discovery skips
# these (and any private module) instead of importing them looking
# for MODE_CLASS.
_NON_MODE_MODULES = frozenset({"__init__", "base", "engine", "registry"})


def _discover() -> dict[str, type[GameMode]]:
    found: dict[str, type[GameMode]] = {}
    orders: dict[str, int] = {}
    package_dir = str(Path(__file__).parent)
    for info in pkgutil.iter_modules([package_dir]):
        if info.name in _NON_MODE_MODULES or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"battletag.modes.{info.name}")
        mode_cls = getattr(module, "MODE_CLASS", None)
        if mode_cls is None:
            continue
        if not (isinstance(mode_cls, type) and issubclass(mode_cls, GameMode)):
            raise TypeError(
                f"battletag.modes.{info.name}.MODE_CLASS is not a GameMode subclass"
            )
        metadata = getattr(mode_cls, "METADATA", None)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
            raise TypeError(
                f"battletag.modes.{info.name}.MODE_CLASS.METADATA must be a dict with a string 'id'"
            )
        mode_id = str(metadata["id"])
        if mode_id in found:
            raise ValueError(f"Duplicate mode id {mode_id!r} discovered in battletag.modes")
        order = getattr(module, "ORDER", None)
        if not isinstance(order, int):
            raise TypeError(
                f"battletag.modes.{info.name} must define an integer ORDER"
            )
        if order in orders.values():
            raise ValueError(f"Duplicate mode ORDER {order} discovered in battletag.modes")
        found[mode_id] = mode_cls
        orders[mode_id] = order
    if not found:
        raise RuntimeError("No game modes discovered in battletag.modes")
    return {mid: found[mid] for mid in sorted(found, key=lambda m: (orders[m], m))}


#: Mode id -> mode class, in catalog (ORDER) order.
MODE_CLASSES: dict[str, type[GameMode]] = _discover()

#: Mode id -> catalog entry (each mode's METADATA dict). The engine,
#: the snapshot, and the app read the catalog from here, exactly as
#: they read the old module-level MODE_CATALOG.
MODE_CATALOG: dict[str, dict[str, Any]] = {
    mode_id: mode_cls.METADATA for mode_id, mode_cls in MODE_CLASSES.items()
}


def get_mode_class(mode_id: str) -> type[GameMode]:
    """The mode class for ``mode_id``. Raises ValueError (the same
    message ``ArenaEngine.select_mode`` has always raised) for an
    unknown id."""
    try:
        return MODE_CLASSES[mode_id]
    except KeyError:
        raise ValueError(f"Unknown mode: {mode_id}") from None
