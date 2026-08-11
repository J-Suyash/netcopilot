"""NetCopilot controller launcher (Gate-1 verified recipe, PLAN.md B.3.7).

Boots the os-ken app stack for the NetCopilot SDN controller.
Run inside the lab container:  python -m netcopilot.controller.manage

Ordering is load-bearing (review N13/N14):
  1. The opt-registering modules MUST be imported BEFORE cfg.CONF([...])
     (ofp-* opts live in os_ken.controller.controller, --observe-links in
     os_ken.topology.switches — NOT in os_ken.flags).
  2. 'os_ken.controller.ofp_handler' MUST be in the app list: its start()
     returns the only long-lived thread; without it run_apps joins an empty
     set and the process exits silently with rc 0.

Hub note (empirical): os-ken defaults to the NATIVE hub (OSKEN_HUB_TYPE
unset). hub.patch(thread=False) below is harmless under native (no-op
monkey patch) and only matters if eventlet mode is enabled later.
"""

import os
import socket
import sys

from os_ken.lib import hub

hub.patch(thread=False)  # no-op under native hub; required if OSKEN_HUB_TYPE=eventlet

from os_ken import cfg, flags  # noqa: F401
from os_ken.base.app_manager import AppManager
from os_ken.controller import controller  # noqa: F401  (registers ofp-* opts)
from os_ken.topology import switches  # noqa: F401  (registers --observe-links)

from netcopilot.controller.app import REST_PORT

CONF = cfg.CONF

DEFAULT_OF_PORT = int(os.environ.get("NETCOPILOT_OF_PORT", "6653"))


def preflight_port(port: int, what: str = "port") -> None:
    """Fail loudly if a port we need is already bound (stale process)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", port))
    except OSError as exc:
        sys.exit(f"FATAL: {what} port {port} already in use (stale controller?): {exc}")
    finally:
        probe.close()


def main(argv=None) -> None:
    # C9: parse here, not in __main__ — main() reads CONF, so any other entry
    # point (integration test, python -c) would otherwise run with CONF
    # unparsed: --observe-links unset means os_ken.topology.api returns empty
    # switches/links/hosts, silently. Same failure class as N4.
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["--ofp-tcp-listen-port", str(DEFAULT_OF_PORT)]
    if "--observe-links" not in args:
        args.append("--observe-links")  # topology/host APIs are empty without it
    CONF(args, project="netcopilot")

    of_port = int(CONF.ofp_tcp_listen_port or DEFAULT_OF_PORT)
    preflight_port(of_port, "OpenFlow")
    preflight_port(REST_PORT, "REST")  # C10: E.8 lists both; guard both
    AppManager.run_apps(
        [
            "os_ken.controller.ofp_handler",  # REQUIRED: opens the OF listener (N13)
            "os_ken.topology.switches",  # topology/host tracking (+ --observe-links)
            "os_ken.app.ofctl.service",  # required by os_ken.app.ofctl.api (N24)
            "netcopilot.controller.app",
        ]
    )


if __name__ == "__main__":
    main()
