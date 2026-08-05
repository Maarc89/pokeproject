# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The suite requires a real Postgres — there is no SQLite fallback. `docker-compose.override.yml`
publishes the Compose database on **5433**, so a local run needs the host and port overridden:

```bash
docker compose up -d db

DJANGO_DEBUG=1 DJANGO_SECRET_KEY=dev-only DJANGO_CACHE_BACKEND=locmem \
POSTGRES_PASSWORD=<from .env> POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433 \
python manage.py test
```

`DJANGO_DEBUG=1` matters here: manifest static storage is off under DEBUG, so tests render
templates without a prior `collectstatic`. CI sets the same flag for the same reason.

```bash
python manage.py test pokedex.tests.test_battle                     # one module
python manage.py test pokedex.tests.test_views.SearchTests          # one class
python manage.py test pokedex.tests.test_views.SearchTests.test_search_renders_the_pokemon

ruff check .
coverage run manage.py test && coverage report
python manage.py makemigrations --check --dry-run                   # CI fails if migrations drifted
```

Production config is verified separately, and CI fails on any warning. The generated key is
required — a short literal trips `security.W009`, which is the check working as intended:

```bash
DJANGO_SECRET_KEY=$(python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())') \
DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS=example.com POSTGRES_PASSWORD=x \
python manage.py check --deploy --fail-level WARNING
```

`.env` cannot be `source`d from bash — `DJANGO_SECRET_KEY` contains unquoted shell metacharacters
(`)`, `&`, `#`). Parse out the one variable you need instead.

## Architecture

A single Django app (`pokedex`) wrapping PokeAPI, with all outbound HTTP isolated in
`services/pokeapi.py` and all scoring in `services/battle.py`. Views orchestrate; they do not
fetch or compute.

### The cost problem that shapes everything

Finding a Pokemon's strongest move means reading every move it can learn, and PokeAPI exposes
those one endpoint at a time — 100–250 requests per Pokemon. Three mechanisms contain this, and
changes should not undermine them:

1. **`include_moves=False` is the default** on `fetch_pokemon_profile`. Search never passes it;
   only `battle_result` does. Adding move data to a page silently makes it hundreds of times more
   expensive.
2. **Move data is cached for a month** (it is immutable once published) and `fetch_moves` reads
   hits with a single `cache.get_many`, so only misses hit the network.
3. **Misses are fetched concurrently** through one module-level `requests.Session` — connection
   pooling and TLS reuse are what make the fan-out affordable.

The cache is therefore load-bearing, not an optimisation. Under Gunicorn, `locmem` is per-worker;
set `DJANGO_CACHE_BACKEND=database` so workers share one cache (and run `createcachetable`).

### Data model

`PokemonSpecies` is shared across all users — one row per Pokemon, not one per user. `SavedPokemon`
is the per-user join onto it, with a uniqueness constraint on `(user, species)`. `Move` is stored
locally for the same immutability reason the cache exists. `Battle.winner = NULL` means a tie.

`views.sync_species` is the single write path from an API profile to the database. It uses
`update_or_create` so existing rows pick up corrected upstream data, and deliberately **only
overwrites `best_move` when the fetch actually resolved one** — otherwise a cheap search would wipe
the move a previous battle lookup stored.

### Battle simulation

`services/battle.py` runs the real damage formula and then simulates turns. Every Pokemon is
normalised to **level 50, 31 IVs, 85 EVs, neutral nature** (`hp_pool` / `battle_stat`), which keeps
a matchup dependent only on the two Pokemon and keeps everything deterministic — no damage roll, no
crits — so tests assert exact numbers.

```
raw    = ((2·50/5 + 2) · power · A/D) / 50 + 2
damage = floor(raw · STAB · type_effectiveness · reliability)
```

`A`/`D` come from `damage_class`: Attack/Defense for physical, Sp.Atk/Sp.Def for special. The type
multiplier is the static 18×18 chart in `data/type_chart.py` (only non-neutral matchups listed;
absent means 1x, and multipliers stack across a dual type, so 4x is reachable).

