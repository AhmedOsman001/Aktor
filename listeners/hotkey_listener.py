import logging
from typing import Callable, Optional

import keyboard

logger = logging.getLogger(__name__)

_REGISTERED: dict[str, object] = {}
_BLOCKED: set[str] = set()


def register(hotkey: str, callback: Callable[[], None]) -> bool:
    normalized = _normalize(hotkey)

    if normalized in _BLOCKED:
        logger.warning("Hotkey '%s' is blocked/conflicted", hotkey)
        return False

    if normalized in _REGISTERED:
        logger.warning("Hotkey '%s' already registered, replacing", hotkey)
        unregister(hotkey)

    try:
        kb_hotkey = keyboard.add_hotkey(normalized, callback, suppress=False)
        _REGISTERED[normalized] = kb_hotkey
        logger.info("Registered hotkey: %s", normalized)
        return True
    except Exception:
        logger.exception("Failed to register hotkey: %s", normalized)
        return False


def unregister(hotkey: str) -> None:
    normalized = _normalize(hotkey)
    if normalized in _REGISTERED:
        try:
            keyboard.remove_hotkey(_REGISTERED[normalized])
        except Exception:
            pass
        del _REGISTERED[normalized]
        logger.info("Unregistered hotkey: %s", normalized)


def unregister_all() -> None:
    for hotkey in list(_REGISTERED.keys()):
        try:
            keyboard.remove_hotkey(_REGISTERED[hotkey])
        except Exception:
            pass
    _REGISTERED.clear()
    _BLOCKED.clear()
    logger.info("All hotkeys unregistered")


def is_registered(hotkey: str) -> bool:
    return _normalize(hotkey) in _REGISTERED


def get_registered() -> list[str]:
    return list(_REGISTERED.keys())


def _normalize(hotkey: str) -> str:
    parts = hotkey.lower().replace(" ", "").split("+")
    order = {"alt": 0, "altgr": 0, "ctrl": 1, "shift": 2, "win": 3}
    mods = sorted([p for p in parts if p in order], key=lambda x: order.get(x, 99))
    keys = [p for p in parts if p not in order]
    return "+".join(mods + keys)
