"""Client for the PokeAPI (https://pokeapi.co/).

All outbound HTTP for this project goes through here. The module exists mainly to
contain one problem: a Pokemon's ``moves`` list has 100-250 entries, and the only
way to learn a move's power is to fetch that move's own endpoint. Done naively
that is one blocking request per move.

Three things keep it cheap:

1. Callers that do not need move data pass ``include_moves=False`` (the default),
   which skips the fan-out entirely.
2. Move data is immutable once published, so it is cached effectively forever and
   only the cache misses are fetched.
3. Those misses are fetched concurrently rather than one at a time.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import requests
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_ROOT = "https://pokeapi.co/api/v2"
USER_AGENT = "pokeproject/1.0 (+https://github.com/Maarc89/pokeproject)"

# (connect timeout, read timeout). Never issue a request without one: an
# unresponsive upstream would otherwise pin a worker until the OS gives up.
TIMEOUT = (5, 10)

MOVE_FETCH_WORKERS = 12
PROFILE_CACHE_TTL = 60 * 60 * 24  # a day; sprites and stats change rarely
MOVE_CACHE_TTL = 60 * 60 * 24 * 30  # a month; move data is effectively immutable
# Flavour text, genus and evolution lines are as immutable as move data.
SPECIES_CACHE_TTL = 60 * 60 * 24 * 30

STAT_ORDER = (
    "hp",
    "attack",
    "defense",
    "special-attack",
    "special-defense",
    "speed",
)


class PokeAPIError(Exception):
    """Base class for every failure this module raises."""


class PokemonNotFound(PokeAPIError):
    """The upstream API has no such Pokemon."""


class PokeAPIUnavailable(PokeAPIError):
    """Upstream was unreachable, timed out, errored, or sent junk."""


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=MOVE_FETCH_WORKERS,
        pool_maxsize=MOVE_FETCH_WORKERS * 2,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    return session


# One session for the process: connection pooling and TLS session reuse are what
# make the concurrent move fan-out affordable.
_session = _build_session()


def normalize_name(raw: str) -> str:
    """Turn user input into a PokeAPI slug.

    PokeAPI uses hyphenated slugs, so "Mr Mime" is ``mr-mime`` -- not the
    percent-encoded "Mr%20Mime" the old code produced, which always 404'd.
    """
    return "-".join(raw.strip().lower().split())


def _get_json(url: str) -> dict:
    try:
        response = _session.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise PokeAPIUnavailable(f"Could not reach PokeAPI: {exc}") from exc

    if response.status_code == 404:
        raise PokemonNotFound(url)

    if response.status_code >= 400:
        raise PokeAPIUnavailable(f"PokeAPI returned HTTP {response.status_code} for {url}")

    try:
        return response.json()
    except ValueError as exc:
        raise PokeAPIUnavailable(f"PokeAPI sent a non-JSON response for {url}") from exc


# A move's raw power says nothing about whether it can actually be used every
# turn. Ranking on power alone hands almost every Pokemon a one-shot move like
# Explosion or Hyper Beam, which is the same failure the old stat-total scoring
# had in a different costume.
#
# PokeAPI's English short_effect is a fixed catalogue string, not free prose, so
# matching these phrases is reading structured data. Anything unrecognised falls
# through to "ordinary move", meaning a new phrase costs us accuracy rather than
# correctness.
_SELF_KO_PHRASE = "user faints"
_RECHARGE_PHRASE = "recharge"
_CHARGE_PHRASE = "turn to charge"


def _english_effect(payload: dict) -> str:
    for entry in payload.get("effect_entries") or []:
        if (entry.get("language") or {}).get("name") == "en":
            return entry.get("short_effect") or ""
    return ""


def _turn_cost(effect: str) -> int:
    """How many turns one use of this move really costs."""
    text = effect.lower()
    if _RECHARGE_PHRASE in text or _CHARGE_PHRASE in text:
        return 2
    return 1


def _fetch_move(name: str, url: str) -> dict | None:
    """Fetch one move. Returns None if it could not be retrieved."""
    try:
        payload = _get_json(url)
    except PokeAPIError:
        # A single unavailable move should weaken the result, not fail the battle.
        logger.warning("Skipping move %r: could not fetch %s", name, url)
        return None

    move_type = (payload.get("type") or {}).get("name") or ""
    damage_class = (payload.get("damage_class") or {}).get("name") or ""
    effect = _english_effect(payload)
    accuracy = payload.get("accuracy")

    return {
        "name": name,
        "display_name": name.replace("-", " ").title(),
        "power": payload.get("power") or 0,
        "type": move_type,
        "damage_class": damage_class,
        # A null accuracy means the move cannot miss (Swift, Aerial Ace).
        "accuracy": 100 if accuracy is None else int(accuracy),
        "turn_cost": _turn_cost(effect),
        "self_ko": _SELF_KO_PHRASE in effect.lower(),
    }


def reliability(move: dict) -> float:
    """Fraction of a move's power it actually delivers per turn.

    Accuracy because a move that misses deals nothing, and turn cost because a
    move that needs a charge or recharge turn only lands every other turn.
    """
    accuracy = move.get("accuracy")
    accuracy = 100 if accuracy is None else int(accuracy)
    return (accuracy / 100) / max(1, int(move.get("turn_cost") or 1))


def fetch_moves(move_entries: list[dict]) -> list[dict]:
    """Resolve a Pokemon's ``moves`` array into full move dicts.

    Cached moves are read in a single bulk lookup; only the misses hit the
    network, and those go out concurrently.
    """
    wanted = {
        entry["move"]["name"]: entry["move"]["url"]
        for entry in move_entries
        if entry.get("move", {}).get("url")
    }
    if not wanted:
        return []

    # The version segment is load-bearing: entries cached for a month under the
    # old shape have no accuracy or turn cost, and would silently battle wrong.
    keys = {name: f"pokeapi:move:v2:{name}" for name in wanted}
    cached = cache.get_many(list(keys.values()))

    moves: list[dict] = []
    missing: dict[str, str] = {}
    for name, url in wanted.items():
        hit = cached.get(keys[name])
        if hit is not None:
            moves.append(hit)
        else:
            missing[name] = url

    logger.debug("moves: %d cached, %d to fetch", len(moves), len(missing))

    if missing:
        fetched: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=MOVE_FETCH_WORKERS) as pool:
            for result in pool.map(
                lambda item: _fetch_move(*item), sorted(missing.items())
            ):
                if result is not None:
                    fetched[keys[result["name"]]] = result
                    moves.append(result)

        if fetched:
            cache.set_many(fetched, MOVE_CACHE_TTL)

    return moves


# Same Type Attack Bonus: in the games a Pokemon hits 50% harder with a move
# matching its own typing.
STAB_MULTIPLIER = 1.5


def best_damaging_move(moves: list[dict], own_types=()) -> dict | None:
    """The strongest damage-dealing move, or None if the Pokemon has none.

    Ranked by power *after* STAB rather than raw power. Without this, nearly
    every Pokemon's "best" move comes out as a universal high-power TM such as
    Hyper Beam or Explosion -- which makes the pick almost identical for
    everyone and means a Pokemon's own typing never affects the battle.
    """
    damaging = [move for move in moves if move["power"]]
    if not damaging:
        return None

    own = {t.lower() for t in own_types}

    def effective_power(move):
        stab = STAB_MULTIPLIER if move["type"] in own else 1.0
        # Name is a tiebreak so the choice is deterministic across runs.
        return (move["power"] * stab, move["name"])

    chosen = max(damaging, key=effective_power)
    return {**chosen, "stab": chosen["type"] in own}


def candidate_moveset(moves: list[dict]) -> list[dict]:
    """Prune a full move list down to the ones that could ever be the best pick.

    Damage depends on a move's effective power, its type, and its damage class.
    For a fixed (type, class) pair damage rises monotonically with effective
    power, so the strongest move of each pair *dominates* every other move in
    that pair against any defender whatsoever -- the rest can never win and are
    dropped.

    "Effective" rather than raw power is what keeps that argument honest once
    accuracy and charge turns are in play: a 150-power move that lands every
    other turn is beaten by a 90-power move that lands every turn.

    That bounds the set at 18 types x 2 classes = 36, and in practice lands
    around 10-20, from a raw list of 100-250. Small enough to store and to scan
    once per battle.
    """
    best: dict[tuple[str, str], tuple[float, dict]] = {}

    for move in moves:
        if not move.get("power") or move.get("self_ko"):
            continue

        effective = move["power"] * reliability(move)
        key = (move.get("type", ""), move.get("damage_class", ""))
        current = best.get(key)
        # Name breaks ties so the set is stable across runs.
        if current is None or (effective, current[1]["name"]) > (current[0], move["name"]):
            best[key] = (effective, move)

    return sorted((move for _, move in best.values()), key=lambda move: move["name"])


def _build_profile(payload: dict, *, include_moves: bool) -> dict:
    stats = {
        stat["stat"]["name"]: int(stat["base_stat"])
        for stat in payload.get("stats", [])
    }

    sprites = payload.get("sprites") or {}
    # "other" holds the higher-quality renders; official-artwork is the poster
    # art, and it carries a shiny variant of its own.
    artwork = (sprites.get("other") or {}).get("official-artwork") or {}

    profile = {
        "number": int(payload["id"]),
        "slug": payload["name"],
        "name": str(payload["name"]).replace("-", " ").title(),
        # A list, not a joined string, so templates can render one badge per type.
        "types": [entry["type"]["name"] for entry in payload.get("types", [])],
        "abilities": [
            entry["ability"]["name"].replace("-", " ").title()
            for entry in payload.get("abilities", [])
        ],
        # front_default is legitimately null for some forms.
        "sprite": sprites.get("front_default") or "",
        "sprite_shiny": sprites.get("front_shiny") or "",
        "artwork": artwork.get("front_default") or "",
        "artwork_shiny": artwork.get("front_shiny") or "",
        "stats": stats,
        # Ordered, display-ready rows. The template used |cut:"-"|title, which
        # turned "special-attack" into "Specialattack"; Django has no filter
        # that swaps a hyphen for a space, so the label is built here.
        "stat_rows": [
            {"label": key.replace("-", " ").title(), "value": stats.get(key, 0)}
            for key in STAT_ORDER
        ],
        "stat_total": sum(stats.get(key, 0) for key in STAT_ORDER),
        # The API reports decimetres and hectograms. Both are kept: the raw
        # value is what gets stored, and the converted one is what templates
        # show -- Django has no arithmetic filter to divide by ten.
        "height": int(payload.get("height") or 0),
        "weight": int(payload.get("weight") or 0),
        "height_m": int(payload.get("height") or 0) / 10,
        "weight_kg": int(payload.get("weight") or 0) / 10,
        "base_experience": int(payload.get("base_experience") or 0),
        "cry": (payload.get("cries") or {}).get("latest") or "",
        "best_move": None,
        # The moves a battle may choose between. Empty on the cheap path.
        "moves": [],
    }

    if include_moves:
        moves = fetch_moves(payload.get("moves", []))
        profile["moves"] = candidate_moveset(moves)
        # Still recorded separately: the Pokedex lists a Pokemon's signature
        # move without any opponent in mind, so it cannot use the battle's
        # matchup-aware choice.
        profile["best_move"] = best_damaging_move(moves, profile["types"])

    return profile


def _clean_flavor_text(raw: str) -> str:
    """Flatten a Pokedex entry into one line.

    The API embeds the original games' line breaks as real newlines and, on the
    page break, a form feed (\\x0c). Rendered as-is they show up as stray gaps
    mid-sentence.
    """
    return " ".join(raw.replace("\x0c", " ").split())


def _first_english(entries: list[dict], key: str) -> str:
    for entry in entries or []:
        if (entry.get("language") or {}).get("name") == "en":
            return entry.get(key) or ""
    return ""


def _species_id(url: str) -> int | None:
    """The numeric id at the end of a PokeAPI resource URL."""
    parts = [part for part in (url or "").split("/") if part]
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return None


def _describe_trigger(details: list[dict]) -> str:
    """How one Pokemon becomes the next, in a few words.

    ``evolution_details`` holds one entry per game version, and they genuinely
    disagree -- Leafeon is a mossy rock in Diamond/Pearl and a Leaf Stone in
    Sword/Shield. Taking the first entry would report whichever game happens to
    come first in the list, so the one flagged ``is_default`` wins.
    """
    if not details:
        return ""

    detail = next((d for d in details if d.get("is_default")), details[0])

    trigger = (detail.get("trigger") or {}).get("name") or ""
    item = (detail.get("item") or {}).get("name") or ""
    time_of_day = detail.get("time_of_day") or ""
    move_type = (detail.get("known_move_type") or {}).get("name") or ""
    location = (detail.get("location") or {}).get("name") or ""

    if item:
        return item.replace("-", " ").title()

    if detail.get("min_level"):
        label = f"Level {detail['min_level']}"
        return f"{label} ({time_of_day})" if time_of_day else label

    if detail.get("min_happiness") or detail.get("min_affection"):
        label = "High friendship"
        if move_type:
            return f"{label}, knowing a {move_type.title()} move"
        return f"{label} ({time_of_day})" if time_of_day else label

    if move_type:
        return f"Knowing a {move_type.title()} move"

    if location:
        return f"Level up at {location.replace('-', ' ').title()}"

    if trigger == "trade":
        return "Trade"

    return trigger.replace("-", " ").capitalize()


def fetch_evolution_chain(url: str) -> list[list[dict]]:
    """The evolution family, flattened into one list per stage.

    A chain is a tree, not a line -- Eevee has eight branches -- so stages are
    grouped by depth and each stage may hold several Pokemon.

    Cached against the chain URL rather than any one Pokemon: a whole family
    shares a single chain, so looking up Charmeleon after Charmander is free.
    """
    if not url:
        return []

    # v2 picks the is_default game version for the trigger text.
    cache_key = f"pokeapi:evolution:v2:{url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _get_json(url)

    stages: list[list[dict]] = []

    def walk(node: dict, depth: int) -> None:
        while len(stages) <= depth:
            stages.append([])

        species = node.get("species") or {}
        number = _species_id(species.get("url", ""))
        slug = species.get("name") or ""

        stages[depth].append({
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "number": number,
            # Built rather than fetched: one request per family member just for
            # a thumbnail would cost more than the chain itself. The sprite repo
            # is keyed by id, and app.js already handles a URL that 404s.
            "sprite": (
                f"https://raw.githubusercontent.com/PokeAPI/sprites/master"
                f"/sprites/pokemon/{number}.png"
                if number
                else ""
            ),
            "trigger": _describe_trigger(node.get("evolution_details") or []),
        })

        for child in node.get("evolves_to") or []:
            walk(child, depth + 1)

    walk(payload.get("chain") or {}, 0)

    cache.set(cache_key, stages, SPECIES_CACHE_TTL)
    return stages


def fetch_species_details(number: int, *, include_evolutions: bool = True) -> dict:
    """The Pokedex-entry half of a Pokemon: description, genus, family.

    Separate from ``fetch_pokemon_profile`` because it is a separate endpoint
    and separately cacheable -- and because it is enrichment, so a caller can
    let it fail without losing the Pokemon itself.
    """
    # This entry embeds a *copy* of the flattened evolution chain, so bumping
    # the chain's own version is not enough -- this one has to move with it.
    cache_key = f"pokeapi:species:v2:{number}:{int(include_evolutions)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _get_json(f"{API_ROOT}/pokemon-species/{number}")

    chain_url = (payload.get("evolution_chain") or {}).get("url") or ""

    details = {
        "genus": _first_english(payload.get("genera") or [], "genus"),
        "flavor_text": _clean_flavor_text(
            _first_english(payload.get("flavor_text_entries") or [], "flavor_text")
        ),
        "is_legendary": bool(payload.get("is_legendary")),
        "is_mythical": bool(payload.get("is_mythical")),
        "habitat": ((payload.get("habitat") or {}).get("name") or "").replace("-", " ").title(),
        "generation": (
            (payload.get("generation") or {}).get("name") or ""
        ).replace("generation-", "").upper(),
        "evolutions": [],
    }

    if include_evolutions and chain_url:
        details["evolutions"] = fetch_evolution_chain(chain_url)

    cache.set(cache_key, details, SPECIES_CACHE_TTL)
    return details


def fetch_pokemon_profile(name: str, *, include_moves: bool = False) -> dict:
    """Look up a Pokemon by name or number.

    ``include_moves`` is off by default because it is the expensive path and only
    the battle screen needs it. Raises PokemonNotFound or PokeAPIUnavailable.
    """
    slug = normalize_name(name)
    if not slug:
        raise PokemonNotFound("empty name")

    # v3 adds stat_rows, height/weight and the cry. Bump this whenever the
    # profile dict changes shape: entries live a day, and a stale one renders
    # as a silently missing section rather than an error.
    cache_key = f"pokeapi:profile:v3:{slug}:{int(include_moves)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _get_json(f"{API_ROOT}/pokemon/{slug}")

    try:
        profile = _build_profile(payload, include_moves=include_moves)
    except (KeyError, TypeError, ValueError) as exc:
        raise PokeAPIUnavailable(f"Unexpected PokeAPI payload for {slug!r}") from exc

    cache.set(cache_key, profile, PROFILE_CACHE_TTL)
    return profile
