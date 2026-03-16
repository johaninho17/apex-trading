"""Shared Ollama host resolution helpers.

Keeps all Ollama callers consistent across Windows and WSL runtimes.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from urllib.parse import urlparse

_DEFAULT_OLLAMA = "http://localhost:11434"


def _normalize_host(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return _DEFAULT_OLLAMA
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    for probe in ("/proc/version", "/proc/sys/kernel/osrelease"):
        try:
            text = open(probe, "r", encoding="utf-8").read().lower()
            if "microsoft" in text or "wsl" in text:
                return True
        except Exception:
            continue
    return False


def _wsl_host_candidates() -> list[str]:
    candidates: list[str] = []
    for env_key in ("OLLAMA_WINDOWS_HOST", "WSL_HOST_IP"):
        raw = str(os.environ.get(env_key, "") or "").strip()
        if raw:
            candidates.append(_normalize_host(f"http://{raw}:11434" if "://" not in raw and ":" not in raw else raw))
    candidates.append("http://host.docker.internal:11434")
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split()
                if len(parts) == 2 and parts[0] == "nameserver":
                    candidates.append(f"http://{parts[1]}:11434")
                    break
    except Exception:
        pass
    return candidates


def _candidate_hosts() -> list[str]:
    hosts: list[str] = []
    env_host = str(os.environ.get("OLLAMA_HOST", "") or "").strip()
    if env_host:
        hosts.append(_normalize_host(env_host))
    hosts.append(_DEFAULT_OLLAMA)
    if _is_wsl():
        hosts.extend(_wsl_host_candidates())
    deduped: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        normalized = _normalize_host(host)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _reachable(url: str, timeout_sec: float = 0.35) -> bool:
    parsed = urlparse(_normalize_host(url))
    host = parsed.hostname
    port = parsed.port or 11434
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def get_ollama_host() -> str:
    for candidate in _candidate_hosts():
        if _reachable(candidate):
            return candidate
    env_host = str(os.environ.get("OLLAMA_HOST", "") or "").strip()
    return _normalize_host(env_host or _DEFAULT_OLLAMA)


def set_ollama_env() -> str:
    host = get_ollama_host()
    os.environ["OLLAMA_HOST"] = host
    return host


def ollama_url(path: str) -> str:
    suffix = "/" + str(path or "").lstrip("/")
    return f"{get_ollama_host()}{suffix}"


__all__ = ["get_ollama_host", "set_ollama_env", "ollama_url"]
