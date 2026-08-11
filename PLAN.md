# NetCopilot — Implementation Plan & Review Brief (v4)

**Project:** LLM-Powered Network Operations Agent for Software-Defined Networks
**Repo:** https://github.com/J-Suyash/netcopilot
**Author:** J-Suyash (4th-year CS, minor in networking)
**Document purpose:** (A) project context, (B) architecture & design, (C) implementation plan, (D) test/evaluation strategy, (E) risks, (F) review brief for independent agents, (G) review history & dispositions.

> **v4 changes (from technical review v3 — os-ken source-verified):** lab process/network wiring fixed (N12): controller + Mininet + Runner all inside the container, listeners bind `0.0.0.0` inside, only `127.0.0.1:5100` + `127.0.0.1:8081` published to host loopback; launcher recipe corrected (N13/N14/N15): `ofp_handler` in the app list, import-before-parse, `eventlet.wsgi.server` spawn, port-in-use preflight; read path via `os_ken.app.ofctl.api` (N16); `mark_dscp` action added (N18); both timeouts forbidden on drops (N19); session id in cookie bits (N20). See Part G.

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
- `proposal.md`, `PLAN.md`, `REVIEW.md` committed and pushed. No code yet.

### A.4 Environment (DECIDED; container basics verified 2026-08-12)
| Thing | Value |
|---|---|
| Dev box | CachyOS/Arch, host Python 3.14, host sudo blocked — irrelevant: **rootful Docker 29.7.2 + docker group verified** |
| Container capability | Verified: `--privileged` netlink ops OK; namespaces present |
| OVS | Host module present; **OVS *inside* the container = the one untested lab piece** (Gate 0) |
| Python toolchain | uv everywhere; `uv python install 3.11` verified rootless |
| Controller | **os-ken 4.2.1** — verified: installs/imports clean on 3.11; `AppManager.run_apps` usable (no wsgi dependency); `OFP_TCP_PORT=6653` is the default; `--observe-links` defaults **False**; `ofctl_v1_3.mod_flow_entry` default `cookie_mask=0` (the N2 footgun, confirmed at `ofctl_v1_3.py:1050`) |
| LLM access | OpenRouter + OpenCode Zen (OpenAI-compatible), keys in `.env`; Ollama fallback |

**Lab layout (N12 — one coherent wiring, decided):**
- **Inside the privileged container:** Mininet + os-ken controller (`manage.py`) + Lab Runner. Listeners bind **`0.0.0.0` inside** the container.
- **Container-internal:** switch→controller on `127.0.0.1:6653` (os-ken default — N17: keep the config knob, spend zero time on it).
- **Published to host loopback ONLY** (never bare `-p PORT:PORT` — that binds all host interfaces and would put a privileged command-exec endpoint on the LAN):
  - `127.0.0.1:5100` → Lab Runner
  - `127.0.0.1:8081` → controller REST surface
- **On the host (uv venv):** agent loop + safety layer + UI; talks to the container only via those two loopback-pinned ports.
- **`--network host` rejected** (veths/bridges would land in the host netns; manual `mn -c` cleanup after crashes). Keep as contingency ONLY if OVS-in-container fights in Gate 0.
- **Honesty note (N9):** `--privileged` + docker group = root-equivalent; the container is isolation convenience, not a security boundary. The Runner hardening is the real protection — precisely because it runs privileged.

**CI note:** GitHub Actions = Ubuntu, rootless unit tests only (Part D).

