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
"""

import logging
import os

from flask import Flask, jsonify, request

from os_ken.app.ofctl import api as ofctl_api  # noqa: F401  (N24: top-level)
from os_ken.base import app_manager
from os_ken.controller import controller as os_ken_controller
from os_ken.controller import ofp_event
from os_ken.controller.handler import MAIN_DISPATCHER, set_ev_cls
from os_ken.lib import hub
from os_ken.lib import ofctl_v1_3

LOG = logging.getLogger(__name__)

REST_PORT = int(os.environ.get("NETCOPILOT_REST_PORT", "8081"))
SESSION = int(os.environ.get("NETCOPILOT_SESSION", "1"))  # Phase 2: audit-tail seed (N28)

MAGIC = 0xA51D  # agent cookie namespace, bits 48-63
MAGIC_MASK = 0xFFFF000000000000
SESSION_MASK = 0xFFFFFFFF00000000
FULL_MASK = 0xFFFFFFFFFFFFFFFF

ACTIONS = {"drop", "output", "mark_dscp"}


class NetCopilotApp(app_manager.OSKenApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self._cookie_lock = hub.semaphore()
        self._op_counter = 0
        self.flask_app = Flask("netcopilot")
        self._register_routes()

    def start(self):
        super().start()
        # N15: never flask_app.run() inside the eventlet-patched process.
        hub.spawn(
            hub.eventlet.wsgi.server,
            hub.eventlet.listen(("0.0.0.0", REST_PORT)),
            self.flask_app,
        )
        # N13: return None — ofp_handler's spawned OpenFlowController is the
        # only long-lived thread handed to run_apps; keep this app threadless.
        return None

    # ------------------------------------------------------------------ #
    # OpenFlow events
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)
    def _on_state_change(self, ev):
        dp = ev.datapath
        if ev.state == os_ken_controller.MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            LOG.info("switch connected: dpid=%s", dp.id)
        elif ev.state == os_ken_controller.DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)
            LOG.info("switch disconnected: dpid=%s", dp.id)

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
        req = dp.ofproto_parser.OFPFlowStatsRequest(dp)
        try:
            reply = ofctl_api.send_msg(
                self,
                req,
                reply_cls=ofp_event.EventOFPFlowStatsReply,
                reply_multi=True,
            )
        except Exception as exc:  # timeout / ofctl service missing (N24)
            return self._error(f"stats request failed: {exc}", 500)
        out = []
        for stat in reply.body:
            for entry in stat:
                out.append(
                    {
                        "cookie": str(entry.cookie),
                        "priority": entry.priority,
                        "match": str(entry.match),
                        "instructions": str(entry.instructions),
                    }
                )
        return jsonify(out)

    def _flows_add(self):
        try:
            data = request.get_json(force=True)
            dp = self._single_dp(data.get("dpid"))
            cookie = int(data["cookie"]) if "cookie" in data else self.alloc_cookie()
            flow = self._build_flow(data, cookie)
            ofctl_v1_3.mod_flow_entry(dp, flow, dp.ofproto.OFPFC_ADD)
        except (ValueError, KeyError, TypeError) as exc:
            return self._error(str(exc))
        except RuntimeError as exc:
            return self._error(str(exc), 404)
        except Exception as exc:  # switch-level errors (incl. table-full) surface here
            LOG.exception("flow add failed")
            return self._error(f"flow add failed: {exc}", 500)
        return jsonify({"cookie": str(cookie)})

    def _flows_del(self):
        try:
            data = request.get_json(force=True)
            dp = self._single_dp(data.get("dpid"))
            cookie = int(data.get("cookie", 0))
            mask = int(data.get("cookie_mask", FULL_MASK))
            # N2: ALWAYS send the mask; mask 0 (mod_flow_entry default)
            # matches every cookie and would wipe the baseline.
            flow = {"cookie": cookie, "cookie_mask": mask, "match": {}}
            ofctl_v1_3.mod_flow_entry(dp, flow, dp.ofproto.OFPFC_DELETE)
        except (ValueError, TypeError) as exc:
            return self._error(str(exc))
        except RuntimeError as exc:
            return self._error(str(exc), 404)
        except Exception as exc:
            LOG.exception("flow delete failed")
            return self._error(f"flow delete failed: {exc}", 500)
        return jsonify({"deleted": True, "cookie": str(cookie), "cookie_mask": str(mask)})
