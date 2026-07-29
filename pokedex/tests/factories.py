"""Shared builders for test data."""


def profile(
    number=25,
    name="Pikachu",
    slug=None,
    types=("electric",),
    abilities=("Static",),
    stats=None,
    best_move=None,
    sprite="https://example.invalid/pikachu.png",
):
    # PokemonSpecies.slug is unique, so it has to track the name -- otherwise
    # two differently-numbered fixtures collide on insert.
    slug = slug or name.strip().lower().replace(" ", "-")
    stats = stats or {
        "hp": 35,
        "attack": 55,
        "defense": 40,
        "special-attack": 50,
        "special-defense": 50,
        "speed": 90,
    }
    return {
        "number": number,
        "name": name,
        "slug": slug,
        "types": list(types),
        "abilities": list(abilities),
        "sprite": sprite,
        "stats": stats,
        "stat_total": sum(stats.values()),
        "best_move": best_move,
    }


def move(name="thunderbolt", power=90, type="electric", damage_class="special", stab=False):
    return {
        "name": name,
        "display_name": name.replace("-", " ").title(),
        "power": power,
        "type": type,
        "damage_class": damage_class,
        "stab": stab,
    }


def api_pokemon_payload(number=25, name="pikachu", types=("electric",), moves=()):
    """A PokeAPI /pokemon/<name> response, trimmed to the fields we read."""
    return {
        "id": number,
        "name": name,
        "types": [{"type": {"name": t}} for t in types],
        "abilities": [{"ability": {"name": "static"}}],
        "sprites": {"front_default": f"https://example.invalid/{name}.png"},
        "stats": [
            {"stat": {"name": "hp"}, "base_stat": 35},
            {"stat": {"name": "attack"}, "base_stat": 55},
            {"stat": {"name": "defense"}, "base_stat": 40},
            {"stat": {"name": "special-attack"}, "base_stat": 50},
            {"stat": {"name": "special-defense"}, "base_stat": 50},
            {"stat": {"name": "speed"}, "base_stat": 90},
        ],
        "moves": [
            {"move": {"name": m, "url": f"https://pokeapi.co/api/v2/move/{m}/"}}
            for m in moves
        ],
    }


def api_move_payload(name="thunderbolt", power=90, type="electric"):
    return {
        "id": 1,
        "name": name,
        "power": power,
        "type": {"name": type},
        "damage_class": {"name": "special"},
    }
