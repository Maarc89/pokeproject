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
    return {
        "name": name,
        "display_name": name.replace("-", " ").title(),
        "power": payload.get("power") or 0,
        "type": move_type,
        "damage_class": damage_class,
    }


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

    keys = {name: f"pokeapi:move:{name}" for name in wanted}
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


def _build_profile(payload: dict, *, include_moves: bool) -> dict:
    stats = {
        stat["stat"]["name"]: int(stat["base_stat"])
        for stat in payload.get("stats", [])
    }

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
        "sprite": (payload.get("sprites") or {}).get("front_default") or "",
        "stats": stats,
        "stat_total": sum(stats.get(key, 0) for key in STAT_ORDER),
        "best_move": None,
    }

    if include_moves:
        profile["best_move"] = best_damaging_move(
            fetch_moves(payload.get("moves", [])), profile["types"]
        )

    return profile


def fetch_pokemon_profile(name: str, *, include_moves: bool = False) -> dict:
    """Look up a Pokemon by name or number.

    ``include_moves`` is off by default because it is the expensive path and only
    the battle screen needs it. Raises PokemonNotFound or PokeAPIUnavailable.
    """
    slug = normalize_name(name)
    if not slug:
        raise PokemonNotFound("empty name")

    cache_key = f"pokeapi:profile:{slug}:{int(include_moves)}"
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
