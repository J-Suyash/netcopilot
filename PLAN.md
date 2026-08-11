# NetCopilot — Implementation Plan & Review Brief (v3)

**Project:** LLM-Powered Network Operations Agent for Software-Defined Networks
**Repo:** https://github.com/J-Suyash/netcopilot
**Author:** J-Suyash (4th-year CS, minor in networking)
**Document purpose:** (A) project context, (B) architecture & design, (C) implementation plan, (D) test/evaluation strategy, (E) risks, (F) review brief for independent agents, (G) review history & dispositions.

> **v3 changes (from technical review v2 — empirical):** controller transport = **own the REST layer in-process** (Option A; `ofctl_rest`/`rest_topology`/`osken-manager` confirmed absent from os-ken both in the 4.2.1 wheel AND in upstream git — nothing to vendor); custom ~15-line launcher `manage.py`; cookie delete **always with `cookie_mask`**; cookie op-id counter **seeded on restart**; `idle_timeout` forbidden on drop flows; verification = pre-probe/post-probe pair; Gate 0 container basics **retired by test** (passes on dev box); QoS metric redefined. See Part G.

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
- `proposal.md`, `PLAN.md` committed and pushed. No code yet.

### A.4 Environment (DECIDED — dev on the local machine; Gates 0 partially verified 2026-08-12)
| Thing | Value |
|---|---|
| Dev box | User's local machine: CachyOS/Arch (`pacman`), host Python **3.14** |
| Privileges | Host sudo **blocked** (`no_new_privs`) — irrelevant for the lab: **rootful Docker 29.7.2 works, user is in the `docker` group** (verified) |
| Container capability | **Verified:** `docker run --privileged` can create netlink devices (`ip link add dummy0`) — Mininet's namespace needs are satisfied |
| OVS kernel module | Present on host ✅; **OVS *inside* the container is the one unverified piece** (Gate 0 retarget, below) |
| Hermes/Ubuntu VM | NOT the dev target. Repo mirror only. |
| Python toolchain | **uv** everywhere; `uv python install 3.11` verified working without root |
| Controller | **os-ken 4.2.1** (installs clean on 3.11, imports clean; no REST apps, no manager binary — see B.3/C.1) |
| LLM access | OpenRouter + OpenCode Zen (OpenAI-compatible), keys in `.env`; Ollama fallback |

**Lab execution strategy (verified, no fallback needed):**
- Rootful Docker, privileged lab container running Mininet (host repo mounted rw). Ports published: 6653 (OpenFlow), 5100 (Lab Runner), 8080 (agent/UI).
- **Honesty note (N9):** a `--privileged` container is *not* a security boundary, and `docker` group membership is root-equivalent on the host. This is an isolation convenience for a student lab, not containment. The Lab Runner hardening (C.1) is what actually protects the machine — precisely because the Runner runs privileged.
- **Gate 0 (day 1, retargeted):** the only remaining lab unknown is OVS *inside* the container — `ovsdb-server` + `ovs-vswitchd` in the container (kernel datapath is host-global) vs. Mininet `--switch user` fallback. Test both; `--switch user` is slower but fully sufficient for a 4-host demo.

**CI note:** GitHub Actions runners are Ubuntu; unit + mock-loop tests must pass there without root or Mininet (Part D).

### A.5 Non-goals (do NOT build)
- No ONOS/ODL/P4/DPDK, no multi-controller clusters, no production claims.
- No LangChain/LlamaIndex — a plain function-calling loop we can explain line-by-line.
- No LLM fine-tuning in v1 (O5 only if schedule allows).
- No real hardware, no internet-facing services (everything binds localhost / container-internal).
- **No general flow-conflict solver** (C.3 — bounded to exact-match + full-shadow on supported fields).
- **No vendored Ryu REST stack** (Option B rejected: ~2000 lines of foreign code + WSGI framework to maintain; we own a 5-endpoint Flask surface instead).

---

## Part B — Architecture & Design

### B.1 Component diagram

