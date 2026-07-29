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
