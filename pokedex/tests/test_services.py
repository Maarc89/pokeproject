import responses
from django.core.cache import cache
from django.test import SimpleTestCase

from ..services.pokeapi import (
    API_ROOT,
    PokeAPIUnavailable,
    PokemonNotFound,
    best_damaging_move,
    candidate_moveset,
    fetch_evolution_chain,
    fetch_moves,
    fetch_pokemon_profile,
    fetch_species_details,
    normalize_name,
)
from .factories import (
    api_evolution_chain_payload,
    api_move_payload,
    api_pokemon_payload,
    api_species_payload,
)


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


class CandidateMovesetTests(SimpleTestCase):
    """Pruning 100-250 moves to the handful that could ever be the best pick."""

    @staticmethod
    def _move(name, power, type="normal", damage_class="physical"):
        return {
            "name": name, "power": power, "type": type,
            "damage_class": damage_class, "accuracy": 100, "turn_cost": 1,
            "self_ko": False,
        }

    def test_keeps_only_the_strongest_of_each_type_and_class(self):
        moveset = candidate_moveset([
            self._move("tackle", 40),
            self._move("body-slam", 85),
            self._move("mega-kick", 120),
        ])

        self.assertEqual([m["name"] for m in moveset], ["mega-kick"])

    def test_a_different_type_is_kept_even_when_weaker(self):
        """A weak move of the right type beats a strong one that is resisted."""
        moveset = candidate_moveset([
            self._move("mega-kick", 120, "normal"),
            self._move("mud-slap", 20, "ground"),
        ])

        self.assertEqual(
            sorted(m["name"] for m in moveset), ["mega-kick", "mud-slap"]
        )

    def test_physical_and_special_of_one_type_are_both_kept(self):
        """They read different defensive stats, so neither dominates."""
        moveset = candidate_moveset([
            self._move("fire-punch", 75, "fire", "physical"),
            self._move("flamethrower", 90, "fire", "special"),
        ])

        self.assertEqual(len(moveset), 2)

    def test_status_moves_are_dropped(self):
        moveset = candidate_moveset([
            self._move("growl", 0),
            self._move("tackle", 40),
        ])

        self.assertEqual([m["name"] for m in moveset], ["tackle"])

    def test_equal_power_breaks_on_name_so_the_set_is_stable(self):
        first = candidate_moveset([self._move("zap-cannon", 50), self._move("aurora", 50)])
        second = candidate_moveset([self._move("aurora", 50), self._move("zap-cannon", 50)])

        self.assertEqual(first, second)
        self.assertEqual(first[0]["name"], "aurora")

    def test_empty_input(self):
        self.assertEqual(candidate_moveset([]), [])

    def test_a_self_knockout_move_never_enters_the_set(self):
        moveset = candidate_moveset([
            dict(self._move("explosion", 250), self_ko=True),
            self._move("tackle", 40),
        ])

        self.assertEqual([m["name"] for m in moveset], ["tackle"])

    def test_effective_power_beats_raw_power(self):
        """A charge move that lands every other turn loses to a steady one."""
        moveset = candidate_moveset([
            dict(self._move("hyper-beam", 150), turn_cost=2),
            self._move("body-slam", 85),
        ])

        self.assertEqual([m["name"] for m in moveset], ["body-slam"])

    def test_accuracy_counts_toward_effective_power(self):
        moveset = candidate_moveset([
            dict(self._move("dynamic-punch", 100), accuracy=50),
            self._move("body-slam", 85),
        ])

        self.assertEqual([m["name"] for m in moveset], ["body-slam"])