```
┌──────────────────────────────────────────────────────────┐
│  Chat UI (Chainlit) — intent in, explainable steps out    │
│  + dry-run toggle + audit panel                          │
│  (topology PNG render = NICE-TO-HAVE, cuttable if P3 slips)│
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Agent Loop (plain Python, OpenAI-compatible tools API)   │
│  system prompt → model → tool_calls → execute → repeat     │
│  max 6 rounds, bounded retries (2) on validation errors    │
└──────────────────────────┬───────────────────────────────┘
                           │ tool calls (JSON)
┌──────────────────────────▼───────────────────────────────┐
│  Tool Layer (8 tools, see B.4) + cookie allocator         │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  SAFETY LAYER (LLM-independent, in-process, mandatory)    │
│  1. pydantic schema validation (strict, extra=forbid)     │
│  2. guardrails (broad-match drops, protected flows,       │
│     name resolution vs. live topology, idle_timeout+drop  │
│     rejection, session limits)                            │
│  3. conflict detection (BOUNDED: exact + full-shadow)     │
│  4. dry-run mode (default ON in UI)                       │
│  5. audit log JSONL keyed on cookie + undo by cookie+mask │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP (localhost: 8080 agent / 5100 lab)
┌──────────────────────────▼───────────────────────────────┐
│  Controller process (host, uv venv) — netcopilot own stack │
│  netcopilot/controller/manage.py   (~15-line launcher:     │
│    cfg.CONF + AppManager.run_apps([...]))                  │
│  netcopilot/controller/app.py      (os-ken app: host/      │
│    topology tracking via os_ken.topology.api + ARP-snoop   │
│    IP learning; baseline L2 flows cookie=0, prio <100)     │
│  embedded Flask REST (5 endpoints, see C.1.4)              │
└──────────────────────────┬───────────────────────────────┘
                           │ OpenFlow 1.3 (port CONTROLLER_PORT=6653, configurable)
┌──────────────────────────▼───────────────────────────────┐
│  LAB (privileged Docker container):                        │
│  Mininet (custom Topo; OVS in-container OR --switch user)  │
│  + Lab Runner (Flask on 127.0.0.1:5100):                  │
│    /verify/{src}/{dst}?proto=icmp|tcp&port=N              │
│    /iperf/{src}/{dst} · /link/down/{a}/{b} · /link/up      │
│    ★ input validation + global request lock + list-arg     │
│      exec (no shell interpolation)                         │
└───────────────────────────────────────────────────────────┘
```

### B.2 Data flow — sample intent: "block 10.0.0.5 from reaching the DB server 10.0.0.20"

1. User sends intent → agent loop.
2. LLM returns `tool_call: install_flow({match: {ipv4_src: 10.0.0.5, ipv4_dst: 10.0.0.20}, action: drop})`.
3. **Safety**: pydantic validates → guardrails (match not too broad; hosts resolve against live topology; no `idle_timeout` on drop) → conflict scan (bounded) → dry-run check.
4. Tool allocates `cookie = COOKIE_MAGIC | op_id` (magic = high 32 bits, monotonic op id = low 32 bits), POSTs to the controller's Flask surface; app builds `OFPFlowMod` (via `os_ken.lib.ofctl_v1_3`/`parser`), sends `datapath.send_msg()`.
5. Audit row written, keyed on cookie.
6. **Verification (N6 — pre/post probe pair):** agent first probes `verify_connectivity(10.0.0.5, 10.0.0.20, proto=icmp)` → **reachable (baseline)**; installs the drop; probes again → **unreachable**. Both results are logged; "blocked" is only claimed on the reachable→unreachable transition. A control pair (a host pair untouched by the intent) may be used to rule out global failures.
7. UI renders each step.

**Verification semantics:** probe protocol must match the intent — ICMP ping for L3 blocks, TCP port probe (`nc` via Runner) for L4 (`tcp_dst`). Never report "verified" on a protocol the intent did not touch.

