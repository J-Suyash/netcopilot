# NetCopilot — Implementation Plan & Review Brief (v5.1)

**Project:** LLM-Powered Network Operations Agent for Software-Defined Networks
**Repo:** https://github.com/J-Suyash/netcopilot
**Author:** J-Suyash (4th-year CS, minor in networking)
**Document purpose:** (A) project context, (B) architecture & design, (C) implementation plan, (D) test/evaluation strategy, (E) risks, (F) review brief for independent agents, (G) review history & dispositions.

> **v5 changes (from technical review v4):** Runner command execution switched to **`h.popen([...])`** — `h.cmd()` always routes through the node's bash, so "list-arg exec" was false comfort (N21); module attribution fixed: topology ← `os_ken.topology.api`, flow stats ← `os_ken.app.ofctl.api` (N22); `mark_dscp` requires a resolved `out_port` — bare SetField blackholes traffic (N23); `os_ken.app.ofctl.service` imported top-level AND named in `run_apps` (N24); **`.gitignore` created now**, before `.env` exists (N25); Phase 0 = days 1–3 (N26); **no agent flow ever carries a timeout** — schema drops the fields entirely (N27); session id = `max(session)+1` from audit tail (N28); os-ken import confinement to `controller/` (N29). Gate-1 artifacts (`.gitignore`, `pyproject.toml`, `manage.py`, `app.py`, `spike_controller.sh`, `lab/Dockerfile`) exist and are import-checked but **not yet tested against a live switch** — the spike proves them.

> **v5.1 (code review of the Gate-1 artifacts, 2026-08-12):** three defects that would have made the spike fail *and misreport why* are fixed in code — dispatcher constants imported from `os_ken.controller.handler` (C1), `reply_cls` = parser message class (C2), `reply_multi` returns a list (C3). The delete-namespace invariant is now enforced code, not a comment (C4). Plus: `EventOFPStateChange` on both dispatchers (C5), barrier-backed error surfacing (C6), spike no longer hardcodes a cookie and exercises the all-agent mask (C7/C8), launcher parses CONF inside `main()` and preflights both ports (C9/C10), REST server shuts down cleanly (C11), Python floor 3.10 to match the container (C12), `lab/compose.yml` pins publishes to host loopback (C13), REST-layer and namespace-guard tests added (C14/C15). See Part G.

---

## Part A — Project Context

### A.1 What we are building
**NetCopilot**: a chat interface where an operator types network intents in natural language ("block all traffic from 10.0.0.5 to the DB VLAN except port 443") and an LLM agent plans, validates, installs, and verifies the corresponding OpenFlow rules on a live SDN (Mininet + os-ken). A hard safety layer sits between the LLM and the network so malformed, conflicting, or dangerous operations are rejected regardless of what the model outputs.

### A.2 Goals (success criteria)
| # | Objective | Criterion |
|---|---|---|
| O1 | LLM agent translates NL intents → structured SDN operations | ≥90% of test intents produce valid executable operations |
| O2 | Safety layer (schema validation, conflict detection, guardrails, dry-run) | 100% of malformed/conflicting/dangerous ops blocked before install |
| O3 | Live SDN integration (flow install/remove/query/verify) | Works end-to-end on Mininet + os-ken |
| O4 | Chat UI demo | 30-second demo: intent → validation → install → verified |
| O5 | (Optional) fine-tuned small model vs API models on IBNBench | Accuracy/cost/latency comparison — only if Phases 0–4 stay on schedule |

### A.3 Current state
- `proposal.md`, `PLAN.md`, `REVIEW.md` committed and pushed.
- **Gate-1 artifacts committed** (2026-08-12): `.gitignore`, `pyproject.toml`, `netcopilot/controller/manage.py`, `netcopilot/controller/app.py` (minimal REST surface), `scripts/spike_controller.sh`, `lab/Dockerfile` (minimal). **Launcher boot-tested on 2026-08-12** (no switch): `/health` + `/switches` serve, OF listener on 6653 confirmed open, process stays alive — three real bugs caught and fixed by the boot test (hub.eventlet absent under native hub → werkzeug make_server; hub.semaphore → hub.Semaphore; missing `__init__.py` → namespace-package Flask crash). **Switch handshake + cookie round-trip still pending** — that is exactly what Gate 1 proves in the lab container.
- **Code review of those artifacts (2026-08-12)** found 3 spike-blocking defects (dispatcher import, `reply_cls` class kind, `reply_multi` list) and 16 more; all fixed, `lab/compose.yml` added, unit suite extended to the REST layer and the delete-namespace guard. Every OpenFlow-touching line is still unexecuted — Gate 1 is the first real proof.

### A.4 Environment (DECIDED; container basics verified 2026-08-12)
| Thing | Value |
|---|---|
| Dev box | CachyOS/Arch, host Python 3.14, host sudo blocked — irrelevant: **rootful Docker 29.7.2 + docker group verified** |
| Container capability | Verified: `--privileged` netlink ops OK; namespaces present |
| OVS | Host module present; **OVS *inside* the container = the one untested lab piece** (Gate 0) |
| Python toolchain | uv everywhere; `uv python install 3.11` verified rootless |
| Controller | **os-ken 4.2.1** — source-verified: `AppManager.run_apps` usable; `ofp_handler` REQUIRED (its `start()` returns the only long-lived thread — omit it and the process **exits silently, rc 0**); opts registered in `controller.py`/`topology/switches.py`, NOT `flags.py`; `OFP_TCP_PORT=6653` default; `--observe-links` default False; `mod_flow_entry` default `cookie_mask=0` (mask-0 = match-everything trap) |
| LLM access | OpenRouter + OpenCode Zen (OpenAI-compatible), keys in `.env`; Ollama fallback |

