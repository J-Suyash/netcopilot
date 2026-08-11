# NetCopilot lab image — Gate-1 minimal (Mininet + os-ken + tooling).
# Ubuntu 22.04 ships Python 3.10 (os-ken requires >=3.10) and Mininet 2.3.
# Privileged container; host repo mounted at /workspace.
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        mininet \
        openvswitch-switch \
        iproute2 \
        ethtool \
        psmisc \
        procps \
        iputils-ping \
        iperf3 \
        curl \
        python3-pip \
    && pip3 install --no-cache-dir os-ken flask \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Gate 0 findings (2026-08-12, verified in this image):
#   iproute2 is NOT pulled in by the mininet package — without `ip`, mn dies
#   with "/bin/sh: 1: ip: not found" during setup. ethtool/psmisc/procps are
#   the same class of implicit Mininet dependency.
#   Start OVS by hand (no systemd in the container):
#     /usr/share/openvswitch/scripts/ovs-ctl --system-id=random start
#   Then:
#     mn --test none --topo single,2 --switch ovs,protocols=OpenFlow13 --controller none
#     mn --test none --topo single,2 --switch ovs,datapath=user   # no-kernel-module fallback