### B.3 Key design decisions
1. **Plain function-calling loop** — no agent framework. One file, ~120 lines, explainable in interviews. Model-agnostic via OpenAI-compatible `/chat/completions` with `tools`.
2. **Safety as a hard gate** — the LLM *proposes*, the safety layer *disposes*. Guardrails are plain Python, not prompts. Injection-resistant at the core: worst case the LLM proposes evil, code rejects it; allowlists are built from resolved controller topology, never from strings the LLM echoed.
3. **Lab Runner as trust boundary** — validates every input against the known host set / `ipaddress`, executes with list args (never shell strings), global lock serializes access to the single Mininet process.
4. **Controller transport = owned REST surface (N1, Option A).** os-ken ships no REST apps and no manager binary (verified: absent from the 4.2.1 wheel AND from upstream git — nothing to vendor). Instead: ~15-line `manage.py` launcher boots our os-ken app via `AppManager.run_apps`; topology read via `os_ken.topology.api.get_all_switch/get_all_link/get_all_host` in-process; flow CRUD built with `os_ken.lib.ofctl_v1_3`/`parser.OFPFlowMod` + `datapath.send_msg()`; one embedded Flask surface (already a dep for the Runner) exposes exactly the 5 endpoints the tools need. Net LOC goes *down* vs. vendoring; we own cookie handling end-to-end.
5. **Flow identity = OpenFlow cookie WITH mask (N2).** Cookie = magic high bits (agent-installed, survives restarts) + op id (low bits). **Delete always sends `cookie_mask`**: single flow `cookie=<full>, cookie_mask=0xFFFFFFFFFFFFFFFF`; all agent flows `cookie=MAGIC<<32, cookie_mask=0xFFFFFFFF00000000`. Never delete with mask 0 (OF1.3 semantics: mask 0 matches *every* cookie → would wipe baseline flows). Baseline L2 flows are pinned to cookie 0 + priority <100.
6. **Cookie op-id counter reseeds on startup (N3)** — from `max(op_id)` over installed agent flows (queried at boot) or the audit JSONL tail. Prevents collision after restart (which would make undo delete the wrong flow).
7. **Launcher is in-repo** — `netcopilot/controller/manage.py` (app code, not lab-image config). Must pass `--observe-links` (default **False** in `os_ken.topology.switches` — without it host/link APIs return empty) and `--ofp-tcp-listen-port`; verify exact opt plumbing against `os_ken/flags.py` in Gate 1.
8. **Deterministic testing via MockLLM** — loop tests use scripted tool-call sequences; no network or API keys in unit/CI tests.
9. **Dry-run default ON** — destructive ops (remove_flow, fail_link) additionally require confirmation when dry-run is off.

### B.4 Tool schemas (JSON function definitions)
| Tool | Parameters (validated by pydantic, extra=forbid) | Returns |
|---|---|---|
| `get_topology` | — | switches, links, hosts (MAC/IP/port) |
| `get_flows` | dpid? | flow table entries (cookie, priority, match, actions, counters) |
| `get_stats` | dpid, port? | byte/packet counters per port |
| `install_flow` | match: {eth_type?, ipv4_src?, ipv4_dst?, ip_proto?, tcp_dst?}, action: drop\|output, priority (100–200), hard_timeout? | cookie, installed rule. **`idle_timeout` rejected for `action=drop`** (N5: a drop flow that idles out silently un-blocks traffic) |
| `remove_flow` | cookie (must match MAGIC bits) | removed rule — delete sent **with cookie+cookie_mask** |
| `verify_connectivity` | src (host name or IP), dst, proto: icmp\|tcp, port? (tcp only), count=2 | reachable/unreachable + rtt (used in pre/post pairs, B.2) |
| `resolve_host` | name or IP | canonical host info or error |
| `fail_link` / `heal_link` | src_switch, dst_switch | link state change (demo/failure scenarios) |

### B.5 Repo layout (target)
```
sdn_ai_copilot/
├── proposal.md
├── PLAN.md                 # this file
├── README.md               # final docs (P4)
├── pyproject.toml          # uv-managed
├── .env.example            # LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, DRY_RUN_DEFAULT,
│                           # CONTROLLER_PORT=6653, LAB_PORT=5100, AGENT_PORT=8080
├── .gitignore              # .env, .venv, __pycache__, audit logs
├── lab/
│   ├── Dockerfile          # privileged lab image: mininet (+ OVS or user-switch)
│   ├── compose.yml         # lab container, repo mounted, ports 6653/5100
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
│   │   ├── guardrails.py   # LLM-independent policy checks (incl. idle_timeout+drop)
│   │   ├── conflicts.py    # BOUNDED overlap detection (see C.3)
│   │   └── audit.py        # JSONL audit keyed on cookie + undo store
│   ├── controller/
│   │   ├── manage.py       # launcher: cfg.CONF + AppManager.run_apps (--observe-links)
│   │   ├── app.py          # os-ken app: topology/host tracking, baseline flows,
│   │   │                   #   OFPFlowMod builder, flow-table-full surfacing
│   │   └── client.py       # HTTP client for OUR Flask REST surface (5 endpoints)
│   └── ui/
│       └── app.py          # Chainlit UI
├── tests/
│   ├── unit/               # schema, guardrails, conflicts, audit, cookie mgmt (no net)
│   ├── integration/        # requires lab container (tagged, local only)
│   └── eval_suite.py       # 20-intent gold set + metrics
└── scripts/
    ├── setup_env.sh        # uv 3.11 venv + deps (host, no root needed)
    ├── build_lab.sh        # build/start lab container
    └── spike_controller.sh # Phase 0 Gate 1: launcher + flow-mod-cookie roundtrip
```

