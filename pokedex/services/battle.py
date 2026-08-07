"""Battle simulation.

The original scoring was ``sum(base stats) + best move power``, later weighted by
type effectiveness. It still had three holes that fed each other: a defender's
Defense and Sp.Def never reduced anything, HP was never a pool, Speed decided
nothing, and the move was picked before the opponent was known -- so type
advantage could be observed but never *played*.

This module uses the real damage formula instead, and then runs the fight.

Every Pokemon is normalised to **level 50, 31 IVs, 85 EVs in every stat,
neutral nature**. Holding that fixed means a matchup depends only on the two
Pokemon rather than on invented training assumptions. 31 IVs is the competitive
standard and 85 EVs is the even spread across all six stats; the frailer 0/0
alternative makes everything so brittle that almost every fight is a one-hit
knockout, which is accurate to the formula but not much of a battle.

It also keeps the whole thing deterministic: no damage roll, no critical hits,
so a given pairing always produces the same battle and the tests can assert
exact numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..data.type_chart import describe, effectiveness
from .pokeapi import STAB_MULTIPLIER, STAT_ORDER, reliability

LEVEL = 50
IV = 31
EV = 85

# Both stat formulas share this term: floor((2 x base + IV + floor(EV/4)) x L/100).
# At level 50 that collapses to base + 26 for every stat, exactly.
_STAT_BASE_BONUS = (IV + EV // 4) // 2

# A fight that has not ended by here never will -- see `simulate`, which only
# reaches this when both sides deal damage but the numbers are absurd.
MAX_TURNS = 200

PHYSICAL = "physical"
SPECIAL = "special"

# Which stats a move uses, by damage class. This pairing is the whole reason
# `damage_class` is fetched, and it was previously stored and displayed but
# never actually read.
_STAT_PAIRS = {
    PHYSICAL: ("attack", "defense"),
    SPECIAL: ("special-attack", "special-defense"),
}


def hp_pool(base_hp: int) -> int:
    """Real HP at level 50. HP alone gets the extra + level + 10 term."""
    return int(base_hp) + _STAT_BASE_BONUS + LEVEL + 10


def battle_stat(base: int) -> int:
    """Any non-HP stat at level 50. Neutral nature, so no final multiplier."""
    return int(base) + _STAT_BASE_BONUS + 5


def _base(profile: dict, key: str) -> int:
    return int((profile.get("stats") or {}).get(key, 0))


@dataclass
class Attack:
    """What one Pokemon's chosen move does to the one in front of it.

    ``hit_damage`` is what a clean hit takes off. ``damage`` is what the move
    averages *per turn* once accuracy and any charge turn are accounted for, and
    is what the simulation and the move choice both run on.
    """

    move: dict | None
    damage: int
    hit_damage: int
    multiplier: float
    stab: bool
    category: str
    attack_stat: int
    defense_stat: int

    @property
    def effectiveness_label(self) -> str:
        return describe(self.multiplier)

    @property
    def accuracy(self) -> int:
        return int((self.move or {}).get("accuracy") or 100)

    @property
    def turn_cost(self) -> int:
        return int((self.move or {}).get("turn_cost") or 1)

    @property
    def is_unreliable(self) -> bool:
        """Whether per-turn damage differs from a clean hit, and so needs explaining."""
        return self.damage != self.hit_damage

    @property
    def is_physical(self) -> bool:
        return self.category == PHYSICAL

    @property
    def attack_label(self) -> str:
        """Which stat pairing produced this, for showing the maths."""
        return "Attack vs Defense" if self.is_physical else "Sp. Atk vs Sp. Def"


NO_ATTACK = Attack(
    move=None,
    damage=0,
    hit_damage=0,
    multiplier=1.0,
    stab=False,
    category=PHYSICAL,
    attack_stat=0,
    defense_stat=0,
)


def damage_of(move: dict, attacker: dict, defender: dict) -> Attack:
    """Apply the damage formula for one move.

        ((2 x Level / 5 + 2) x Power x A / D) / 50 + 2, then x STAB x type

    ``A``/``D`` are Attack/Defense for a physical move and Sp.Atk/Sp.Def for a
    special one. A move that lands at all does at least 1 damage; only an
    immunity produces 0.

    The result is then scaled to a per-turn average by the move's reliability,
    so a 90%-accurate move and one that needs a charge turn are both discounted
    against a move that simply lands every turn.
    """
    power = int(move.get("power") or 0)
    category = move.get("damage_class") or PHYSICAL
    attack_key, defense_key = _STAT_PAIRS.get(category, _STAT_PAIRS[PHYSICAL])

    attack_stat = battle_stat(_base(attacker, attack_key))
    # A defender with no data would otherwise divide by zero.
    defense_stat = max(1, battle_stat(_base(defender, defense_key)))

    move_type = move.get("type") or ""
    multiplier = effectiveness(move_type, defender.get("types") or [])
    stab = move_type in {t.lower() for t in attacker.get("types") or []}

    raw = ((2 * LEVEL / 5 + 2) * power * attack_stat / defense_stat) / 50 + 2
    on_hit = raw * (STAB_MULTIPLIER if stab else 1.0) * multiplier

    def landed(value: float) -> int:
        return 0 if multiplier == 0 else max(1, math.floor(value))

    return Attack(
        move=move,
        damage=landed(on_hit * reliability(move)),
        hit_damage=landed(on_hit),
        multiplier=multiplier,
        stab=stab,
        category=category,
        attack_stat=attack_stat,
        defense_stat=defense_stat,
    )


def choose_move(attacker: dict, defender: dict) -> Attack:
    """The attacker's best move *against this particular defender*.

    This is the part that makes typing playable rather than decorative: a
    Fire/Flying attacker drops Fire Blast for Earthquake when it is looking at
    a Rock type, because the damage is computed per matchup instead of once in
    advance.

    Falls back to the single stored ``best_move`` for profiles that predate the
    candidate moveset (anything cached or saved before this change).
    """
    candidates = attacker.get("moves") or []
    if not candidates:
        stored = attacker.get("best_move")
        candidates = [stored] if stored else []

    attacks = [
        damage_of(move, attacker, defender)
        for move in candidates
        # A move that faints its own user cannot win a war of attrition, and
        # modelling what it *does* win is well beyond this simulation.
        if move.get("power") and not move.get("self_ko")
    ]
    if not attacks:
        return NO_ATTACK

    # Name breaks ties so an even matchup always picks the same move.
    return max(attacks, key=lambda attack: (attack.damage, attack.move["name"]))


@dataclass
class Contender:
    """One side of a battle, with its attack already resolved against the other."""

    profile: dict
    hp: int
    speed: int
    attack: Attack
    # The opponent's HP pool, needed to report turns-to-KO. Filled in by
    # `resolve` once both sides exist, since neither can know it alone.
    opposing_hp: int = 0

    @property
    def name(self) -> str:
        return self.profile["name"]

    @property
    def types(self) -> list:
        return self.profile.get("types") or []

    @property
    def stat_total(self) -> int:
        return stat_score(self.profile)

    @property
    def best_move(self) -> dict | None:
        """The move actually chosen for this fight."""
        return self.attack.move

    @property
    def turns_to_ko(self) -> int | None:
        """Turns needed to faint the opponent, or None if it never can."""
        if self.attack.damage <= 0:
            return None
        return math.ceil(self.opposing_hp / self.attack.damage)


@dataclass
class Turn:
    """One blow, for the battle log."""

    number: int
    attacker_is_first: bool
    attacker_name: str
    defender_name: str
    move: dict | None
    damage: int
    multiplier: float
    stab: bool
    remaining_hp: int
    max_hp: int
    fainted: bool
    # Only an interactive game rolls, so these stay false for the deterministic
    # simulation. Defaulted so the one construction below, and every test that
    # asserts on it, is untouched -- and so `partials/_battle_log.html` renders
    # a played turn and a simulated one through the same code.
    missed: bool = False
    crit: bool = False
    # Set when the attacker could not act at all (charging or recharging).
    stalled: bool = False

    @property
    def effectiveness_label(self) -> str:
        return describe(self.multiplier)

    @property
    def remaining_pct(self) -> int:
        if not self.max_hp:
            return 0
        return round(self.remaining_hp / self.max_hp * 100)


@dataclass
class BattleOutcome:
    first: Contender
    second: Contender
    turns: list[Turn] = field(default_factory=list)

    @property
    def _final(self) -> Turn | None:
        """The knockout blow, if there was one."""
        if self.turns and self.turns[-1].fainted:
            return self.turns[-1]
        return None

    @property
    def is_tie(self) -> bool:
        return self._final is None

    # Explicit booleans, because the template must never identify the winner by
    # comparing names -- that broke for two Pokemon sharing a name.
    @property
    def first_won(self) -> bool:
        final = self._final
        return final is not None and final.attacker_is_first

    @property
    def second_won(self) -> bool:
        final = self._final
        return final is not None and not final.attacker_is_first

    @property
    def winner(self) -> Contender | None:
        if self._final is None:
            return None
        return self.first if self.first_won else self.second

    @property
    def loser(self) -> Contender | None:
        if self._final is None:
            return None
        return self.second if self.first_won else self.first

    @property
    def total_turns(self) -> int:
        return len(self.turns)

    @property
    def stalemate(self) -> bool:
        """Neither side can damage the other -- a tie, but for a distinct reason."""
        return not self.turns


def stat_score(profile: dict) -> int:
    stats = profile.get("stats") or {}
    return sum(int(stats.get(key, 0)) for key in STAT_ORDER)


def outspeeds(speed: int, total: int, other_speed: int, other_total: int) -> bool:
    """Whether a side acts first. Speed, then bulk, then declaration order.

    Spelled out separately from `_strikes_first` so the interactive battle can
    order its turns by exactly this rule rather than a copy of it -- a played
    game that resolved turns in a different order from the simulation of the
    same matchup would be a confusing bug to chase.
    """
    if speed != other_speed:
        return speed > other_speed
    if total != other_total:
        return total > other_total
    return True


def _strikes_first(first: Contender, second: Contender) -> bool:
    """Whether `first` acts first."""
    return outspeeds(first.speed, first.stat_total, second.speed, second.stat_total)


def simulate(first: Contender, second: Contender) -> list[Turn]:
    """Trade blows until someone faints, recording every one."""
    if first.attack.damage <= 0 and second.attack.damage <= 0:
        # Nothing can happen; don't emit 200 turns of zeroes.
        return []

    sides = (first, second)
    hp = [first.hp, second.hp]
    order = (0, 1) if _strikes_first(first, second) else (1, 0)

    turns: list[Turn] = []
    number = 0

    while number < MAX_TURNS:
        for attacker_index in order:
            defender_index = 1 - attacker_index
            attacker = sides[attacker_index]
            defender = sides[defender_index]

            number += 1
            hp[defender_index] = max(0, hp[defender_index] - attacker.attack.damage)
            fainted = hp[defender_index] == 0

            turns.append(
                Turn(
                    number=number,
                    attacker_is_first=attacker_index == 0,
                    attacker_name=attacker.name,
                    defender_name=defender.name,
                    move=attacker.attack.move,
                    damage=attacker.attack.damage,
                    multiplier=attacker.attack.multiplier,
                    stab=attacker.attack.stab,
                    remaining_hp=hp[defender_index],
                    max_hp=defender.hp,
                    fainted=fainted,
                )
            )

            if fainted:
                return turns

    return turns


def build_contender(profile: dict, opponent: dict) -> Contender:
    """Resolve one Pokemon's stats and its move choice against an opponent."""
    return Contender(
        profile=profile,
        hp=hp_pool(_base(profile, "hp")),
        speed=battle_stat(_base(profile, "speed")),
        attack=choose_move(profile, opponent),
    )


def resolve(first: dict, second: dict) -> BattleOutcome:
    """Run the battle between two Pokemon profiles."""
    one = build_contender(first, second)
    two = build_contender(second, first)

    # Each side needs the other's HP to report its own turns-to-KO.
    one.opposing_hp = two.hp
    two.opposing_hp = one.hp

    return BattleOutcome(first=one, second=two, turns=simulate(one, two))


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