Four things are intentional and easy to regress:

- **Moves are chosen per matchup.** `choose_move` scores every candidate against *this* defender, so
  a Fire attacker drops its Fire move for Ground against a Rock type. Picking one move up front is
  what made typing unplayable in the old scoring.
- **`reliability` discounts unusable moves.** Accuracy and charge/recharge turns scale damage to a
  per-turn average, and self-KO moves are excluded outright. Without this, ranking by power hands
  nearly every Pokemon Explosion or Hyper Beam — the same failure the old scoring had. `Attack`
  carries both `damage` (per-turn average) and `hit_damage` (a clean hit).
- **`candidate_moveset` prunes on effective power, not raw power.** The dominance argument that
  bounds the set at one move per (type, damage class) only holds for the quantity damage is
  monotonic in.
- **`BattleOutcome` exposes explicit `first_won` / `second_won`.** Templates must not identify the
  winner by comparing names — that broke for two Pokemon sharing a name. `Turn.attacker_is_first`
  exists for the same reason.

### Cache versioning

Every cached shape carries a version segment — `pokeapi:profile:v3:`, `pokeapi:move:v2:`,
`pokeapi:species:v2:`, `pokeapi:evolution:v2:`. **Change the shape, bump the segment.** Entries live
a day to a month, and under `DJANGO_CACHE_BACKEND=database` they survive a container rebuild, so a
stale one shows up as a silently missing section rather than an error.

`pokeapi:species:` embeds a *copy* of the flattened evolution chain, so bumping
`pokeapi:evolution:` alone does not reach it — both have to move together.

### Templates and progressive enhancement

`main/_pokemon_detail.html` is the search result on its own. `index` returns it bare for
`?partial=1` and embeds it via `{% include %}` otherwise, so the live-search swap and the full page
render cannot drift apart. `app.js` delegates every handler at the document — anything bound
per-element would be lost when a search replaces the result container.

Shiny state and every filter live in the URL, never the session, so pages stay linkable and work
with JavaScript off. Controls that change one parameter use `{% querystring %}` to carry the rest —
and must pass `partial=None`, or a link rendered inside a partial fetch will navigate to a bare
fragment.

### Errors and settings

`pokeapi` raises only `PokemonNotFound` (→ `main/404.html`, 404) or `PokeAPIUnavailable`
(→ `main/upstream_error.html`, 503); views catch both. A single unfetchable move is logged and
skipped rather than failing the whole battle.

Settings are environment-driven and **fail closed**: `DEBUG` defaults off, and `DJANGO_SECRET_KEY`
and `POSTGRES_PASSWORD` raise `ImproperlyConfigured` when it is. `DJANGO_BEHIND_TLS` governs HSTS,
the HTTPS redirect, and secure cookies as one group — set it to `0` for a plain-HTTP local run,
because secure cookies are never sent over HTTP and every POST would fail CSRF.

## Conventions

- Forms normalise input via `normalize_name` in `clean_*` (PokeAPI slugs are hyphenated: `mr-mime`).
  `BattleForm.clean` only catches identical *strings*; `"pikachu"` vs `"25"` is caught in the view
  after both resolve to a number.
- Views are pinned with `@require_GET` / `@require_POST`. Search is GET so results are linkable.
- Tests: `SimpleTestCase` + the `responses` library for the HTTP layer; `TestCase` + `patch` on
  `pokedex.views.fetch_pokemon_profile` for views. Build fixtures with `tests/factories.py` rather
  than inline dicts — `profile()` keeps `slug` tracking `name`, which the unique constraint needs.
- Comments here explain *why*, usually naming the bug the code prevents. Match that when editing.
- Django's `{# #}` **cannot span lines** — a multi-line one renders to the page as visible text, and
  the template still returns 200, so nothing else catches it. Use `{% comment %}`. Enforced by
  `tests/test_templates.py`.
