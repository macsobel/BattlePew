"""Extract BattleTag audio, font, and texture assets from the official Ubisoft installer (.zip or Data1.cab)."""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from typing import Callable

from PIL import Image

MANIFEST_PATH = pathlib.Path(__file__).resolve().parent / "data" / "installer_assets_manifest.json"
MUSIC_TRACKS = ("musicmenu.ogg", "musicingame.ogg", "musicintroduction.ogg")


def _load_manifest() -> dict[str, dict[str, str]]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _read_cab_bytes(source_path: pathlib.Path, progress_cb: Callable[[float, str], None] | None) -> bytes:
    if not source_path.is_file():
        raise FileNotFoundError(f"Installer file not found: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix == ".cab":
        if progress_cb:
            progress_cb(0.08, "Reading Data1.cab…")
        return source_path.read_bytes()

    if suffix == ".zip":
        if progress_cb:
            progress_cb(0.04, "Opening BattleTag_Installer.zip…")
        with zipfile.ZipFile(source_path, "r") as zf:
            cab_entry = None
            for info in zf.infolist():
                if info.filename.lower().endswith("data1.cab"):
                    cab_entry = info
                    break
            if cab_entry is None:
                raise ValueError("Data1.cab was not found inside the selected ZIP archive.")
            if progress_cb:
                progress_cb(0.08, "Reading Data1.cab from installer archive…")
            return zf.read(cab_entry)

    raise ValueError("Please select BattleTag_Installer.zip or Data1.cab.")


def extract_assets_from_installer(
    source_path: pathlib.Path,
    dest_root: pathlib.Path,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, object]:
    """Extract audio, fonts, and textures (converted TGA -> PNG) from the Ubisoft installer."""
    manifest = _load_manifest()
    cab = _read_cab_bytes(source_path, progress_cb)

    if cab[:4] != b"MSCF":
        raise ValueError("Invalid Data1.cab header (expected MSCF cabinet).")

    (coff_files,) = struct.unpack_from("<I", cab, 0x10)
    c_folders, c_files = struct.unpack_from("<HH", cab, 0x1A)

    folders: list[tuple[int, int, int]] = []
    off = 0x24
    for _ in range(c_folders):
        coff_cab_start, c_cfdata, type_compress = struct.unpack_from("<IHH", cab, off)
        folders.append((coff_cab_start, c_cfdata, type_compress))
        off += 8

    files_by_folder: dict[int, list[tuple[int, int, str, str, str]]] = {}
    needed_folders: set[int] = set()
    off = coff_files
    for _ in range(c_files):
        cb_file, uoff_start, i_folder, _date, _time, _attribs = struct.unpack_from("<IIHHHH", cab, off)
        off += 16
        end = cab.index(b"\x00", off)
        cab_name = cab[off:end].decode("latin1")
        off = end + 1
        entry = manifest.get(cab_name)
        if entry is not None:
            files_by_folder.setdefault(i_folder, []).append(
                (uoff_start, cb_file, cab_name, entry["kind"], entry["dest"])
            )
            needed_folders.add(i_folder)

    if not needed_folders:
        raise ValueError("No BattleTag assets matched inside Data1.cab. Is this the right installer?")

    game_root = dest_root / "game" / "Program Files" / "Ubisoft" / "BattleTag"
    static_assets_root = dest_root / "static-assets"
    music_wav_dir = dest_root / "music-wav"
    music_wav_dir.mkdir(parents=True, exist_ok=True)
    static_assets_root.mkdir(parents=True, exist_ok=True)

    sorted_folders = sorted(needed_folders)
    total_folders = len(sorted_folders)
    extracted_count = 0

    for idx, i_folder in enumerate(sorted_folders):
        coff_cab_start, c_cfdata, type_compress = folders[i_folder]
        if type_compress not in (0, 1):
            raise ValueError(f"Unsupported CAB compression type: {type_compress}")

        if progress_cb:
            frac = 0.10 + 0.74 * (idx / max(1, total_folders))
            progress_cb(frac, f"Extracting game assets ({idx + 1}/{total_folders})…")

        pos = coff_cab_start
        chunks: list[bytes] = []
        hist = b""
        for _ in range(c_cfdata):
            _csum, cb_data, cb_uncomp = struct.unpack_from("<IHH", cab, pos)
            pos += 8
            block = cab[pos : pos + cb_data]
            pos += cb_data
            if type_compress == 0:
                out = block
            else:
                if block[:2] != b"CK":
                    raise ValueError("Corrupt MSZIP block in Data1.cab.")
                decomp = zlib.decompressobj(-15, zdict=hist)
                out = decomp.decompress(block[2:]) + decomp.flush()
                hist = (hist + out)[-32768:]
            if len(out) != cb_uncomp:
                raise ValueError("Decompressed block length mismatch in Data1.cab.")
            chunks.append(out)

        folder_data = b"".join(chunks)
        for uoff_start, cb_file, _cab_name, kind, rel_dest in files_by_folder[i_folder]:
            fbytes = folder_data[uoff_start : uoff_start + cb_file]
            if kind == "audio":
                target_file = game_root / rel_dest
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(fbytes)
            elif kind == "font":
                target_file = static_assets_root / rel_dest
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(fbytes)
            elif kind == "texture":
                target_file = static_assets_root / rel_dest
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(io.BytesIO(fbytes)) as img:
                    img.save(target_file, format="PNG")
            extracted_count += 1

    afconvert = shutil.which("afconvert") or ("/usr/bin/afconvert" if sys.platform == "darwin" else None)
    ffmpeg = shutil.which("ffmpeg")

    for idx, ogg_name in enumerate(MUSIC_TRACKS):
        ogg_path = game_root / "Data" / "Audio" / ogg_name
        wav_path = music_wav_dir / ogg_name.replace(".ogg", ".wav")
        if not ogg_path.is_file():
            continue
        if progress_cb:
            frac = 0.85 + 0.13 * (idx / len(MUSIC_TRACKS))
            progress_cb(frac, f"Preparing {ogg_name}…")
        if afconvert and pathlib.Path(afconvert).exists():
            res = subprocess.run(
                [afconvert, "-f", "WAVE", "-d", "LEI16", str(ogg_path), str(wav_path)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"afconvert failed for {ogg_name}: {res.stderr.strip()}")
        elif ffmpeg:
            res = subprocess.run(
                [ffmpeg, "-y", "-i", str(ogg_path), str(wav_path)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"ffmpeg failed for {ogg_name}: {res.stderr.strip()}")
        else:
            # Windows (Edge WebView2) and Linux (WebKitGTK / browsers) play Ogg Vorbis natively.
            shutil.copy2(ogg_path, music_wav_dir / ogg_name)

    if progress_cb:
        progress_cb(1.0, "All game assets ready!")

    return {"ok": True, "extracted_count": extracted_count, "dest": str(dest_root)}


# Backwards-compatible alias
extract_audio_from_installer = extract_assets_from_installer
