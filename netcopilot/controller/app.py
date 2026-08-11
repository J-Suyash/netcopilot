"""NetCopilot os-ken controller app — Gate-1 scope.

Serves a minimal REST surface (health, switches, flow CRUD) with
cookie-based flow identity (PLAN.md B.3.5). Phase 1 extends this with
topology/host enrichment (os_ken.topology.api + ARP-snoop IP learning),
baseline L2 flows (cookie 0, priority <100), and structured error
surfacing (flow-table-full etc.).

Import discipline (N24/N29): os_ken.app.ofctl.api MUST be imported at
module top level — its require_app('os_ken.app.ofctl.service') registers
on this module's frame, and the service is also named explicitly in
manage.py's run_apps list. No direct `import eventlet` here: everything
goes through os_ken.lib.hub so CI unit tests never get monkey-patched.

Dispatcher constants come from os_ken.controller.handler, NOT from
os_ken.controller.controller — that module only re-exports
HANDSHAKE_DISPATCHER and DEAD_DISPATCHER, so `controller.MAIN_DISPATCHER`
raises AttributeError inside the event handler and the datapath map stays
empty forever (review C1).
"""

import logging
import os

from flask import Flask, jsonify, request
from os_ken.app.ofctl import api as ofctl_api
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from os_ken.lib import hub, ofctl_v1_3
from werkzeug.serving import make_server

LOG = logging.getLogger(__name__)

REST_PORT = int(os.environ.get("NETCOPILOT_REST_PORT", "8081"))
SESSION = int(os.environ.get("NETCOPILOT_SESSION", "1"))  # Phase 2: audit-tail seed (N28)

MAGIC = 0xA51D  # agent cookie namespace, bits 48-63
MAGIC_MASK = 0xFFFF000000000000
SESSION_MASK = 0xFFFFFFFF00000000
FULL_MASK = 0xFFFFFFFFFFFFFFFF

ACTIONS = {"drop", "output", "mark_dscp"}


def is_agent_delete(cookie: int, mask: int) -> bool:
    """True iff a delete request is confined to the agent cookie namespace.

    The invariant PLAN B.3.5 promises, enforced instead of documented (C4):
    the mask must cover the magic bits and the cookie must carry them. A
    mask of 0 — mod_flow_entry's default — matches *every* cookie and would
    take the cookie-0 baseline flows down with it.
    """
    return (mask & MAGIC_MASK) == MAGIC_MASK and (cookie >> 48) == MAGIC


