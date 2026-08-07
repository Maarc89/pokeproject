import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ..models import Battle, BattleGame, PokemonSpecies, SavedPokemon
from ..services import interactive
from ..services.pokeapi import PokeAPIUnavailable, PokemonNotFound
from .factories import move, profile

PASSWORD = "a-good-test-password-42"


class SearchTests(TestCase):
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_renders_the_pokemon(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pikachu")
        self.assertContains(response, "Electric")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_does_not_request_move_data(self, fetch):
        fetch.return_value = profile()

        self.client.get(reverse("index"), {"pokemon": "pikachu"})

        _, kwargs = fetch.call_args
        self.assertFalse(kwargs.get("include_moves", False))

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_caches_the_species_locally(self, fetch):
        fetch.return_value = profile()

        self.client.get(reverse("index"), {"pokemon": "pikachu"})

        species = PokemonSpecies.objects.get(number=25)
        self.assertEqual(species.name, "Pikachu")
        self.assertEqual(species.types, ["electric"])

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_repeat_search_refreshes_rather_than_duplicating(self, fetch):
        fetch.return_value = profile()
        self.client.get(reverse("index"), {"pokemon": "pikachu"})

        fetch.return_value = profile(name="Pikachu Redux")
        self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertEqual(PokemonSpecies.objects.count(), 1)
        self.assertEqual(PokemonSpecies.objects.get(number=25).name, "Pikachu Redux")

    def test_blank_search_shows_the_empty_state(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search for a Pokemon")

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokemonNotFound("nope"))
    def test_unknown_pokemon_returns_404(self, _fetch):
        response = self.client.get(reverse("index"), {"pokemon": "missingno"})
        self.assertEqual(response.status_code, 404)

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokeAPIUnavailable("boom"))
    def test_upstream_failure_returns_503_not_a_crash(self, _fetch):
        """Regression: this path used to fall off the end of the view and
        return None, which Django surfaced as a 500."""
        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})
        self.assertEqual(response.status_code, 503)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_saves_for_authenticated_users_only(self, fetch):
        fetch.return_value = profile()

        self.client.get(reverse("index"), {"pokemon": "pikachu"})
        self.assertEqual(SavedPokemon.objects.count(), 0)

        User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)
        self.client.get(reverse("index"), {"pokemon": "pikachu"})
        self.assertEqual(SavedPokemon.objects.count(), 1)


