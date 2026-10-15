"""Shared switch and ordering for automatic mobile target selection."""

from collections.abc import Callable, Iterable


def emulator_discovery_enabled() -> bool:
    # Resolve lazily: settings initialization can itself load runtime modules.
    from artemis.config.settings import settings

    return settings.ARTEMIS_EMULATOR_DISCOVERY_ENABLED


def is_emulator_serial(serial: str) -> bool:
    return serial.startswith("emulator-") or "127.0.0.1" in serial or "localhost" in serial


def automatic_device_candidates[T](
    devices: Iterable[T], *, is_emulator: Callable[[T], bool]
) -> list[T]:
    """Keep inventory/explicit targets intact; filter and order automatic candidates."""
    enabled = emulator_discovery_enabled()
    eligible = [device for device in devices if enabled or not is_emulator(device)]
    return sorted(eligible, key=is_emulator)
