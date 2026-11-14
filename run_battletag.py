#!/usr/bin/env python3
"""Launch the BattlePew macOS Replacement Server (FastAPI + uvicorn)."""

from __future__ import annotations

import argparse
import os
import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="BattlePew Arena Master Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0 for LAN Phone HUD)")
    parser.add_argument("--port", type=int, default=3466, help="Bind port (default: 3466)")
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Authorize live USB HID writes to the physical UbiConnect (0x044F:0xB365).",
    )
    args = parser.parse_args()

    os.environ["BATTLETAG_PORT"] = str(args.port)
    if args.confirm_write:
        os.environ["BATTLETAG_CONFIRM_WRITE"] = "1"

    uvicorn.run("battletag.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()

