from __future__ import annotations

from importlib import import_module
from typing import Any


def _bot_module():
    return import_module(".bot", __name__)


def _execution_module():
    return import_module(".execution", __name__)


async def get_bot_status(*args: Any, **kwargs: Any):
    return await _bot_module().get_bot_status(*args, **kwargs)


def get_bot_status_snapshot(*args: Any, **kwargs: Any):
    return _bot_module().get_bot_status_snapshot(*args, **kwargs)


def start_bot(*args: Any, **kwargs: Any):
    return _bot_module().start_bot(*args, **kwargs)


def stop_bot(*args: Any, **kwargs: Any):
    return _bot_module().stop_bot(*args, **kwargs)


def flatten_all_positions(*args: Any, **kwargs: Any):
    return _bot_module().flatten_all_positions(*args, **kwargs)


async def list_recent_actions(*args: Any, **kwargs: Any):
    return await _bot_module().list_recent_actions(*args, **kwargs)


async def clear_recent_actions(*args: Any, **kwargs: Any):
    return await _bot_module().clear_recent_actions(*args, **kwargs)


def current_crypto_config(*args: Any, **kwargs: Any):
    return _bot_module().current_crypto_config(*args, **kwargs)


def save_crypto_config(*args: Any, **kwargs: Any):
    return _bot_module().save_crypto_config(*args, **kwargs)


def list_assets(*args: Any, **kwargs: Any):
    return _bot_module().list_assets(*args, **kwargs)


def get_positions(*args: Any, **kwargs: Any):
    return _bot_module().get_positions(*args, **kwargs)


def get_account(*args: Any, **kwargs: Any):
    return _bot_module().get_account(*args, **kwargs)


def place_crypto_order(*args: Any, **kwargs: Any):
    return _execution_module().place_crypto_order(*args, **kwargs)


__all__ = [
    "clear_recent_actions",
    "current_crypto_config",
    "flatten_all_positions",
    "get_account",
    "get_bot_status",
    "get_bot_status_snapshot",
    "get_positions",
    "list_assets",
    "list_recent_actions",
    "place_crypto_order",
    "save_crypto_config",
    "start_bot",
    "stop_bot",
]