class LiveSearchTests(TestCase):
    """?partial=1 returns just the result, for the in-place swap."""

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_partial_returns_the_fragment_alone(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(
            reverse("index"), {"pokemon": "pikachu", "partial": "1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pikachu")
        # No page furniture: this is spliced into an existing document.
        self.assertNotContains(response, "<!DOCTYPE html>")
        self.assertNotContains(response, "site-header")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_fragment_and_the_full_page_agree(self, fetch):
        """One template, so the two renders cannot drift apart."""
        fetch.return_value = profile()

        full = self.client.get(reverse("index"), {"pokemon": "pikachu"})
        partial = self.client.get(
            reverse("index"), {"pokemon": "pikachu", "partial": "1"}
        )

        self.assertIn(partial.content.decode().strip(), full.content.decode())

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_partial_honours_shiny(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(
            reverse("index"), {"pokemon": "pikachu", "partial": "1", "shiny": "1"}
        )

        self.assertContains(response, "pikachu-art-shiny.png")

    def test_partial_without_a_search_falls_back_to_the_full_page(self):
        """Nothing to splice in, so there is no fragment to return."""
        response = self.client.get(reverse("index"), {"partial": "1"})

        self.assertContains(response, "<!DOCTYPE html>")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_missing_pokemon_still_renders_the_error_page(self, fetch):
        """The JS gives up on a non-200 and navigates, so this must be complete."""
        fetch.side_effect = PokemonNotFound("nosuchmon")

        response = self.client.get(
            reverse("index"), {"pokemon": "nosuchmon", "partial": "1"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "<!DOCTYPE html>", status_code=404)


class BattlePrefillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)
        self.species = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu", types=["electric"]
        )
        SavedPokemon.objects.create(user=self.user, species=self.species)

    def test_first_and_second_prefill_the_form(self):
        response = self.client.get(
            reverse("battle"), {"first": "pikachu", "second": "charizard"}
        )

        self.assertContains(response, 'value="pikachu"')
        self.assertContains(response, 'value="charizard"')

    def test_saved_pokemon_are_offered_as_quick_picks(self):
        response = self.client.get(reverse("battle"))

        self.assertContains(response, "From your Pokedex")
        self.assertContains(response, "Pikachu")

    def test_a_quick_pick_fills_the_second_slot_once_the_first_is_taken(self):
        response = self.client.get(reverse("battle"), {"first": "charizard"})

        # The slug, not the display name -- a URL carrying "Mr. Mime" reads wrong.
        self.assertContains(response, "second=pikachu")
        self.assertContains(response, "first=charizard")

    def test_only_your_own_pokemon_are_offered(self):
        misty = User.objects.create_user("misty", password=PASSWORD)
        other = PokemonSpecies.objects.create(
            number=7, slug="squirtle", name="Squirtle", types=["water"]
        )
        SavedPokemon.objects.create(user=misty, species=other)

        response = self.client.get(reverse("battle"))

        self.assertNotContains(response, "Squirtle")


class RematchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_result_offers_a_rematch_and_a_swap(self, fetch):
        fetch.side_effect = [
            profile(number=25, name="Pikachu", best_move=move()),
            profile(number=6, name="Charizard", types=("fire",), best_move=move()),
        ]

        response = self.client.post(
            reverse("battle_result"), {"pokemon1": "pikachu", "pokemon2": "charizard"}
        )

        self.assertContains(response, "Rematch")
        self.assertContains(response, "Swap sides")
        # Slugs, not the raw input, so a rematch of "25" replays as pikachu.
        self.assertContains(response, 'name="pokemon1" value="pikachu"')
        self.assertContains(response, 'name="pokemon2" value="charizard"')
        self.assertContains(response, 'name="pokemon1" value="charizard"')


class PokedexDetailTests(TestCase):
    """Species enrichment on the search page."""

    SPECIES = {
        "genus": "Mouse Pokemon",
        "flavor_text": "It keeps its tail raised to monitor its surroundings.",
        "is_legendary": False,
        "is_mythical": False,
        "habitat": "Forest",
        "generation": "I",
        "evolutions": [
            [{"slug": "pichu", "name": "Pichu", "number": 172, "sprite": "", "trigger": ""}],
            [{"slug": "pikachu", "name": "Pikachu", "number": 25, "sprite": "",
              "trigger": "High friendship"}],
        ],
    }

    @patch("pokedex.views.fetch_species_details")
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_shows_the_pokedex_entry(self, fetch, species):
        fetch.return_value = profile()
        species.return_value = dict(self.SPECIES)

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "It keeps its tail raised")
        self.assertContains(response, "Mouse Pokemon")
        self.assertContains(response, "Generation I")

    @patch("pokedex.views.fetch_species_details")
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_shows_height_weight_and_cry(self, fetch, species):
        fetch.return_value = profile()
        species.return_value = dict(self.SPECIES)

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "0.4&nbsp;m")
        self.assertContains(response, "6&nbsp;kg")
        # Never autoplay: a cry is a novelty, not an announcement.
        self.assertContains(response, 'preload="none"')

    @patch("pokedex.views.fetch_species_details")
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_shows_the_evolution_line(self, fetch, species):
        fetch.return_value = profile()
        species.return_value = dict(self.SPECIES)

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "Evolution line")
        self.assertContains(response, "Pichu")
        self.assertContains(response, "High friendship")

    @patch("pokedex.views.fetch_species_details")
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_single_stage_family_shows_no_evolution_section(self, fetch, species):
        fetch.return_value = profile()
        species.return_value = dict(self.SPECIES, evolutions=[[{"name": "Ditto"}]])

        response = self.client.get(reverse("index"), {"pokemon": "ditto"})

        self.assertNotContains(response, "Evolution line")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_defensive_matchups_are_computed_locally(self, fetch):
        """No API call: the type chart is local data."""
        fetch.return_value = profile(types=("electric",))

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "Type matchups")
        self.assertContains(response, "Weak to")
        self.assertContains(response, "Ground")

    @patch("pokedex.views.fetch_species_details")
    @patch("pokedex.views.fetch_pokemon_profile")
    def test_species_failure_costs_the_description_not_the_pokemon(self, fetch, species):
        """Enrichment must degrade, never 503 a page that already succeeded."""
        fetch.return_value = profile()
        species.side_effect = PokeAPIUnavailable("species endpoint down")

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pikachu")
        self.assertContains(response, "Type matchups")
        self.assertNotContains(response, "Evolution line")


