"""Cross-platform GitHub Release version checker for BattlePew."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

DEFAULT_GITHUB_REPO = "macsobel/BattlePew"
DEFAULT_TIMEOUT_SECONDS = 6.0

_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?$")
_FALLBACK_CA_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)


def normalize_version(raw: str) -> str:
    """Strip whitespace and an optional leading 'v'/'V' from a version string."""
    cleaned = str(raw or "").strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]
    return cleaned


def parse_version(raw: str) -> tuple[tuple[int, ...], int, str] | None:
    """Parse a release tag or version string into a comparable tuple.

    Returns ``(core_ints, stability_flag, prerelease)`` where ``stability_flag``
    is ``1`` for stable releases and ``0`` for prereleases, or ``None`` if invalid.
    """
    cleaned = normalize_version(raw)
    if "+" in cleaned:
        cleaned = cleaned.split("+", 1)[0]
    match = _VERSION_RE.match(cleaned)
    if not match:
        return None
    core_str, prerelease = match.group(1), match.group(2)
    core = tuple(int(part) for part in core_str.split("."))
    return (core, 0 if prerelease else 1, prerelease or "")


def _compare_prerelease(lhs: str, rhs: str) -> int:
    """Compare SemVer prerelease identifiers; return -1, 0, or 1."""
    if lhs == rhs:
        return 0
    left_parts = lhs.split(".")
    right_parts = rhs.split(".")
    for left, right in zip(left_parts, right_parts):
        left_is_num = left.isdigit()
        right_is_num = right.isdigit()
        if left_is_num and right_is_num:
            li, ri = int(left), int(right)
            if li != ri:
                return -1 if li < ri else 1
        elif left_is_num != right_is_num:
            # Numeric identifiers always have lower precedence than non-numeric.
            return -1 if left_is_num else 1
        elif left != right:
            return -1 if left < right else 1
    if len(left_parts) != len(right_parts):
        return -1 if len(left_parts) < len(right_parts) else 1
    return 0


def is_newer_version(latest: str, current: str) -> bool:
    """Return True iff *latest* parses as strictly greater than *current*."""
    parsed_latest = parse_version(latest)
    parsed_current = parse_version(current)
    if parsed_latest is None or parsed_current is None:
        return False

    latest_core, latest_stable, latest_pre = parsed_latest
    current_core, current_stable, current_pre = parsed_current

    width = max(len(latest_core), len(current_core))
    padded_latest = latest_core + (0,) * (width - len(latest_core))
    padded_current = current_core + (0,) * (width - len(current_core))
    if padded_latest != padded_current:
        return padded_latest > padded_current

    if latest_stable != current_stable:
        return latest_stable > current_stable

    if latest_stable == 0 and current_stable == 0:
        return _compare_prerelease(latest_pre, current_pre) > 0

    return False


def detect_platform_key(sys_platform: str | None = None) -> str:
    """Normalize runtime OS into 'macos', 'windows', or 'linux'."""
    plat = (sys_platform if sys_platform is not None else sys.platform).lower()
    if plat == "darwin":
        return "macos"
    if plat in ("win32", "cygwin", "msys"):
        return "windows"
    return "linux"


def platform_label(platform_key: str) -> str:
    """Human-readable label for a platform key."""
    return {
        "macos": "macOS",
        "windows": "Windows",
        "linux": "Linux",
    }.get(platform_key, "Desktop")


def is_safe_github_url(url: str | None) -> bool:
    """Allow only HTTPS URLs pointing to GitHub hosts."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in ("github.com", "www.github.com") or host.endswith(".githubusercontent.com")


def _linux_arch_compatible(asset_name_lower: str, machine: str) -> bool:
    """Avoid picking an x86_64 binary on arm64 Linux (or vice versa)."""
    m = (machine or "").lower()
    is_arm = any(tok in m for tok in ("arm64", "aarch64"))
    is_x86 = any(tok in m for tok in ("x86_64", "amd64"))

    asset_has_x86 = "x86_64" in asset_name_lower or "amd64" in asset_name_lower
    asset_has_arm = "arm64" in asset_name_lower or "aarch64" in asset_name_lower

    if is_arm and asset_has_x86 and not asset_has_arm:
        return False
    if is_x86 and asset_has_arm and not asset_has_x86:
        return False
    return True


def select_download_asset(
    assets: list[dict[str, Any]],
    platform_key: str,
    machine: str | None = None,
) -> dict[str, Any] | None:
    """Choose the best matching release asset for *platform_key*."""
    if not isinstance(assets, list):
        return None
    arch = (machine if machine is not None else platform.machine()).lower()

    best_score = 0
    best_asset: dict[str, Any] | None = None

    for raw in assets:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        url = raw.get("browser_download_url")
        if not isinstance(name, str) or not is_safe_github_url(url):
            continue
        lower = name.lower()
        score = 0

        if platform_key == "macos":
            if lower.endswith(".dmg"):
                score = 30
            elif lower.endswith(".zip") and any(tok in lower for tok in ("macos", "darwin", "osx")):
                score = 20
        elif platform_key == "windows":
            if lower.endswith(".exe"):
                score = 30
            elif lower.endswith(".msi"):
                score = 25
            elif lower.endswith(".zip") and ("windows" in lower or "win" in lower):
                score = 15
        elif platform_key == "linux":
            if not _linux_arch_compatible(lower, arch):
                continue
            if lower.endswith(".appimage"):
                score = 30
            elif (lower.endswith(".deb") or lower.endswith(".rpm")) and "linux" in lower:
                score = 20
            elif (lower.endswith(".tar.gz") or lower.endswith(".tar.xz")) and "linux" in lower:
                score = 15

        if score > best_score:
            best_score = score
            best_asset = {
                "name": name,
                "url": str(url).strip(),
                "size": raw.get("size"),
            }

    return best_asset


