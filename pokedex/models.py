from django.conf import settings
from django.db import models


class Move(models.Model):
    """A PokeAPI move.

    Stored locally because move data never changes once published, and looking
    it up is the single most expensive thing this app does.
    """

    name = models.SlugField(max_length=100, unique=True)
    power = models.PositiveSmallIntegerField(default=0)
    type = models.CharField(max_length=20, blank=True)
    damage_class = models.CharField(max_length=20, blank=True)
    # These three were fetched and then thrown away on the trip to the database.
    # An interactive battle has to roll accuracy for real and make a charge move
    # actually cost a turn, so it needs them -- and with them present this row is
    # a complete mirror of the move dict, which is what lets a played turn be a
    # pure DB read instead of a PokeAPI call.
    accuracy = models.PositiveSmallIntegerField(default=100)
    turn_cost = models.PositiveSmallIntegerField(default=1)
    self_ko = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.name.replace("-", " ").title()

    def as_dict(self):
        """This row in the shape ``services.pokeapi._fetch_move`` produces.

        The battle engine takes plain dicts, not models, so that it can run
        against a freshly fetched profile or a stored one without caring which.
        This is the adapter for the stored side.
        """
        return {
            "name": self.name,
            "display_name": self.display_name,
            "power": self.power,
            "type": self.type,
            "damage_class": self.damage_class,
            "accuracy": self.accuracy,
            "turn_cost": self.turn_cost,
            "self_ko": self.self_ko,
        }


class PokemonSpecies(models.Model):
    """A Pokemon as PokeAPI describes it.

    One row per Pokemon, shared by every user. Previously each user got their own
    copy of this data, so fifty users saving Pikachu meant fifty identical rows.
    """

    number = models.PositiveIntegerField(unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=100, db_index=True)
    types = models.JSONField(default=list)
    abilities = models.JSONField(default=list)
    # Four images, all present in the one /pokemon/ payload we already fetch --
    # shiny forms cost no extra requests, they were simply being discarded.
    # sprite* are the 96px game sprites; artwork* is the high-resolution
    # official art, which is what the detail page shows.
    sprite = models.URLField(blank=True)
    sprite_shiny = models.URLField(blank=True)
    artwork = models.URLField(blank=True)
    artwork_shiny = models.URLField(blank=True)
    stats = models.JSONField(default=dict)

    # Reported by the API in decimetres and hectograms; templates convert.
    height = models.PositiveSmallIntegerField(default=0)
    weight = models.PositiveIntegerField(default=0)
    base_experience = models.PositiveSmallIntegerField(default=0)
    cry = models.URLField(blank=True)
    # The signature move shown in Pokedex listings: strongest overall, chosen
    # with no opponent in mind. A battle does not use this -- see
    # `candidate_moves` -- but a list page has nobody to compare against.
    best_move = models.ForeignKey(
        Move,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="strongest_for",
    )
    # The moves a battle may choose between: the strongest of each
    # (type, damage class) pair, since the rest can never beat them. Shared
    # rather than copied, for the same reason species rows are.
    candidate_moves = models.ManyToManyField(
        Move,
        blank=True,
        related_name="candidate_for",
    )
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("number",)
        verbose_name = "Pokemon species"
        verbose_name_plural = "Pokemon species"

    def __str__(self):
        return f"#{self.number} {self.name}"

    @property
    def stat_total(self):
        return sum(self.stats.values()) if self.stats else 0

    @property
    def height_m(self):
        return self.height / 10

    @property
    def weight_kg(self):
        return self.weight / 10


