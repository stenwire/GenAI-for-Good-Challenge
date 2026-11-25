"""Competition limits (brief §4.5). Config may tighten these, never loosen them."""

import ipaddress
from urllib.parse import urlsplit

MAX_TOTAL_PARAMS = 3_000_000_000
MAX_CALLS_PER_QUESTION = 15
MAX_GENERATED_TOKENS_PER_QUESTION = 12_000


def is_local_url(url: str) -> bool:
    """Inference may only reach a model server on this machine (no network, brief §4.5)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False
