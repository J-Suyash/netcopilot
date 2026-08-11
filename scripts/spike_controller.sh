#!/usr/bin/env bash
# Gate 1 spike: launcher boots, OF listener opens, cookie round-trips,
# masked delete preserves the cookie-0 baseline. (PLAN.md Phase 0 Gate 1)
# Run INSIDE the lab container from /workspace.
set -u
cd "$(dirname "$0")/.."

CTRL_LOG=/tmp/netcopilot_ctrl.log
REST=http://127.0.0.1:8081
PASS=0
FAIL=0

say() { printf '\n== %s\n' "$*"; }
ok()  { echo "  PASS: $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }

cleanup() {
  [ -n "${CTRL_PID:-}" ] && kill "$CTRL_PID" 2>/dev/null
  [ -n "${MN_PID:-}" ] && kill "$MN_PID" 2>/dev/null
  mn -c >/dev/null 2>&1
}
trap cleanup EXIT

say "1. preflight: port 6653 must be free"
if ss -ltn | grep -q ':6653 '; then
  bad "6653 already bound (stale controller?)"
  exit 1
else
  ok "6653 free"
fi

say "2. boot launcher (python -m netcopilot.controller.manage)"
PYTHONPATH=. python3 -m netcopilot.controller.manage >"$CTRL_LOG" 2>&1 &
CTRL_PID=$!
for _ in $(seq 1 20); do
  curl -sf "$REST/health" >/dev/null 2>&1 && break
  sleep 0.5
done
if curl -sf "$REST/health" >/dev/null 2>&1; then
  ok "REST /health up"
else
  bad "controller never came up"
  tail -20 "$CTRL_LOG"
  exit 1
fi

say "3. OF listener socket open (N13 guard)"
if ss -ltn | grep -q ':6653 '; then
  ok "listening on 6653"
else
  bad "no OF listener — ofp_handler missing?"
  tail -20 "$CTRL_LOG"
  exit 1
fi

say "4. bring up a switch with 2 host ports (MN_SWITCH=ovs|user; default ovs)"
# C8: single,2 — a single,1 switch has no port 2, so the baseline flow in
# step 7 would be rejected and the mask check would report a false "mask bug".
SWITCH="${MN_SWITCH:-ovs}"
timeout 90 mn --topo single,2 \
  --controller=remote,ip=127.0.0.1,port=6653 \
  --switch "$SWITCH",protocols=OpenFlow13 >/tmp/mn.log 2>&1 &
MN_PID=$!
sleep 6
DPIDS=""
for _ in $(seq 1 20); do
  DPIDS=$(curl -sf "$REST/switches" 2>/dev/null | tr -d '[]" \n')
  [ -n "$DPIDS" ] && break
  sleep 0.5
done
if [ -n "$DPIDS" ]; then
  ok "switch connected (dpid=$DPIDS)"
else
  bad "no switch handshake"
  tail -20 "$CTRL_LOG"
  tail -10 /tmp/mn.log
  exit 1
fi

say "5. install agent drop flow — cookie allocated by the controller (prio 150)"
# C7: never hardcode the cookie. The server allocates magic|session|op and
# returns it; a literal drifts from MAGIC and gets refused by is_agent_delete.
ADD_RESP=$(curl -sf -X POST "$REST/flows" \
  -d '{"priority":150,"match":{"eth_type":2048,"ipv4_src":"10.0.0.5","ipv4_dst":"10.0.0.20"},"action":"drop"}' \
  2>/dev/null)
AGENT_COOKIE=$(printf '%s' "$ADD_RESP" | tr -dc '0-9')
if [ -n "$AGENT_COOKIE" ]; then
  ok "flow add accepted (cookie=$AGENT_COOKIE)"
else
  bad "flow add failed: $ADD_RESP"
  tail -20 "$CTRL_LOG"
  exit 1
fi

say "6. read back — cookie intact"
sleep 1
FLOWS=$(curl -sf "$REST/flows")
if echo "$FLOWS" | grep -q "$AGENT_COOKIE"; then
  ok "agent cookie present in flow dump"
else
  bad "agent cookie missing from dump"
  echo "$FLOWS"
  exit 1
fi

say "7. all-agent mask delete removes agent flows, spares the cookie-0 baseline"
# C7: delete with the ALL-AGENT mask (0xFFFF000000000000) — that is the mask
# whose blast radius N2 was about. A full-mask delete only proves exact match.
MAGIC_MASK=18446462598732840960
curl -sf -X POST "$REST/flows" \
  -d '{"cookie":0,"priority":50,"match":{"eth_type":2048,"ipv4_dst":"10.0.0.20"},"action":"output","out_port":1}' \
  >/dev/null 2>&1
sleep 1
DEL_RESP=$(curl -s -X DELETE "$REST/flows" \
  -d "{\"cookie\":$AGENT_COOKIE,\"cookie_mask\":$MAGIC_MASK}" 2>/dev/null)
sleep 1
FLOWS=$(curl -sf "$REST/flows")
if echo "$FLOWS" | grep -q "$AGENT_COOKIE"; then
  bad "agent flow survived the all-agent mask delete: $DEL_RESP"
  echo "$FLOWS"
else
  ok "agent flow deleted (all-agent mask)"
fi
if echo "$FLOWS" | grep -q '"cookie": *"0"'; then
  ok "baseline cookie-0 flow survives (mask discipline holds)"
else
  bad "baseline wiped — mask bug!"
  echo "$FLOWS"
fi

say "8. delete outside the agent namespace is refused (C4)"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$REST/flows" \
  -d '{"cookie":0,"cookie_mask":0}')
if [ "$CODE" = "403" ]; then
  ok "mask-0 wipe refused with 403"
else
  bad "mask-0 wipe returned $CODE — namespace guard missing"
fi

say "9. scope note"
ok "Runner injection-string test (N21) is a Phase-1 first test, not a spike concern"


say "SUMMARY: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
