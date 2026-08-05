# PokeProject

A Django Pokedex built on [PokeAPI](https://pokeapi.co/). Search Pokemon by name
or number, collect them to your account, and pit two against each other in a
battle scored on base stats and type matchups.

## Features

- **Search** by name or number. Results are shareable URLs (`/?pokemon=pikachu`),
  and searching swaps the result in place rather than reloading the page.
- **Pokedex entries** — the classic description, category, height and weight,
  habitat, the Pokemon's cry, what it is weak and resistant to, and its full
  evolution line with the conditions for each step.
- **Shiny forms** — a toggle on any Pokemon or your whole collection
  (`/?pokemon=gyarados&shiny=1`). Linkable, and it works with JavaScript off.
- **Your Pokedex** — anything you search while logged in is saved. Filter by
  type, sort, paginate, remove.
- **Battle** — a real fight at level 50. Each Pokemon picks its best move
  *against the other*, damage runs through the standard formula (physical moves
  read Defense, special moves read Sp. Def), and whoever knocks the other out
  first wins. Speed decides who swings first. Water really does beat fire.
- **Battle history** — every result is recorded, with a turn-by-turn log.
- Type-coloured badges, per-stat comparison bars, dark mode, keyboard
  navigation, and a responsive layout.

## Requirements

- Python 3.12+
- PostgreSQL 16
- Docker and Docker Compose (for the containerised setup)

## Quick start (Docker)

```bash
git clone https://github.com/Maarc89/pokeproject.git
cd pokeproject

cp .env.example .env      # Windows: copy .env.example .env
```

Fill in `.env` — `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` have no defaults
and the stack will refuse to start without them:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Then:

```bash
docker compose up --build
```

The app is at <http://localhost:8000>.

Compose brings up three services: `db` (Postgres), `migrate` (runs migrations
once and exits, so replicas never race), and `web` (Gunicorn). `web` waits for
both. `docker-compose.override.yml` is applied automatically and publishes
Postgres on **5433** for local tooling; deploy with the base file alone:

```bash
docker compose -f docker-compose.yml up -d
```

## Running without Docker

```bash
python -m venv .venv
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

# Point at a Postgres instance -- e.g. the compose one on 5433
export POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_PASSWORD=...
export DJANGO_DEBUG=1

python manage.py migrate
python manage.py createsuperuser      # optional, for /admin/
python manage.py runserver
```

## Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | — | **Required** unless `DJANGO_DEBUG=1`. |
| `DJANGO_DEBUG` | `0` | Fails closed. Never `1` in a deployment. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. |
| `DJANGO_BEHIND_TLS` | on when `DEBUG=0` | Governs HSTS, the HTTPS redirect, and secure cookies together. Set `0` only for a plain-HTTP local run — secure cookies are not sent over HTTP, so leaving it on there makes every POST fail CSRF. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Needed once served from a real HTTPS domain. |
| `DJANGO_CACHE_BACKEND` | `locmem` | `locmem` \| `database` \| `redis`. Use `database` under Gunicorn so workers share one cache. |
| `DJANGO_LOG_LEVEL` | `INFO` | |
| `POSTGRES_DB` | `pokeproject_db` | |
| `POSTGRES_USER` | `admin` | |
| `POSTGRES_PASSWORD` | — | **Required** unless `DJANGO_DEBUG=1`. |
| `POSTGRES_HOST` | `localhost` | `db` inside Compose. |
| `POSTGRES_PORT` | `5432` | |

## Tests

```bash
python manage.py test          # 179 tests
ruff check .
coverage run manage.py test && coverage report
```

Production configuration is verified separately, and CI fails on any warning:

```bash
DJANGO_DEBUG=0 python manage.py check --deploy --fail-level WARNING
```

CI (`.github/workflows/ci.yml`) runs lint, the suite against a real Postgres
service, the deploy checks, and a Docker build on every push and PR.

## A note on PokeAPI usage

Finding a Pokemon's strongest move means reading every move it can learn, and
PokeAPI exposes those one endpoint at a time — 100 to 250 requests per Pokemon.
Three things keep that affordable:

1. Search never fetches move data at all; only the battle screen needs it.
2. Move data is immutable, so it is cached and stored locally and fetched at
   most once ever.
3. The remaining misses are fetched concurrently, and are then pruned to the
   strongest move of each type and damage class — the only ones that could ever
   be the best choice against anything.

A cold battle takes about a second; a repeat is instant. Please keep the cache
enabled rather than hammering a free public API.

Shiny art, height, weight and the cry cost nothing extra: they are already in
the response a search makes. A Pokedex entry adds two more cached requests, for
the species description and the evolution line — the latter keyed on the family,
so the rest of a chain is free once you have looked at one of its members.

## Project layout

```
pokedex/
  data/type_chart.py     18x18 type table, plus each type's defensive profile
  services/pokeapi.py    all outbound HTTP, caching, and concurrency
  services/battle.py     damage formula, move choice, turn simulation
  forms.py               search, battle, and registration forms
  models.py              Move, PokemonSpecies, SavedPokemon, Battle
  templates/             base.html + partials; every page extends it
    main/_pokemon_detail.html   the search result, shared by the full page
                                render and the ?partial=1 fetch
static/
  css/styles.css         design tokens, light/dark, type colours
  js/app.js              loading states, autocomplete, sprite fallbacks,
                         shiny swap, and in-place search
```

## License

Pokemon and all related names are trademarks of Nintendo, Game Freak, and The
Pokemon Company. This is an unaffiliated educational project.
