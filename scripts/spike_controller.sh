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

say "4. bring up a single switch (MN_SWITCH=ovs|user; default ovs)"
SWITCH="${MN_SWITCH:-ovs}"
timeout 90 mn --topo single,1 \
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

say "5. install agent drop flow with cookie 0xA51D000000000001 (prio 150)"
AGENT_COOKIE=11883923268576870401
if curl -sf -X POST "$REST/flows" \
  -d "{\"cookie\":$AGENT_COOKIE,\"priority\":150,\"match\":{\"eth_type\":2048,\"ipv4_src\":\"10.0.0.5\",\"ipv4_dst\":\"10.0.0.20\"},\"action\":\"drop\"}" \
  >/dev/null 2>&1; then
  ok "flow add accepted"
else
  bad "flow add failed"
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

say "7. baseline cookie-0 flow survives a masked agent delete"
curl -sf -X POST "$REST/flows" \
  -d '{"cookie":0,"priority":50,"match":{"eth_type":2048,"ipv4_dst":"10.0.0.20"},"action":"output","out_port":2}' \
  >/dev/null 2>&1
sleep 1
curl -sf -X DELETE "$REST/flows" \
  -d "{\"cookie\":$AGENT_COOKIE,\"cookie_mask\":18446744073709551615}" >/dev/null 2>&1
sleep 1
FLOWS=$(curl -sf "$REST/flows")
if echo "$FLOWS" | grep -q "$AGENT_COOKIE"; then
  bad "agent flow survived masked delete"
  echo "$FLOWS"
else
  ok "agent flow deleted (full mask)"
fi
if echo "$FLOWS" | grep -q '"cookie": "0"'; then
  ok "baseline cookie-0 flow survives (mask discipline holds)"
else
  bad "baseline wiped — mask bug!"
  echo "$FLOWS"
fi

say "8. scope note"
ok "Runner injection-string test (N21) is a Phase-1 first test, not a spike concern"

say "SUMMARY: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