**Lab layout (decided in v4):** Mininet + os-ken controller + Lab Runner all inside the privileged container; listeners bind `0.0.0.0` inside; publish ONLY `127.0.0.1:5100` (Runner) + `127.0.0.1:8081` (controller REST) — never bare `-p` (would put a privileged exec endpoint on the LAN). Switch→controller internal on `127.0.0.1:6653`. Agent + safety + UI on host (uv venv). `--network host` rejected (netns pollution); contingency only if OVS-in-container AND `--switch user` both fail.
**Honesty note (N9):** `--privileged` + docker group = root-equivalent; the container is isolation convenience, not a security boundary. Runner hardening is the real protection — precisely because it runs privileged.

**CI note:** GitHub Actions = Ubuntu, rootless unit tests only (Part D).

### A.5 Non-goals & hard constraints (do NOT build / do NOT violate)
- No ONOS/ODL/P4/DPDK, no multi-controller clusters, no production claims.
- No LangChain/LlamaIndex — plain function-calling loop, explainable line-by-line.
- No LLM fine-tuning in v1 (O5 only if schedule allows).
- No real hardware, no internet-facing services; all published ports pinned to host loopback.
- No general flow-conflict solver (C.3 — exact + full-shadow on supported fields only).
- No vendored Ryu REST stack (Option A owns the Flask surface).
- **No timeouts on any agent-installed flow** (N19+N27 — "flows are explicit until removed"; schema has no timeout fields at all).
- **`os_ken` imports are confined to `netcopilot/controller/`** (N29 — `os_ken.lib.hub` monkey-patches sockets on import; if it leaks into `agent/`/`safety/`/`ui/`, the CI unit suite starts monkey-patching under pytest). `client.py` is HTTP-only for exactly this reason.

---

## Part B — Architecture & Design

### B.1 Component diagram

