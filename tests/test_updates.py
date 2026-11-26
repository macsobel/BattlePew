"""Tests for cross-platform GitHub Releases update checker and endpoints."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import battletag
from battletag.updates import (
    UpdateChecker,
    detect_platform_key,
    is_newer_version,
    is_safe_github_url,
    parse_version,
    select_download_asset,
)


def _sample_release_payload(tag: str = "v2.1.0", *, draft: bool = False, prerelease: bool = False) -> dict:
    return {
        "tag_name": tag,
        "name": f"BattlePew {tag}",
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/macsobel/BattlePew/releases/tag/{tag}",
        "published_at": "2026-11-26T12:00:00Z",
        "assets": [
            {
                "name": f"BattlePew-macOS-{tag.lstrip('v')}-abc1234.zip",
                "browser_download_url": f"https://github.com/macsobel/BattlePew/releases/download/{tag}/BattlePew-macOS-{tag.lstrip('v')}-abc1234.zip",
                "size": 52_000_000,
            },
            {
                "name": f"BattlePew-Windows-{tag.lstrip('v')}-abc1234.exe",
                "browser_download_url": f"https://github.com/macsobel/BattlePew/releases/download/{tag}/BattlePew-Windows-{tag.lstrip('v')}-abc1234.exe",
                "size": 48_000_000,
            },
            {
                "name": f"BattlePew-Linux-{tag.lstrip('v')}-abc1234-x86_64.AppImage",
                "browser_download_url": f"https://github.com/macsobel/BattlePew/releases/download/{tag}/BattlePew-Linux-{tag.lstrip('v')}-abc1234-x86_64.AppImage",
                "size": 55_000_000,
            },
        ],
    }


def test_version_parsing_and_comparison() -> None:
    assert parse_version("v2.1.0") == ((2, 1, 0), 1, "")
    assert parse_version("2.0.0-rc.1") == ((2, 0, 0), 0, "rc.1")
    assert parse_version("not-a-version") is None

    assert is_newer_version("v2.1.0", "2.0.0") is True
    assert is_newer_version("2.0.1", "2.0.0") is True
    assert is_newer_version("2.1.1", "2.1") is True
    assert is_newer_version("2.1", "2.1.0") is False
    assert is_newer_version("v2.0.0", "2.0.0") is False
    assert is_newer_version("1.9.9", "2.0.0") is False
    assert is_newer_version("2.0.0", "2.0.0-rc.1") is True
    assert is_newer_version("2.0.0-rc.2", "2.0.0-rc.1") is True
    assert is_newer_version("invalid", "2.0.0") is False


def test_platform_detection_and_url_safety() -> None:
    assert detect_platform_key("darwin") == "macos"
    assert detect_platform_key("win32") == "windows"
    assert detect_platform_key("cygwin") == "windows"
    assert detect_platform_key("linux") == "linux"

    assert is_safe_github_url("https://github.com/macsobel/BattlePew/releases") is True
    assert is_safe_github_url("https://objects.githubusercontent.com/some/asset.exe") is True
    assert is_safe_github_url("http://github.com/macsobel/BattlePew") is False
    assert is_safe_github_url("https://evil.example.com/BattlePew.exe") is False
    assert is_safe_github_url("javascript:alert(1)") is False


def test_select_download_asset_for_all_three_platforms() -> None:
    assets = _sample_release_payload("v2.1.0")["assets"]

    mac_asset = select_download_asset(assets, "macos", "arm64")
    assert mac_asset is not None
    assert mac_asset["name"] == "BattlePew-macOS-2.1.0-abc1234.zip"

    # Prefer .dmg over .zip on macOS if both exist
    mac_with_dmg = assets + [
        {
            "name": "BattlePew-2.1.0.dmg",
            "browser_download_url": "https://github.com/macsobel/BattlePew/releases/download/v2.1.0/BattlePew-2.1.0.dmg",
        }
    ]
    assert select_download_asset(mac_with_dmg, "macos", "arm64")["name"] == "BattlePew-2.1.0.dmg"

    win_asset = select_download_asset(assets, "windows", "AMD64")
    assert win_asset is not None
    assert win_asset["name"] == "BattlePew-Windows-2.1.0-abc1234.exe"

    linux_x86 = select_download_asset(assets, "linux", "x86_64")
    assert linux_x86 is not None
    assert linux_x86["name"] == "BattlePew-Linux-2.1.0-abc1234-x86_64.AppImage"

    # arm64 Linux should not select an x86_64-only AppImage
    assert select_download_asset(assets, "linux", "aarch64") is None

    # Unsafe non-GitHub URLs are filtered out
    unsafe = [
        {
            "name": "BattlePew-Windows-2.1.0.exe",
            "browser_download_url": "https://evil.example.com/payload.exe",
        }
    ]
    assert select_download_asset(unsafe, "windows", "AMD64") is None


@pytest.mark.parametrize("platform_key,expected_ext", [("macos", ".zip"), ("windows", ".exe"), ("linux", ".AppImage")])
def test_update_checker_newer_release_per_os(platform_key: str, expected_ext: str) -> None:
    checker = UpdateChecker(
        current_version="2.0.0",
        platform_key=platform_key,
        machine="x86_64",
        fetcher=lambda _repo, _ver: _sample_release_payload("v2.1.0"),
    )
    res = checker.check_sync(force=True)
    assert res["status"] == "update_available"
    assert res["update_available"] is True
    assert res["latest_version"] == "2.1.0"
    assert res["download_url"].endswith(expected_ext)
    assert res["release_url"] == "https://github.com/macsobel/BattlePew/releases/tag/v2.1.0"


def test_update_checker_fallback_and_edge_states() -> None:
    # Missing OS asset falls back to GitHub release page
    no_assets_payload = _sample_release_payload("v2.1.0")
    no_assets_payload["assets"] = []
    c1 = UpdateChecker("2.0.0", platform_key="linux", machine="x86_64", fetcher=lambda *_: no_assets_payload)
    r1 = c1.check_sync()
    assert r1["status"] == "update_available"
    assert r1["asset_name"] is None
    assert r1["download_url"] == r1["release_url"]

    # Up to date
    c2 = UpdateChecker("2.1.0", platform_key="macos", fetcher=lambda *_: _sample_release_payload("v2.1.0"))
    r2 = c2.check_sync()
    assert r2["status"] == "up_to_date"
    assert r2["update_available"] is False

    # Draft / prerelease ignored even if returned
    c3 = UpdateChecker("2.0.0", fetcher=lambda *_: _sample_release_payload("v3.0.0", prerelease=True))
    assert c3.check_sync()["status"] == "no_releases"

    # 404 -> no releases published yet
    c4 = UpdateChecker("2.0.0", fetcher=lambda *_: None)
    assert c4.check_sync()["status"] == "no_releases"

    # Rate limit error
    def _rate_limit(*_args):
        raise urllib.error.HTTPError(
            url="https://api.github.com/repos/macsobel/BattlePew/releases/latest",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

    c5 = UpdateChecker("2.0.0", fetcher=_rate_limit)
    r5 = c5.check_sync()
    assert r5["status"] == "error"
    assert "rate limit" in r5["error"].lower()

    # Network unreachable / offline
    def _offline(*_args):
        raise urllib.error.URLError("Name or service not known")

    c6 = UpdateChecker("2.0.0", fetcher=_offline)
    r6 = c6.check_sync()
    assert r6["status"] == "error"
    assert "internet" in r6["error"].lower()


def test_desktop_launcher_open_external_allows_only_http_https(monkeypatch) -> None:
    import desktop_launcher

    opened: list[str] = []
    monkeypatch.setattr(desktop_launcher.webbrowser, "open", lambda url, new=0: opened.append(url) or True)

    api = desktop_launcher.DesktopApi(initial_fullscreen=False)
    assert api.open_external("https://github.com/macsobel/BattlePew/releases") is True
    assert opened == ["https://github.com/macsobel/BattlePew/releases"]
    assert api.open_external("javascript:alert(1)") is False
    assert api.open_external("file:///etc/passwd") is False
    assert opened == ["https://github.com/macsobel/BattlePew/releases"]


def test_app_endpoints_expose_version_and_updates(monkeypatch) -> None:
    import battletag.app as app_mod

    checker = UpdateChecker(
        current_version=battletag.__version__,
        platform_key="macos",
        fetcher=lambda _repo, _ver: _sample_release_payload("v9.9.0"),
    )
    monkeypatch.setattr(app_mod, "update_checker", checker)
    mock_ws = MagicMock()
    monkeypatch.setattr(app_mod.ws_manager, "broadcast", mock_ws)

    dummy_req = SimpleNamespace(url=SimpleNamespace(port=3466))

    async def _exercise() -> tuple[dict, dict, dict]:
        async def _noop_broadcast(_payload):
            return None

        mock_ws.side_effect = _noop_broadcast
        before_resp = await app_mod.get_updates()
        check_resp = await app_mod.check_updates()
        state_resp = await app_mod.get_state(dummy_req)
        return (
            json.loads(before_resp.body.decode("utf-8")),
            json.loads(check_resp.body.decode("utf-8")),
            json.loads(state_resp.body.decode("utf-8")),
        )

    before, checked, state = asyncio.run(_exercise())
    assert before["status"] == "idle"
    assert before["current_version"] == battletag.__version__

    assert checked["status"] == "update_available"
    assert checked["update_available"] is True
    assert checked["latest_version"] == "9.9.0"

    assert state["app_version"] == battletag.__version__
    assert state["update_status"]["status"] == "update_available"
    assert state["update_status"]["latest_version"] == "9.9.0"
