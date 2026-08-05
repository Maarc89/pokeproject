import logging

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .data.type_chart import defensive_profile
from .forms import BattleForm, PokemonSearchForm, RegistrationForm
from .models import Battle, Move, PokemonSpecies, SavedPokemon
from .services import battle as battle_service
from .services.pokeapi import (
    PokeAPIError,
    PokeAPIUnavailable,
    PokemonNotFound,
    fetch_pokemon_profile,
    fetch_species_details,
)

logger = logging.getLogger(__name__)

SAVED_PER_PAGE = 12
HISTORY_PER_PAGE = 20


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
