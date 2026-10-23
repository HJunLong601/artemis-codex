"""Explicit, mutually exclusive native-client model profiles.

The inactive profile is data only: it is never merged into active routes or
used as a fallback. Files without this optional selector retain legacy routing.
"""

from copy import deepcopy

NATIVE_CLIENTS = frozenset({"codex", "claude_code"})


def active_native_client(config: dict) -> str | None:
    profiles = config.get("native_clients")
    if profiles is None:
        return None
    if isinstance(profiles, dict) and "active" not in profiles:
        return None
    if not isinstance(profiles, dict) or profiles.get("active") not in NATIVE_CLIENTS:
        raise ValueError("Native client selection must be codex or claude_code.")
    return profiles["active"]


def native_client_view(config: dict) -> dict:
    selected = active_native_client(config)
    if selected is None:
        return config
    if "planner" in config and "utils" in config:
        raise ValueError("Native client selection requires unified default/nodes configuration.")
    profile = config["native_clients"].get(selected)
    if not isinstance(profile, dict) or not isinstance(profile.get("default"), dict):
        raise ValueError("The selected native client profile is not configured.")
    nodes = profile.get("nodes", {})
    if not isinstance(nodes, dict):
        raise ValueError("Native client nodes must be a mapping.")
    for route in [profile["default"], *nodes.values()]:
        if not isinstance(route, dict) or route.get("provider", selected) != selected:
            raise ValueError("Native client profiles cannot use the other provider's routes.")
        fallback = route.get("fallback", {})
        if not isinstance(fallback, dict) or fallback.get("provider", selected) != selected:
            raise ValueError("Native client profiles cannot fall back to the other provider.")
    if profile["default"].get("provider") != selected:
        raise ValueError("Native client default provider must match the selected client.")
    return {**config, "default": deepcopy(profile["default"]), "nodes": deepcopy(nodes)}
