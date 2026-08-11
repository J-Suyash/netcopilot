"""Unit tests for the controller app — no switch, no network, no API keys.

Covers PLAN.md D: cookie allocator (N2/N3/N20), flow building incl.
mark_dscp output composition (N23), validation, and launcher preflight.
"""

import socket

import pytest

from netcopilot.controller import manage
from netcopilot.controller.app import (
    FULL_MASK,
    MAGIC,
    MAGIC_MASK,
    SESSION_MASK,
    NetCopilotApp,
)


@pytest.fixture()
def app():
    return NetCopilotApp()


class TestBuildFlow:
    def test_drop_has_no_actions(self, app):
        flow = app._build_flow(
            {"match": {"eth_type": 0x0800}, "action": "drop"}, cookie=1
        )
        assert flow["actions"] == []
        assert flow["cookie"] == 1

    def test_output_requires_out_port(self, app):
        with pytest.raises(KeyError):
            app._build_flow({"action": "output"}, cookie=1)

    def test_mark_dscp_compiles_to_setfield_plus_output(self, app):
        # N23: SetField alone blackholes — Output must follow.
        flow = app._build_flow(
            {
                "match": {"eth_type": 0x0800},
                "action": "mark_dscp",
                "dscp": 46,
                "out_port": 3,
            },
            cookie=2,
        )
        assert flow["actions"] == [
            {"type": "SET_FIELD", "field": "ip_dscp", "value": 46},
            {"type": "OUTPUT", "port": 3},
        ]

    @pytest.mark.parametrize("dscp", [-1, 64, 100])
    def test_mark_dscp_rejects_out_of_range(self, app, dscp):
        with pytest.raises(ValueError):
            app._build_flow({"action": "mark_dscp", "dscp": dscp, "out_port": 1}, cookie=3)

    def test_unknown_action_rejected(self, app):
        with pytest.raises(ValueError):
            app._build_flow({"action": "goto_table"}, cookie=4)


class TestCookieAllocation:
    def test_magic_and_session_bits(self, app):
        cookie = app.alloc_cookie()
        assert cookie & MAGIC_MASK == MAGIC << 48
        assert cookie & SESSION_MASK  # session bits set

    def test_monotonic_within_session(self, app):
        first = app.alloc_cookie()
        second = app.alloc_cookie()
        assert second > first

    def test_masks_are_correct(self):
        assert FULL_MASK == 0xFFFFFFFFFFFFFFFF
        assert SESSION_MASK == 0xFFFFFFFF00000000
        assert MAGIC_MASK == 0xFFFF000000000000
        assert SESSION_MASK & MAGIC_MASK == MAGIC_MASK  # nested namespaces


class TestLauncherPreflight:
    def test_rejects_bound_port(self):
        with socket.socket() as s:
            s.bind(("0.0.0.0", 0))
            s.listen(1)
            port = s.getsockname()[1]
            with pytest.raises(SystemExit):
                manage.preflight_port(port)

    def test_accepts_free_port(self):
        with socket.socket() as s:
            s.bind(("0.0.0.0", 0))
            port = s.getsockname()[1]
            s.close()
        manage.preflight_port(port)  # must not raise
