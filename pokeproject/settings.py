"""Django settings for pokeproject.

Configuration comes from environment variables. See .env.example for the full
list; docker-compose.yml supplies them in a container.

https://docs.djangoproject.com/en/6.0/ref/settings/
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# Defaults to False: a deploy that forgets to set this should fail closed, not
# serve tracebacks and settings to the world.
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DEBUG is off. "
            "Generate one with: python -c \"from django.core.management.utils import "
            'get_random_secret_key as k; print(k())"'
        )
    # Development only, and only ever reached with DEBUG on. Deliberately not a
    # real key committed to the repo, which is what used to live here.
    SECRET_KEY = "django-insecure-dev-only-key-do-not-use-in-production"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "pokedex",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pokeproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # No explicit DIRS needed: APP_DIRS finds pokedex/templates/.
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "pokeproject.wsgi.application"
ASGI_APPLICATION = "pokeproject.asgi.application"


# Database

POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
if not POSTGRES_PASSWORD and not DEBUG:
    raise ImproperlyConfigured("POSTGRES_PASSWORD must be set when DEBUG is off.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "pokeproject_db"),
        "USER": os.environ.get("POSTGRES_USER", "admin"),
        "PASSWORD": POSTGRES_PASSWORD or "admin",
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Reuse connections instead of a fresh TCP handshake per request.
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}


# Caching
#
# This is load-bearing, not an optimisation: PokeAPI move lookups are cached
# here, and without it every battle re-fetches hundreds of immutable records.
# LocMemCache is per-process, so under gunicorn each worker keeps its own copy;
# DatabaseCache is shared. Create its table with:
#   python manage.py createcachetable
CACHE_BACKEND = os.environ.get("DJANGO_CACHE_BACKEND", "locmem")

if CACHE_BACKEND == "database":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "pokedex_cache",
        }
    }
elif CACHE_BACKEND == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pokeproject",
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Compressing and content-hashing static files is the entire point of
        # having WhiteNoise installed -- but the manifest backend requires
        # `collectstatic` to have run, so it is off in development.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Security
#
# HSTS, secure cookies and the HTTPS redirect all assume TLS is actually in
# front of the app. Turning them on when it is not does not merely fail to
# help -- secure cookies are never sent over plain HTTP, so every POST dies
# with a CSRF failure. One flag governs the whole group.
#
# Default on whenever DEBUG is off: a real deployment must opt *out*, not in.
# Set DJANGO_BEHIND_TLS=0 only for a plain-HTTP local `docker compose up`.
BEHIND_TLS = env_bool("DJANGO_BEHIND_TLS", not DEBUG)

if BEHIND_TLS:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Trust the reverse proxy's forwarded scheme, otherwise SECURE_SSL_REDIRECT
    # loops forever behind a TLS-terminating load balancer.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True


# Logging
#
# Replaces the print() calls the views used to use.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "pokedex": {
            "handlers": ["console"],
            "level": os.environ.get("POKEDEX_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
