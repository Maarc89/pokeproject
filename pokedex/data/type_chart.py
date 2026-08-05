"""Pokemon type effectiveness (Generation VI onwards).

Static data, so there is no reason to spend an API call on it. Only the
non-neutral matchups are listed; anything absent is 1x.

``TYPE_CHART[attacking_type][defending_type]`` -> damage multiplier.
"""

TYPES = (
    "normal",
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
    "fairy",
)

TYPE_CHART: dict[str, dict[str, float]] = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire": {
        "fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0, "bug": 2.0,
        "rock": 0.5, "dragon": 0.5, "steel": 2.0,
    },
    "water": {
        "fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0,
        "dragon": 0.5,
    },
    "electric": {
        "water": 2.0, "electric": 0.5, "grass": 0.5, "ground": 0.0,
        "flying": 2.0, "dragon": 0.5,
    },
    "grass": {
        "fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5, "ground": 2.0,
        "flying": 0.5, "bug": 0.5, "rock": 2.0, "dragon": 0.5, "steel": 0.5,
    },
    "ice": {
        "fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 0.5, "ground": 2.0,
        "flying": 2.0, "dragon": 2.0, "steel": 0.5,
    },
    "fighting": {
        "normal": 2.0, "ice": 2.0, "poison": 0.5, "flying": 0.5, "psychic": 0.5,
        "bug": 0.5, "rock": 2.0, "ghost": 0.0, "dark": 2.0, "steel": 2.0,
        "fairy": 0.5,
    },
    "poison": {
        "grass": 2.0, "poison": 0.5, "ground": 0.5, "rock": 0.5, "ghost": 0.5,
        "steel": 0.0, "fairy": 2.0,
    },
    "ground": {
        "fire": 2.0, "electric": 2.0, "grass": 0.5, "poison": 2.0, "flying": 0.0,
        "bug": 0.5, "rock": 2.0, "steel": 2.0,
    },
    "flying": {
        "electric": 0.5, "grass": 2.0, "fighting": 2.0, "bug": 2.0, "rock": 0.5,
        "steel": 0.5,
    },
    "psychic": {
        "fighting": 2.0, "poison": 2.0, "psychic": 0.5, "dark": 0.0, "steel": 0.5,
    },
    "bug": {
        "fire": 0.5, "grass": 2.0, "fighting": 0.5, "poison": 0.5, "flying": 0.5,
        "psychic": 2.0, "ghost": 0.5, "dark": 2.0, "steel": 0.5, "fairy": 0.5,
    },
    "rock": {
        "fire": 2.0, "ice": 2.0, "fighting": 0.5, "ground": 0.5, "flying": 2.0,
        "bug": 2.0, "steel": 0.5,
    },
    "ghost": {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon": {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark": {
        "fighting": 0.5, "psychic": 2.0, "ghost": 2.0, "dark": 0.5, "fairy": 0.5,
    },
    "steel": {
        "fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2.0, "rock": 2.0,
        "steel": 0.5, "fairy": 2.0,
    },
    "fairy": {
        "fire": 0.5, "fighting": 2.0, "poison": 0.5, "dragon": 2.0, "dark": 2.0,
        "steel": 0.5,
    },
}


def effectiveness(attacking_type: str, defending_types) -> float:
    """Combined multiplier of one attacking type against a defender.

    Multipliers stack across the defender's types, so a 2x against both halves
    of a dual type is 4x. Unknown types are treated as neutral.
    """
    if not attacking_type:
        return 1.0

    row = TYPE_CHART.get(attacking_type.lower())
    if row is None:
        return 1.0

    multiplier = 1.0
    for defending_type in defending_types or ():
        multiplier *= row.get(defending_type.lower(), 1.0)
    return multiplier


def defensive_profile(defending_types) -> dict[str, list[dict]]:
    """How every attacking type fares against this defender.

    The mirror of ``effectiveness``: that answers "what does my move do to
    them", this answers "what should they fear". Neutral matchups are omitted --
    listing a dozen 1x rows tells the reader nothing.

    Each bucket is ordered worst-first for the defender, so the most urgent
    information comes first.
    """
    weaknesses, resistances, immunities = [], [], []

    for attacking in TYPES:
        multiplier = effectiveness(attacking, defending_types)
        entry = {"type": attacking, "multiplier": multiplier}
        if multiplier == 0:
            immunities.append(entry)
        elif multiplier > 1:
            weaknesses.append(entry)
        elif multiplier < 1:
            resistances.append(entry)

    weaknesses.sort(key=lambda entry: (-entry["multiplier"], entry["type"]))
    resistances.sort(key=lambda entry: (entry["multiplier"], entry["type"]))
    immunities.sort(key=lambda entry: entry["type"])

    return {
        "weaknesses": weaknesses,
        "resistances": resistances,
        "immunities": immunities,
    }


def describe(multiplier: float) -> str:
    """Human-readable label for a multiplier, for display next to the number."""
    if multiplier == 0:
        return "No effect"
    if multiplier >= 4:
        return "Devastating"
    if multiplier > 1:
        return "Super effective"
    if multiplier == 1:
        return "Neutral"
    if multiplier > 0.25:
        return "Not very effective"
    return "Barely scratches"
