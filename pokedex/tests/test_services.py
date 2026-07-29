import responses
from django.core.cache import cache
from django.test import SimpleTestCase

from ..services.pokeapi import (
    API_ROOT,
    PokeAPIUnavailable,
    PokemonNotFound,
    best_damaging_move,
    fetch_pokemon_profile,
    normalize_name,
)
from .factories import api_move_payload, api_pokemon_payload


class NormalizeNameTests(SimpleTestCase):
    def test_lowercases_and_trims(self):
        self.assertEqual(normalize_name("  PIKACHU "), "pikachu")

    def test_spaces_become_hyphens(self):
        # The old code percent-encoded the space, which always 404'd because
        # PokeAPI slugs are hyphenated.
        self.assertEqual(normalize_name("Mr Mime"), "mr-mime")

    def test_collapses_repeated_whitespace(self):
        self.assertEqual(normalize_name("mr   mime"), "mr-mime")


class BestDamagingMoveTests(SimpleTestCase):
    @staticmethod
    def _move(name, power, type="normal"):
        return {"name": name, "power": power, "type": type}

    def test_picks_highest_power(self):
        moves = [
            self._move("tackle", 40),
            self._move("thunderbolt", 90, "electric"),
            self._move("quick-attack", 40),
        ]
        self.assertEqual(best_damaging_move(moves)["name"], "thunderbolt")

    def test_ignores_status_moves(self):
        moves = [self._move("growl", 0), self._move("tackle", 40)]
        self.assertEqual(best_damaging_move(moves)["name"], "tackle")

    def test_returns_none_when_no_damaging_move(self):
        self.assertIsNone(best_damaging_move([self._move("growl", 0)]))

    def test_stab_beats_slightly_higher_raw_power(self):
        """Otherwise every Pokemon's 'best' move is a universal TM.

        Hyper Beam (150, Normal) is learnable by almost everything. Without
        STAB weighting, Charizard's strongest move is Normal-type and its Fire
        typing never influences a battle at all.
        """
        moves = [
            self._move("hyper-beam", 150, "normal"),
            self._move("fire-blast", 110, "fire"),
        ]

        chosen = best_damaging_move(moves, own_types=["fire", "flying"])

        # 110 * 1.5 = 165 > 150
        self.assertEqual(chosen["name"], "fire-blast")
        self.assertTrue(chosen["stab"])

    def test_raw_power_still_wins_when_the_gap_is_large_enough(self):
        moves = [
            self._move("explosion", 250, "normal"),
            self._move("ember", 40, "fire"),
        ]

        chosen = best_damaging_move(moves, own_types=["fire"])

        self.assertEqual(chosen["name"], "explosion")
        self.assertFalse(chosen["stab"])

    def test_no_own_types_means_no_stab(self):
        chosen = best_damaging_move([self._move("fire-blast", 110, "fire")])
        self.assertFalse(chosen["stab"])


