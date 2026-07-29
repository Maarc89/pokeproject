"""Battle scoring.

The old scoring was ``sum(base stats) + best move power``, which ignored types
entirely -- so Charizard and Blastoise were decided purely on stat totals. Here
the attacker's strongest move is weighed against the defender's typing, which is
the part of Pokemon that actually matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.type_chart import describe, effectiveness
from .pokeapi import STAB_MULTIPLIER, STAT_ORDER

# How much the type-adjusted move contributes relative to raw stats. Base stat
# totals run ~200-700, move power ~40-150, so scaling the move up keeps type
# advantage meaningful without letting it swamp a large stat gap.
MOVE_WEIGHT = 2.0


@dataclass
class Contender:
    """One side of a battle, with its score broken down for display."""

    profile: dict
    stat_score: int
    move_power: int
    multiplier: float
    stab: bool
    move_score: int
    total: int

    @property
    def name(self):
        return self.profile["name"]

    @property
    def effectiveness_label(self):
        return describe(self.multiplier)

    @property
    def best_move(self):
        return self.profile.get("best_move")


@dataclass
class BattleOutcome:
    first: Contender
    second: Contender

    @property
    def is_tie(self):
        return self.first.total == self.second.total

    # Explicit booleans so the template never has to identify the winner by
    # comparing names, which broke for two Pokemon with the same name.
    @property
    def first_won(self):
        return self.first.total > self.second.total

    @property
    def second_won(self):
        return self.second.total > self.first.total

    @property
    def winner(self) -> Contender | None:
        if self.is_tie:
            return None
        return self.first if self.first.total > self.second.total else self.second

    @property
    def loser(self) -> Contender | None:
        if self.is_tie:
            return None
        return self.second if self.first.total > self.second.total else self.first


def stat_score(profile: dict) -> int:
    stats = profile.get("stats") or {}
    return sum(int(stats.get(key, 0)) for key in STAT_ORDER)


def score(attacker: dict, defender: dict) -> Contender:
    """Score one attacker against the Pokemon it is facing."""
    move = attacker.get("best_move")
    power = int(move["power"]) if move else 0
    move_type = move["type"] if move else ""
    stab = bool(move and move.get("stab"))

    multiplier = effectiveness(move_type, defender.get("types", []))
    base = stat_score(attacker)
    stab_bonus = STAB_MULTIPLIER if stab else 1.0
    weighted_move = round(power * multiplier * stab_bonus * MOVE_WEIGHT)

    return Contender(
        profile=attacker,
        stat_score=base,
        move_power=power,
        multiplier=multiplier,
        stab=stab,
        move_score=weighted_move,
        total=base + weighted_move,
    )


def resolve(first: dict, second: dict) -> BattleOutcome:
    """Score both sides against each other."""
    return BattleOutcome(first=score(first, second), second=score(second, first))


def stat_comparison(first: dict, second: dict) -> list[dict]:
    """Per-stat rows for the result table, with each side's share as a percent."""
    rows = []
    for key in STAT_ORDER:
        left = int((first.get("stats") or {}).get(key, 0))
        right = int((second.get("stats") or {}).get(key, 0))
        total = left + right
        rows.append(
            {
                "label": key.replace("-", " ").title(),
                "left": left,
                "right": right,
                # Percentages drive the width of the comparison bars.
                "left_pct": round(left / total * 100) if total else 50,
                "right_pct": round(right / total * 100) if total else 50,
                "leader": "left" if left > right else "right" if right > left else "",
            }
        )
    return rows
