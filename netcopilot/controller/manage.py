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

import socket
import sys

from os_ken.lib import hub

hub.patch(thread=False)  # no-op under native hub; required if OSKEN_HUB_TYPE=eventlet

from os_ken import cfg, flags  # noqa: F401
from os_ken.base.app_manager import AppManager
from os_ken.controller import controller  # noqa: F401  (registers ofp-* opts)
from os_ken.topology import switches  # noqa: F401  (registers --observe-links)

CONF = cfg.CONF


def preflight_port(port: int) -> None:
    """Fail loudly if the OpenFlow port is already bound (stale process)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", port))
    except OSError as exc:
        sys.exit(f"FATAL: port {port} already in use (stale controller?): {exc}")
    finally:
        probe.close()


def main() -> None:
    of_port = int(CONF.ofp_tcp_listen_port or 6653)
    preflight_port(of_port)
    AppManager.run_apps(
        [
            "os_ken.controller.ofp_handler",  # REQUIRED: opens the OF listener (N13)
            "os_ken.topology.switches",  # topology/host tracking (+ --observe-links)
            "os_ken.app.ofctl.service",  # required by os_ken.app.ofctl.api (N24)
            "netcopilot.controller.app",
        ]
    )


if __name__ == "__main__":
    # import-before-parse (N14) — the modules above registered these opts.
    CONF(
        ["--observe-links", "--ofp-tcp-listen-port", "6653"],
        project="netcopilot",
    )
    main()
