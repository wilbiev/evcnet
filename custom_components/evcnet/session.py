"""Minimal per-account cookie persistence for EVC-net."""

import hashlib
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api import EvcNetApiClient


def create_client(hass: HomeAssistant | None, data: dict[str, Any]):
    """Create an account-scoped client and store for its cookies."""
    assert hass is not None

    identity = f"{data['base_url'].rstrip('/')}\0{data['username']}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    store = Store(hass, 1, f"evcnet.session.{key}", private=True)

    client = EvcNetApiClient(
        data["base_url"],
        data["username"],
        data["password"],
        async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar()),
    )

    async def save(cookies):
        await store.async_save({"cookies": cookies})

    return client, store, save