### A.5 Non-goals (do NOT build)
- No ONOS/ODL/P4/DPDK, no multi-controller clusters, no production claims.
- No LangChain/LlamaIndex — plain function-calling loop, explainable line-by-line.
- No LLM fine-tuning in v1 (O5 only if schedule allows).
- No real hardware, no internet-facing services; all published ports pinned to host loopback.
- No general flow-conflict solver (C.3 — exact + full-shadow on supported fields only).
- No vendored Ryu REST stack (Option A owns a 5-endpoint Flask surface instead).
- No `hard_timeout`/`idle_timeout` on drop flows (N19 — blocks are explicit until removed).

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
│     resolution vs live topology, NO timeouts on drops,    │
│     session limits)                                       │
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
│  │   cfg.CONF → AppManager.run_apps([ofp_handler, app,  │ │
│  │   topology.switches]))  ← N13/N14                    │ │
│  │ app.py     (os-ken app: topology/host via            │ │
│  │   os_ken.app.ofctl.api + ARP-snoop IP learning;      │ │
│  │   baseline L2 flows cookie=0 prio<100; flow-table-   │ │
│  │   full surfacing)                                    │ │
│  │ REST surface: Flask app via eventlet.wsgi.server     │ │
│  │   (hub.spawn) on 0.0.0.0:8081   ← N15               │ │
│  └───────────────┬─────────────────────────────────────┘ │
│                  │ OpenFlow 1.3 — 127.0.0.1:6653 (internal)│
│  ┌───────────────▼─────────────────────────────────────┐ │
│  │ Mininet (custom Topo; OVS in-container OR --switch   │ │
│  │ user)                                               │ │
│  │ Lab Runner (Flask, 0.0.0.0:5100): /verify, /iperf,  │ │
│  │   /link/down, /link/up — input validation + global  │ │
│  │   lock + list-arg exec (no shell interpolation)     │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### B.2 Data flow — sample intent: "block 10.0.0.5 from reaching the DB server 10.0.0.20"

1. User sends intent → agent loop.
2. LLM returns `tool_call: install_flow({match: {ipv4_src: 10.0.0.5, ipv4_dst: 10.0.0.20}, action: drop})`.
3. **Safety**: pydantic validates → guardrails (match not too broad; hosts resolve against live topology; no timeouts on drop) → conflict scan (bounded) → dry-run check.
4. Tool allocates cookie (see B.3.5), POSTs to `127.0.0.1:8081`; app builds `OFPFlowMod` (`ofctl_v1_3.to_match` + `mod_flow_entry`), sends via `datapath.send_msg()`.
5. Audit row written, keyed on cookie.
6. **Verification (N6 pre/post pair):** probe → reachable (baseline); install; probe → unreachable. "Blocked" claimed only on the reachable→unreachable transition; optional control pair rules out global failures.
7. UI renders each step.

**Verification semantics:** probe protocol matches the intent — ICMP for L3, TCP port probe for L4 (`tcp_dst`). Never report "verified" on a protocol the intent did not touch.