class SavedPokemon(models.Model):
    """A Pokemon in a particular user's Pokedex."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_pokemon",
    )
    species = models.ForeignKey(
        PokemonSpecies,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("species__number",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "species"], name="unique_species_per_user"
            ),
        ]
        verbose_name_plural = "Saved Pokemon"

    def __str__(self):
        return f"{self.species.name} ({self.user})"


class Battle(models.Model):
    """The record of one battle, kept so results are linkable and reviewable."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="battles",
    )
    species_1 = models.ForeignKey(
        PokemonSpecies, on_delete=models.CASCADE, related_name="battles_as_first"
    )
    species_2 = models.ForeignKey(
        PokemonSpecies, on_delete=models.CASCADE, related_name="battles_as_second"
    )
    # Null means the battle was a tie.
    winner = models.ForeignKey(
        PokemonSpecies,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="battles_won",
    )
    # Damage dealt per turn. Rows created before the simulation rewrite hold the
    # old additive score instead, which is not comparable -- hence turns_*
    # being null for exactly those rows.
    score_1 = models.PositiveIntegerField()
    score_2 = models.PositiveIntegerField()
    turns_1 = models.PositiveSmallIntegerField(null=True, blank=True)
    turns_2 = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        result = self.winner.name if self.winner else "tie"
        return f"{self.species_1.name} vs {self.species_2.name}: {result}"

    @property
    def is_tie(self):
        return self.winner_id is None


class BattleGame(models.Model):
    """A battle the user is *playing*, one move at a time.

    Separate from `Battle`, which records a finished deterministic simulation.
    Its `score_1`/`score_2` mean "damage per turn" and are not comparable to a
    played game, so mixing the two would quietly corrupt the history page.

    State lives here rather than in the session because a game should survive a
    refresh, be linkable, and be resumable -- the same reasoning that keeps the
    shiny flag and every filter in the URL.
    """

    WON = "won"
    LOST = "lost"
    TIE = "tie"
    FORFEIT = "forfeit"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="battle_games",
    )
    player_species = models.ForeignKey(
        PokemonSpecies, on_delete=models.CASCADE, related_name="games_as_player"
    )
    opponent_species = models.ForeignKey(
        PokemonSpecies, on_delete=models.CASCADE, related_name="games_as_opponent"
    )

    # Every roll in this game derives from this seed plus the turn and the move
    # chosen, so a turn always resolves the same way however often it is
    # recomputed. See services.interactive.rng_for -- it is what stops a player
    # refreshing a miss into a critical hit.
    seed = models.BigIntegerField()

    player_hp = models.PositiveIntegerField()
    opponent_hp = models.PositiveIntegerField()
    player_max_hp = models.PositiveIntegerField()
    opponent_max_hp = models.PositiveIntegerField()

    # The four moves each side brought, pinned to the game rather than derived
    # from the species on every request. The player picks theirs at the draft
    # step, so they are not recoverable any other way -- and pinning the
    # opponent's too means a finished battle still reads correctly even if the
    # stored candidate set is later rebuilt.
    player_moves = models.ManyToManyField(Move, related_name="drafted_for", blank=True)
    opponent_moves = models.ManyToManyField(
        Move, related_name="faced_in", blank=True
    )

    # Doubles as an optimistic-concurrency guard: the move form carries the turn
    # it was rendered for, and a POST that disagrees is refused. Without it,
    # going Back and submitting a different move lets a player scout the rolls
    # one move at a time.
    turn_number = models.PositiveSmallIntegerField(default=0)

    # A move with a charge or recharge turn costs the following action.
    player_recharging = models.BooleanField(default=False)
    opponent_recharging = models.BooleanField(default=False)

    # Serialised Turns. JSON rather than a table because the log is only ever
    # rendered, never queried -- the same call `PokemonSpecies.stats` makes.
    log = models.JSONField(default=list)

    result = models.CharField(max_length=10, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return (
            f"{self.player_species.name} vs {self.opponent_species.name}"
            f" ({self.result or 'in progress'})"
        )

    @property
    def is_finished(self):
        return bool(self.result)

    @property
    def player_hp_pct(self):
        return self._pct(self.player_hp, self.player_max_hp)

    @property
    def opponent_hp_pct(self):
        return self._pct(self.opponent_hp, self.opponent_max_hp)

    @staticmethod
    def _pct(current, maximum):
        if not maximum:
            return 0
        return round(current / maximum * 100)
