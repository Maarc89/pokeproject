import logging
import random

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .data.type_chart import defensive_profile, describe, effectiveness
from .forms import BattleForm, PokemonSearchForm, RegistrationForm
from .models import Battle, BattleGame, Move, PokemonSpecies, SavedPokemon
from .services import battle as battle_service
from .services import interactive
from .services.pokeapi import (
    PokeAPIError,
    PokeAPIUnavailable,
    PokemonNotFound,
    fetch_pokemon_profile,
    fetch_species_details,
    normalize_name,
)

logger = logging.getLogger(__name__)

SAVED_PER_PAGE = 12
HISTORY_PER_PAGE = 20
GAMES_PER_PAGE = 20


def _not_found(request, name):
    return render(request, "main/404.html", {"searched_for": name}, status=404)


def _upstream_error(request):
    return render(request, "main/upstream_error.html", status=503)


def _wants_shiny(request):
    """Whether to show shiny art, from `?shiny=1`.

    In the URL rather than the session so a shiny is linkable and a refresh
    keeps it -- the same reason search is a GET.
    """
    return request.GET.get("shiny") in {"1", "true", "on"}


def _species_details(number):
    """Pokedex-entry extras, or None if they could not be fetched.

    Deliberately swallows every PokeAPI failure: this is enrichment on a page
    that has already succeeded, so a flaky species endpoint should cost the
    reader a description, not the Pokemon. Mirrors how a single unfetchable
    move weakens a battle rather than failing it.
    """
    try:
        return fetch_species_details(number)
    except PokeAPIError:
        logger.warning("Species details unavailable for #%s", number, exc_info=True)
        return None


@transaction.atomic
def sync_species(profile):
    """Persist (or refresh) the local copy of a Pokemon fetched from PokeAPI.

    ``update_or_create`` rather than ``get_or_create`` so an existing row picks up
    corrected upstream data instead of staying frozen at whatever it looked like
    the first time anyone searched for it.
    """
    def store(payload):
        stored, _ = Move.objects.update_or_create(
            name=payload["name"],
            defaults={
                "power": payload["power"],
                "type": payload["type"],
                "damage_class": payload["damage_class"],
                # Fetched all along, but previously dropped here. An interactive
                # battle rolls accuracy and pays for a charge turn, so it needs
                # them stored rather than re-fetched every turn.
                "accuracy": payload.get("accuracy", 100),
                "turn_cost": payload.get("turn_cost", 1),
                "self_ko": payload.get("self_ko", False),
            },
        )
        return stored

    move = None
    best_move = profile.get("best_move")
    if best_move:
        move = store(best_move)

    candidates = [store(payload) for payload in profile.get("moves") or []]

    defaults = {
        "slug": profile["slug"],
        "name": profile["name"],
        "types": profile["types"],
        "abilities": profile["abilities"],
        "sprite": profile["sprite"],
        "sprite_shiny": profile.get("sprite_shiny", ""),
        "artwork": profile.get("artwork", ""),
        "artwork_shiny": profile.get("artwork_shiny", ""),
        "stats": profile["stats"],
        "height": profile.get("height", 0),
        "weight": profile.get("weight", 0),
        "base_experience": profile.get("base_experience", 0),
        "cry": profile.get("cry", ""),
    }
    # Only overwrite best_move when this fetch actually resolved one, so a cheap
    # search does not wipe the move a previous battle lookup stored.
    if move is not None:
        defaults["best_move"] = move

    species, _ = PokemonSpecies.objects.update_or_create(
        number=profile["number"], defaults=defaults
    )

    # Same reasoning as best_move: a search resolves no moves, and must not wipe
    # the moveset a previous battle lookup stored.
    if candidates:
        species.candidate_moves.set(candidates)

    return species


@require_GET
def index(request):
    """Search screen.

    Search is a GET so results are linkable and a refresh does not trigger the
    browser's resubmit prompt.
    """
    form = PokemonSearchForm(request.GET or None)
    profile = None
    just_saved = False
    species_details = None
    defensive = None

    if form.is_valid():
        name = form.cleaned_data["pokemon"]
        try:
            # include_moves stays off here: this page never shows move data, and
            # fetching it costs one request per move.
            profile = fetch_pokemon_profile(name)
        except PokemonNotFound:
            return _not_found(request, name)
        except PokeAPIUnavailable:
            logger.exception("PokeAPI unavailable while searching for %r", name)
            return _upstream_error(request)

        species = sync_species(profile)
        species_details = _species_details(profile["number"])
        # Free: the type chart is local data, so this costs no request at all.
        defensive = defensive_profile(profile["types"])

        if request.user.is_authenticated:
            _, just_saved = SavedPokemon.objects.get_or_create(
                user=request.user, species=species
            )

    context = {
        "form": form,
        "profile": profile,
        "just_saved": just_saved,
        "shiny": _wants_shiny(request),
        "species": species_details,
        "defensive": defensive,
    }

    # app.js asks for just the result so a search can swap in place. Same view,
    # same context, same fragment the full page includes -- only the wrapper
    # differs, so the two can never disagree.
    if request.GET.get("partial") == "1" and profile:
        return render(request, "main/_pokemon_detail.html", context)

    return render(request, "main/index.html", context)