class ShinyToggleTests(TestCase):
    """?shiny=1 has to survive being linked, filtered and paginated."""

    def setUp(self):
        self.user = User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_shows_normal_art_by_default(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "pikachu-art.png")
        self.assertNotContains(response, 'src="https://example.invalid/pikachu-art-shiny.png"')

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_search_shows_shiny_art_when_asked(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(reverse("index"), {"pokemon": "pikachu", "shiny": "1"})

        self.assertContains(response, 'src="https://example.invalid/pikachu-art-shiny.png"')

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_both_urls_are_emitted_for_the_client_side_swap(self, fetch):
        fetch.return_value = profile()

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, 'data-sprite="https://example.invalid/pikachu-art.png"')
        self.assertContains(
            response, 'data-sprite-shiny="https://example.invalid/pikachu-art-shiny.png"'
        )

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_toggle_keeps_the_search_term(self, fetch):
        """Switching to shiny must not throw away what you searched for."""
        fetch.return_value = profile()

        response = self.client.get(reverse("index"), {"pokemon": "pikachu"})

        self.assertContains(response, "pokemon=pikachu&amp;shiny=1")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_pokemon_without_shiny_art_falls_back(self, fetch):
        fetch.return_value = profile(artwork_shiny="", sprite_shiny="")

        response = self.client.get(reverse("index"), {"pokemon": "pikachu", "shiny": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pikachu-art.png")

    def test_collection_toggle_keeps_filter_and_sort(self):
        species = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu", types=["electric"],
            sprite="https://example.invalid/p.png",
            sprite_shiny="https://example.invalid/p-shiny.png",
        )
        SavedPokemon.objects.create(user=self.user, species=species)

        response = self.client.get(
            reverse("my_pokemon"), {"type": "electric", "sort": "name"}
        )

        content = response.content.decode()
        self.assertIn("shiny=1", content)
        self.assertIn("type=electric", content)
        self.assertIn("sort=name", content)

    def test_collection_renders_shiny_sprites(self):
        species = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu", types=["electric"],
            sprite="https://example.invalid/p.png",
            sprite_shiny="https://example.invalid/p-shiny.png",
        )
        SavedPokemon.objects.create(user=self.user, species=species)

        response = self.client.get(reverse("my_pokemon"), {"shiny": "1"})

        self.assertContains(response, 'src="https://example.invalid/p-shiny.png"')

    def test_the_filter_form_carries_shiny_through(self):
        """The filter form replaces the query string, so it must resubmit shiny."""
        species = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu", types=["electric"]
        )
        SavedPokemon.objects.create(user=self.user, species=species)

        response = self.client.get(reverse("my_pokemon"), {"shiny": "1"})

        self.assertContains(response, '<input type="hidden" name="shiny" value="1">')


