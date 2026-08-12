"""NetCopilot campus topology (issue #1): 2 core + 2 access switches, 4 hosts.

Usage (inside the lab container):
    mn --custom lab/topo.py --topo netcopilot \
       --controller=remote,ip=127.0.0.1,port=6653 \
       --switch ovs,protocols=OpenFlow13

Layout:
    c1 ── c2        (core interconnect)
    │╲    ╱│
    a1 ── a2        (full mesh: every access switch connects to both cores)
    │     │
   web   db
   dmz  client      (web+dmz on a1; db+client on a2)

Host names/IPs/MACs come from lab.hosts — the single source of truth.
"""

from mininet.topo import Topo

from lab.hosts import (
    ACCESS_SWITCHES,
    CORE_SWITCHES,
    HOST_MACS,
    HOSTS,
)


class NetCopilotTopo(Topo):
    """Campus topology: c1/c2 core, a1/a2 access, four role-named hosts."""

    def build(self):
        cores = [self.addSwitch(f"c{index}") for index in range(1, CORE_SWITCHES + 1)]
        access = [
            self.addSwitch(f"a{index}") for index in range(1, ACCESS_SWITCHES + 1)
        ]

        # Core interconnect + full mesh between access and core switches.
        self.addLink(cores[0], cores[1])
        for core in cores:
            for acc in access:
                self.addLink(core, acc)

        # Host placement: web+dmz on a1, db+client on a2.
        placement = {
            "web": access[0],
            "dmz": access[0],
            "db": access[1],
            "client": access[1],
        }
        for name, acc in placement.items():
            host = self.addHost(name, ip=f"{HOSTS[name]}/24", mac=HOST_MACS[name])
            self.addLink(acc, host)


# Mininet resolves `--topo netcopilot` to the class named NetCopilotTopo.
topos = {"netcopilot": NetCopilotTopo}