@require_GET
def healthz(request):
    """Liveness probe for the container healthcheck.

    Touches the database so a web process that cannot reach Postgres is
    reported unhealthy rather than merely accepting connections.
    """
    try:
        PokemonSpecies.objects.exists()
    except Exception:
        logger.exception("Health check failed")
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def pokemon_names(request):
    """Autocomplete suggestions, drawn from Pokemon already seen locally."""
    names = list(
        PokemonSpecies.objects.order_by("number").values_list("name", flat=True)[:1200]
    )
    return JsonResponse({"names": names})


def register_view(request):
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome, {user.username}! Your account is ready.")
            return redirect("index")
    else:
        form = RegistrationForm()

    return render(request, "registration/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            # Honour ?next= so @login_required sends people where they were going
            # instead of dumping everyone on the home page.
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect("index")
    else:
        form = AuthenticationForm(request)

    return render(
        request,
        "registration/login.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("index")


@login_required
@require_GET
def my_pokemon(request):
    saved = SavedPokemon.objects.filter(user=request.user).select_related(
        "species", "species__best_move"
    )

    type_filter = request.GET.get("type", "").strip().lower()
    if type_filter:
        saved = saved.filter(species__types__contains=[type_filter])

    sort = request.GET.get("sort", "number")
    order = {
        "number": "species__number",
        "name": "species__name",
        "recent": "-saved_at",
    }.get(sort, "species__number")
    saved = saved.order_by(order)

    page = Paginator(saved, SAVED_PER_PAGE).get_page(request.GET.get("page"))

    # Types the user actually owns, for the filter control.
    owned_types = sorted(
        {
            type_name
            for types in SavedPokemon.objects.filter(user=request.user).values_list(
                "species__types", flat=True
            )
            for type_name in types
        }
    )

    return render(
        request,
        "main/my_pokemon.html",
        {
            "page_obj": page,
            "owned_types": owned_types,
            "active_type": type_filter,
            "active_sort": sort,
            "shiny": _wants_shiny(request),
        },
    )


@login_required
@require_POST
def delete_saved_pokemon(request, pk):
    saved = get_object_or_404(SavedPokemon, pk=pk, user=request.user)
    name = saved.species.name
    saved.delete()
    messages.success(request, f"{name} was removed from your Pokedex.")
    return redirect(request.POST.get("next") or "my_pokemon")


@login_required
@require_GET
def battle_view(request):
    """The matchup form, optionally pre-filled from `?first=` and `?second=`.

    Lets a Pokedex entry link straight into a battle rather than making you
    retype a name you are already looking at.
    """
    initial = {
        key: value
        for key, value in (
            ("pokemon1", request.GET.get("first", "").strip()),
            ("pokemon2", request.GET.get("second", "").strip()),
        )
        if value
    }

    # Slug for the link, name for the label: the form normalises either way,
    # but a URL carrying "Mr. Mime" instead of "mr-mime" reads like a bug.
    saved = list(
        SavedPokemon.objects.filter(user=request.user)
        .order_by("species__number")
        .values("species__name", "species__slug")
    )
    quick_picks = [
        {"name": row["species__name"], "slug": row["species__slug"]} for row in saved
    ]

    return render(
        request,
        "main/battle.html",
        {"form": BattleForm(initial=initial), "quick_picks": quick_picks},
    )


@login_required
@require_POST
def battle_result(request):
    form = BattleForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect("battle")

    first_name = form.cleaned_data["pokemon1"]
    second_name = form.cleaned_data["pokemon2"]

    try:
        # Moves are needed here -- this is the one place that justifies the cost.
        first = fetch_pokemon_profile(first_name, include_moves=True)
        second = fetch_pokemon_profile(second_name, include_moves=True)
    except PokemonNotFound as exc:
        return _not_found(request, str(exc))
    except PokeAPIUnavailable:
        logger.exception("PokeAPI unavailable during battle %r vs %r", first_name, second_name)
        return _upstream_error(request)

    # Catches "pikachu" vs "25", which the form cannot see because it only
    # compares the raw input strings.
    if first["number"] == second["number"]:
        messages.error(request, "Pick two different Pokemon.")
        return redirect("battle")

    outcome = battle_service.resolve(first, second)

    species_1 = sync_species(first)
    species_2 = sync_species(second)
    winner_species = None
    if outcome.winner is not None:
        winner_species = (
            species_1 if outcome.winner is outcome.first else species_2
        )

    Battle.objects.create(
        user=request.user,
        species_1=species_1,
        species_2=species_2,
        winner=winner_species,
        score_1=outcome.first.attack.damage,
        score_2=outcome.second.attack.damage,
        turns_1=outcome.first.turns_to_ko,
        turns_2=outcome.second.turns_to_ko,
    )

    return render(
        request,
        "main/battle_result.html",
        {
            "outcome": outcome,
            "stat_rows": battle_service.stat_comparison(first, second),
            # For the rematch and swap-sides buttons. The slugs, not the raw
            # input, so a rematch of "25" replays as "pikachu".
            "first_slug": first["slug"],
            "second_slug": second["slug"],
        },
    )


@login_required
@require_GET
def battle_history(request):
    battles = Battle.objects.filter(user=request.user).select_related(
        "species_1", "species_2", "winner"
    )
    page = Paginator(battles, HISTORY_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "main/battle_history.html", {"page_obj": page})


# --------------------------------------------------------------------------
# Interactive battle
#
# The auto-simulation above answers "who would win". These views let the user
# play it out instead, one move per request. Everything they need -- stats,
# types, the four-move set -- is already in the database by the time a game
# starts, so a turn costs no PokeAPI request at all.
# --------------------------------------------------------------------------


def _candidates(species):
    """Every move a species could bring, as engine dicts."""
    return [move.as_dict() for move in species.candidate_moves.all()]


def _auto_moveset(species):
    """The four moves a species brings when nobody drafted for it.

    Used for the opponent, and for the player on every entry point that skips
    the draft screen -- "Play it out" from a simulation, and a rematch of a game
    that was never drafted.
    """
    return interactive.player_moveset(_candidates(species))


def _moves_by_name(move_dicts):
    """The stored Move rows for a set of engine dicts.

    They always exist by this point -- `sync_species` writes every candidate
    move before anything can be drafted from it.
    """
    return list(Move.objects.filter(name__in=[move["name"] for move in move_dicts]))


def _game_moveset(stored, species):
    """A side's moveset for this game, falling back to the automatic pick.

    The fallback covers games created before the moveset was pinned; without it
    they would load with no moves at all and silently become unplayable.
    """
    drafted = [move.as_dict() for move in stored]
    return drafted or _auto_moveset(species)


def _load_state(game):
    """Rebuild the engine's view of a game from the stored row."""
    return interactive.GameState(
        seed=game.seed,
        player=interactive.species_to_profile(
            game.player_species,
            _game_moveset(game.player_moves.all(), game.player_species),
        ),
        opponent=interactive.species_to_profile(
            game.opponent_species,
            _game_moveset(game.opponent_moves.all(), game.opponent_species),
        ),
        player_hp=game.player_hp,
        opponent_hp=game.opponent_hp,
        player_max_hp=game.player_max_hp,
        opponent_max_hp=game.opponent_max_hp,
        turn_number=game.turn_number,
        player_recharging=game.player_recharging,
        opponent_recharging=game.opponent_recharging,
        result=game.result,
    )


def _store_state(game, state, new_turns):
    """Write a played round back, appending to the log rather than replacing it."""
    game.player_hp = state.player_hp
    game.opponent_hp = state.opponent_hp
    game.turn_number = state.turn_number
    game.player_recharging = state.player_recharging
    game.opponent_recharging = state.opponent_recharging
    game.log = list(game.log) + [interactive.turn_to_json(turn) for turn in new_turns]

    if state.result and not game.result:
        game.result = state.result
        game.finished_at = timezone.now()

    game.save()


def _move_cards(state):
    """The player's moves, each with what it would do to *this* opponent.

    The multiplier only, not a damage figure: the point of the game is to read
    the matchup, and printing the answer above every button removes the choice.
    """
    defending = state.opponent.get("types") or []
    own = {t.lower() for t in state.player.get("types") or []}

    cards = []
    for move in state.player.get("moves") or []:
        multiplier = effectiveness(move.get("type", ""), defending)
        cards.append(
            {
                "move": move,
                "multiplier": multiplier,
                "label": describe(multiplier),
                "stab": move.get("type") in own,
            }
        )
    return cards


def _game_context(game):
    state = _load_state(game)
    return state, {
        "game": game,
        "state": state,
        "move_cards": _move_cards(state),
        # Rehydrated into the same dataclass `simulate` produces, so
        # partials/_battle_log.html renders a played battle unchanged.
        "turns": [interactive.turn_from_json(entry) for entry in game.log],
    }


@login_required
@require_GET
def play_setup(request):
    """Pick the two Pokemon for a played battle."""
    initial = {
        key: value
        for key, value in (
            ("pokemon1", request.GET.get("first", "").strip()),
            ("pokemon2", request.GET.get("second", "").strip()),
        )
        if value
    }

    quick_picks = [
        {"name": row["species__name"], "slug": row["species__slug"]}
        for row in SavedPokemon.objects.filter(user=request.user)
        .order_by("species__number")
        .values("species__name", "species__slug")
    ]

    return render(
        request,
        "main/battle_play_setup.html",
        {"form": BattleForm(initial=initial), "quick_picks": quick_picks},
    )


def _species_for_play(name):
    """The stored species for a name, fetching it only if we have to.

    A battle-ready species is one whose candidate moveset is already stored --
    a species row alone is not enough, because a plain search creates one with
    no moves at all. When that holds we skip PokeAPI entirely, which is what
    makes the draft screen free: it has already paid for the fetch, and
    `play_start` should not pay again.
    """
    slug = normalize_name(name)
    lookup = {"number": int(slug)} if slug.isdigit() else {"slug": slug}

    species = PokemonSpecies.objects.filter(**lookup).first()
    if species is not None and species.candidate_moves.exists():
        return species, False

    profile = fetch_pokemon_profile(slug, include_moves=True)
    return sync_species(profile), True


def _drafted_moves(request, species):
    """The moves the player ticked, validated against what this Pokemon has.

    Returns None when nothing was submitted, which means "no draft happened" --
    every entry point that skips the draft screen relies on that to fall through
    to the automatic pick.
    """
    submitted = request.POST.getlist("move")
    if not submitted:
        return None

    allowed = {move.name: move for move in species.candidate_moves.all()}
    chosen = [allowed[name] for name in submitted if name in allowed]
    # Silently dropping unknown names would let a tampered form quietly play a
    # different battle from the one the page offered.
    if len(chosen) != len(submitted):
        raise ValueError("That is not one of this Pokemon's moves.")
    if len(chosen) > interactive.MOVE_SLOTS:
        raise ValueError(f"Pick at most {interactive.MOVE_SLOTS} moves.")

    return chosen


@login_required
@require_POST
def play_draft(request):
    """Choose your four moves before the fight starts.

    A separate screen rather than part of the arena because it is a different
    decision: the arena asks "what beats what is in front of me right now", and
    this asks "what do I want to have available at all". It is also the only
    place the full candidate set is worth showing -- ten to twenty moves is too
    many to scan every turn, which is why a battle only offers four.
    """
    form = BattleForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect("play_setup")

    try:
        player_species, _ = _species_for_play(form.cleaned_data["pokemon1"])
        opponent_species, _ = _species_for_play(form.cleaned_data["pokemon2"])
    except PokemonNotFound as exc:
        return _not_found(request, str(exc))
    except PokeAPIUnavailable:
        logger.exception("PokeAPI unavailable drafting a game")
        return _upstream_error(request)

    if player_species.number == opponent_species.number:
        messages.error(request, "Pick two different Pokemon.")
        return redirect("play_setup")

    defending = opponent_species.types or []
    own = {t.lower() for t in player_species.types or []}
    suggested = {move["name"] for move in _auto_moveset(player_species)}

    options = []
    for move in _candidates(player_species):
        multiplier = effectiveness(move.get("type", ""), defending)
        options.append(
            {
                "move": move,
                "multiplier": multiplier,
                "label": describe(multiplier),
                "stab": move.get("type") in own,
                # Pre-ticked, so the screen is a refinement rather than a chore:
                # accepting the default is exactly the old behaviour.
                "selected": move["name"] in suggested,
            }
        )
    # Strongest against this opponent first -- the ordering that makes the
    # decision readable, rather than alphabetical.
    options.sort(key=lambda entry: (-entry["multiplier"], -entry["move"]["power"]))

    return render(
        request,
        "main/battle_play_draft.html",
        {
            "player": player_species,
            "opponent": opponent_species,
            "options": options,
            "opponent_moves": _auto_moveset(opponent_species),
            "slots": interactive.MOVE_SLOTS,
            "shiny": _wants_shiny(request),
        },
    )


@login_required
@require_POST
def play_start(request):
    """Create a game.

    Reached either from the draft screen (carrying a chosen moveset) or straight
    from a rematch or a simulation's "Play it out" (carrying none, in which case
    the automatic four are used).
    """
    form = BattleForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect("play_setup")

    first_name = form.cleaned_data["pokemon1"]
    second_name = form.cleaned_data["pokemon2"]

    try:
        player_species, _ = _species_for_play(first_name)
        opponent_species, _ = _species_for_play(second_name)
    except PokemonNotFound as exc:
        return _not_found(request, str(exc))
    except PokeAPIUnavailable:
        logger.exception("PokeAPI unavailable starting a game: %r vs %r", first_name, second_name)
        return _upstream_error(request)

    # Same trap as battle_result: the form only compares the raw strings, so
    # "pikachu" versus "25" gets this far.
    if player_species.number == opponent_species.number:
        messages.error(request, "Pick two different Pokemon.")
        return redirect("play_setup")

    try:
        drafted = _drafted_moves(request, player_species)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("play_setup")

    player_moves = (
        [move.as_dict() for move in drafted]
        if drafted is not None
        else _auto_moveset(player_species)
    )
    opponent_moves = _auto_moveset(opponent_species)

    state = interactive.GameState.start(
        seed=random.getrandbits(62),
        player=interactive.species_to_profile(player_species, player_moves),
        opponent=interactive.species_to_profile(opponent_species, opponent_moves),
    )

    game = BattleGame.objects.create(
        user=request.user,
        player_species=player_species,
        opponent_species=opponent_species,
        seed=state.seed,
        player_hp=state.player_hp,
        opponent_hp=state.opponent_hp,
        player_max_hp=state.player_max_hp,
        opponent_max_hp=state.opponent_max_hp,
    )
    # Pinned to the game so the battle stays reproducible even if the stored
    # candidate set is later rebuilt.
    game.player_moves.set(_moves_by_name(player_moves))
    game.opponent_moves.set(_moves_by_name(opponent_moves))

    if not player_moves and not opponent_moves:
        # Neither side can attack, so there is nothing to play. Say so now
        # rather than after a hundred empty rounds.
        game.result = BattleGame.TIE
        game.finished_at = timezone.now()
        game.save(update_fields=["result", "finished_at"])

    return redirect("play", pk=game.pk)


@login_required
@require_GET
def play(request, pk):
    """The arena. A plain GET, so refreshing re-renders and never re-rolls."""
    game = get_object_or_404(
        BattleGame.objects.select_related("player_species", "opponent_species"),
        pk=pk,
        user=request.user,
    )
    _, context = _game_context(game)
    context["shiny"] = _wants_shiny(request)
    return render(request, "main/battle_play.html", context)


@login_required
@require_POST
def play_move(request, pk):
    """Play one move, then redirect. Post/Redirect/Get, so a refresh is safe."""
    game = get_object_or_404(
        BattleGame.objects.select_related("player_species", "opponent_species"),
        pk=pk,
        user=request.user,
    )

    if game.is_finished:
        messages.error(request, "That battle is already over.")
        return redirect("play", pk=game.pk)

    # The turn guard. The form carries the turn it was rendered for; anything
    # else is a resubmitted or stale request. Without this, going Back and
    # picking a different move lets a player try each one and keep the best
    # roll -- the seeding alone cannot stop that, because a different move is
    # legitimately a different roll.
    try:
        submitted_turn = int(request.POST.get("turn", ""))
    except ValueError:
        submitted_turn = -1

    if submitted_turn != game.turn_number:
        messages.error(request, "That move was already played.")
        return redirect("play", pk=game.pk)

    state = _load_state(game)
    move_name = request.POST.get("move", "")

    if interactive.find_move(state.player.get("moves") or [], move_name) is None:
        messages.error(request, "That is not one of your moves.")
        return redirect("play", pk=game.pk)

    turns = interactive.advance(state, move_name, log_length=len(game.log))
    _store_state(game, state, turns)

    return redirect("play", pk=game.pk)


@login_required
@require_POST
def play_forfeit(request, pk):
    game = get_object_or_404(BattleGame, pk=pk, user=request.user)
    if not game.is_finished:
        game.result = BattleGame.FORFEIT
        game.finished_at = timezone.now()
        game.save(update_fields=["result", "finished_at"])
        messages.success(request, "Battle forfeited.")
    return redirect("play", pk=game.pk)


@login_required
@require_GET
def play_history(request):
    games = BattleGame.objects.filter(user=request.user).select_related(
        "player_species", "opponent_species"
    )
    page = Paginator(games, GAMES_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "main/battle_play_history.html", {"page_obj": page})