### B.3 Key design decisions
1. **Plain function-calling loop** — no agent framework; model-agnostic via OpenAI-compatible `tools` API.
2. **Safety as a hard gate** — LLM proposes, code disposes. Guardrails are Python, not prompts; allowlists from resolved controller topology, never LLM-echoed strings.
3. **Lab Runner as trust boundary** — validated inputs, list-arg exec, global lock.
4. **Controller transport = owned REST surface (N1/N16):** ~15-line `manage.py` launcher; topology reads via `os_ken.app.ofctl.api` (`get_datapath`, `send_msg` with `reply_cls`/`reply_multi`, backed by `OfctlService`); flow writes via `os_ken.lib.ofctl_v1_3` (`to_match` + `mod_flow_entry`); Flask app served via `eventlet.wsgi.server` under `hub.spawn` (never `flask_app.run()` inside an eventlet-monkey-patched process — N15). Five endpoints, owned end-to-end.
5. **Flow identity = cookie WITH session namespace (N2/N20):** 64-bit cookie = `magic:16 | session_id:16 | op_id:32`. Masks: single flow → `cookie=<full>, cookie_mask=0xFFFFFFFFFFFFFFFF`; per-session undo → `cookie=(magic<<48)|(session<<32), cookie_mask=0xFFFFFFFF00000000`; all-agent → `cookie=magic<<48, cookie_mask=0xFFFF000000000000`. **Never delete with mask 0** (default in `mod_flow_entry` — matches every cookie; would wipe baseline). Baseline flows: cookie 0, priority <100.
6. **Op-id counter reseeds on startup (N3)** from `max(op_id)` over installed agent flows (or audit tail) — prevents post-restart cookie collision.
7. **Launcher recipe (N13/N14/N15 — verified against os-ken source):**
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
    'os_ken.controller.ofp_handler',          # REQUIRED — opens the OF listener socket (N13)
    'netcopilot.controller.app',
    'os_ken.topology.switches',
])
```
   Plus, in the app's `start()`: `hub.spawn(eventlet.wsgi.server, eventlet.listen(('0.0.0.0', REST_PORT)), flask_app)`. Preflight: if port 6653 is already bound (stale process), exit with a clear error — most likely day-3 annoyance.
8. **Deterministic testing via MockLLM** — no network or API keys in unit/CI tests.
9. **Dry-run default ON** — destructive ops require confirmation when off.
10. **Drop flows: no timeouts, ever (N19)** — `idle_timeout` and `hard_timeout` both rejected when `action=drop` ("blocks are explicit until removed"). Removed only by explicit `remove_flow` → audit event.

### B.4 Tool schemas (JSON function definitions)
| Tool | Parameters (pydantic, extra=forbid) | Returns |
|---|---|---|
| `get_topology` | — | switches, links, hosts (MAC/IP/port) |
| `get_flows` | dpid? | flow table entries (cookie, priority, match, actions, counters) |
| `get_stats` | dpid, port? | byte/packet counters per port |
| `install_flow` | match: {eth_type?, ipv4_src?, ipv4_dst?, ip_proto?, tcp_dst?}, action: **drop\|output\|mark_dscp**, **dscp: int 0–63 (required iff mark_dscp)**, priority (100–200) | cookie, installed rule. **No timeouts accepted on drop** (N5+N19) |
| `remove_flow` | cookie (magic + session bits must match) | removed rule — delete with cookie+cookie_mask |
| `verify_connectivity` | src, dst, proto: icmp\|tcp, port? (tcp only), count=2 | reachable/unreachable + rtt (pre/post pairs, B.2) |
| `resolve_host` | name or IP | canonical host info or error |
| `fail_link` / `heal_link` | src_switch, dst_switch | link state change (demo/failure scenarios) |

### B.5 Repo layout (target)
```
sdn_ai_copilot/
├── proposal.md
├── PLAN.md                 # this file
├── REVIEW.md               # review records (v1–v3)
├── README.md               # final docs (P4)
├── pyproject.toml          # uv-managed
├── .env.example            # LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, DRY_RUN_DEFAULT,
│                           # CONTROLLER_PORT=6653, REST_PORT=8081, LAB_PORT=5100
├── .gitignore              # .env, .venv, __pycache__, audit logs
├── lab/
│   ├── Dockerfile          # privileged lab image: mininet + os-ken + runner
│   ├── compose.yml         # publishes ONLY 127.0.0.1:5100 + 127.0.0.1:8081
│   ├── topo.py             # Mininet Topo class (campus/leaf-spine)
│   └── runner.py           # Flask lab server (validated inputs, global lock)
├── netcopilot/
│   ├── __init__.py
│   ├── config.py           # env → dataclass config
│   ├── agent/
│   │   ├── loop.py         # function-calling loop
│   │   ├── llm.py          # LLMClient (OpenRouter/Zen/Ollama/Mock) + prompts.py
│   │   └── tools.py        # tool registry: schemas + handlers + cookie allocator
│   ├── safety/
│   │   ├── schema.py       # pydantic models per tool (strict, extra=forbid)
│   │   ├── guardrails.py   # LLM-independent policy checks (incl. no-timeouts-on-drop)
│   │   ├── conflicts.py    # BOUNDED overlap detection (C.3; mark_dscp-aware)
│   │   └── audit.py        # JSONL audit keyed on cookie + undo store
│   ├── controller/
│   │   ├── manage.py       # launcher (recipe B.3.7) — preflight port check
│   │   ├── app.py          # os-ken app: topology/host tracking, baseline flows,
│   │   │                   #   OFPFlowMod builder, flow-table-full surfacing
│   │   └── client.py       # HTTP client for REST (127.0.0.1:8081) + Runner (127.0.0.1:5100)
│   └── ui/
│       └── app.py          # Chainlit UI
├── tests/
│   ├── unit/               # schema, guardrails, conflicts, audit, cookie mgmt (no net)
│   ├── integration/        # requires lab container (tagged, local only)
│   └── eval_suite.py       # 20-intent gold set + metrics
└── scripts/
    ├── setup_env.sh        # uv 3.11 venv + deps (host, no root)
    ├── build_lab.sh        # build/start lab container
    └── spike_controller.sh # Gate 1: launcher socket check + cookie round-trip
