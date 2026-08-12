"""Unit tests for the host vocabulary — no Mininet, no root, CI-safe.

PLAN.md D + issue #1: names/IPs/MACs are the LLM vocabulary and the Runner's
validation allowlist, so their consistency is tested without a lab.
"""

from ipaddress import IPv4Network, ip_address

import pytest

from lab.hosts import (
    HOST_MACS,
    HOSTS,
    SUBNET,
    host_ip,
    host_ips,
    is_known_host,
    is_lab_address,
    is_valid_ip,
)


class TestVocabulary:
    def test_four_role_hosts(self):
        assert set(HOSTS) == {"web", "db", "client", "dmz"}

    def test_ips_unique_and_in_subnet(self):
        network = IPv4Network(SUBNET)
        ips = list(HOSTS.values())
        assert len(ips) == len(set(ips)), "duplicate IPs"
        for ip in ips:
            assert ip_address(ip) in network, f"{ip} not in {SUBNET}"

    def test_expected_ip_mapping(self):
        assert HOSTS["client"] == "10.0.0.5"
        assert HOSTS["db"] == "10.0.0.20"

    def test_macs_derived_from_ip_octet_and_unique(self):
        macs = list(HOST_MACS.values())
        assert len(macs) == len(set(macs)), "duplicate MACs"
        for name, mac in HOST_MACS.items():
            octet = int(HOSTS[name].rsplit(".", 1)[1])
            assert mac == f"02:00:00:00:00:{octet:02x}", f"{name}: {mac}"

    def test_macs_locally_administered(self):
        for mac in HOST_MACS.values():
            assert mac.startswith("02:"), f"{mac} not locally administered"

    def test_vocabulary_is_immutable(self):
        with pytest.raises(TypeError):
            HOSTS["app"] = "10.0.0.99"
        with pytest.raises(TypeError):
            HOST_MACS["app"] = "02:00:00:00:00:99"


class TestHostIp:
    def test_resolves_known_name(self):
        assert host_ip("web") == HOSTS["web"]

    def test_unknown_name_raises_with_helpful_message(self):
        with pytest.raises(KeyError, match="unknown host 'router'"):
            host_ip("router")

    def test_host_ips_matches_mapping(self):
        assert host_ips() == set(HOSTS.values())


class TestStrictAllowlist:
    @pytest.mark.parametrize("value", ["web", "db", "client", "dmz"])
    def test_names_are_known(self, value):
        assert is_known_host(value)

    @pytest.mark.parametrize("value", ["10.0.0.5", "10.0.0.10", "10.0.0.20", "10.0.0.30"])
    def test_host_ips_are_known(self, value):
        assert is_known_host(value)

    @pytest.mark.parametrize(
        "value",
        [
            "10.0.0.99",  # in-subnet but NOT a host — must be rejected
            "8.8.8.8",
            "not-an-ip",
            "10.0.1.1",
            "10.0.0.5; rm -rf /",
            "",
        ],
    )
    def test_anything_else_rejected(self, value):
        assert not is_known_host(value), f"{value!r} must not pass the strict allowlist"


class TestLabAddress:
    @pytest.mark.parametrize("value", ["10.0.0.1", "10.0.0.99", "10.0.0.254"])
    def test_subnet_addresses_are_lab_addresses(self, value):
        assert is_lab_address(value)

    @pytest.mark.parametrize("value", ["8.8.8.8", "10.0.1.1", "web", ""])
    def test_foreign_values_rejected(self, value):
        assert not is_lab_address(value)


class TestValidIp:
    def test_is_valid_ip(self):
        assert is_valid_ip("10.0.0.5")
        assert is_valid_ip("8.8.8.8")
        assert not is_valid_ip("10.0.0.5; rm -rf /")
        assert not is_valid_ip("web")