```
HOST (uv venv, no root)
┌──────────────────────────────────────────────────────────┐
│  Chat UI (Chainlit) — intent in, explainable steps out    │
│  + dry-run toggle + audit panel                           │
│  (topology PNG = NICE-TO-HAVE, cuttable if P3 slips)      │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Agent Loop (plain Python, OpenAI-compatible tools API)   │
│  max 6 rounds, bounded retries (2) on validation errors   │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Tool Layer (8 tools, B.4) + cookie allocator             │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  SAFETY LAYER (LLM-independent, in-process, mandatory)    │
│  1. pydantic schema (strict, extra=forbid)                │
│  2. guardrails (broad drops, protected flows, name        │
│     resolution vs live topology, session limits)          │
│  3. conflict detection (BOUNDED: exact + full-shadow)     │
│  4. dry-run (default ON)                                  │
│  5. audit JSONL keyed on cookie + undo by cookie+mask     │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP → 127.0.0.1:8081 (REST) · 127.0.0.1:5100 (Runner)
                           │ (loopback-pinned publishes only)
┌──────────────────────────▼───────────────────────────────┐
│  PRIVILEGED LAB CONTAINER                                  │
│  ┌─ os-ken controller (netcopilot/controller/) ─────────┐ │
│  │ manage.py  (launcher: hub.patch → import opts →      │ │
│  │   cfg.CONF → AppManager.run_apps([ofp_handler,       │ │
│  │   topology.switches, ofctl.service, app]))           │ │
│  │ app.py     (os-ken app; topology/hosts ←             │ │
│  │   os_ken.topology.api; flow stats ←                  │ │
│  │   os_ken.app.ofctl.api — imported at module top      │ │
│  │   level (N24); baseline L2 flows cookie=0 prio<100;  │ │
│  │   flow-table-full surfacing)                         │ │
│  │ REST: Flask via werkzeug make_server + │ │
│  │   hub.spawn (0.0.0.0:8081, native hub, │ │
│  │   N15-amended) — 5 endpoints           │ │
│  └───────────────┬─────────────────────────────────────┘ │
│                  │ OpenFlow 1.3 — 127.0.0.1:6653 (internal)│
│  ┌───────────────▼─────────────────────────────────────┐ │
│  │ Mininet (custom Topo; OVS in-container OR --switch   │ │
│  │ user)                                               │ │
│  │ Lab Runner (Flask, 0.0.0.0:5100): /verify, /iperf,  │ │
│  │   /link/down, /link/up                               │ │
│  │   ★ host cmds via h.popen([...]) (shell=False,      │ │
│  │     N21) — NEVER h.cmd() with any non-literal input  │ │
│  │   ★ input validation against resolved host set /    │ │
│  │     ipaddress; global lock (load-bearing: sendCmd   │ │
│  │     asserts not self.waiting)                       │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### B.2 Data flow — sample intent: "block 10.0.0.5 from reaching the DB server 10.0.0.20"

1. User sends intent → agent loop.
2. LLM returns `tool_call: install_flow({match: {ipv4_src: 10.0.0.5, ipv4_dst: 10.0.0.20}, action: drop})`.
3. **Safety**: pydantic validates → guardrails (match not too broad; hosts resolve against live topology) → conflict scan (bounded) → dry-run check.
4. Tool allocates cookie (`magic:16|session:16|op:32`), POSTs to `127.0.0.1:8081`; app builds `OFPFlowMod` (`ofctl_v1_3.to_match` + `mod_flow_entry`), sends via `datapath.send_msg()`.
5. Audit row written, keyed on cookie.
6. **Verification (N6 pre/post pair):** probe → reachable (baseline); install; probe → unreachable. "Blocked" claimed only on the reachable→unreachable transition; optional control pair rules out global failures.
7. UI renders each step.

**Verification semantics:** probe protocol matches the intent — ICMP for L3, TCP port probe for L4 (`tcp_dst`) via Python socket through `h.popen` (no `nc` dependency: busybox `nc` lacks `-z`; don't rely on it — C.1).

### B.3 Key design decisions
1. **Plain function-calling loop** — no agent framework; model-agnostic via OpenAI-compatible `tools` API.
2. **Safety as a hard gate** — LLM proposes, code disposes. Guardrails are Python, not prompts; allowlists from resolved controller topology, never LLM-echoed strings.
3. **Lab Runner as trust boundary** — input validation is THE control (dst ∈ resolved host set or `ipaddress.ip_address()`; port int 1–65535; count bounded). Execution uses **`h.popen([...])`** (`shell=False`, argv built by `mnexec`) — **`h.cmd()` is banned for anything non-literal**: it joins args into one string into the host's persistent bash, so `h.cmd(["ping","-c","2",dst])` with `dst="8.8.8.8; rm -rf /"` becomes `ping -c 2 8.8.8.8; rm -rf /` run by bash as root in a privileged container (N21). Reserve `h.cmd()` for strings built entirely from literals. **Global lock is load-bearing for correctness** — `sendCmd` asserts `not self.waiting`, so concurrent `h.cmd()`/`h.popen()` on one host raises AssertionError, not just races.
4. **Controller transport = owned REST surface (N1/N16/N22/N24):** launcher `manage.py`; **topology/hosts ← `os_ken.topology.api`** (`get_all_switch/get_all_link/get_all_host`); **flow stats ← `os_ken.app.ofctl.api`** (`get_datapath`, `send_msg(reply_cls=..., reply_multi=True)`, backed by `OfctlService`); flow writes ← `os_ken.lib.ofctl_v1_3` (`to_match` + `mod_flow_entry`). `os_ken.app.ofctl.api` **imported at module top level** of `app.py` (its `require_app('os_ken.app.ofctl.service')` registers on the importing frame — a lazy import means the brick never loads and every read raises at runtime) AND `'os_ken.app.ofctl.service'` named explicitly in `run_apps`. **Read-path call shape (C2/C3):** `reply_cls` must be an OpenFlow **message** class (`dp.ofproto_parser.OFPFlowStatsReply`) — the ofctl service runs it through `ofp_msg_to_ev_cls()`, so passing an `Event*` class raises `KeyError`; and `reply_multi=True` returns a **list of replies**, so iterate `for msg in replies: for entry in msg.body`. **Write-path error surfacing (C6):** `mod_flow_entry` is fire-and-forget, so a flow-mod is followed by an `OFPBarrierRequest` through `ofctl_api` under a write lock; any `EventOFPErrorMsg` (bad match, `OFPFMFC_TABLE_FULL`) has landed by the time the barrier reply returns and is reported as a switch-rejection error. Flask is served via `werkzeug.make_server(...).serve_forever` in a `hub.spawn`ed thread — **not** `eventlet.wsgi.server` (absent under the native hub) and never `flask_app.run()` (N15, as amended by the boot test in B.3.7).
5. **Flow identity = cookie with session namespace (N2/N20/N28):** `magic:16 (0xA51D) | session:16 | op:32`. Masks: single → full `0xFFFFFFFFFFFFFFFF`; per-session → `0xFFFFFFFF00000000`; all-agent → `0xFFFF000000000000`. **Never delete with mask 0** (`mod_flow_entry` defaults to it — matches every cookie). Baseline flows: cookie 0, priority <100. **Session id = `max(session)+1` from the audit JSONL tail at startup** (same read as the op-id reseed) — never random (collides across restarts) or wall-clock (wraps).
6. **Op-id counter reseeds on startup (N3)** from `max(op_id)` over installed agent flows (queried at boot) or audit tail — prevents post-restart cookie collision.
7. **Launcher recipe (N13/N14/N15 — source-verified):**
```python
# netcopilot/controller/manage.py
from os_ken.lib import hub
hub.patch(thread=False)                      # before anything imports socket
from os_ken import cfg, flags                # noqa: F401
from os_ken.controller import controller      # noqa: F401 — registers ofp-* CLI opts
from os_ken.topology import switches          # noqa: F401 — registers --observe-links
from os_ken.base.app_manager import AppManager

