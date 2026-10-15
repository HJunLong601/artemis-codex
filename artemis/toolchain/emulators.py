"""Local Android AVD inventory from the user's SDK and configured data directories."""

import logging
from collections import OrderedDict
import os
from pathlib import Path
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 10.0
_CACHE_MAX_ENTRIES = 16
_cache: OrderedDict[tuple, tuple[float, tuple[str, ...]]] = OrderedDict()
_inflight: set[tuple] = set()
_condition = threading.Condition()
_generation = 0


def clear_avd_inventory_cache() -> None:
    """Force a new inventory without publishing an already-running stale scan."""
    global _generation
    with _condition:
        _generation += 1
        _cache.clear()
        _condition.notify_all()


def _avd_directories() -> list[Path]:
    directories: list[Path] = []
    if avd_home := os.getenv("ANDROID_AVD_HOME"):
        directories.append(Path(avd_home).expanduser())
    for env_name in ("ANDROID_EMULATOR_HOME", "ANDROID_USER_HOME"):
        if user_home := os.getenv(env_name):
            directories.append(Path(user_home).expanduser() / "avd")
    if legacy_home := os.getenv("ANDROID_SDK_HOME"):
        directories.append(Path(legacy_home).expanduser() / ".android" / "avd")
    directories.append(Path.home() / ".android" / "avd")
    return list(dict.fromkeys(directories))


def _path_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return str(path.absolute()), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(path.absolute()), None, None


def list_installed_avds(emulator_path: str | None) -> list[str]:
    """Coalesce repeated SDK scans; environment/directory changes invalidate immediately."""
    directories = _avd_directories()
    key = (
        _path_signature(Path(emulator_path).expanduser()) if emulator_path else None,
        tuple(_path_signature(path) for path in directories),
    )
    with _condition:
        while True:
            generation = _generation
            entry = _cache.get(key)
            if entry is not None and time.monotonic() < entry[0]:
                _cache.move_to_end(key)
                return list(entry[1])
            token = generation, key
            if token not in _inflight:
                _inflight.add(token)
                break
            _condition.wait()
    names = None
    try:
        names = _scan_installed_avds(emulator_path, directories)
        return list(names)
    finally:
        with _condition:
            _inflight.discard(token)
            if names is not None and generation == _generation:
                _cache[key] = time.monotonic() + _CACHE_TTL_SECONDS, tuple(names)
                _cache.move_to_end(key)
                while len(_cache) > _CACHE_MAX_ENTRIES:
                    _cache.popitem(last=False)
            _condition.notify_all()


def _scan_installed_avds(emulator_path: str | None, avd_dirs: list[Path]) -> list[str]:
    """Prefer SDK inventory, ignoring launcher logs; use user directories as fallback."""
    if emulator_path:
        try:
            result = subprocess.run(
                [emulator_path, "-list-avds"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            names = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
                and not re.match(
                    r"^(?:INFO|WARNING|WARN|ERROR|DEBUG|VERBOSE|FATAL)\s*[|:]", line.strip()
                )
            ]
            if names:
                return list(dict.fromkeys(names))
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Emulator AVD listing unavailable: %s", exc)

    names = []
    for directory in avd_dirs:
        try:
            if directory.is_dir():
                for ini_file in sorted(directory.glob("*.ini")):
                    if ini_file.stem not in names:
                        names.append(ini_file.stem)
        except OSError as exc:
            logger.debug("AVD directory unavailable %s: %s", directory, exc)
    return names