---

## Part C — Implementation Plan

### Phase 0 — Environment bootstrap (days 1–3)
**Gate 0 (day 1, retargeted — container basics already verified 2026-08-12):**
1. Docker/privileged/netlink: ✅ DONE (docker 29.7.2, docker group, `--privileged` netlink ops OK).
2. **Remaining lab unknown: OVS in-container.** Build lab image; test `ovsdb-server` + `ovs-vswitchd` inside (kernel datapath is host-global — pass through or fall back), and in the same gate test `mn --topo linear,2 --switch user`. `--switch user` needs no OVS at all and is fully sufficient for a 4-host demo — if either path works, the lab is green.

**Gate 1 (day 1, rewritten around N1/N4 — the controller spike):**
1. `uv venv --python 3.11` + `uv pip install os-ken flask` (os-ken 4.2.1 installs/imports clean on 3.11 — verified).
2. Write minimal `netcopilot/controller/manage.py` (~15 lines): `cfg.CONF([...])` (incl. `--ofp-tcp-listen-port 6653`, `--observe-links`) then `AppManager.run_apps(['netcopilot.controller.app', 'os_ken.topology.switches'])`. Verify exact opt names against `os_ken/flags.py`.
3. Boot a trivial app against the lab's Mininet; send **one `OFPFlowMod` with a cookie** (`parser`/`os_ken.lib.ofctl_v1_3`); dump flows back and confirm **the cookie round-trips intact**.
4. Same spike exercises: baseline flow with cookie 0, delete with `cookie_mask=0xFFFFFFFFFFFFFFFF`, confirm baseline survives.
→ This single spike de-risks the controller, the launcher, and cookie semantics together. **If it green-lights, the plan holds.**

**Exit criteria:** OpenFlow 1.3 topology with cookie-correct flow install/delete/readback working; launcher proven; lab strategy confirmed; commands documented in README.