class NetCopilotApp(app_manager.OSKenApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self._cookie_lock = hub.Semaphore()  # class, not function (native hub)
        self._write_lock = hub.Semaphore()  # serialises flow-mod + barrier + error read
        self._op_counter = 0
        self._last_error = None
        self._srv = None
        self.flask_app = Flask("netcopilot")
        self._register_routes()

    def start(self):
        super().start()
        # N15 (revised after empirical check): os-ken defaults to the NATIVE hub
        # (OSKEN_HUB_TYPE unset) — hub.eventlet does NOT exist under it, so the
        # original eventlet.wsgi.server recipe would AttributeError at runtime.
        # Serve Flask via werkzeug in a spawned native thread instead: no eventlet,
        # no monkey-patching, safe under either hub type. flask_app.run() would
        # block this thread forever — make_server().serve_forever() is the pattern.
        self._srv = make_server("0.0.0.0", REST_PORT, self.flask_app, threaded=True)
        hub.spawn(self._srv.serve_forever)
        # N13: return None — ofp_handler's spawned OpenFlowController is the
        # only long-lived thread handed to run_apps; keep this app threadless.

    def stop(self):
        # C11: release REST_PORT on shutdown, otherwise the next boot's
        # preflight trips over our own leftover listener.
        if self._srv is not None:
            self._srv.shutdown()
        super().stop()

    # ------------------------------------------------------------------ #
    # OpenFlow events
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _on_state_change(self, ev):
        # C5: both dispatchers must be registered (os-ken's own dpset.py does
        # the same) — with MAIN only, the disconnect branch never fires and a
        # dead Datapath object keeps serving writes that go nowhere.
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            LOG.info("switch connected: dpid=%s", dp.id)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)
            LOG.info("switch disconnected: dpid=%s", dp.id)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def _on_error(self, ev):
        # C6: flow-mods are fire-and-forget; the switch reports rejection
        # (bad match, OFPFMFC_TABLE_FULL, ...) asynchronously right here.
        msg = ev.msg
        self._last_error = f"OFPErrorMsg type={msg.type} code={msg.code}"
        LOG.error("switch error: %s", self._last_error)

    # ------------------------------------------------------------------ #
    # Cookie allocation (N2/N3/N20/N28)
    # ------------------------------------------------------------------ #
    def alloc_cookie(self) -> int:
        with self._cookie_lock:
            self._op_counter += 1
            return (MAGIC << 48) | (SESSION << 32) | self._op_counter

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _single_dp(self, dpid=None):
        if dpid is not None:
            dp = self.datapaths.get(int(dpid))
            if dp is None:
                raise RuntimeError(f"unknown dpid {dpid}")
            return dp
        if len(self.datapaths) != 1:
            raise RuntimeError(
                f"expected exactly 1 switch, got {len(self.datapaths)}; pass dpid"
            )
        return next(iter(self.datapaths.values()))

    def _build_flow(self, data: dict, cookie: int) -> dict:
        match = data.get("match") or {}
        action = data.get("action")
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {sorted(ACTIONS)}")
        flow = {
            "cookie": cookie,
            "priority": int(data.get("priority", 150)),
            "match": match,
        }
        if action == "drop":
            flow["actions"] = []
        elif action == "output":
            flow["actions"] = [{"type": "OUTPUT", "port": int(data["out_port"])}]
        else:  # mark_dscp — N23: SetField alone blackholes; always Output after.
            dscp = int(data.get("dscp", -1))
            if not 0 <= dscp <= 63:
                raise ValueError("dscp must be int 0-63")
            flow["actions"] = [
                {"type": "SET_FIELD", "field": "ip_dscp", "value": dscp},
                {"type": "OUTPUT", "port": int(data["out_port"])},
            ]
        return flow

    @staticmethod
    def _error(message: str, status: int = 400):
        return jsonify({"error": message}), status

    # ------------------------------------------------------------------ #
    # REST routes
    # ------------------------------------------------------------------ #
    def _register_routes(self):
        a = self.flask_app
        a.add_url_rule("/health", "health", self._health)
        a.add_url_rule("/switches", "switches", self._switches)
        a.add_url_rule("/flows", "flows", self._flows, methods=["GET"])
        a.add_url_rule("/flows", "flows_add", self._flows_add, methods=["POST"])
        a.add_url_rule("/flows", "flows_del", self._flows_del, methods=["DELETE"])

    def _health(self):
        return jsonify({"status": "ok", "switches": len(self.datapaths)})

    def _switches(self):
        return jsonify({"dpids": sorted(self.datapaths.keys())})

    def _flows(self):
        try:
            dp = self._single_dp(request.args.get("dpid"))
        except RuntimeError as exc:
            return self._error(str(exc))
        parser = dp.ofproto_parser
        req = parser.OFPFlowStatsRequest(dp)
        try:
            # C2: reply_cls is an OpenFlow *message* class — the ofctl service
            # runs it through ofp_msg_to_ev_cls(), so an Event* class KeyErrors.
            # C3: reply_multi=True returns a LIST of replies, not one message.
            replies = ofctl_api.send_msg(
                self,
                req,
                reply_cls=parser.OFPFlowStatsReply,
                reply_multi=True,
            )
        except Exception as exc:  # noqa: BLE001 — HTTP boundary: surface any
            # ofctl failure (timeout, service missing per N24) as a 500.
            return self._error(f"stats request failed: {exc}", 500)
        out = []
        for msg in replies:
            for entry in msg.body:
                out.append(
                    {
                        "cookie": str(entry.cookie),
                        "priority": entry.priority,
                        "match": ofctl_v1_3.match_to_str(entry.match),
                        "actions": ofctl_v1_3.actions_to_str(entry.instructions),
                        "packet_count": entry.packet_count,
                        "byte_count": entry.byte_count,
                    }
                )
        return jsonify(out)

    def _flows_add(self):
        # Validate before touching the switch: a malformed payload is a 400
        # whether or not a switch happens to be connected.
        try:
            data = request.get_json(force=True)
            cookie = int(data["cookie"]) if "cookie" in data else self.alloc_cookie()
            flow = self._build_flow(data, cookie)
        except (ValueError, KeyError, TypeError) as exc:
            return self._error(str(exc))
        try:
            dp = self._single_dp(data.get("dpid"))
        except RuntimeError as exc:
            return self._error(str(exc), 404)
        try:
            with self._write_lock:
                self._last_error = None
                ofctl_v1_3.mod_flow_entry(dp, flow, dp.ofproto.OFPFC_ADD)
                # C6: the barrier reply arrives only after the switch has fully
                # processed the flow-mod, so any error it raised is already in
                # _last_error. The write lock makes that attribution sound.
                ofctl_api.send_msg(
                    self,
                    dp.ofproto_parser.OFPBarrierRequest(dp),
                    reply_cls=dp.ofproto_parser.OFPBarrierReply,
                )
                switch_error = self._last_error
        except Exception as exc:
            LOG.exception("flow add failed")
            return self._error(f"flow add failed: {exc}", 500)
        if switch_error:
            return self._error(f"switch rejected flow: {switch_error}", 500)
        return jsonify({"cookie": str(cookie)})

    def _flows_del(self):
        try:
            data = request.get_json(force=True)
            cookie = int(data.get("cookie", 0))
            mask = int(data.get("cookie_mask", FULL_MASK))
        except (ValueError, TypeError) as exc:
            return self._error(str(exc))
        # C4: enforce the agent namespace here, at the boundary — this is the
        # one invariant everything else rests on, so it is code, not a comment.
        if not is_agent_delete(cookie, mask):
            return self._error(
                "refusing delete outside the agent cookie namespace "
                f"(cookie={cookie:#018x}, mask={mask:#018x})",
                403,
            )
        try:
            dp = self._single_dp(data.get("dpid"))
        except RuntimeError as exc:
            return self._error(str(exc), 404)
        try:
            flow = {"cookie": cookie, "cookie_mask": mask, "match": {}}
            ofctl_v1_3.mod_flow_entry(dp, flow, dp.ofproto.OFPFC_DELETE)
        except Exception as exc:
            LOG.exception("flow delete failed")
            return self._error(f"flow delete failed: {exc}", 500)
        return jsonify({"deleted": True, "cookie": str(cookie), "cookie_mask": str(mask)})