```

---

## Part C — Implementation Plan

### Phase 0 — Environment bootstrap (days 1–2)
**Gate 0 (day 1):** OVS-in-container vs `--switch user` (container basics already verified). Build lab image; test `ovsdb-server`+`ovs-vswitchd` in-container; same gate tests `mn --topo linear,2 --switch user`. Either path green = lab green. (If OVS fights AND `--switch user` works, ship `--switch user`; only if both fail, revisit `--network host`.)

**Gate 1 (day 1–2, launcher-first — independent of Gate 0):**
1. `uv venv --python 3.11` + `uv pip install os-ken flask`.
2. Write `netcopilot/controller/manage.py` per B.3.7 recipe (import-before-parse, `ofp_handler` in app list, preflight port check).
3. **Launcher import-correctness test (no Mininet needed):** boot it; assert the OF listener socket on 6653 is actually open (this catches the N13 class of silent failure); assert `--observe-links` accepted.
4. **Cookie round-trip (needs Gate 0 switch):** send one `OFPFlowMod` with a cookie via `ofctl_v1_3`; dump flows back; cookie intact; baseline cookie-0 flow survives a masked delete.
→ One spike de-risks launcher + wiring + cookie semantics. Realistic: Gate 0 + launcher test day 1, cookie round-trip day 2.

**Exit:** OpenFlow 1.3 topology, cookie-correct install/delete/readback, launcher proven, wiring proven (host agent → published ports), commands documented.

### Phase 1 — Lab + controller plumbing (days 4–8)
1. `lab/topo.py` — campus topology: 2 core + 2 access switches, 4 hosts (web, db, client, dmz), IP plan 10.0.0.0/24.
2. `lab/runner.py` — hardened: `/verify/{src}/{dst}?proto=icmp|tcp&port=N`, `/iperf`, `/link/down`, `/link/up`; every src/dst validated against known host set OR `ipaddress.ip_address()`; list-arg exec only; global `threading.Lock`; binds `0.0.0.0:5100` inside container (published to `127.0.0.1:5100`).
3. `netcopilot/controller/manage.py` — launcher per recipe; port preflight; clean shutdown.
4. `netcopilot/controller/app.py` (os-ken) — topology/host tracking via `os_ken.app.ofctl.api` + ARP-snoop IP learning only (N10); baseline L2 flows cookie=0, prio<100; `OFPFMFC_TABLE_FULL` + error replies surfaced as structured tool errors; Flask REST (5 endpoints) served via `eventlet.wsgi.server` under `hub.spawn` on `0.0.0.0:8081`.
5. `netcopilot/controller/client.py` — typed wrappers over REST + Runner: `add_flow(cookie,...)`, `delete_flow(cookie,cookie_mask)`, `get_flows`, `get_topology`, `get_ports`, `verify`, `fail_link`; timeouts + controller-health check (controller-down failure mode).
6. `scripts/build_lab.sh`, `scripts/spike_controller.sh`.
**Exit:** curl can install/remove a flow (cookie + mask verified in flow dump) and verify a ping via Runner; host map populated; controller-down produces a clean tool error; host→container wiring proven via published loopback ports.

### Phase 2 — Agent loop (days 9–17)
**Tasks (TDD, MockLLM first):**
1. `config.py` + `.env.example`.
2. `agent/llm.py` — `LLMClient.chat(messages, tools)`; OpenRouter-compatible (covers Zen), Ollama, `MockLLM`.
3. `agent/tools.py` — registry + handlers; **cookie allocator**: `magic:16|session:16|op:32`, monotonic op under lock, **seeded from `max(op_id)` over installed agent flows at startup** (N3).
4. `agent/loop.py` — validate → guardrails → conflicts → execute → append → repeat (≤6 rounds, ≤2 validation retries); timeouts, error propagation.
5. `netcopilot/cli.py` — `python -m netcopilot "block 10.0.0.5 from 10.0.0.20"`.
**Exit:** mock-LLM e2e green (scripted install→verify asserts flow + cookie on switch); CLI works on 3 real scenarios with a real LLM.

### Phase 3 — Safety layer + UI (days 18–27)
1. `safety/schema.py` — pydantic, `extra=forbid`; validation errors fed back (bounded, 2 retries).
2. `safety/guardrails.py` — reject: wildcard/drop-all at high priority; unknown hosts/IPs (allowlist from resolved topology); removal of non-agent flows (cookie magic+session check); **any timeout on drop flows (N19)**; >50 flow changes/session; management-plane targets. Machine-readable reasons.
3. `safety/conflicts.py` — BOUNDED: (a) identical match + same priority + different action; (b) full shadow either direction on supported match fields only. **mark_dscp is a first-class action here: a marking flow shadowing a drop (or vice versa) is a genuine conflict.** General N-field intersection out of scope.
4. `safety/audit.py` — JSONL keyed on cookie: intent, tool call, validations, executed ops, pre/post verification; **undo = per-session cookie+mask delete** (other sessions' flows untouched — N20).
5. `ui/app.py` — Chainlit chat, tool-call cards, dry-run toggle, audit panel. Topology PNG = cuttable nice-to-have.
**Exit:** unit suite green (D); UI demos all 5 scenarios; safety cases all rejected.

### Phase 4 — Evaluation, docs, packaging (days 28–40)
1. `tests/eval_suite.py` — 20-intent gold set (5 × 4: security, QoS, observability, failure diagnosis, safety).
   - **QoS = `mark_dscp` flows** (N18 resolved): metric "correct flow installed with expected match/action/dscp", verified via flow dump — not throughput. iperf only in failure-diagnosis.
   - Metrics: op success rate, safety rejection rate (100% on safety cases), rounds, latency, token cost.
2. README: architecture, setup, demo script, interview talking points.
3. Demo video (30–60s) + GitHub release. 4. Report skeleton in `docs/`.
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

Key tests: guardrail table-driven (≥15, incl. prompt-injection intents; **timeouts-on-drop both kinds rejected**); conflict matrix (≥10, **incl. mark_dscp-vs-drop shadow case**); **undo-all leaves cookie-0 baseline intact**; **counter reseed after restart**; **per-session undo leaves other sessions' flows**; undo order-independence; dry-run blocks all writes; audit completeness; Runner injection strings rejected + serialization; flow-table-full surfaces as structured error; **launcher smoke: OF listener socket open (N13 regression)**.

---

## Part E — Risks & Open Questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| E.1 | OVS *inside* lab container doesn't run | Medium | Gate 0 day 1; `--switch user` fallback (bounded); `--network host` contingency only if both fail |
| E.2 | Launcher/wiring silent failures (ofp_handler, oslo.config order) | Retired by design | Recipe B.3.7 (source-verified); launcher socket smoke test; Gate 1 day 1–2 |
| E.3 | Small/free LLMs unreliable at tool-calling | Medium | Bounded retries + validation feedback; structured JSON fallback; cheap paid backup |
| E.4 | Scope creep (UI polish, PNG, queues, conflict solver, O5) | High | Non-goals (A.5); gated phases; PNG cuttable |
| E.5 | API cost | Low | MockLLM; small models; token budget logging |
| E.6 | Concurrent intents race on single Mininet | Medium | Runner global lock |
| E.7 | Verification claims that prove nothing | Low | Pre/post probe pair (B.2) |
| E.8 | Port-in-use from stale process (6653/8081/5100) | Medium | Preflight in `manage.py` + clear error (day-3 annoyance killer) |

Open questions: none blocking.

---

## Part F — REVIEW BRIEF (for the reviewing agent)

> **You are reviewing an implementation plan, not executing it.** Read `PLAN.md` (this file), `proposal.md`, and `REVIEW.md` (prior review records — check your findings against history so you don't re-flag fixed items). Do NOT modify files. Produce a review report to `REVIEW.md` (append) or return in full in your final message if you cannot write files. **Prefer empirical spikes over trusting docs — three prior reviews caught real bugs this way (Part G).**

### F.1 Context you need
- Author: 4th-year CS student, **8 weeks**, dev box = CachyOS/Arch, host sudo blocked but rootful Docker + docker group verified; host Python 3.14, uv provides 3.11; no GPU; minimal API budget. Lab = privileged container (Mininet + os-ken + Runner inside; only `127.0.0.1:5100` + `127.0.0.1:8081` published). CI = GitHub Actions (Ubuntu, rootless units only).
- Differentiator = safety layer + demo quality; core loop (NL intent → validated OpenFlow rule on live emulated network) must work by end of Phase 2 (~day 17).
- This is v4; three prior reviews' findings are folded in (Part G). Review the CURRENT state.

### F.2 Review dimensions — check ALL explicitly
1. **Feasibility & environment**: OVS-in-container vs `--switch user`; os-ken 4.2.1 on py3.11; launcher recipe (import-before-parse, `ofp_handler` required, `--observe-links` default False — verify against source); port wiring (container-internal 6653, loopback-pinned publishes 5100/8081); cookie+cookie_mask semantics (mask 0 = match-everything); uv-on-Arch without root.
2. **Architecture correctness**: B.2 with owned-REST design? Safety bypass via (a) prompt-injected intent, (b) hallucinating LLM, (c) tool-output poisoning? Cookie identity sound (magic/session/op bits, mask discipline, reseed)? Runner trust boundary complete? Pre/post probe pair sufficient?
3. **Completeness**: missing tools/endpoints/failure modes (controller-down ✅, flow-table-full ✅, LLM timeout, Mininet host crash, port-in-use ✅ E.8, concurrency ✅)? Eval suite measurable (QoS via mark_dscp ✅)?
4. **Scope control**: silent 2-week eaters? Non-goals sufficient? Anything cut/deferred?
5. **Testability**: MockLLM sound? CI rootless-clean? Exit criteria objectively checkable? Launcher smoke test present?
6. **Security**: `.env` gitignored ✅; loopback-pinned publishes ✅ (never bare `-p`); privileged container honestly framed ✅; prompt-injection defensible end-to-end?
7. **Sequencing**: hardest unknowns first (Gates 0/1 day 1–2)? Gate 1 split (launcher test independent of Gate 0) correct?

### F.3 What to keep in mind
- **Correctness of claims > politeness**: verify or flag "verify" — three prior reviews caught real bugs by testing (Part G).
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

### v1 → v2 → v3 (all accepted; v1/v2 dispositions superseded by later fixes)
- v1: Ryu→os-ken (BLOCKER), cookie identity, Runner injection, bounded conflicts, port config, lock, protocol-matched verify, spike day 1, env decision.
- v2: os-ken packaging gap → Option A (BLOCKER), cookie_mask discipline, op-id reseed, launcher gap, idle_timeout+drop, pre/post probe, QoS metric, Gate 0 retarget, honest container framing, lean on topology module, eventlet compat retired.

### Review v3 (external agent, os-ken source-verified): GO / APPROVE WITH CHANGES — all 9 findings ACCEPTED into v4:

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| N12 | MAJOR | Host/container wiring broken 3 ways (controller-on-host unreachable; Runner loopback inside container unpublished; 8080 nothing to publish) | Controller + Mininet + Runner all inside container; listeners `0.0.0.0` inside; publish only `127.0.0.1:5100` + `127.0.0.1:8081` (never bare `-p`); `--network host` rejected (netns pollution) |
| N13 | MAJOR | `ofp_handler` omitted from app list → no OF listener socket, silent no-handshake | Added to `run_apps` list (B.3.7); launcher smoke test asserts socket open (D) |
| N14 | MAJOR | oslo.config: opts registered in `controller.py`/`topology/switches.py`, NOT `flags.py` → parse-before-import = unrecognized arguments | Import-before-parse recipe (B.3.7), exactly as ryu-manager did |
| N15 | MINOR | `flask_app.run()` in eventlet-monkey-patched process = hang hazard | Serve via `hub.spawn(eventlet.wsgi.server, eventlet.listen(...), flask_app)` in app `start()` (B.3.7) |
| N16 | MINOR | Read path needs the `waiters` multipart-reply machinery (`ofctl_v1_3.get_flow_stats` requires correlation dict) | Use shipped `os_ken.app.ofctl.api` (`get_datapath`, `send_msg(reply_cls, reply_multi=True)`, `OfctlService`) for reads; `to_match`+`mod_flow_entry` for writes |
| N17 | NIT | 6653 already os-ken default | Keep knob, zero effort; removed from risk framing |
| N18 | MINOR | QoS category unexecutable (no `mark_dscp` action in schema) | `action: mark_dscp` + `dscp: 0–63` added (B.4); conflict semantics mark_dscp-aware (C.3) |
| N19 | NIT | `hard_timeout` has the same silent-unblock property as `idle_timeout` | Both timeouts forbidden on drop flows (A.5, B.3.10, C.3.2); blocks explicit until removed |
| N20 | NIT | All-agent mask delete nukes other sessions' flows | Cookie = `magic:16|session:16|op:32`; per-session undo mask (B.3.5); test (D) |

**Reviewer's questions → decisions:** Q1 controller inside container (Q2: yes, agent uses published `127.0.0.1:8081` REST + `127.0.0.1:5100` Runner; nothing else published); Q3 `mark_dscp` added, 20 gold intents kept; Q4 both timeouts forbidden on drops.

*End of plan. Reviewer: see Part F. Author: reply to every finding with accept/fix/reject-reason before implementation starts.*