class MoveMetadataTests(SimpleTestCase):
    """Reading accuracy and usability off the move payload."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _fetch(self, **payload_fields):
        responses.add(
            responses.GET,
            "https://pokeapi.co/api/v2/move/test-move/",
            json=api_move_payload(name="test-move", **payload_fields),
            status=200,
        )
        return fetch_moves([{
            "move": {
                "name": "test-move",
                "url": "https://pokeapi.co/api/v2/move/test-move/",
            }
        }])[0]

    @responses.activate
    def test_accuracy_is_read(self):
        self.assertEqual(self._fetch(accuracy=85)["accuracy"], 85)

    @responses.activate
    def test_null_accuracy_means_the_move_cannot_miss(self):
        """Swift and Aerial Ace report null, which is 'always hits', not zero."""
        self.assertEqual(self._fetch(accuracy=None)["accuracy"], 100)

    @responses.activate
    def test_a_recharge_move_costs_two_turns(self):
        got = self._fetch(effect="User foregoes its next turn to recharge.")
        self.assertEqual(got["turn_cost"], 2)

    @responses.activate
    def test_a_charge_move_costs_two_turns(self):
        got = self._fetch(effect="Requires a turn to charge before attacking.")
        self.assertEqual(got["turn_cost"], 2)

    @responses.activate
    def test_a_self_knockout_move_is_flagged(self):
        self.assertTrue(self._fetch(effect="User faints.")["self_ko"])

    @responses.activate
    def test_an_ordinary_move_is_unflagged(self):
        got = self._fetch(effect="Inflicts regular damage.")
        self.assertEqual(got["turn_cost"], 1)
        self.assertFalse(got["self_ko"])

    @responses.activate
    def test_unrecognised_effect_text_degrades_to_an_ordinary_move(self):
        """A phrase we do not know must cost accuracy, never correctness."""
        got = self._fetch(effect="Summons a thunderstorm of unknown provenance.")
        self.assertEqual(got["turn_cost"], 1)
        self.assertFalse(got["self_ko"])


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

    def _register_move(self, name, **fields):
        """One move with its own power/type, for tests that need them to differ."""
        responses.add(
            responses.GET,
            f"https://pokeapi.co/api/v2/move/{name}/",
            json=api_move_payload(name=name, **fields),
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
        self.assertEqual(profile["moves"], [])
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
    def test_battle_path_stores_a_pruned_moveset(self):
        self._register_pokemon(moves=["thunderbolt", "dig", "growl"])
        self._register_move("thunderbolt", power=90, type="electric")
        self._register_move("dig", power=80, type="ground")
        self._register_move("growl", power=None, type="normal")

        profile = fetch_pokemon_profile("pikachu", include_moves=True)

        # Two damaging moves of different types survive; the status move does not.
        self.assertEqual(sorted(m["name"] for m in profile["moves"]), ["dig", "thunderbolt"])

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
    def test_shiny_and_artwork_urls_come_free_with_the_search(self):
        """All four images are in the payload the cheap path already fetches."""
        self._register_pokemon()

        profile = fetch_pokemon_profile("pikachu")

        self.assertEqual(profile["sprite_shiny"], "https://example.invalid/pikachu-shiny.png")
        self.assertEqual(profile["artwork"], "https://example.invalid/pikachu-art.png")
        self.assertEqual(
            profile["artwork_shiny"], "https://example.invalid/pikachu-art-shiny.png"
        )
        self.assertEqual(len(responses.calls), 1, "shiny art must cost no extra request")

    @responses.activate
    def test_missing_shiny_and_artwork_become_empty_strings(self):
        payload = api_pokemon_payload()
        payload["sprites"]["front_shiny"] = None
        payload["sprites"]["other"] = {}
        responses.add(responses.GET, f"{API_ROOT}/pokemon/pikachu", json=payload, status=200)

        profile = fetch_pokemon_profile("pikachu")

        self.assertEqual(profile["sprite_shiny"], "")
        self.assertEqual(profile["artwork"], "")
        self.assertEqual(profile["artwork_shiny"], "")

    @responses.activate
    def test_stat_labels_are_spaced(self):
        """The template's |cut:"-"|title turned "special-attack" into "Specialattack"."""
        self._register_pokemon()

        labels = [row["label"] for row in fetch_pokemon_profile("pikachu")["stat_rows"]]

        self.assertIn("Special Attack", labels)
        self.assertIn("Special Defense", labels)
        self.assertEqual(labels[0], "Hp")

    @responses.activate
    def test_free_payload_fields_are_read(self):
        """Height, weight and the cry are already in the response we fetch."""
        self._register_pokemon()

        profile = fetch_pokemon_profile("pikachu")

        self.assertEqual(profile["height"], 4)
        self.assertEqual(profile["weight"], 60)
        self.assertEqual(profile["base_experience"], 112)
        self.assertEqual(profile["cry"], "https://example.invalid/pikachu.ogg")
        # 4 decimetres is 0.4 m; 60 hectograms is 6 kg.
        self.assertAlmostEqual(profile["height_m"], 0.4)
        self.assertAlmostEqual(profile["weight_kg"], 6.0)

    @responses.activate
    def test_types_are_a_list_of_lowercase_slugs(self):
        responses.add(
            responses.GET,
            f"{API_ROOT}/pokemon/charizard",
            json=api_pokemon_payload(number=6, name="charizard", types=("fire", "flying")),
            status=200,
        )

        self.assertEqual(fetch_pokemon_profile("charizard")["types"], ["fire", "flying"])