class FetchProfileTests(SimpleTestCase):
    def setUp(self):
        # LocMemCache persists across tests in a process, and every assertion
        # here counts requests, so it must start empty.
        cache.clear()
        self.addCleanup(cache.clear)

    def _register_pokemon(self, name="pikachu", moves=()):
        responses.add(
            responses.GET,
            f"{API_ROOT}/pokemon/{name}",
            json=api_pokemon_payload(name=name, moves=moves),
            status=200,
        )

    def _register_moves(self, *names):
        for name in names:
            responses.add(
                responses.GET,
                f"https://pokeapi.co/api/v2/move/{name}/",
                json=api_move_payload(name=name),
                status=200,
            )

    @responses.activate
    def test_search_path_does_not_fetch_moves(self):
        """The single most important behaviour in this module.

        The search screen never shows move data, so it must not pay for it.
        """
        self._register_pokemon(moves=["tackle", "thunderbolt", "growl"])

        profile = fetch_pokemon_profile("pikachu")

        self.assertEqual(profile["name"], "Pikachu")
        self.assertIsNone(profile["best_move"])
        self.assertEqual(len(responses.calls), 1, "search must issue exactly one request")

    @responses.activate
    def test_battle_path_fetches_each_move_once(self):
        """N moves cost N requests -- not N per Pokemon, per battle, forever."""
        self._register_pokemon(moves=["tackle", "thunderbolt", "growl"])
        self._register_moves("tackle", "thunderbolt", "growl")

        profile = fetch_pokemon_profile("pikachu", include_moves=True)

        self.assertEqual(profile["best_move"]["name"], "thunderbolt")
        self.assertEqual(profile["best_move"]["power"], 90)
        self.assertEqual(len(responses.calls), 4)  # 1 pokemon + 3 moves

    @responses.activate
    def test_repeat_lookup_is_served_from_cache(self):
        self._register_pokemon(moves=["tackle"])
        self._register_moves("tackle")

        fetch_pokemon_profile("pikachu", include_moves=True)
        before = len(responses.calls)
        fetch_pokemon_profile("pikachu", include_moves=True)

        self.assertEqual(len(responses.calls), before, "second lookup must not hit the network")

    @responses.activate
    def test_moves_are_cached_across_different_pokemon(self):
        """Move data is immutable, so a move fetched once is never fetched again."""
        self._register_pokemon("pikachu", moves=["tackle"])
        self._register_pokemon("raichu", moves=["tackle"])
        self._register_moves("tackle")

        fetch_pokemon_profile("pikachu", include_moves=True)
        calls_after_first = len(responses.calls)

        fetch_pokemon_profile("raichu", include_moves=True)

        # Only the raichu document itself; "tackle" was already cached.
        self.assertEqual(len(responses.calls), calls_after_first + 1)

    @responses.activate
    def test_unknown_pokemon_raises_not_found(self):
        responses.add(responses.GET, f"{API_ROOT}/pokemon/missingno", status=404)
        with self.assertRaises(PokemonNotFound):
            fetch_pokemon_profile("missingno")

    @responses.activate
    def test_server_error_raises_unavailable(self):
        responses.add(responses.GET, f"{API_ROOT}/pokemon/pikachu", status=500)
        with self.assertRaises(PokeAPIUnavailable):
            fetch_pokemon_profile("pikachu")

    @responses.activate
    def test_non_json_response_raises_unavailable(self):
        responses.add(
            responses.GET, f"{API_ROOT}/pokemon/pikachu", body="<html>oops</html>", status=200
        )
        with self.assertRaises(PokeAPIUnavailable):
            fetch_pokemon_profile("pikachu")

    @responses.activate
    def test_malformed_payload_raises_unavailable(self):
        responses.add(responses.GET, f"{API_ROOT}/pokemon/pikachu", json={"nope": 1}, status=200)
        with self.assertRaises(PokeAPIUnavailable):
            fetch_pokemon_profile("pikachu")

    @responses.activate
    def test_a_failing_move_does_not_fail_the_lookup(self):
        self._register_pokemon(moves=["tackle", "thunderbolt"])
        self._register_moves("thunderbolt")
        responses.add(
            responses.GET, "https://pokeapi.co/api/v2/move/tackle/", status=500
        )

        profile = fetch_pokemon_profile("pikachu", include_moves=True)

        self.assertEqual(profile["best_move"]["name"], "thunderbolt")

    @responses.activate
    def test_missing_sprite_becomes_empty_string(self):
        payload = api_pokemon_payload()
        payload["sprites"]["front_default"] = None
        responses.add(responses.GET, f"{API_ROOT}/pokemon/pikachu", json=payload, status=200)

        self.assertEqual(fetch_pokemon_profile("pikachu")["sprite"], "")

    @responses.activate
    def test_types_are_a_list_of_lowercase_slugs(self):
        responses.add(
            responses.GET,
            f"{API_ROOT}/pokemon/charizard",
            json=api_pokemon_payload(number=6, name="charizard", types=("fire", "flying")),
            status=200,
        )

        self.assertEqual(fetch_pokemon_profile("charizard")["types"], ["fire", "flying"])
