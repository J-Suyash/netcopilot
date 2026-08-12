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

    def test_macs_unique_and_locally_administered(self):
        macs = list(HOST_MACS.values())
        assert len(macs) == len(set(macs)), "duplicate MACs"
        for mac in macs:
            assert mac.startswith("02:"), f"{mac} not locally administered"


class TestHostIp:
    def test_resolves_known_name(self):
        assert host_ip("web") == HOSTS["web"]

    def test_unknown_name_raises_with_helpful_message(self):
        with pytest.raises(KeyError, match="unknown host 'router'"):
            host_ip("router")

    def test_host_ips_matches_mapping(self):
        assert host_ips() == set(HOSTS.values())


class TestValidation:
    @pytest.mark.parametrize("value", ["web", "db", "client", "dmz"])
    def test_names_are_known(self, value):
        assert is_known_host(value)

    @pytest.mark.parametrize("value", ["10.0.0.5", "10.0.0.20", "10.0.0.99"])
    def test_subnet_ips_are_known(self, value):
        assert is_known_host(value)

    @pytest.mark.parametrize("value", ["8.8.8.8", "not-an-ip", "10.0.1.1", ""])
    def test_foreign_values_rejected(self, value):
        assert not is_known_host(value)

    def test_is_valid_ip(self):
        assert is_valid_ip("10.0.0.5")
        assert not is_valid_ip("10.0.0.5; rm -rf /")
        assert not is_valid_ip("web")
