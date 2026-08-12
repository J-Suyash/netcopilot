"""NetCopilot host vocabulary — single source of truth for names, IPs, MACs.

The LLM agent resolves intents against these names (PLAN.md C.1.1, issue #1).
This module deliberately does NOT import Mininet so it can be imported by the
Lab Runner, the controller, and unit tests without root or a lab container.

Stability contract: changing a name or IP breaks the LLM vocabulary and any
installed flows / audit records that reference them. Change only with intent.
The mappings are immutable (MappingProxyType): silent mutation is a TypeError.
"""

from collections.abc import Mapping
from ipaddress import IPv4Network, ip_address
from types import MappingProxyType

SUBNET = "10.0.0.0/24"
_NETWORK = IPv4Network(SUBNET)

# name -> IP.
HOSTS: Mapping[str, str] = MappingProxyType(
    {
        "web": "10.0.0.10",
        "db": "10.0.0.20",
        "client": "10.0.0.5",
        "dmz": "10.0.0.30",
    }
)

# Locally-administered unicast MACs derived from the IP's last octet, so a
# host's MAC is stable under host-set changes: inserting a name that sorts
# earlier must not renumber existing hosts (that would silently invalidate
# controller host tracking and prior audit rows).
HOST_MACS: Mapping[str, str] = MappingProxyType(
    {
        name: f"02:00:00:00:00:{int(ip.rsplit('.', 1)[1]):02x}"
        for name, ip in HOSTS.items()
    }
)

# The strict allowlist: exactly the four role names and their four IPs.
_KNOWN_IPS: frozenset[str] = frozenset(HOSTS.values())

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
    return set(_KNOWN_IPS)


def is_known_host(name_or_ip: str) -> bool:
    """STRICT allowlist: only the four role names or their four IPs.

    Anything else — including other addresses in the lab subnet — is NOT
    known. Guardrails and the Runner must use this (PLAN B.3.2: allowlists
    from resolved topology, never from strings the model produced).
    """
    return name_or_ip in HOSTS or name_or_ip in _KNOWN_IPS


def is_lab_address(value: str) -> bool:
    """Loose check: any address inside the lab subnet (not necessarily a host).

    Use only where subnet membership itself is the requirement; the Runner's
    input validation is the strict `is_known_host`.
    """
    try:
        return ip_address(value) in _NETWORK
    except ValueError:
        return False


def is_valid_ip(value: str) -> bool:
    """Parse check only: any syntactically valid IP address.

    NOT sufficient for the Runner — input validation there must use
    `is_known_host` (the allowlist is the actual control, PLAN N21).
    """
    try:
        ip_address(value)
        return True
    except ValueError:
        return False
