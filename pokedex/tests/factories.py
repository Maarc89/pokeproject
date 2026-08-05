"""Shared builders for test data."""


def profile(
    number=25,
    name="Pikachu",
    slug=None,
    types=("electric",),
    abilities=("Static",),
    stats=None,
    best_move=None,
    moves=None,
    sprite="https://example.invalid/pikachu.png",
    sprite_shiny="https://example.invalid/pikachu-shiny.png",
    artwork="https://example.invalid/pikachu-art.png",
    artwork_shiny="https://example.invalid/pikachu-art-shiny.png",
    height=4,
    weight=60,
    base_experience=112,
    cry="https://example.invalid/pikachu.ogg",
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
        "sprite_shiny": sprite_shiny,
        "artwork": artwork,
        "artwork_shiny": artwork_shiny,
        "stats": stats,
        "stat_total": sum(stats.values()),
        "stat_rows": [
            {"label": key.replace("-", " ").title(), "value": value}
            for key, value in stats.items()
        ],
        # Decimetres and hectograms, plus the metres/kilograms the templates use.
        "height": height,
        "weight": weight,
        "height_m": height / 10,
        "weight_kg": weight / 10,
        "base_experience": base_experience,
        "cry": cry,
        "best_move": best_move,
        # Defaults to just the signature move, so a fixture that only sets
        # best_move still battles exactly as it reads.
        "moves": list(moves) if moves is not None else ([best_move] if best_move else []),
    }


def move(
    name="thunderbolt",
    power=90,
    type="electric",
    damage_class="special",
    stab=False,
    accuracy=100,
    turn_cost=1,
    self_ko=False,
):
    return {
        "name": name,
        "display_name": name.replace("-", " ").title(),
        "power": power,
        "type": type,
        "damage_class": damage_class,
        "stab": stab,
        "accuracy": accuracy,
        "turn_cost": turn_cost,
        "self_ko": self_ko,
    }


def api_pokemon_payload(number=25, name="pikachu", types=("electric",), moves=()):
    """A PokeAPI /pokemon/<name> response, trimmed to the fields we read."""
    return {
        "id": number,
        "name": name,
        "types": [{"type": {"name": t}} for t in types],
        "abilities": [{"ability": {"name": "static"}}],
        "sprites": {
            "front_default": f"https://example.invalid/{name}.png",
            "front_shiny": f"https://example.invalid/{name}-shiny.png",
            "other": {
                "official-artwork": {
                    "front_default": f"https://example.invalid/{name}-art.png",
                    "front_shiny": f"https://example.invalid/{name}-art-shiny.png",
                },
            },
        },
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
        # Decimetres and hectograms, as the API reports them.
        "height": 4,
        "weight": 60,
        "base_experience": 112,
        "cries": {"latest": f"https://example.invalid/{name}.ogg"},
    }


def api_species_payload(
    number=25,
    name="pikachu",
    genus="Mouse Pokemon",
    flavor="When several of\nthese POKeMON\ngather, their\x0celectricity could\nbuild.",
    is_legendary=False,
    is_mythical=False,
    chain_url="https://pokeapi.co/api/v2/evolution-chain/10/",
):
    """A /pokemon-species/<id> response, trimmed to the fields we read."""
    return {
        "id": number,
        "name": name,
        "genera": [
            {"language": {"name": "ja"}, "genus": "Something else"},
            {"language": {"name": "en"}, "genus": genus},
        ],
        "flavor_text_entries": [
            {"language": {"name": "ja"}, "flavor_text": "japanese text"},
            {"language": {"name": "en"}, "flavor_text": flavor},
        ],
        "is_legendary": is_legendary,
        "is_mythical": is_mythical,
        "habitat": {"name": "forest"},
        "generation": {"name": "generation-i"},
        "evolution_chain": {"url": chain_url},
    }


def api_evolution_chain_payload():
    """A three-stage linear chain, as /evolution-chain/<id> returns it."""
    def node(number, name, evolves_to=(), level=None):
        return {
            "species": {
                "name": name,
                "url": f"https://pokeapi.co/api/v2/pokemon-species/{number}/",
            },
            "evolution_details": (
                [{"trigger": {"name": "level-up"}, "min_level": level}] if level else []
            ),
            "evolves_to": list(evolves_to),
        }

    return {
        "chain": node(
            4, "charmander",
            [node(5, "charmeleon", [node(6, "charizard", level=36)], level=16)],
        )
    }


def api_move_payload(
    name="thunderbolt", power=90, type="electric", accuracy=100, effect=""
):
    return {
        "id": 1,
        "name": name,
        "power": power,
        "accuracy": accuracy,
        "type": {"name": type},
        "damage_class": {"name": "special"},
        # short_effect is where a recharge, charge turn or self-KO is declared.
        "effect_entries": [{"language": {"name": "en"}, "short_effect": effect}],
    }
