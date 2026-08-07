"""Interactive battle: the same fight, but the player picks the move.

`battle.py` simulates a matchup end to end and hands back a replay. This module
turns that into a game -- one round per request, the player choosing an attack
and an AI answering with `battle.choose_move`.

Two deliberate differences from the simulation:

**It rolls.** Real damage variance (85-100%), critical hits, and an actual
accuracy check, because a game wants the tension and a comparison does not.
`reliability` still governs *move selection* on both sides -- it is the expected
damage a move delivers, which is the right thing to choose on and the quantity
`candidate_moveset`'s pruning argument depends on -- but it no longer stands in
for resolving the hit.

**It has no state of its own.** Everything lives in a `GameState` the caller
owns, so the engine stays free of the database and its tests need no fixtures.

The rolls are seeded, and seeded in a very particular way: see `rng_for`.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, fields

from .battle import (
    Turn,
    battle_stat,
    choose_move,
    damage_of,
    hp_pool,
    outspeeds,
    stat_score,
)
from .pokeapi import reliability

# Generation VI, matching the type chart this project already uses.
CRIT_CHANCE = 1 / 16
CRIT_MULTIPLIER = 1.5

# The games' damage roll: a uniform 85-100% of the computed damage.
MIN_ROLL = 85
MAX_ROLL = 100

# Four moves, as in the games. The point of the limit is that it makes the
# choice a real one -- with sixteen buttons there is only ever one right answer
# and no reason to think about it.
MOVE_SLOTS = 4

# Rounds, not turns: both sides act in a round. A fight this long is not going
# to end, and the log would be unreadable anyway.
MAX_ROUNDS = 100

PLAYER = "player"
OPPONENT = "opponent"


def rng_for(seed: int, turn: int, actor: str, move_name: str) -> random.Random:
    """The random stream for one side's action on one turn.

    The whole anti-cheat design rests on this being a pure function of the
    *decision* rather than of when it happens to be computed. Submitting the
    same move on the same turn always produces the same rolls, so refreshing
    cannot turn a miss into a critical hit. The move name is in the key so that
    picking a different move genuinely re-rolls; the turn guard in the view is
    what stops a player going back and trying each move in turn to find out.

    Seeding `random.Random` with a string runs it through SHA-512, which is
    stable across processes and platforms. `hash()` is *not* -- it is salted per
    process, and using it here would make every test unreproducible.
    """
    return random.Random(f"{seed}:{turn}:{actor}:{move_name}")


@dataclass
class HitResult:
    """What one rolled attack did."""

    move: dict | None
    damage: int
    multiplier: float
    stab: bool
    missed: bool = False
    crit: bool = False

    @property
    def immune(self) -> bool:
        return self.multiplier == 0


def resolve_hit(move: dict, attacker: dict, defender: dict, rng: random.Random) -> HitResult:
    """Roll one attack.

    Builds on `damage_of`, which already applies the damage formula, STAB and
    type effectiveness and hands back `hit_damage` -- a clean hit, before the
    per-turn reliability discount. That is exactly the number a rolled hit
    wants, so the deterministic engine needs no changes at all to support this.

    The roll is applied to the finished figure rather than threaded through the
    formula. It shifts the result by at most a point against the games' own
    rounding, and it keeps the bound obvious: a non-critical hit always lands in
    [floor(hit x 0.85), hit].
    """
    base = damage_of(move, attacker, defender)

    # Drawn unconditionally, even at 100 accuracy, so that the sequence of draws
    # does not depend on the move's accuracy.
    accuracy_roll = rng.randint(1, 100)
    crit = rng.random() < CRIT_CHANCE
    damage_roll = rng.randint(MIN_ROLL, MAX_ROLL)

    # An immunity is not a miss, and should not be reported as one -- "it had no
    # effect" and "it missed" tell the player to do very different things.
    if base.multiplier == 0:
        return HitResult(move=move, damage=0, multiplier=0.0, stab=base.stab)

    if accuracy_roll > int(move.get("accuracy") or 100):
        return HitResult(
            move=move,
            damage=0,
            multiplier=base.multiplier,
            stab=base.stab,
            missed=True,
        )

    damage = base.hit_damage * damage_roll / 100
    if crit:
        damage *= CRIT_MULTIPLIER

    return HitResult(
        move=move,
        # A move that lands does at least a point, as in `damage_of`.
        damage=max(1, math.floor(damage)),
        multiplier=base.multiplier,
        stab=base.stab,
        crit=crit,
    )


def player_moveset(moves: list[dict], limit: int = MOVE_SLOTS) -> list[dict]:
    """The four moves a Pokemon brings to a played battle.

    Ranked on effective power, the same quantity `candidate_moveset` prunes on.
    Because that set already holds at most one move per (type, damage class),
    taking the top few gives a type-diverse set for free rather than four
    variations on the same attack.
    """
    usable = [move for move in moves if move.get("power") and not move.get("self_ko")]
    # Name breaks ties so a Pokemon always brings the same four moves.
    ranked = sorted(usable, key=lambda move: (-(move["power"] * reliability(move)), move["name"]))
    return sorted(ranked[:limit], key=lambda move: move["name"])


def species_to_profile(species, moves: list[dict]) -> dict:
    """A stored `PokemonSpecies` in the shape the battle engine expects.

    The engine takes plain dicts so it does not care whether a Pokemon came from
    PokeAPI or from the database. This is the seam for the stored side, and it
    is what lets a played turn run without a single outbound request: the stats,
    types and moveset a battle needs are all already local.

    Issues no queries -- the caller passes the moves, so the engine and its
    tests stay clear of the database.
    """
    return {
        "number": species.number,
        "slug": species.slug,
        "name": species.name,
        "types": list(species.types or []),
        "stats": dict(species.stats or {}),
        "sprite": species.sprite,
        "sprite_shiny": species.sprite_shiny,
        "artwork": species.artwork,
        "artwork_shiny": species.artwork_shiny,
        "moves": list(moves),
    }


@dataclass
class GameState:
    """Everything a round needs, and nothing about how it is stored."""

    seed: int
    player: dict
    opponent: dict
    player_hp: int
    opponent_hp: int
    player_max_hp: int
    opponent_max_hp: int
    turn_number: int = 0
    player_recharging: bool = False
    opponent_recharging: bool = False
    result: str = ""

    @classmethod
    def start(cls, seed: int, player: dict, opponent: dict) -> GameState:
        player_hp = hp_pool((player.get("stats") or {}).get("hp", 0))
        opponent_hp = hp_pool((opponent.get("stats") or {}).get("hp", 0))
        return cls(
            seed=seed,
            player=player,
            opponent=opponent,
            player_hp=player_hp,
            opponent_hp=opponent_hp,
            player_max_hp=player_hp,
            opponent_max_hp=opponent_hp,
        )

    @property
    def is_finished(self) -> bool:
        return bool(self.result)

    def _speed(self, profile: dict) -> int:
        return battle_stat((profile.get("stats") or {}).get("speed", 0))

    @property
    def player_moves_first(self) -> bool:
        """Fixed for the whole game: with no stat stages, Speed never changes."""
        return outspeeds(
            self._speed(self.player),
            stat_score(self.player),
            self._speed(self.opponent),
            stat_score(self.opponent),
        )


def find_move(moves: list[dict], name: str) -> dict | None:
    return next((move for move in moves if move["name"] == name), None)


def _stalled_turn(number: int, is_player: bool, attacker: str, defender: str,
                  remaining_hp: int, max_hp: int) -> Turn:
    """A turn spent charging or recharging rather than attacking."""
    return Turn(
        number=number,
        attacker_is_first=is_player,
        attacker_name=attacker,
        defender_name=defender,
        move=None,
        damage=0,
        multiplier=1.0,
        stab=False,
        remaining_hp=remaining_hp,
        max_hp=max_hp,
        fainted=False,
        stalled=True,
    )


def advance(state: GameState, move_name: str, log_length: int = 0) -> list[Turn]:
    """Play one round: the player's move, then the opponent's, in Speed order.

    Mutates `state` and returns just this round's turns, which the caller
    appends to the stored log. `log_length` is only there to keep `Turn.number`
    running across the whole battle rather than restarting each round.
    """
    if state.is_finished:
        return []

    player_move = find_move(state.player.get("moves") or [], move_name)

    # The AI picks against the player it is actually facing, which is the whole
    # reason `choose_move` takes a defender. It is choosing from the same four
    # moves the player can see, because `species_to_profile` was handed a
    # `player_moveset` -- the opponent does not get a bigger book.
    opponent_move = choose_move(state.opponent, state.player).move

    order = (PLAYER, OPPONENT) if state.player_moves_first else (OPPONENT, PLAYER)
    turns: list[Turn] = []
    number = log_length

    for actor in order:
        is_player = actor == PLAYER
        attacker = state.player if is_player else state.opponent
        defender = state.opponent if is_player else state.player
        move = player_move if is_player else opponent_move
        recharging = state.player_recharging if is_player else state.opponent_recharging
        defender_hp = state.opponent_hp if is_player else state.player_hp
        defender_max = state.opponent_max_hp if is_player else state.player_max_hp

        number += 1

        if recharging:
            # The cost of last turn's move. Clear it, lose the action.
            if is_player:
                state.player_recharging = False
            else:
                state.opponent_recharging = False
            turns.append(
                _stalled_turn(
                    number, is_player, attacker["name"], defender["name"],
                    defender_hp, defender_max,
                )
            )
            continue

        if move is None:
            turns.append(
                Turn(
                    number=number,
                    attacker_is_first=is_player,
                    attacker_name=attacker["name"],
                    defender_name=defender["name"],
                    move=None,
                    damage=0,
                    multiplier=1.0,
                    stab=False,
                    remaining_hp=defender_hp,
                    max_hp=defender_max,
                    fainted=False,
                )
            )
            continue

        hit = resolve_hit(
            move,
            attacker,
            defender,
            rng_for(state.seed, state.turn_number, actor, move["name"]),
        )

        remaining = max(0, defender_hp - hit.damage)
        if is_player:
            state.opponent_hp = remaining
        else:
            state.player_hp = remaining

        # A charge or recharge move costs the following action.
        if int(move.get("turn_cost") or 1) > 1:
            if is_player:
                state.player_recharging = True
            else:
                state.opponent_recharging = True

        fainted = remaining == 0
        turns.append(
            Turn(
                number=number,
                attacker_is_first=is_player,
                attacker_name=attacker["name"],
                defender_name=defender["name"],
                move=move,
                damage=hit.damage,
                multiplier=hit.multiplier,
                stab=hit.stab,
                remaining_hp=remaining,
                max_hp=defender_max,
                fainted=fainted,
                missed=hit.missed,
                crit=hit.crit,
            )
        )

        if fainted:
            state.result = "won" if is_player else "lost"
            break

    state.turn_number += 1

    if not state.is_finished and state.turn_number >= MAX_ROUNDS:
        state.result = "tie"

    return turns


# Turns are stored as JSON on the game and rehydrated for the template, which
# means `partials/_battle_log.html` renders a played battle and a simulated one
# through exactly the same markup.
_TURN_FIELDS = {entry.name for entry in fields(Turn)}


def turn_to_json(turn: Turn) -> dict:
    return asdict(turn)


def turn_from_json(payload: dict) -> Turn:
    """Rebuild a Turn, tolerating a log written before a field existed."""
    return Turn(**{key: value for key, value in payload.items() if key in _TURN_FIELDS})
