"""NetCopilot campus topology (issue #1): 2 core + 2 access switches, 4 hosts.

Usage (inside the lab container):
    PYTHONPATH=. mn --custom lab/topo.py --topo netcopilot \
       --controller=remote,ip=127.0.0.1,port=6653 \
       --switch ovs,protocols=OpenFlow13

PYTHONPATH is required: `mn` is a script, so sys.path[0] is /usr/bin and the
cwd is never added — without it, `import lab.hosts` fails with
ModuleNotFoundError. scripts/run_lab.sh (issue #6) will own this.

IMPORTANT — keep all imports inside build(): `mn` execs the --custom file and
pushes every module-level name into its own globals, so a module-level
`from lab.hosts import HOSTS` silently replaces mn's host-type registry and
bring-up dies with 'error: proc is unknown'.

Layout (10 links):
    c1 ── c2        core interconnect
    │╲    ╱│
    a1 ── a2        full mesh: every access switch to both cores, plus the
    │     │         a1-a2 link — the redundant route the failure-diagnosis
   web   db         scenarios (fail_link/heal_link) actually diagnose
   dmz  client      web+dmz on a1; db+client on a2
"""

from mininet.topo import Topo


class NetCopilotTopo(Topo):
    """Campus topology: c1/c2 core, a1/a2 access, four role-named hosts."""

    def build(self):
        # Local import: module-level names in a --custom file collide with
        # mn's globals (its HOSTS registry in particular). See module docstring.
        from lab.hosts import (
            ACCESS_SWITCHES,
            CORE_SWITCHES,
            HOST_MACS,
            HOSTS,
        )

        cores = [self.addSwitch(f"c{index}") for index in range(1, CORE_SWITCHES + 1)]
        access = [
            self.addSwitch(f"a{index}") for index in range(1, ACCESS_SWITCHES + 1)
        ]

        # Core interconnect + every access switch meshed to both cores.
        self.addLink(cores[0], cores[1])
        for core in cores:
            for acc in access:
                self.addLink(core, acc)
        # Redundant a1-a2 path (deliberate: failure-diagnosis scenarios).
        self.addLink(access[0], access[1])

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


# Mininet resolves `--topo netcopilot` via this registry (lowercase is the
# sanctioned pattern: mn merges it into its own TOPOS).
topos = {"netcopilot": NetCopilotTopo}
