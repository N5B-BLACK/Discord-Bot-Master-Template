"""
XP/level curve for the leveling system (Phase 2).

Uses the same curve popularized by MEE6/most Discord leveling bots so numbers
feel familiar to anyone who's used another leveling bot before:
    xp needed to go from level N to N+1 = 5*N^2 + 50*N + 100

This is intentionally a pure-math module with no DB/discord.py imports so it's
trivial to unit-test and reuse from both cogs/leveling.py (XP awarding) and any
future dashboard leaderboard page.
"""


def xp_to_next_level(level: int) -> int:
    """XP required to go from `level` to `level + 1`."""
    return 5 * (level ** 2) + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """Cumulative XP required to *reach* `level` from 0."""
    return sum(xp_to_next_level(n) for n in range(level))


def level_from_xp(total_xp: int) -> int:
    """Current level for a given amount of total accumulated XP."""
    level = 0
    remaining = total_xp
    while remaining >= xp_to_next_level(level):
        remaining -= xp_to_next_level(level)
        level += 1
    return level


def progress_in_level(total_xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_current_level, xp_needed_for_current_level)."""
    level = level_from_xp(total_xp)
    xp_into_level = total_xp - total_xp_for_level(level)
    return level, xp_into_level, xp_to_next_level(level)
