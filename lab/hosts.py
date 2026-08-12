"""NetCopilot host vocabulary — single source of truth for names, IPs, MACs.

The LLM agent resolves intents against these names (PLAN.md C.1.1, issue #1).
This module deliberately does NOT import Mininet so it can be imported by the
Lab Runner, the controller, and unit tests without root or a lab container.

Stability contract: changing a name or IP breaks the LLM vocabulary and any
installed flows / audit records that reference them. Change only with intent.
"""

from ipaddress import IPv4Network, ip_address

SUBNET = "10.0.0.0/24"
_NETWORK = IPv4Network(SUBNET)

# name -> IP. Ordering is stable (sorted) for deterministic MAC assignment.
HOSTS: dict[str, str] = {
    "web": "10.0.0.10",
    "db": "10.0.0.20",
    "client": "10.0.0.5",
    "dmz": "10.0.0.30",
}

# Locally-administered unicast MACs, derived deterministically from host order.
# Stable MACs keep the controller's host tracking deterministic across runs.
HOST_MACS: dict[str, str] = {
    name: f"02:00:00:00:00:{index:02x}"
    for index, name in enumerate(sorted(HOSTS), start=1)
}

# Switch roles used by lab/topo.py (2 core + 2 access).
CORE_SWITCHES = 2
ACCESS_SWITCHES = 2


def host_ip(name: str) -> str:
    """Resolve a host role name to its IP. Raises KeyError with a helpful message."""
    try:
        return HOSTS[name]
    except KeyError:
        raise KeyError(f"unknown host {name!r}; known hosts: {sorted(HOSTS)}") from None


def host_ips() -> set[str]:
    """All host IPs — the allowlist the Runner/guardrails validate against."""
    return set(HOSTS.values())


def is_known_host(name_or_ip: str) -> bool:
    """True if the string is a host role name or one of the host IPs."""
    if name_or_ip in HOSTS:
        return True
    try:
        return ip_address(name_or_ip) in _NETWORK
    except ValueError:
        return False


def is_valid_ip(value: str) -> bool:
    """True if the string parses as any IP address (allowlist-free check)."""
    try:
        ip_address(value)
        return True
    except ValueError:
        return False
