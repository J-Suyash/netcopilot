"""Integration test for the campus topology — requires Mininet (lab container).

Importorskip keeps CI (no Mininet) collection-safe; the `integration` marker
keeps the test out of default CI runs. Run inside the lab container:
    pytest tests/integration -m integration
"""

import pytest

pytest.importorskip("mininet")  # must run before importing lab.topo

from lab.hosts import HOSTS
from lab.topo import NetCopilotTopo

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def topo():
    return NetCopilotTopo()


class TestTopologyStructure:
    def test_switch_roles(self, topo):
        switches = sorted(topo.switches())
        assert [s for s in switches if s.startswith("c")] == ["c1", "c2"]
        assert [s for s in switches if s.startswith("a")] == ["a1", "a2"]

    def test_all_four_hosts_present(self, topo):
        assert sorted(topo.hosts()) == sorted(HOSTS)

    def test_every_host_has_a_link(self, topo):
        for name in HOSTS:
            assert any(
                (name in link) for link in topo.links()
            ), f"host {name} has no link"

    def test_link_counts(self, topo):
        links = topo.links()
        assert len(links) == 13  # 1 core-core + 8 access-core + 4 host links