cfg.CONF(['--observe-links', '--ofp-tcp-listen-port', '6653'], project='netcopilot')
AppManager.run_apps([
    'os_ken.controller.ofp_handler',          # REQUIRED — opens the OF listener; omit it
                                              # and the process exits silently rc 0 (N13)
    'os_ken.topology.switches',               # topology/host tracking
    'os_ken.app.ofctl.service',               # required by os_ken.app.ofctl.api (N24)
    'netcopilot.controller.app',
])
```
   Plus: in `app.start()` → `srv = make_server("0.0.0.0", REST_PORT, flask_app, threaded=True); hub.spawn(srv.serve_forever)`. **Empirical revision of N15 (2026-08-12 boot test):** os-ken defaults to the NATIVE hub (`OSKEN_HUB_TYPE` unset) and `hub.eventlet` does NOT exist under it — the original `eventlet.wsgi.server` recipe raised AttributeError at runtime. Werkzeug's `make_server` in a spawned native thread needs no eventlet, no monkey-patching, and works under either hub type. **Preflight:** if port 6653 is already bound (stale process), exit with a clear error.
8. **Deterministic testing via MockLLM** — no network or API keys in unit/CI tests.
9. **Dry-run default ON** — destructive ops require confirmation when off.
10. **No timeouts on agent flows, period (N19+N27)** — the schema has no timeout fields; removal is explicit (`remove_flow`) → audit event.

### B.4 Tool schemas (JSON function definitions)
| Tool | Parameters (pydantic, extra=forbid) | Returns |
|---|---|---|
| `get_topology` | — | switches, links, hosts (MAC/IP/port) |
| `get_flows` | dpid? | flow table entries (cookie, priority, match, actions, counters) |
| `get_stats` | dpid, port? | byte/packet counters per port |
| `install_flow` | match: {eth_type?, ipv4_src?, ipv4_dst?, ip_proto?, tcp_dst?}, action: **drop\|output\|mark_dscp**, **out_port (required iff output/mark_dscp; resolved from host map)**, **dscp: int 0–63 (required iff mark_dscp)**, priority (100–200) | cookie, installed rule. **No timeout fields exist** (N27) |
| `remove_flow` | cookie (magic + session bits must match) | removed rule — delete with cookie+cookie_mask |
| `verify_connectivity` | src, dst, proto: icmp\|tcp, port? (tcp only), count=2 | reachable/unreachable + rtt (pre/post pairs, B.2) |
| `resolve_host` | name or IP | canonical host info or error |
| `fail_link` / `heal_link` | src_switch, dst_switch | link state change (demo/failure scenarios) |

**`mark_dscp` semantics (N23):** OF1.3 has no implicit forwarding — a bare `SetField(ip_dscp)` action list **drops the packet**. `mark_dscp` therefore compiles to `[SetField(ip_dscp=n), Output(out_port)]` with `out_port` resolved from the host map (same resolution `action: output` needs). Multi-table (`goto_table:1`) is the documented ceiling, not v1 scope. Integration test asserts a marked flow **still forwards** (flow-dump "looks right" while silently severing connectivity is exactly the failure this catches).

### B.5 Repo layout (target)
```
sdn_ai_copilot/
├── proposal.md
├── PLAN.md                 # this file
├── REVIEW.md               # review records (v1–v4)
├── README.md               # final docs (P4)
├── .gitignore              # ✅ EXISTS (N25) — .env, .venv, __pycache__, *.jsonl, stray dotfiles
├── pyproject.toml          # ✅ EXISTS — uv-managed
├── .env.example            # LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, DRY_RUN_DEFAULT,
│                           # CONTROLLER_PORT=6653, REST_PORT=8081, LAB_PORT=5100
├── lab/
│   ├── Dockerfile          # ✅ EXISTS (minimal, Gate-1) — mininet + os-ken + flask + curl
│   ├── compose.yml         # publishes ONLY 127.0.0.1:5100 + 127.0.0.1:8081
│   ├── topo.py             # Mininet Topo class (campus/leaf-spine) — Phase 1
│   └── runner.py           # Flask lab server — Phase 1 (h.popen, validation, global lock)
├── netcopilot/
│   ├── __init__.py
│   ├── config.py           # env → dataclass config — Phase 2
│   ├── agent/              # Phase 2 (loop.py, llm.py, tools.py) — NO os_ken imports (N29)
│   ├── safety/             # Phase 3 (schema.py, guardrails.py, conflicts.py, audit.py)
│   ├── controller/
│   │   ├── manage.py       # ✅ EXISTS — launcher recipe (B.3.7) + port preflight
│   │   ├── app.py          # ✅ EXISTS (Gate-1 scope) — REST surface, cookie alloc
│   │   └── client.py       # HTTP client for REST + Runner — Phase 1
│   └── ui/                 # Phase 3 (Chainlit app.py)
├── tests/                  # Phase 2/3 (unit/, integration/, eval_suite.py)
└── scripts/
    ├── setup_env.sh        # uv 3.11 venv + deps (host, no root)
    ├── build_lab.sh        # build/start lab container
    └── spike_controller.sh # ✅ EXISTS — Gate 1: launcher + cookie round-trip
