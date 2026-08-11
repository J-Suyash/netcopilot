"""SDN controller integration (os-ken app + launcher + HTTP client)."""

# os_ken imports are confined to this package (PLAN.md A.5, N29): agent/,
# safety/, and ui/ must never import os_ken — os_ken.lib.hub can monkey-patch
# sockets when eventlet mode is enabled, which would pollute CI unit tests.