class SpeciesDetailsTests(SimpleTestCase):
    """The Pokedex-entry half: description, genus, family."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _register_species(self, number=25, **fields):
        responses.add(
            responses.GET,
            f"{API_ROOT}/pokemon-species/{number}",
            json=api_species_payload(number=number, **fields),
            status=200,
        )

    def _register_chain(self, url="https://pokeapi.co/api/v2/evolution-chain/10/"):
        responses.add(
            responses.GET, url, json=api_evolution_chain_payload(), status=200
        )

    @responses.activate
    def test_reads_genus_and_flags(self):
        self._register_species(is_legendary=True)
        self._register_chain()

        details = fetch_species_details(25)

        self.assertEqual(details["genus"], "Mouse Pokemon")
        self.assertTrue(details["is_legendary"])
        self.assertFalse(details["is_mythical"])
        self.assertEqual(details["habitat"], "Forest")
        self.assertEqual(details["generation"], "I")

    @responses.activate
    def test_flavor_text_is_flattened(self):
        """The API embeds the games' line breaks and a form feed (\x0c)."""
        self._register_species()
        self._register_chain()

        text = fetch_species_details(25)["flavor_text"]

        self.assertNotIn("\n", text)
        self.assertNotIn("\x0c", text)
        self.assertIn("gather, their electricity", text)

    @responses.activate
    def test_non_english_entries_are_ignored(self):
        self._register_species()
        self._register_chain()

        details = fetch_species_details(25)

        self.assertNotIn("japanese", details["flavor_text"])
        self.assertNotEqual(details["genus"], "Something else")

    @responses.activate
    def test_a_repeat_lookup_is_cached(self):
        self._register_species()
        self._register_chain()

        fetch_species_details(25)
        before = len(responses.calls)
        fetch_species_details(25)

        self.assertEqual(len(responses.calls), before)

    @responses.activate
    def test_evolutions_can_be_skipped(self):
        self._register_species()

        details = fetch_species_details(25, include_evolutions=False)

        self.assertEqual(details["evolutions"], [])
        self.assertEqual(len(responses.calls), 1)


class EvolutionChainTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    URL = "https://pokeapi.co/api/v2/evolution-chain/10/"

    def _register(self, payload=None):
        responses.add(
            responses.GET,
            self.URL,
            json=payload or api_evolution_chain_payload(),
            status=200,
        )

    @responses.activate
    def test_a_linear_chain_becomes_one_pokemon_per_stage(self):
        self._register()

        stages = fetch_evolution_chain(self.URL)

        self.assertEqual([[m["name"] for m in s] for s in stages],
                         [["Charmander"], ["Charmeleon"], ["Charizard"]])

    @responses.activate
    def test_evolution_triggers_are_described(self):
        self._register()

        stages = fetch_evolution_chain(self.URL)

        self.assertEqual(stages[0][0]["trigger"], "")  # the base has none
        self.assertEqual(stages[1][0]["trigger"], "Level 16")
        self.assertEqual(stages[2][0]["trigger"], "Level 36")

    @responses.activate
    def test_sprites_are_derived_from_the_species_id(self):
        """Built, not fetched: a request per family member is not worth a thumbnail."""
        self._register()

        first = fetch_evolution_chain(self.URL)[0][0]

        self.assertEqual(first["number"], 4)
        self.assertTrue(first["sprite"].endswith("/pokemon/4.png"))

    @responses.activate
    def test_a_branching_chain_puts_siblings_in_one_stage(self):
        """Eevee is the case a flat list would get wrong."""
        def species(number, name):
            return {
                "name": name,
                "url": f"https://pokeapi.co/api/v2/pokemon-species/{number}/",
            }

        self._register({
            "chain": {
                "species": species(133, "eevee"),
                "evolution_details": [],
                "evolves_to": [
                    {"species": species(134, "vaporeon"), "evolution_details": [
                        {"trigger": {"name": "use-item"}, "item": {"name": "water-stone"}}
                    ], "evolves_to": []},
                    {"species": species(135, "jolteon"), "evolution_details": [
                        {"trigger": {"name": "use-item"}, "item": {"name": "thunder-stone"}}
                    ], "evolves_to": []},
                ],
            }
        })

        stages = fetch_evolution_chain(self.URL)

        self.assertEqual(len(stages), 2)
        self.assertEqual([m["name"] for m in stages[1]], ["Vaporeon", "Jolteon"])
        self.assertEqual(stages[1][0]["trigger"], "Water Stone")

    @responses.activate
    def test_the_default_game_version_decides_the_trigger(self):
        """Details disagree across games; the first entry is not authoritative.

        Leafeon is a mossy rock in Diamond/Pearl and a Leaf Stone in
        Sword/Shield. Only the latter carries is_default.
        """
        self._register({
            "chain": {
                "species": {
                    "name": "eevee",
                    "url": "https://pokeapi.co/api/v2/pokemon-species/133/",
                },
                "evolution_details": [],
                "evolves_to": [{
                    "species": {
                        "name": "leafeon",
                        "url": "https://pokeapi.co/api/v2/pokemon-species/470/",
                    },
                    "evolution_details": [
                        {
                            "trigger": {"name": "level-up"},
                            "location": {"name": "eterna-forest"},
                        },
                        {
                            "trigger": {"name": "use-item"},
                            "item": {"name": "leaf-stone"},
                            "is_default": True,
                        },
                    ],
                    "evolves_to": [],
                }],
            }
        })

        stages = fetch_evolution_chain(self.URL)

        self.assertEqual(stages[1][0]["trigger"], "Leaf Stone")

    @responses.activate
    def test_the_chain_is_cached_against_its_url(self):
        """A family shares one chain, so the second member costs nothing."""
        self._register()

        fetch_evolution_chain(self.URL)
        before = len(responses.calls)
        fetch_evolution_chain(self.URL)

        self.assertEqual(len(responses.calls), before)

    def test_no_url_is_not_an_error(self):
        self.assertEqual(fetch_evolution_chain(""), [])