def _build_ssl_context() -> ssl.SSLContext:
    """Create a verified TLS context that also works inside bundled Python runtimes."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) > 0:
        return ctx

    try:
        import certifi  # type: ignore[import-not-found]

        cafile = certifi.where()
        if cafile and os.path.isfile(cafile):
            ctx.load_verify_locations(cafile=cafile)
            return ctx
    except Exception:
        pass

    for candidate in _FALLBACK_CA_PATHS:
        if os.path.isfile(candidate):
            try:
                ctx.load_verify_locations(cafile=candidate)
                return ctx
            except Exception:
                continue

    return ctx


def fetch_latest_github_release(
    repo: str,
    current_version: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Fetch the latest published GitHub Release JSON from api.github.com.

    Uses `/releases/latest`, which GitHub restricts to published non-draft,
    non-prerelease releases (never GitHub Actions artifacts or workflow builds).
    Returns None when the repository has no published releases yet (HTTP 404).
    """
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"BattlePew/{normalize_version(current_version) or '0'}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    ctx = _build_ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


class UpdateChecker:
    """Thread-safe GitHub Releases update checker with cached state."""

    def __init__(
        self,
        current_version: str,
        repo: str | None = None,
        platform_key: str | None = None,
        machine: str | None = None,
        fetcher: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.repo = (repo or os.environ.get("BATTLETAG_UPDATE_REPO") or DEFAULT_GITHUB_REPO).strip()
        self.current_version = normalize_version(current_version)
        self.platform = platform_key or detect_platform_key()
        self.platform_label = platform_label(self.platform)
        self.machine = machine if machine is not None else platform.machine()
        self._fetcher = fetcher or fetch_latest_github_release
        self._lock = threading.Lock()

        self._state: dict[str, Any] = {
            "status": "idle",
            "update_available": False,
            "latest_version": None,
            "latest_tag": None,
            "release_name": None,
            "release_url": None,
            "download_url": None,
            "asset_name": None,
            "published_at": None,
            "checked_at": None,
            "error": None,
        }

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            snap = dict(self._state)
        snap.update(
            {
                "current_version": self.current_version,
                "platform": self.platform,
                "platform_label": self.platform_label,
                "repo": self.repo,
                "releases_page_url": f"https://github.com/{self.repo}/releases",
            }
        )
        return snap

    def check_sync(self, force: bool = True) -> dict[str, Any]:
        with self._lock:
            if not force and self._state["checked_at"] is not None and self._state["status"] != "idle":
                return self._snapshot_unlocked()
            self._state["status"] = "checking"
            self._state["error"] = None

        try:
            payload = self._fetcher(self.repo, self.current_version)
            if payload is None or payload.get("draft") or payload.get("prerelease"):
                with self._lock:
                    self._state.update(
                        {
                            "status": "no_releases",
                            "update_available": False,
                            "latest_version": None,
                            "latest_tag": None,
                            "release_name": None,
                            "release_url": None,
                            "download_url": None,
                            "asset_name": None,
                            "published_at": None,
                            "checked_at": time.time(),
                            "error": None,
                        }
                    )
                    return self._snapshot_unlocked()

            tag_name = str(payload.get("tag_name") or "").strip()
            if parse_version(tag_name) is None:
                with self._lock:
                    self._state.update(
                        {
                            "status": "error",
                            "update_available": False,
                            "checked_at": time.time(),
                            "error": f"Latest release tag '{tag_name}' is not a valid version.",
                        }
                    )
                    return self._snapshot_unlocked()

            raw_html_url = payload.get("html_url")
            release_url = (
                str(raw_html_url).strip()
                if is_safe_github_url(raw_html_url)
                else f"https://github.com/{self.repo}/releases"
            )
            asset = select_download_asset(payload.get("assets") or [], self.platform, self.machine)
            download_url = asset["url"] if asset else release_url
            asset_name = asset["name"] if asset else None

            newer = is_newer_version(tag_name, self.current_version)
            with self._lock:
                self._state.update(
                    {
                        "status": "update_available" if newer else "up_to_date",
                        "update_available": newer,
                        "latest_version": normalize_version(tag_name),
                        "latest_tag": tag_name,
                        "release_name": str(payload.get("name") or tag_name),
                        "release_url": release_url,
                        "download_url": download_url,
                        "asset_name": asset_name,
                        "published_at": payload.get("published_at"),
                        "checked_at": time.time(),
                        "error": None,
                    }
                )
                return self._snapshot_unlocked()
        except urllib.error.HTTPError as exc:
            msg = (
                "GitHub API rate limit exceeded. Please try again later."
                if exc.code in (403, 429)
                else f"GitHub returned HTTP {exc.code}."
            )
        except (urllib.error.URLError, TimeoutError, OSError):
            msg = "Could not reach GitHub. Check your internet connection."
        except Exception as exc:
            msg = f"Update check failed: {exc}"

        with self._lock:
            self._state.update(
                {
                    "status": "error",
                    "update_available": False,
                    "checked_at": time.time(),
                    "error": msg,
                }
            )
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        snap = dict(self._state)
        snap.update(
            {
                "current_version": self.current_version,
                "platform": self.platform,
                "platform_label": self.platform_label,
                "repo": self.repo,
                "releases_page_url": f"https://github.com/{self.repo}/releases",
            }
        )
        return snap

    async def check_async(self, force: bool = True) -> dict[str, Any]:
        return await asyncio.to_thread(self.check_sync, force)
