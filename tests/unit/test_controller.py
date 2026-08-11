"""Unit tests for the controller app — no switch, no network, no API keys.

Covers PLAN.md D: cookie allocator (N2/N3/N20), flow building incl.
mark_dscp output composition (N23), the agent-namespace delete guard (C4),
REST error mapping, and launcher preflight.
"""

import socket

import pytest

from netcopilot.controller import manage
from netcopilot.controller.app import (
    FULL_MASK,
    MAGIC,
    MAGIC_MASK,
    SESSION,
    SESSION_MASK,
    NetCopilotApp,
    is_agent_delete,
)


@pytest.fixture()
def app():
    return NetCopilotApp()


@pytest.fixture()
def client(app):
    app.flask_app.config.update(TESTING=True)
    return app.flask_app.test_client()



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
        assert cookie >> 48 == MAGIC
        # C14: `cookie & SESSION_MASK` cannot fail — SESSION_MASK contains
        # MAGIC_MASK, so the magic bits alone satisfy it. Check the field.
        assert (cookie >> 32) & 0xFFFF == SESSION

    def test_monotonic_within_session(self, app):
        first = app.alloc_cookie()
        second = app.alloc_cookie()
        assert second > first
        assert first >> 32 == second >> 32  # same magic|session namespace

    def test_masks_are_correct(self):
        assert FULL_MASK == 0xFFFFFFFFFFFFFFFF
        assert SESSION_MASK == 0xFFFFFFFF00000000
        assert MAGIC_MASK == 0xFFFF000000000000
        assert SESSION_MASK & MAGIC_MASK == MAGIC_MASK  # nested namespaces


class TestAgentNamespaceGuard:
    """C4: the delete guard is the invariant everything else rests on."""

    def test_full_mask_agent_cookie_allowed(self, app):
        assert is_agent_delete(app.alloc_cookie(), FULL_MASK)

    def test_session_mask_allowed(self, app):
        assert is_agent_delete(app.alloc_cookie(), SESSION_MASK)

    def test_all_agent_mask_allowed(self):
        assert is_agent_delete(MAGIC << 48, MAGIC_MASK)

    def test_mask_zero_refused(self, app):
        # mod_flow_entry's default: matches every cookie, wipes the baseline.
        assert not is_agent_delete(app.alloc_cookie(), 0)

    def test_baseline_cookie_refused(self):
        assert not is_agent_delete(0, FULL_MASK)

    def test_foreign_cookie_refused(self):
        assert not is_agent_delete((0xBEEF << 48) | 1, FULL_MASK)


class TestRestSurface:
    """No switch attached: schema errors must still be 400, not 404."""

    def test_health(self, client):
        body = client.get("/health").get_json()
        assert body == {"status": "ok", "switches": 0}

    def test_switches_empty(self, client):
        assert client.get("/switches").get_json() == {"dpids": []}

    def test_unknown_action_is_400(self, client):
        assert client.post("/flows", json={"action": "goto_table"}).status_code == 400

    def test_output_without_port_is_400(self, client):
        assert client.post("/flows", json={"action": "output"}).status_code == 400

    def test_bad_dscp_is_400(self, client):
        resp = client.post(
            "/flows", json={"action": "mark_dscp", "dscp": 99, "out_port": 1}
        )
        assert resp.status_code == 400

    def test_valid_payload_without_switch_is_404(self, client):
        resp = client.post("/flows", json={"action": "drop", "match": {}})
        assert resp.status_code == 404

    def test_delete_mask_zero_is_403(self, client):
        resp = client.delete("/flows", json={"cookie": 0, "cookie_mask": 0})
        assert resp.status_code == 403

    def test_delete_foreign_cookie_is_403(self, client):
        resp = client.delete("/flows", json={"cookie": 1, "cookie_mask": FULL_MASK})
        assert resp.status_code == 403

    def test_delete_agent_cookie_passes_guard(self, client, app):
        # Guard satisfied, no switch to talk to → 404, not 403.
        resp = client.delete(
            "/flows", json={"cookie": app.alloc_cookie(), "cookie_mask": FULL_MASK}
        )
        assert resp.status_code == 404



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
