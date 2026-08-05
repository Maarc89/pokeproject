from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ..models import Battle, PokemonSpecies, SavedPokemon
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


class PokemonNamesApiTests(TestCase):
    def test_returns_known_names(self):
        PokemonSpecies.objects.create(number=25, slug="pikachu", name="Pikachu")
        response = self.client.get(reverse("pokemon_names"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["names"], ["Pikachu"])
