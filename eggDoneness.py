"""Simple fixture module used by legacy tests."""

from __future__ import annotations


def egg_doneness(seconds: int) -> dict[str, str]:
    """Return a coarse egg state for the legacy test suite."""
    if seconds < 2:
        return {"name": "날계란", "emoji": "🥚💧"}
    if seconds < 5:
        return {"name": "반반숙", "emoji": "흐르는 느낌"}
    if seconds < 10:
        return {"name": "반숙", "emoji": "🟡🏆"}
    return {"name": "터짐", "emoji": "💥💀"}
