from django.shortcuts import render
import urllib.request
import json
from http import HTTPStatus
from urllib.error import HTTPError
from django.http import HttpResponse
from .models import Pokemon
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required


def index(request):
    try:
        if request.method == 'POST':
            pokemon_name = request.POST['pokemon'].lower()
            pokemon_name = pokemon_name.replace(' ', '%20')

            # Verificar si el Pokémon ya está en la base de datos
            pokemon = Pokemon.objects.filter(name__iexact=pokemon_name).first()

            if pokemon:
                print("Pokémon ya existe en la base de datos.")
            else:
                url_pokeapi = urllib.request.Request(f'https://pokeapi.co/api/v2/pokemon/{pokemon_name}')
                url_pokeapi.add_header('User-Agent', 'charmander')

                source = urllib.request.urlopen(url_pokeapi).read()
                list_of_data = json.loads(source)

                tipos = ", ".join([tipo['type']['name'].capitalize() for tipo in list_of_data['types']])
                habilidades = ", ".join(
                    [habilidad['ability']['name'].capitalize() for habilidad in list_of_data['abilities']])

                # Verificar si el usuario está autenticado
                if request.user.is_authenticated:
                    user = request.user
                else:
                    user = None  # O asignar un usuario predeterminado si lo prefieres

                # Guardar en la base de datos
                pokemon = Pokemon.objects.create(
                    number=list_of_data['id'],
                    name=list_of_data['name'].capitalize(),
                    types=tipos,
                    abilities=habilidades,
                    sprite=list_of_data['sprites']['front_default'],
                    user=user
                )

                print(f"Pokémon {pokemon.name} guardado en la base de datos.")

            data = {
                "number": pokemon.number,
                "name": pokemon.name,
                "types": pokemon.types,
                "abilities": pokemon.abilities,
                "sprite": pokemon.sprite,
            }

        else:
            data = {}

        return render(request, 'main/index.html', data)

    except HTTPError as e:
        if e.code == 404:
            return render(request, 'main/404.html')


# Vista de registro
def register(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('index')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})


# Vista de inicio de sesión
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('index')
        else:
            return render(request, 'registration/login.html', {'error': 'Usuario o contraseña incorrectos'})
    return render(request, 'registration/login.html')


# Vista de Pokémon guardados
@login_required
def my_pokemon(request):
    user_pokemon = Pokemon.objects.filter(user=request.user)
    return render(request, 'main/my_pokemon.html', {'pokemon_list': user_pokemon})