class MyPokemonTests(TestCase):
    def setUp(self):
        self.ash = User.objects.create_user("ash", password=PASSWORD)
        self.misty = User.objects.create_user("misty", password=PASSWORD)
        self.pikachu = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu", types=["electric"]
        )
        self.charizard = PokemonSpecies.objects.create(
            number=6, slug="charizard", name="Charizard", types=["fire", "flying"]
        )
        SavedPokemon.objects.create(user=self.ash, species=self.pikachu)
        SavedPokemon.objects.create(user=self.misty, species=self.charizard)
        self.client.login(username="ash", password=PASSWORD)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("my_pokemon"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_shows_only_your_own_pokemon(self):
        """Nothing previously proved user A could not see user B's Pokedex."""
        response = self.client.get(reverse("my_pokemon"))
        self.assertContains(response, "Pikachu")
        self.assertNotContains(response, "Charizard")

    def test_type_filter(self):
        SavedPokemon.objects.create(user=self.ash, species=self.charizard)

        response = self.client.get(reverse("my_pokemon"), {"type": "fire"})

        self.assertContains(response, "Charizard")
        self.assertNotContains(response, "#25")

    def test_pagination_kicks_in(self):
        for number in range(100, 130):
            species = PokemonSpecies.objects.create(
                number=number, slug=f"mon-{number}", name=f"Mon{number}"
            )
            SavedPokemon.objects.create(user=self.ash, species=species)

        response = self.client.get(reverse("my_pokemon"))

        self.assertTrue(response.context["page_obj"].has_next())
        self.assertEqual(len(response.context["page_obj"]), 12)

    def test_empty_state(self):
        SavedPokemon.objects.filter(user=self.ash).delete()
        response = self.client.get(reverse("my_pokemon"))
        self.assertContains(response, "not saved any Pokemon")


class DeleteSavedPokemonTests(TestCase):
    def setUp(self):
        self.ash = User.objects.create_user("ash", password=PASSWORD)
        self.misty = User.objects.create_user("misty", password=PASSWORD)
        species = PokemonSpecies.objects.create(number=25, slug="pikachu", name="Pikachu")
        self.saved = SavedPokemon.objects.create(user=self.ash, species=species)

    def test_owner_can_delete(self):
        self.client.login(username="ash", password=PASSWORD)
        response = self.client.post(
            reverse("delete_saved_pokemon", args=[self.saved.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SavedPokemon.objects.filter(pk=self.saved.pk).exists())

    def test_another_user_cannot_delete_your_pokemon(self):
        self.client.login(username="misty", password=PASSWORD)
        response = self.client.post(
            reverse("delete_saved_pokemon", args=[self.saved.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SavedPokemon.objects.filter(pk=self.saved.pk).exists())

    def test_get_is_rejected(self):
        self.client.login(username="ash", password=PASSWORD)
        response = self.client.get(reverse("delete_saved_pokemon", args=[self.saved.pk]))
        self.assertEqual(response.status_code, 405)


class BattleTests(TestCase):
    def setUp(self):
        User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    def test_battle_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("battle"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_result_declares_a_winner(self, fetch):
        fetch.side_effect = [
            profile(number=6, name="Charizard", slug="charizard", types=("fire", "flying"),
                    best_move=move("flamethrower", power=90, type="fire")),
            profile(number=9, name="Blastoise", slug="blastoise", types=("water",),
                    best_move=move("surf", power=90, type="water")),
        ]

        response = self.client.post(
            reverse("battle_result"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )

        self.assertEqual(response.status_code, 200)
        # Water beats fire, so the type matchup has to carry the result.
        self.assertContains(response, "Blastoise wins")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_result_requests_move_data(self, fetch):
        fetch.side_effect = [profile(number=1, name="A"), profile(number=2, name="B")]

        self.client.post(reverse("battle_result"), {"pokemon1": "a", "pokemon2": "b"})

        for call in fetch.call_args_list:
            self.assertTrue(call.kwargs.get("include_moves"))

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_battle_is_recorded(self, fetch):
        fetch.side_effect = [profile(number=1, name="A"), profile(number=2, name="B")]

        self.client.post(reverse("battle_result"), {"pokemon1": "a", "pokemon2": "b"})

        self.assertEqual(Battle.objects.count(), 1)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_same_pokemon_by_different_identifiers_is_rejected(self, fetch):
        """Regression: "pikachu" vs "25" is the same Pokemon, but the old
        string comparison let it through and Pikachu tied with itself."""
        fetch.side_effect = [profile(number=25, name="Pikachu"), profile(number=25, name="Pikachu")]

        response = self.client.post(
            reverse("battle_result"), {"pokemon1": "pikachu", "pokemon2": "25"}
        )

        self.assertRedirects(response, reverse("battle"))
        self.assertEqual(Battle.objects.count(), 0)

    def test_identical_names_are_rejected_by_the_form(self):
        response = self.client.post(
            reverse("battle_result"), {"pokemon1": "pikachu", "pokemon2": "Pikachu"}
        )
        self.assertRedirects(response, reverse("battle"))

    def test_missing_input_is_rejected(self):
        response = self.client.post(reverse("battle_result"), {"pokemon1": "pikachu"})
        self.assertRedirects(response, reverse("battle"))

    def test_get_redirects_to_the_form(self):
        response = self.client.get(reverse("battle_result"))
        self.assertEqual(response.status_code, 405)

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokemonNotFound("nope"))
    def test_unknown_pokemon_returns_404(self, _fetch):
        response = self.client.post(
            reverse("battle_result"), {"pokemon1": "missingno", "pokemon2": "pikachu"}
        )
        self.assertEqual(response.status_code, 404)


class BattleHistoryTests(TestCase):
    def setUp(self):
        self.ash = User.objects.create_user("ash", password=PASSWORD)
        self.misty = User.objects.create_user("misty", password=PASSWORD)
        a = PokemonSpecies.objects.create(number=6, slug="charizard", name="Charizard")
        b = PokemonSpecies.objects.create(number=9, slug="blastoise", name="Blastoise")
        Battle.objects.create(user=self.misty, species_1=a, species_2=b,
                              winner=b, score_1=1, score_2=2)

    def test_requires_login(self):
        response = self.client.get(reverse("battle_history"))
        self.assertEqual(response.status_code, 302)

    def test_only_shows_your_own_battles(self):
        self.client.login(username="ash", password=PASSWORD)
        response = self.client.get(reverse("battle_history"))
        self.assertContains(response, "No battles yet")


class PlayTests(TestCase):
    """The interactive battle.

    The turn guard and Post/Redirect/Get are what make a played battle honest,
    so most of these are about what happens when a request arrives twice or out
    of order rather than about the fight itself.
    """

    def setUp(self):
        User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    def contenders(self):
        return [
            profile(number=6, name="Charizard", slug="charizard", types=("fire", "flying"),
                    moves=[move("flamethrower", power=90, type="fire"),
                           move("wing-attack", power=60, type="flying",
                                damage_class="physical")]),
            profile(number=9, name="Blastoise", slug="blastoise", types=("water",),
                    moves=[move("surf", power=90, type="water")]),
        ]

    def start(self, fetch):
        fetch.side_effect = self.contenders()
        self.client.post(
            reverse("play_start"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )
        return BattleGame.objects.get()

    def test_setup_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("play_setup"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_starting_a_game_redirects_to_the_arena(self, fetch):
        fetch.side_effect = self.contenders()

        response = self.client.post(
            reverse("play_start"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )

        game = BattleGame.objects.get()
        self.assertRedirects(response, reverse("play", kwargs={"pk": game.pk}))
        self.assertEqual(game.turn_number, 0)
        self.assertEqual(game.player_hp, game.player_max_hp)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_starting_a_game_requests_move_data(self, fetch):
        fetch.side_effect = self.contenders()

        self.client.post(
            reverse("play_start"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )

        for call in fetch.call_args_list:
            self.assertTrue(call.kwargs.get("include_moves"))

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_playing_a_turn_needs_no_further_api_calls(self, fetch):
        """The whole point of storing accuracy and turn cost on Move: after
        set-up the battle runs entirely on local data."""
        game = self.start(fetch)
        fetch.reset_mock()
        fetch.side_effect = None

        self.client.get(reverse("play", kwargs={"pk": game.pk}))
        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": game.turn_number},
        )

        fetch.assert_not_called()

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_arena_offers_the_stored_moves(self, fetch):
        game = self.start(fetch)

        response = self.client.get(reverse("play", kwargs={"pk": game.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flamethrower")
        self.assertContains(response, "Wing Attack")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_move_advances_the_turn_and_redirects(self, fetch):
        game = self.start(fetch)

        response = self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": 0},
        )

        self.assertRedirects(response, reverse("play", kwargs={"pk": game.pk}))
        game.refresh_from_db()
        self.assertEqual(game.turn_number, 1)
        self.assertTrue(game.log)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_stale_turn_is_refused(self, fetch):
        """Regression guard for save-scumming: without this, going Back and
        submitting a different move lets a player retry until one crits."""
        game = self.start(fetch)
        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": 0},
        )
        game.refresh_from_db()
        before = (game.turn_number, game.player_hp, game.opponent_hp, len(game.log))

        # Exactly what a resubmitted form from the previous page would send.
        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "wing-attack", "turn": 0},
        )

        game.refresh_from_db()
        self.assertEqual(
            (game.turn_number, game.player_hp, game.opponent_hp, len(game.log)), before
        )

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_missing_turn_is_refused(self, fetch):
        game = self.start(fetch)

        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}), {"move": "flamethrower"}
        )

        game.refresh_from_db()
        self.assertEqual(game.turn_number, 0)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_refreshing_the_arena_does_not_advance_anything(self, fetch):
        game = self.start(fetch)
        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": 0},
        )
        game.refresh_from_db()
        before = (game.turn_number, game.player_hp, game.opponent_hp)

        for _ in range(3):
            self.client.get(reverse("play", kwargs={"pk": game.pk}))

        game.refresh_from_db()
        self.assertEqual((game.turn_number, game.player_hp, game.opponent_hp), before)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_replaying_the_same_turn_rolls_the_same(self, fetch):
        """Two games on the same seed and the same move must land identically,
        which is what makes a refusal safe to redirect rather than error."""
        game = self.start(fetch)
        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": 0},
        )
        game.refresh_from_db()
        first = game.opponent_hp

        rerun = BattleGame.objects.create(
            user=game.user,
            player_species=game.player_species,
            opponent_species=game.opponent_species,
            seed=game.seed,
            player_hp=game.player_max_hp,
            opponent_hp=game.opponent_max_hp,
            player_max_hp=game.player_max_hp,
            opponent_max_hp=game.opponent_max_hp,
        )
        self.client.post(
            reverse("play_move", kwargs={"pk": rerun.pk}),
            {"move": "flamethrower", "turn": 0},
        )
        rerun.refresh_from_db()

        self.assertEqual(rerun.opponent_hp, first)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_move_the_pokemon_does_not_have_is_refused(self, fetch):
        game = self.start(fetch)

        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "hyper-beam", "turn": 0},
        )

        game.refresh_from_db()
        self.assertEqual(game.turn_number, 0)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_finished_game_refuses_further_moves(self, fetch):
        game = self.start(fetch)
        game.result = BattleGame.FORFEIT
        game.save(update_fields=["result"])

        self.client.post(
            reverse("play_move", kwargs={"pk": game.pk}),
            {"move": "flamethrower", "turn": 0},
        )

        game.refresh_from_db()
        self.assertEqual(game.turn_number, 0)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_forfeiting_ends_the_game(self, fetch):
        game = self.start(fetch)

        self.client.post(reverse("play_forfeit", kwargs={"pk": game.pk}))

        game.refresh_from_db()
        self.assertEqual(game.result, BattleGame.FORFEIT)
        self.assertIsNotNone(game.finished_at)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_another_users_game_is_not_reachable(self, fetch):
        game = self.start(fetch)
        User.objects.create_user("misty", password=PASSWORD)
        self.client.login(username="misty", password=PASSWORD)

        self.assertEqual(
            self.client.get(reverse("play", kwargs={"pk": game.pk})).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                reverse("play_move", kwargs={"pk": game.pk}),
                {"move": "flamethrower", "turn": 0},
            ).status_code,
            404,
        )

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_same_pokemon_by_different_identifiers_is_rejected(self, fetch):
        fetch.side_effect = [
            profile(number=25, name="Pikachu"), profile(number=25, name="Pikachu")
        ]

        response = self.client.post(
            reverse("play_start"), {"pokemon1": "pikachu", "pokemon2": "25"}
        )

        self.assertRedirects(response, reverse("play_setup"))
        self.assertEqual(BattleGame.objects.count(), 0)

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokemonNotFound("nope"))
    def test_unknown_pokemon_returns_404(self, _fetch):
        response = self.client.post(
            reverse("play_start"), {"pokemon1": "missingno", "pokemon2": "pikachu"}
        )
        self.assertEqual(response.status_code, 404)

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokeAPIUnavailable("boom"))
    def test_upstream_failure_returns_503(self, _fetch):
        response = self.client.post(
            reverse("play_start"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )
        self.assertEqual(response.status_code, 503)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_playing_to_a_finish_records_a_result(self, fetch):
        game = self.start(fetch)

        for _ in range(interactive.MAX_ROUNDS):
            game.refresh_from_db()
            if game.result:
                break
            self.client.post(
                reverse("play_move", kwargs={"pk": game.pk}),
                {"move": "flamethrower", "turn": game.turn_number},
            )

        game.refresh_from_db()
        self.assertIn(game.result, {"won", "lost", "tie"})
        self.assertIsNotNone(game.finished_at)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_history_lists_your_games_only(self, fetch):
        self.start(fetch)

        response = self.client.get(reverse("play_history"))
        self.assertContains(response, "Charizard")

        User.objects.create_user("misty", password=PASSWORD)
        self.client.login(username="misty", password=PASSWORD)
        self.assertContains(self.client.get(reverse("play_history")), "No games yet")

    def test_move_endpoint_rejects_get(self):
        self.assertEqual(
            self.client.get(reverse("play_move", kwargs={"pk": 1})).status_code, 405
        )


class PlayDraftTests(TestCase):
    """Choosing your four moves before the fight.

    The draft is optional by design: every entry point that skips it must still
    produce a playable game with the automatic four, so a good half of these are
    about the fallback rather than the drafting.
    """

    def setUp(self):
        User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    def contenders(self):
        return [
            profile(number=6, name="Charizard", slug="charizard", types=("fire", "flying"),
                    moves=[move("flamethrower", power=90, type="fire"),
                           move("fire-blast", power=110, type="fire"),
                           move("air-slash", power=75, type="flying"),
                           move("dragon-claw", power=80, type="dragon",
                                damage_class="physical"),
                           move("earthquake", power=100, type="ground",
                                damage_class="physical"),
                           move("focus-blast", power=120, type="fighting", accuracy=70)]),
            profile(number=9, name="Blastoise", slug="blastoise", types=("water",),
                    moves=[move("surf", power=90, type="water")]),
        ]

    def draft(self, fetch):
        fetch.side_effect = self.contenders()
        return self.client.post(
            reverse("play_draft"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )

    def start_with(self, moves):
        return self.client.post(
            reverse("play_start"),
            {"pokemon1": "charizard", "pokemon2": "blastoise", "move": moves},
        )

    def test_draft_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("play_draft"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_draft_lists_every_candidate_move(self, fetch):
        response = self.draft(fetch)

        self.assertEqual(response.status_code, 200)
        for name in ["Flamethrower", "Fire Blast", "Air Slash", "Dragon Claw",
                     "Earthquake", "Focus Blast"]:
            self.assertContains(response, name)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_draft_pre_ticks_the_automatic_four(self, fetch):
        """Accepting the default has to reproduce the old behaviour exactly, or
        the draft screen is a chore rather than a refinement."""
        response = self.draft(fetch)

        ticked = re.findall(
            r'name="move" value="([^"]+)"[^>]*checked', response.content.decode()
        )
        species = PokemonSpecies.objects.get(number=6)
        automatic = [
            entry["name"]
            for entry in interactive.player_moveset(
                [m.as_dict() for m in species.candidate_moves.all()]
            )
        ]
        self.assertEqual(sorted(ticked), sorted(automatic))
        self.assertEqual(len(ticked), interactive.MOVE_SLOTS)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_draft_shows_what_the_opponent_will_attack_with(self, fetch):
        response = self.draft(fetch)

        self.assertContains(response, "It will attack with")
        self.assertContains(response, "Surf")

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_draft_does_not_refetch_when_starting(self, fetch):
        """The draft screen already paid for the fetch; play_start must not pay
        again just because it was handed a name instead of a row."""
        self.draft(fetch)
        fetch.reset_mock()
        fetch.side_effect = None

        self.start_with(["earthquake", "air-slash"])

        fetch.assert_not_called()

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_drafted_moves_are_the_ones_stored(self, fetch):
        self.draft(fetch)

        self.start_with(["earthquake", "air-slash"])

        game = BattleGame.objects.get()
        self.assertEqual(
            sorted(game.player_moves.values_list("name", flat=True)),
            ["air-slash", "earthquake"],
        )

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_arena_offers_exactly_the_drafted_moves(self, fetch):
        self.draft(fetch)
        self.start_with(["earthquake", "air-slash"])
        game = BattleGame.objects.get()

        response = self.client.get(reverse("play", kwargs={"pk": game.pk}))

        offered = re.findall(r'name="move" value="([^"]+)"', response.content.decode())
        self.assertEqual(sorted(offered), ["air-slash", "earthquake"])

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_more_than_four_moves_is_refused(self, fetch):
        self.draft(fetch)

        response = self.start_with([
            "flamethrower", "fire-blast", "air-slash", "dragon-claw", "earthquake"
        ])

        self.assertRedirects(response, reverse("play_setup"))
        self.assertEqual(BattleGame.objects.count(), 0)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_move_the_pokemon_cannot_learn_is_refused(self, fetch):
        """A tampered form must not quietly play a different battle from the one
        the page offered."""
        self.draft(fetch)

        response = self.start_with(["earthquake", "hyper-beam"])

        self.assertRedirects(response, reverse("play_setup"))
        self.assertEqual(BattleGame.objects.count(), 0)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_starting_without_a_draft_falls_back_to_the_automatic_four(self, fetch):
        """The path taken by "Play it out" from a simulation and by a rematch."""
        fetch.side_effect = self.contenders()

        self.client.post(
            reverse("play_start"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )

        game = BattleGame.objects.get()
        self.assertEqual(game.player_moves.count(), interactive.MOVE_SLOTS)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_the_opponent_moveset_is_pinned_too(self, fetch):
        self.draft(fetch)
        self.start_with(["earthquake"])

        game = BattleGame.objects.get()
        self.assertEqual(
            list(game.opponent_moves.values_list("name", flat=True)), ["surf"]
        )

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_pinned_moveset_survives_the_candidate_set_being_rebuilt(self, fetch):
        """The reason the moveset is pinned to the game rather than derived: a
        finished battle has to keep reading the way it was played."""
        self.draft(fetch)
        self.start_with(["earthquake", "air-slash"])
        game = BattleGame.objects.get()

        game.player_species.candidate_moves.clear()

        response = self.client.get(reverse("play", kwargs={"pk": game.pk}))
        offered = re.findall(r'name="move" value="([^"]+)"', response.content.decode())
        self.assertEqual(sorted(offered), ["air-slash", "earthquake"])

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_a_game_with_no_pinned_moveset_still_loads(self, fetch):
        """Games created before the moveset was pinned fall back to the
        automatic pick rather than becoming unplayable."""
        self.draft(fetch)
        self.start_with(["earthquake"])
        game = BattleGame.objects.get()
        game.player_moves.clear()
        game.opponent_moves.clear()

        response = self.client.get(reverse("play", kwargs={"pk": game.pk}))

        self.assertEqual(response.status_code, 200)
        offered = re.findall(r'name="move" value="([^"]+)"', response.content.decode())
        self.assertEqual(len(offered), interactive.MOVE_SLOTS)

    @patch("pokedex.views.fetch_pokemon_profile")
    def test_same_pokemon_is_rejected_at_the_draft(self, fetch):
        fetch.side_effect = [
            profile(number=25, name="Pikachu", moves=[move("thunderbolt")]),
            profile(number=25, name="Pikachu", moves=[move("thunderbolt")]),
        ]

        response = self.client.post(
            reverse("play_draft"), {"pokemon1": "pikachu", "pokemon2": "25"}
        )

        self.assertRedirects(response, reverse("play_setup"))

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokemonNotFound("nope"))
    def test_unknown_pokemon_returns_404(self, _fetch):
        response = self.client.post(
            reverse("play_draft"), {"pokemon1": "missingno", "pokemon2": "pikachu"}
        )
        self.assertEqual(response.status_code, 404)

    @patch("pokedex.views.fetch_pokemon_profile", side_effect=PokeAPIUnavailable("boom"))
    def test_upstream_failure_returns_503(self, _fetch):
        response = self.client.post(
            reverse("play_draft"), {"pokemon1": "charizard", "pokemon2": "blastoise"}
        )
        self.assertEqual(response.status_code, 503)

    def test_draft_rejects_get(self):
        self.assertEqual(self.client.get(reverse("play_draft")).status_code, 405)


class PokemonNamesApiTests(TestCase):
    def test_returns_known_names(self):
        PokemonSpecies.objects.create(number=25, slug="pikachu", name="Pikachu")
        response = self.client.get(reverse("pokemon_names"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["names"], ["Pikachu"])
