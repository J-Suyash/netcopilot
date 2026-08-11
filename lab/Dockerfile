# NetCopilot lab image — Gate-1 minimal (Mininet + os-ken + tooling).
# Ubuntu 22.04 ships Python 3.10 (os-ken requires >=3.10) and Mininet 2.3.
# Privileged container; host repo mounted at /workspace.
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        mininet \
        openvswitch-switch \
        iputils-ping \
        iperf3 \
        curl \
        python3-pip \
    && pip3 install --no-cache-dir os-ken flask \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Gate 0 checks (run inside container):
#   ovs-vswitchd --version            (OVS userspace works?)
#   mn --topo single,1 --switch user  (user-switch fallback works?)
#   mn --topo single,1 --switch ovs   (OVS datapath works? needs kernel module passthrough)