```

---

## Part C — Implementation Plan

### Phase 0 — Environment bootstrap (days 1–3)
**Gate 0 (day 1):** OVS-in-container vs `--switch user` (container basics already verified). Build lab image (`lab/Dockerfile` exists); test `ovsdb-server`+`ovs-vswitchd` in-container; same gate tests `mn --topo linear,2 --switch user`. Either green = lab green. Also on day 1: confirm `.gitignore` covers `.env` (it does — created with the artifacts; **verify on your local clone**, where stray shell dotfiles were observed untracked).

**Gate 1 (day 1–2, launcher-first — independent of Gate 0):**
1. Host venv: `uv venv --python 3.11` + `uv pip install -e .` (pyproject exists).
2. **Launcher import-correctness (no Mininet needed):** boot `manage.py`; assert 6653 listener is open (catches the N13 silent-rc-0 class); assert `--observe-links` accepted.
3. **Cookie round-trip (needs Gate 0 switch):** run `scripts/spike_controller.sh` — brings up a single switch, POSTs a cookie'd drop flow, dumps it back (cookie intact), deletes with full mask, asserts a cookie-0 baseline flow survives.
4. **Runner injection sanity (N21):** first thing Phase 1 builds (below) — but on day 2, verify the `h.popen` path with an actual injection string and assert **no side effect**, not merely rejection.

**Exit:** OpenFlow 1.3 topology, cookie-correct install/delete/readback, launcher + wiring proven, commands documented in README.

### Phase 1 — Lab + controller plumbing (days 4–8)
1. `lab/topo.py` — campus topology: 2 core + 2 access switches, 4 hosts (web, db, client, dmz), IP plan 10.0.0.0/24.
2. `lab/runner.py` — hardened: `/verify/{src}/{dst}?proto=icmp|tcp&port=N`, `/iperf`, `/link/down`, `/link/up`; validation = THE control (host set / `ipaddress`); **`h.popen([...])` for all host commands** — `h.cmd()` banned for non-literals; **global `threading.Lock`** (load-bearing: `sendCmd` asserts `not self.waiting`); TCP probe via Python socket (no `nc` dependency); binds `0.0.0.0:5100`. **First test written: injection string through `/verify` asserts no shell-metachar side effect.**
3. `netcopilot/controller/manage.py` — polish launcher (config, logging, clean shutdown).
4. `netcopilot/controller/app.py` — extend from Gate-1 scope: topology/host tracking via `os_ken.topology.api` + ARP-snoop IP learning only (N10); baseline L2 flows cookie=0 prio<100; `OFPFMFC_TABLE_FULL` + error replies surfaced as structured tool errors.
5. `netcopilot/controller/client.py` — typed wrappers over REST + Runner: `add_flow`, `delete_flow(cookie,cookie_mask)`, `get_flows`, `get_topology`, `get_ports`, `verify`, `fail_link`; timeouts + controller-health check.
6. `scripts/build_lab.sh` + `compose.yml` (loopback-pinned publishes).
**Exit:** curl can install/remove a flow (cookie + mask verified in dump) and verify a ping via Runner; host map populated; controller-down produces a clean tool error; host→container wiring proven.

### Phase 2 — Agent loop (days 9–17)
**Tasks (TDD, MockLLM first):**
1. `config.py` + `.env.example`.
2. `agent/llm.py` — `LLMClient.chat(messages, tools)`; OpenRouter-compatible (covers Zen), Ollama, `MockLLM`.
3. `agent/tools.py` — registry + handlers; **cookie allocator**: `magic|session|op`, monotonic under lock, seeded from `max(op_id)` over installed agent flows at startup (N3); session from audit tail (N28).
4. `agent/loop.py` — validate → guardrails → conflicts → execute → append → repeat (≤6 rounds, ≤2 validation retries); timeouts, error propagation. **No os_ken imports here (N29).**
5. `netcopilot/cli.py` — `python -m netcopilot "block 10.0.0.5 from 10.0.0.20"`.
**Exit:** mock-LLM e2e green (scripted install→verify asserts flow + cookie on switch); CLI works on 3 real scenarios with a real LLM.

### Phase 3 — Safety layer + UI (days 18–27)
1. `safety/schema.py` — pydantic, `extra=forbid` (no timeout fields — N27); validation errors fed back (bounded, 2 retries).
2. `safety/guardrails.py` — reject: wildcard/drop-all at high priority; unknown hosts/IPs (allowlist from resolved topology); removal of non-agent flows (cookie magic+session check); >50 flow changes/session; management-plane targets.
3. `safety/conflicts.py` — BOUNDED: (a) identical match + same priority + different action; (b) full shadow either direction on supported match fields only. **`mark_dscp` is first-class: a marking flow shadowing a drop (or vice versa) is a genuine conflict** (N18-adjacent). General intersection out of scope.
4. `safety/audit.py` — JSONL keyed on cookie: intent, tool call, validations, executed ops, pre/post verification; **undo = per-session cookie+mask delete** (other sessions untouched — N20).
5. `ui/app.py` — Chainlit chat, tool-call cards, dry-run toggle, audit panel. Topology PNG = cuttable nice-to-have.
**Exit:** unit suite green (D); UI demos all 5 scenarios; safety cases all rejected.

### Phase 4 — Evaluation, docs, packaging (days 28–40)
1. `tests/eval_suite.py` — 20-intent gold set (5 × 4: security, QoS, observability, failure diagnosis, safety).
   - QoS = `mark_dscp` flows: metric "correct flow installed with expected match/action/dscp AND still forwards", verified via flow dump + connectivity (N23). iperf only in failure-diagnosis.
   - Metrics: op success rate, safety rejection rate (100% on safety cases), rounds, latency, token cost.
2. README: architecture, setup, demo script, interview talking points. 3. Demo video + GitHub release. 4. Report skeleton in `docs/`.
**Exit:** O1–O4 met; repo public and polished.

### Optional Phase 5 — Research extension (only if Phases 0–4 done early)
Fine-tune Qwen2.5-3B (LoRA) on IBNBench; compare accuracy/cost/latency vs API zero-shot on the same eval suite → conference paper draft.

---

## Part D — Testing & Evaluation Strategy

| Layer | Tooling | Requires | Runs in CI |
|---|---|---|---|
| Unit (schema, guardrails, conflicts, audit, cookie mgmt, config) | pytest | nothing | ✅ |
| Loop e2e with MockLLM | pytest + fake controller client | nothing | ✅ |
| Integration (real lab container) | pytest, tagged `integration` | lab container | ❌ (local) |
| Real-LLM smoke + eval suite | script + CSV | API key (.env) | ❌ (manual) |
| Lint | ruff | — | ✅ |

Key tests: guardrail table-driven (≥15, incl. prompt-injection intents); conflict matrix (≥10, incl. mark_dscp-vs-drop shadow); **undo-all leaves cookie-0 baseline intact**; **counter reseed after restart**; **per-session undo leaves other sessions' flows**; undo order-independence; dry-run blocks all writes; audit completeness; **Runner injection string → assert NO side effect (stronger than rejection — N21)**; **marked flow still forwards (N23)**; Runner serialization (parallel requests); flow-table-full surfaces as structured error; **launcher smoke: OF listener socket open (N13 regression)**.

**Import-confinement guard (N29):** a CI unit test asserts `netcopilot/agent/` and `netcopilot/safety/` contain no `os_ken` import (grep over the packages).

---

## Part E — Risks & Open Questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| E.1 | OVS *inside* lab container doesn't run | Medium | Gate 0 day 1; `--switch user` fallback (bounded); `--network host` contingency only if both fail |
| E.2 | Launcher/wiring silent failures | Retired by design | Source-verified recipe; launcher smoke test; Gate 1 day 1–2 |
| E.3 | Small/free LLMs unreliable at tool-calling | Medium | Bounded retries + validation feedback; structured JSON fallback; cheap paid backup |
| E.4 | Scope creep (UI polish, PNG, queues, conflict solver, O5) | High | Non-goals (A.5); gated phases; PNG cuttable |
| E.5 | API cost | Low | MockLLM; small models; token budget logging |
| E.6 | Concurrent intents race on single Mininet | Medium | Runner global lock (load-bearing — `sendCmd` asserts) |
| E.7 | Verification claims that prove nothing | Low | Pre/post probe pair (B.2) |
| E.8 | Port-in-use from stale process (6653/8081/5100) | Medium | Preflight in `manage.py` + clear error |
| E.9 | `.env`/key leak via missing gitignore on local clone | Low (now) | `.gitignore` committed; verify on local clone day 1 (N25) |

Open questions: none blocking. Topology size (default 2+2/4 hosts); QoS metric settled (DSCP flow present AND forwarding).

---

## Part F — REVIEW BRIEF (for the reviewing agent)

> **You are reviewing an implementation plan, not executing it.** Read `PLAN.md` (this file), `proposal.md`, and `REVIEW.md` (prior review records — check your findings against history so you don't re-flag fixed items). Do NOT modify files. Produce a review report to `REVIEW.md` (append) or return in full in your final message if you cannot write files. **Prefer empirical spikes over trusting docs — four prior reviews caught real bugs this way (Part G).**

### F.1 Context you need
- Author: 4th-year CS student, **8 weeks**, dev box = CachyOS/Arch, host sudo blocked but rootful Docker + docker group verified; host Python 3.14, uv provides 3.11; no GPU; minimal API budget. Lab = privileged container (Mininet + os-ken + Runner inside; only `127.0.0.1:5100` + `127.0.0.1:8081` published). CI = GitHub Actions (Ubuntu, rootless units only).
- Differentiator = safety layer + demo quality; core loop (NL intent → validated OpenFlow rule on live emulated network) must work by end of Phase 2 (~day 17).
- This is v5; four prior reviews' findings are folded in (Part G). Gate-1 artifacts exist but are untested against a live switch. Review the CURRENT state.

### F.2 Review dimensions — check ALL explicitly
1. **Feasibility & environment**: OVS-in-container vs `--switch user`; os-ken 4.2.1 on py3.11; launcher recipe (ofp_handler required — silent rc-0 exit if omitted; import-before-parse; `--observe-links`); port wiring (internal 6653, loopback-pinned 5100/8081); cookie+cookie_mask semantics; `h.popen` vs `h.cmd` (N21); uv-on-Arch without root.
2. **Architecture correctness**: B.2 with owned-REST design? Safety bypass via (a) prompt-injected intent, (b) hallucinating LLM, (c) tool-output poisoning? Cookie identity sound (magic/session/op, masks, reseed, session source)? Runner trust boundary complete (validation + popen + lock)? Pre/post probe pair sufficient? `mark_dscp` compiles to SetField+Output (N23)?
3. **Completeness**: missing tools/endpoints/failure modes (controller-down ✅, flow-table-full ✅, LLM timeout, Mininet host crash, port-in-use ✅, concurrency ✅)? TCP probe tooling specified (python socket, no nc)? Eval suite measurable (QoS = flow present AND forwards)?
4. **Scope control**: silent 2-week eaters? Non-goals sufficient? Anything cut/deferred?
5. **Testability**: MockLLM sound? CI rootless-clean? Import-confinement guard (N29) present? Launcher smoke test present?
6. **Security**: `.gitignore` committed (N25 — verify local clone)? Loopback-pinned publishes? Privileged container honestly framed? Prompt-injection defensible end-to-end? Runner root-exec controls accurate (popen + validation)?
7. **Sequencing**: hardest unknowns first (Gates 0/1 days 1–3)? Gate 1 split (launcher test independent of Gate 0) correct?

### F.3 What to keep in mind
- **Correctness of claims > politeness**: verify or flag "verify" — four prior reviews caught real bugs by testing.
- **Proportion**: 8-week UG project. Reject over-engineering as much as under-specification.
- **Constraints**: no GPU, minimal spend, no host root, explainable code.
- **Prioritize**: rank by threat to the "core loop works by day 17" milestone.
- **Verdicts**: APPROVE / APPROVE WITH CHANGES / MAJOR REVISION.

### F.4 Required output format
```
VERDICT: <APPROVE | APPROVE WITH CHANGES | MAJOR REVISION>

