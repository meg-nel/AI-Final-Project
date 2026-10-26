"""Hearth & Horizon backend. Importing the package loads .env first, so every module sees the settings."""

from . import config  # noqa: F401  (loads <repo>/.env, else <repo>/../.env)
