"""Local Android AVD inventory from the user's SDK and configured data directories."""

import logging
import os
from pathlib import Path
import re
import subprocess

logger = logging.getLogger(__name__)


def list_installed_avds(emulator_path: str | None) -> list[str]:
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

    avd_dirs: list[Path] = []
    if avd_home := os.getenv("ANDROID_AVD_HOME"):
        avd_dirs.append(Path(avd_home).expanduser())
    for env_name in ("ANDROID_EMULATOR_HOME", "ANDROID_USER_HOME"):
        if user_home := os.getenv(env_name):
            avd_dirs.append(Path(user_home).expanduser() / "avd")
    if legacy_home := os.getenv("ANDROID_SDK_HOME"):
        avd_dirs.append(Path(legacy_home).expanduser() / ".android" / "avd")
    avd_dirs.append(Path.home() / ".android" / "avd")
    names = []
    for directory in dict.fromkeys(avd_dirs):
        if directory.is_dir():
            for ini_file in sorted(directory.glob("*.ini")):
                if ini_file.stem not in names:
                    names.append(ini_file.stem)
    return names