FINDINGS:
ID | SEVERITY (BLOCKER/MAJOR/MINOR/NIT) | LOCATION | ISSUE | SUGGESTED FIX

ANSWERS TO REVIEW DIMENSIONS (F.2 items 1–7, one short paragraph each)

TOP 5 RISKS (ranked, with why)

QUESTIONS FOR THE AUTHOR (anything you could not determine)
```

---

## Part G — Review History & Dispositions

### v1 → v2 → v3 (all accepted; dispositions superseded by later fixes)
- v1: Ryu→os-ken (BLOCKER), cookie identity, Runner injection, bounded conflicts, port config, lock, protocol-matched verify, spike day 1, env decision.
- v2: os-ken packaging gap → Option A (BLOCKER), cookie_mask discipline, op-id reseed, launcher gap, idle_timeout+drop, pre/post probe, QoS metric, Gate 0 retarget, honest container framing, lean on topology module, eventlet compat retired.
- v3: wiring (N12), `ofp_handler` required (N13), oslo.config order (N14), eventlet WSGI (N15 — **empirically amended 2026-08-12: native hub → werkzeug make_server**), ofctl read path (N16), 6653 default (N17), mark_dscp action (N18), timeouts on drops (N19), session cookie bits (N20).

### Review v4 (external agent, os-ken source-verified): GO / APPROVE WITH CHANGES — all 9 findings ACCEPTED into v5:

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| N21 | MAJOR | "list-arg exec" is false comfort — `h.cmd()` always joins args into one string into the node's persistent **bash**; injection = RCE as root in privileged container | Runner uses **`h.popen([...])`** (shell=False) for all host commands; `h.cmd()` banned for non-literals; validation remains THE control; day-1 injection-string test asserts no side effect; global lock documented as load-bearing (`sendCmd` asserts `not self.waiting`) |
| N22 | MINOR | v4 regressed attribution: `os_ken.app.ofctl.api` has only `get_datapath`/`send_msg`; topology/hosts come from `os_ken.topology.api` | B.1/B.3.4 corrected: topology ← `os_ken.topology.api`; flow stats ← `os_ken.app.ofctl.api` |
| N23 | MINOR | Bare `SetField(ip_dscp)` blackholes traffic (no implicit forwarding in OF1.3) | `mark_dscp` compiles to `[SetField(ip_dscp=n), Output(out_port)]`, out_port resolved from host map; integration test asserts marked flow still forwards |
| N24 | MINOR | `ofctl.api` requires `OfctlService`; `require_app` registers on the importing module's frame — lazy import = brick never loads | Top-level import in `app.py` AND explicit `'os_ken.app.ofctl.service'` in `run_apps` (belt and braces) |
| N25 | MINOR | No `.gitignore`; repo root held untracked shell dotfiles; Phase 2 will create `.env` with keys | `.gitignore` committed NOW (covers `.env`, `.venv/`, `__pycache__/`, `*.jsonl`, stray dotfiles); verify on local clone day 1 |
| N26 | NIT | Day 3 unassigned (Phase 0 = days 1–2, Phase 1 = days 4–8) | Phase 0 = days 1–3 |
| N27 | NIT | Timeout params vanished from `install_flow` row; ambiguity for output/mark_dscp | **No agent flow ever carries a timeout** — schema drops the fields entirely; consistent with "blocks explicit until removed" |
| N28 | NIT | Session-id source unspecified (random collides, wall-clock wraps) | `max(session)+1` from audit tail at startup |
| N29 | NIT | `os_ken.lib.hub` monkey-patches sockets on import — must not leak into CI-tested packages | Hard constraint (A.5) + CI grep guard (D): os_ken imports confined to `netcopilot/controller/` |

**Reviewer's questions → decisions:** Q1 `h.popen(list)` confirmed, day-1 injection-string verification added; Q2 `mark_dscp` = required resolved `out_port` (multi-table is the documented ceiling, not v1); Q3 no timeouts at all — schema has no timeout fields; Q4 session id from audit tail.

**Reviewer's note (adopted):** this is the last paper review worth doing — remaining risk is empirical (Gate 0/1), not editorial. Plan text is frozen unless a spike fails; future reviews should review *code*, not the plan.

### Code review v5 (Gate-1 artifacts, 2026-08-12): docs GO / code NOT YET — all 19 findings ACCEPTED, fixes applied in the same session:

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| C1 | BLOCKER | `os_ken.controller.controller` re-exports only `HANDSHAKE_`/`DEAD_DISPATCHER` — `controller.MAIN_DISPATCHER` raised AttributeError on every state change, so `datapaths` stayed empty and the spike would report "no switch handshake" on a *successful* handshake | Dispatcher constants imported from `os_ken.controller.handler`; stale `controller` import dropped from `app.py` |
| C2 | BLOCKER | `reply_cls=ofp_event.EventOFPFlowStatsReply` — the ofctl service runs `reply_cls` through `ofp_msg_to_ev_cls()`, so an `Event*` class KeyErrors (os-ken's own docstring uses `parser.OFPPortDescStatsReply`) | `reply_cls=dp.ofproto_parser.OFPFlowStatsReply` |
| C3 | BLOCKER | `reply_multi=True` returns a **list** of replies; code did `reply.body` → `AttributeError`, so every `GET /flows` 500'd and the cookie round-trip could not pass | Iterate `for msg in replies: for entry in msg.body` |
| C4 | MAJOR | `_flows_del` accepted any cookie and any mask incl. `cookie_mask: 0` — the mask-0 wipe the plan warns about was documented in a comment but not enforced | `is_agent_delete(cookie, mask)` module function enforced at the boundary → 403; unit tests for every mask class |
| C5 | MAJOR | `EventOFPStateChange` registered for `MAIN_DISPATCHER` only (os-ken's `dpset.py` uses both) — disconnect branch was dead code, dead `Datapath` kept serving writes | Both dispatchers registered |
| C6 | MAJOR | `mod_flow_entry` is fire-and-forget: a rejected flow (bad match, `OFPFMFC_TABLE_FULL`) still returned HTTP 200 + cookie, making the plan's "table-full surfaced as structured error" impossible | `EventOFPErrorMsg` handler + `OFPBarrierRequest` after each flow-mod under a write lock; switch rejection returns 500 with the OF type/code |
| C7 | MAJOR | Spike hardcoded `11883923268576870401` = `0xa4ec2d3fc1860001`, not the `0xA51D…0001` its comment claimed — outside the agent namespace, and step 7 deleted with the *full* mask so the N2 hazard was never exercised | Spike POSTs without a cookie, reads the allocated cookie from the response, deletes with the **all-agent mask**; new step asserts a mask-0 wipe is refused (403) |
| C8 | MINOR | Baseline flow output to **port 2** on a `--topo single,1` switch (no such port) — would be rejected silently and misreported as "baseline wiped — mask bug!" | `--topo single,2`; baseline outputs to port 1. Also fixed the `"cookie": "0"` grep, which required a space Flask's compact JSON never emits |
| C9 | MINOR | `CONF(...)` parsed only under `__main__` while `main()` reads CONF — any other entry point boots with `--observe-links` unset, so topology APIs return empty silently (N4's failure class again) | Parse inside `main()`; `--observe-links` force-appended; port overridable via argv/env |
| C10 | MINOR | Preflight guarded 6653 only; a stale 8081 makes `make_server` raise inside a spawned thread → process alive with no REST surface, reported as "controller never came up" | Both ports preflighted |
| C11 | MINOR | Werkzeug server never shut down — leftover listener is the stale-port annoyance the preflight exists for | `stop()` calls `srv.shutdown()` |
| C12 | MINOR | `requires-python = ">=3.11"` vs lab container `ubuntu:22.04` = Python 3.10, where the code actually runs | Floor lowered to `>=3.10`, ruff `target-version = "py310"` |
| C13 | MINOR | No `compose.yml`, so the loopback-pinned publishes (the security-relevant half of N12) depended on whoever typed `docker run` | `lab/compose.yml` committed: only `127.0.0.1:8081` + `127.0.0.1:5100`, 6653 deliberately unpublished |
| C14 | MINOR | `assert cookie & SESSION_MASK` cannot fail — `SESSION_MASK` contains `MAGIC_MASK`, so the test passed even with `SESSION=0` | Assert the field: `(cookie >> 32) & 0xFFFF == SESSION` |
| C15 | MINOR | REST layer had zero tests, though `flask_app.test_client()` needs no switch — three of the blockers lived in paths a test client walks | `TestRestSurface` + `TestAgentNamespaceGuard`; validation now runs before datapath resolution so schema errors are 400 regardless of switch presence |
| C16 | NIT | N29 confinement silently assumed the native hub *default* | `conftest.py` pins `OSKEN_HUB_TYPE=native` |
| C17 | NIT | B.3.4 still described the eventlet WSGI recipe that B.3.7 had already retired | B.3.4 rewritten; read/write call shapes documented so C2/C3/C6 cannot regress |
| C18 | NIT | Flow dump returned `str(match)`/`str(instructions)`; the Phase-4 eval metric would have to parse them | `ofctl_v1_3.match_to_str` (dict) + `actions_to_str` (list), plus packet/byte counters |
| C19 | NIT | `.gitignore` used `.idea/`/`.vscode/` (directory-only patterns) while the clone has them as files | Slash-less variants added; `.gitmodules` left tracked-or-deleted as an author decision |

**Still unverified by any test:** every OpenFlow-touching path. C1–C3 and C6 are reasoned from the os-ken source, not executed — Gate 1 in the lab container is the first real proof.

*End of plan. Reviewer: see Part F. Author: reply to every finding with accept/fix/reject-reason before implementation starts.*