### Phase 1 — Lab + controller plumbing (days 4–8)
1. `lab/topo.py` — campus topology: 2 core + 2 access switches, 4 hosts (web, db, client, dmz), IP plan 10.0.0.0/24.
2. `lab/runner.py` — **hardened**: `/verify/{src}/{dst}?proto=icmp|tcp&port=N`, `/iperf`, `/link/down`, `/link/up`; every `src`/`dst` validated against known host set OR `ipaddress.ip_address()`; host commands as **argument lists** (`h.cmd(["ping","-c",str(n),dst])` — no f-string shell); **global `threading.Lock`**; binds 127.0.0.1:5100.
3. `netcopilot/controller/manage.py` — launcher (proven in Gate 1, polished: config, logging, clean shutdown).
4. `netcopilot/controller/app.py` (os-ken) — host tracking via `os_ken.topology.api` + **ARP-snoop IP learning only** (N10: the topology module already tracks MAC/port — don't rebuild it); baseline L2 flows **cookie=0, priority <100**; **`OFPFMFC_TABLE_FULL` and other error replies surfaced as structured tool errors** (never swallowed).
5. `netcopilot/controller/client.py` — typed wrappers over our Flask REST: `add_flow(cookie,...)`, `delete_flow(cookie,cookie_mask)`, `get_flows`, `get_topology`, `get_ports`; timeouts + controller-health check (controller-down failure mode).
6. `scripts/build_lab.sh`, `scripts/spike_controller.sh`.
**Exit:** curl can install/remove a flow (cookie + mask verified in flow dump) and verify a ping via the Runner; host map populated; controller-down produces a clean tool error.

### Phase 2 — Agent loop (days 9–17)
**Tasks (TDD, MockLLM first):**
1. `config.py` + `.env.example` (provider, base_url, key, model, dry-run default, ports).
2. `agent/llm.py` — `LLMClient.chat(messages, tools) -> response`; OpenRouter-compatible (also covers Zen), Ollama, `MockLLM`.
3. `agent/tools.py` — registry: JSON schema per tool + handlers calling controller/lab clients; **cookie allocator**: monotonic op id under lock, **seeded at startup from `max(op_id)` over installed agent flows** (query flows at boot; fall back to audit JSONL tail) — N3.
4. `agent/loop.py` — loop: system prompt → model → tool_calls → validate → guardrails → conflicts → execute → append result → repeat (≤6 rounds, ≤2 validation retries) → final message; timeouts, error propagation.
5. `netcopilot/cli.py` — `python -m netcopilot "block 10.0.0.5 from 10.0.0.20"`.
**Exit:** mock-LLM e2e test green (scripted install→verify sequence asserts the flow exists with the right cookie); CLI works on 3 real scenarios with a real LLM.

### Phase 3 — Safety layer + UI (days 18–27)
1. `safety/schema.py` — pydantic per tool, `extra=forbid`; validation errors fed back to LLM (bounded, 2 retries).
2. `safety/guardrails.py` — reject: wildcard/drop-all at high priority; unknown hosts/IPs (allowlist from **resolved controller topology**, never LLM-echoed strings); removal of non-agent flows (cookie magic check); **`idle_timeout` on drop flows (N5)**; >50 flow changes/session; management-plane targets. Machine-readable rejection reasons.
3. `safety/conflicts.py` — **BOUNDED**: (a) identical match + same priority + different action; (b) new flow fully shadows / is fully shadowed by an existing flow **on supported match fields only** (the 5 in `install_flow`). OUT OF SCOPE: general N-field geometric intersection. Structured conflict report back to the LLM.
4. `safety/audit.py` — JSONL per operation keyed on cookie: intent, tool call, validation results, executed ops, verification (pre/post probes); **undo = delete by cookie+mask** (order-independent; full mask for single flow, MAGIC mask for all-agent).
5. `ui/app.py` — Chainlit chat, tool-call cards, dry-run toggle, audit panel. **Topology PNG = nice-to-have** (cuttable if Phase 3 slips — N/Q4).
**Exit:** unit suite green (Part D); UI demonstrates all 5 demo scenarios; safety cases all rejected.

### Phase 4 — Evaluation, docs, packaging (days 28–40)
1. `tests/eval_suite.py` — 20-intent gold set (5 categories × 4: security, QoS, observability, failure diagnosis, safety) with expected operations.
   - **QoS category (N7/Q5): 4 intents = DSCP-marking flows** (install flow with `set_field ip_dscp` + match). **Stated metric: "correct flow installed with expected match/action", verified via flow dump** — NOT throughput (iperf measures nothing about QoS without queues). iperf is used only in failure-diagnosis scenarios.
   - Metrics: op success rate, safety rejection rate (100% on safety cases), rounds, latency, token cost.
2. README: architecture diagram, setup, demo script, interview talking points.
3. Demo video (30–60s) + GitHub release.
4. Report skeleton (thesis format) in `docs/`.
**Exit:** O1–O4 met; repo public and polished.

### Optional Phase 5 — Research extension (only if Phases 0–4 done early)
Fine-tune Qwen2.5-3B (LoRA) on IBNBench intent→flow-rule pairs; compare accuracy/cost/latency vs. API zero-shot models using the same eval suite → conference paper draft.

---

## Part D — Testing & Evaluation Strategy

| Layer | Tooling | Requires | Runs in CI |
|---|---|---|---|
| Unit (schema, guardrails, conflicts, audit, cookie mgmt, config) | pytest | nothing | ✅ |
| Loop e2e with MockLLM | pytest + fake controller client | nothing | ✅ |
| Integration (real Mininet + os-ken in lab container) | pytest, tagged `integration` | lab container running | ❌ (local) |
| Real-LLM smoke + eval suite | script + CSV output | API key (.env) | ❌ (manual) |
| Lint | ruff | — | ✅ |

Key tests (incl. reviewer-mandated additions):
- Guardrail table-driven cases (≥15, incl. prompt-injection attempts as intents; **incl. `idle_timeout`+drop rejected — N5**)
- Conflict matrix (≥10: exact, full-shadow both directions, non-overlapping)
- **Undo-all leaves cookie-0 baseline flows intact (N2)** — the `cookie_mask` regression test
- **Counter reseed after simulated restart (N3)** — old flows' cookies don't collide with new
- Undo correctness (order-independent), dry-run blocks all writes, audit completeness
- Runner input validation (injection strings rejected), Runner serialization (parallel requests)
- Flow-table-full error surfaces as a structured tool error (not swallowed)

---

## Part E — Risks & Open Questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| E.1 | OVS *inside* lab container doesn't run (kernel datapath host-global) | Medium | Gate 0 day 1; `--switch user` fallback (bounded: slower switching, fine for 4-host demo) — container basics already verified |
| E.2 | os-ken packaging gap (no REST apps/manager) — **RESOLVED by design** (Option A: owned launcher + Flask surface) | Retired | Gate 1 day 1 spike proves launcher + cookie round-trip; os-ken 4.2.1 on py3.11 verified import-clean (eventlet 0.41.1, deprecation warning only) |
| E.3 | Small/free LLMs unreliable at tool-calling | Medium | Bounded retries + validation feedback; structured JSON fallback; cheap paid models as backup |
| E.4 | Scope creep (UI polish, topology PNG, OVS queues, general conflict solver, O5) | High | Explicit non-goals (A.5); phases gated by exit criteria; conflicts bounded; PNG marked cuttable |
| E.5 | API cost during dev | Low | MockLLM for tests; small models for dev; token budget logging |
| E.6 | Concurrent eval intents race on single Mininet process | Medium | Runner global lock (C.1) |
| E.7 | Verification claims that prove nothing (unreachable ≠ blocked) | Low | Pre/post probe pair mandatory in tool flow (B.2, N6) |

Open questions: none blocking. Topology size (default 2+2/4 hosts); QoS metric settled (DSCP flow present, N7).

---

## Part F — REVIEW BRIEF (for the reviewing agent)

> **You are reviewing an implementation plan, not executing it.** Read `PLAN.md` (this file) and `proposal.md` in the repo root. Do NOT modify any files. Produce a review report and write it to `REVIEW.md` in the repo root (or return it in full in your final message if you cannot write files). **Prefer empirical spikes over trusting docs — two prior reviews caught real issues this way (Part G).**

### F.1 Context you need
- Author: 4th-year CS student (minor: networking), **8 weeks**, dev box = CachyOS/Arch, host sudo blocked but **rootful Docker + docker group verified working**; host Python 3.14, uv provides 3.11 venvs; no GPU; minimal API budget. Mininet lab runs in a privileged container (A.4). CI = GitHub Actions (Ubuntu, rootless unit tests only).
- Differentiator = safety layer + demo quality; core loop (NL intent → validated OpenFlow rule on a live emulated network) must work by end of Phase 2 (~day 17).
- This is v3: two prior reviews' findings are folded in (Part G). Review the CURRENT state.

### F.2 Review dimensions — check ALL explicitly
1. **Feasibility & environment**: Would anything fail on the stated environment? Sanity-check: OVS-in-container vs `--switch user`, os-ken 4.2.1 on py3.11, the `manage.py` launcher approach (`cfg.CONF` + `AppManager.run_apps`, `--observe-links` default False — verify against `os_ken/flags.py`), OpenFlow 1.3 port 6653 on both sides, cookie+cookie_mask semantics (delete with mask 0 = match-every-cookie), uv-on-Arch without root.
2. **Architecture correctness**: Does B.2 work with the owned-REST design (B.3.4)? Can the safety layer be bypassed by (a) prompt-injected intent, (b) hallucinating LLM emitting valid-JSON-but-evil tool calls, (c) attacker-controlled strings in tool output fed back into prompts? Is cookie-as-identity sound (magic bits, mask discipline, restart reseed)? Is the Runner trust boundary complete (validation, list-arg exec, lock)? Is the pre/post probe pair sufficient (control pair, protocol matching)?
3. **Completeness**: Missing tools, endpoints, failure modes (controller down ✅, flow-table-full ✅, LLM timeout, Mininet host crash, port-in-use 6653/5100/8080, concurrent sessions ✅ lock)? Anything in the eval suite unmeasurable?
4. **Scope control**: Any task that will silently eat >2 weeks? Are non-goals (A.5) sufficient? Should anything be cut or deferred?
5. **Testability**: MockLLM strategy sound? CI unit suite passable without root/Mininet? Exit criteria objectively checkable?
6. **Security**: `.env` gitignored ✅; Runner localhost + validated inputs ✅; privileged container honestly framed as non-boundary (N9) ✅; prompt-injection surface defensible end-to-end?
7. **Sequencing**: Are phases ordered to de-risk the hardest unknowns first (Gates 0/1 day 1)? Anything blocking that should move earlier/later?

### F.3 What to keep in mind while reviewing
- **Correctness of claims > politeness**: verify (docs/web/spike) or flag as "verify" — do not assume the plan is right. Two prior reviews caught real bugs by testing.
- **Proportion**: 8-week UG project. Reject over-engineering as much as under-specification.
- **Constraints**: no GPU, minimal spend, no host root, explainable code matters more than clever code.
- **Prioritize**: rank findings by threat to the "core loop works by day 17" milestone.
- **Verdict options**: APPROVE / APPROVE WITH CHANGES / MAJOR REVISION.

### F.4 Required output format
```
VERDICT: <APPROVE | APPROVE WITH CHANGES | MAJOR REVISION>

FINDINGS:
ID | SEVERITY (BLOCKER/MAJOR/MINOR/NIT) | LOCATION (section/task) | ISSUE | SUGGESTED FIX

ANSWERS TO REVIEW DIMENSIONS (F.2 items 1–7, one short paragraph each)

TOP 5 RISKS (ranked, with why)

QUESTIONS FOR THE AUTHOR (anything you could not determine)
```
Severity guide: BLOCKER = plan cannot succeed as written; MAJOR = should fix before/early in implementation; MINOR = fix during implementation; NIT = style/optional.

---

## Part G — Review History & Dispositions

### Review v1 (external agent): GO / APPROVE WITH CHANGES — all findings accepted (see v2-era history; superseded by v2/v3 fixes listed below).

### Review v2 (external agent, empirical spikes): GO / APPROVE WITH CHANGES — all 11 findings ACCEPTED and incorporated in v3:

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| N1 | BLOCKER | os-ken ships no `ofctl_rest`/`rest_topology`/`wsgi`/`osken-manager` (verified: wheel AND upstream git — nothing to vendor) | **Option A adopted** (owned launcher + in-process topology API + Flask REST, 5 endpoints); Option B (vendor Ryu REST) rejected; B.1/B.5/C.1 rewritten |
| N2 | MAJOR | Delete-by-cookie without `cookie_mask` = mask-0 matches every cookie → wipes baseline | Always send mask; full mask for single flow, MAGIC mask for all-agent; regression test (D) |
| N3 | MAJOR | In-memory op-id counter resets on restart → cookie collision | Seed from `max(op_id)` over installed agent flows (or audit tail) at startup; test (D) |
| N4 | MAJOR | No launcher without `osken-manager`; `--observe-links` defaults False → empty topology APIs | In-repo `manage.py` (15 lines, `cfg.CONF` + `AppManager.run_apps`), verified in Gate 1 |
| N5 | MINOR | `idle_timeout` on drop flow → block silently lifts | Guardrail rejects `idle_timeout` when `action=drop`; `hard_timeout` only; test (D) |
| N6 | MINOR | "unreachable" ≠ "blocked" (unlearned flow, dead host, race) | Pre/post probe pair; control pair optional; protocol-matched probing (B.2) |
| N7 | MINOR | QoS intents unmeasurable without queues | Metric = "correct DSCP-marking flow installed" (flow dump); iperf only for failure diagnosis (C.4) |
| N8 | MINOR | Fallback B (SSH lab) dead weight — Gate 0 verified | Deleted; Gate 0 retargeted to OVS-in-container vs `--switch user` |
| N9 | MINOR | Plan implied privileged container = security boundary | Honest framing added (A.4): isolation convenience, not containment; Runner hardening is the real protection |
| N10 | NIT | Rebuilding host tracking that `os_ken.topology.switches` already does | Lean on `topology/api.py`; add only ARP-snoop IP learning |
| N11 | NIT | E.2 (eventlet compat) retired empirically | E.2 rewritten: real controller risk = packaging gap → resolved by design (Option A) |

**Reviewer's open questions → decisions:** Q1 Option A ✅ (Q2: `ofctl_rest` NOT in upstream git either — vendoring impossible, Option A confirmed as the only lean path); Q3 launcher = in-repo `netcopilot/controller/manage.py`; Q4 topology PNG = cuttable nice-to-have; Q5 QoS intents = DSCP-marking flows, metric = "flow installed with expected match/action".

*End of plan. Reviewer: see Part F. Author: reply to every finding with accept/fix/reject-reason before implementation starts.*
