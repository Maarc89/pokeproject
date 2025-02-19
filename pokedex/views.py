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
from django.contrib.auth.models import User
from django.contrib import messages


def index(request):
    try:
        if request.method == 'POST':
            pokemon_name = request.POST['pokemon'].lower()
            pokemon_name = pokemon_name.replace(' ', '%20')
            url_pokeapi = urllib.request.Request(f'https://pokeapi.co/api/v2/pokemon/{pokemon_name}')
            url_pokeapi.add_header('User-Agent', 'charmander')

            source = urllib.request.urlopen(url_pokeapi).read()

            list_of_data = json.loads(source)

            tipos = ", ".join([tipo['type']['name'].capitalize() for tipo in list_of_data['types']])
            habilidades = ", ".join([habilidad['ability']['name'].capitalize() for habilidad in list_of_data['abilities']])

            data = {
                "number": str(list_of_data['id']),
                "name": str(list_of_data['name']).capitalize(),
                "types": tipos,
                "abilities": habilidades,
                "sprite": str(list_of_data['sprites']['front_default']),
            }

            # Si el usuario está autenticado, guarda el Pokémon en la base de datos
            if request.user.is_authenticated:
                user = request.user
                # Verificar si el Pokémon ya está guardado por este usuario
                pokemon, created = Pokemon.objects.get_or_create(
                    user=user,
                    number=data["number"],  # Guarda el Pokémon con su número
                    name=data["name"],
                    types=data["types"],
                    abilities=data["abilities"],
                    sprite=data["sprite"]
                )
                if created:
                    print(f'Nuevo Pokémon guardado: {pokemon.name} para {user.username}')
                else:
                    print(f'El Pokémon {pokemon.name} ya está guardado en la base de datos')

            print(data)

        else:
            data = {}

        return render(request, 'main/index.html', data)
    except HTTPError as e:
        if e.code == 404:
            return render(request, 'main/404.html')


# Vista de registro
def register_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        password2 = request.POST['password2']

        # Comprobar que las contraseñas coincidan
        if password == password2:
            # Crear el nuevo usuario
            try:
                user = User.objects.create_user(username=username, password=password)
                messages.success(request, "Registro exitoso. Ahora puedes iniciar sesión.")
                return redirect('login')  # Redirige a la página de login
            except Exception as e:
                messages.error(request, f"Error al registrar: {e}")
        else:
            messages.error(request, "Las contraseñas no coinciden")

    return render(request, 'registration/register.html')


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
            messages.error(request, "Credenciales incorrectas")
            return render(request, 'registration/login.html', {'error': 'Usuario o contraseña incorrectos'})
    return render(request, 'registration/login.html')


# Vista de Pokémon guardados
@login_required
def my_pokemon(request):
    user_pokemon = Pokemon.objects.filter(user=request.user)
    return render(request, 'main/my_pokemon.html', {'pokemon_list': user_pokemon})
