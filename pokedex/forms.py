from django import forms
from django.contrib.auth.forms import UserCreationForm

from .services.pokeapi import normalize_name


class RegistrationForm(UserCreationForm):
    """Registration via Django's form so AUTH_PASSWORD_VALIDATORS actually run.

    The previous view called ``User.objects.create_user`` directly, which skips
    validation entirely -- "1" was an acceptable password.
    """

    class Meta(UserCreationForm.Meta):
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"autocomplete": "username", "autofocus": True}
        )
        self.fields["password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["password2"].widget.attrs["autocomplete"] = "new-password"


class PokemonSearchForm(forms.Form):
    """A GET form, so results are linkable and refresh does not re-POST."""

    pokemon = forms.CharField(
        max_length=60,
        label="Pokemon name or number",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search your Pokemon...",
                "list": "pokemon-suggestions",
                "autocomplete": "off",
            }
        ),
    )

    def clean_pokemon(self):
        return normalize_name(self.cleaned_data["pokemon"])


class BattleForm(forms.Form):
    pokemon1 = forms.CharField(
        max_length=60,
        label="First Pokemon",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. charizard",
                "list": "pokemon-suggestions",
                "autocomplete": "off",
            }
        ),
    )
    pokemon2 = forms.CharField(
        max_length=60,
        label="Second Pokemon",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. blastoise",
                "list": "pokemon-suggestions",
                "autocomplete": "off",
            }
        ),
    )

    def clean_pokemon1(self):
        return normalize_name(self.cleaned_data["pokemon1"])

    def clean_pokemon2(self):
        return normalize_name(self.cleaned_data["pokemon2"])

    def clean(self):
        cleaned = super().clean()
        first, second = cleaned.get("pokemon1"), cleaned.get("pokemon2")
        # Only catches identical input; "pikachu" vs "25" is the same Pokemon and
        # is caught in the view, after both names resolve to a number.
        if first and second and first == second:
            raise forms.ValidationError("Pick two different Pokemon.")
        return cleaned
