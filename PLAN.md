# NetCopilot — Implementation Plan & Review Brief (v2)

**Project:** LLM-Powered Network Operations Agent for Software-Defined Networks
**Repo:** https://github.com/J-Suyash/netcopilot
**Author:** J-Suyash (4th-year CS, minor in networking)
**Document purpose:** (A) project context, (B) architecture & design, (C) implementation plan, (D) test/evaluation strategy, (E) risks, (F) review brief for independent agents, (G) review history & dispositions.

> **v2 changes (from technical review v1):** dev target = local Arch/CachyOS box (NOT the Ubuntu VM); controller = **os-ken** (not Ryu, no ONOS fallback); flow identity = OpenFlow **cookie** (not priority band); Lab Runner = injection-hardened (validated inputs, list-arg exec, global lock); conflict detection = explicitly bounded; port 6653 configurable; verification matches intent protocol. See Part G for the full disposition.

---

## Part A — Project Context

### A.1 What we are building
**NetCopilot**: a chat interface where an operator types network intents in natural language ("block all traffic from 10.0.0.5 to the DB VLAN except port 443") and an LLM agent plans, validates, installs, and verifies the corresponding OpenFlow rules on a live SDN (Mininet + controller). A hard safety layer sits between the LLM and the network so malformed, conflicting, or dangerous operations are rejected regardless of what the model outputs.

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

### A.4 Environment (DECIDED — dev happens on the local machine, not the Hermes VM)
| Thing | Value |
|---|---|
| Dev box | User's local machine: CachyOS/Arch (`pacman`), host Python **3.14** |
| Privileges | **sudo BLOCKED** (`no_new_privs` set) — cannot elevate to root on the host |
| OVS kernel module | Present ✅ |
| Hermes/Ubuntu VM | NOT the dev target. It hosts a repo mirror only. |
| Mininet | Requires root ⇒ cannot run natively on the dev box ⇒ **lab runs inside a container** (strategy below) |
| Python toolchain | **uv** everywhere; `uv python install 3.11` provides a 3.11 interpreter without root |
| LLM access | OpenRouter + OpenCode Zen (OpenAI-compatible), keys in `.env`; Ollama fallback |

**Lab execution strategy (confirm in Phase 0 gate 0):**
- **Primary:** privileged Docker container (`--privileged` + `NET_ADMIN`, OVS userspace inside) running Mininet + os-ken + Lab Runner; repo mounted read-write. The daemon (root) creates the container, so host `no_new_privs` does not matter.
- **Fallback A:** rootless Podman/user-namespace container if rootful Docker is unavailable.
- **Fallback B (last resort):** run the lab on the Hermes Ubuntu VM over SSH from the dev box. Contradicts the local-dev preference but keeps the demo alive.
- **Gate 0 (Phase 0, day 1):** verify container runtime + `mn --switch user` inside the container works, THEN write code. If none of the above works, stop and re-plan the lab strategy before writing a single line of agent code.

**CI note:** GitHub Actions runners are Ubuntu; unit + mock-loop tests must pass there without root or Mininet (Part D).

### A.5 Non-goals (do NOT build)
- No ONOS/ODL/P4/DPDK, no multi-controller clusters, no production claims.
- No LangChain/LlamaIndex — a plain function-calling loop we can explain line-by-line.
- No LLM fine-tuning in v1 (O5 only if schedule allows).
- No real hardware, no internet-facing services (everything binds localhost / container-internal).
- **No general flow-conflict solver** (see C.3 — bounded to exact-match + full-shadow on supported fields).

---

## Part B — Architecture & Design

### B.1 Component diagram

```
┌──────────────────────────────────────────────────────────┐
│  Chat UI (Chainlit) — intent in, explainable steps out    │
│  + dry-run toggle + audit panel + topology view           │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Agent Loop (plain Python, OpenAI-compatible tools API)   │
│  system prompt → model → tool_calls → execute → repeat     │
│  max 6 rounds, bounded retries (2) on validation errors    │
└──────────────────────────┬───────────────────────────────┘
                           │ tool calls (JSON)
┌──────────────────────────▼───────────────────────────────┐
│  Tool Layer (8 tools, see B.4)                            │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  SAFETY LAYER (LLM-independent, in-process, mandatory)    │
│  1. pydantic schema validation (strict, extra=forbid)     │
│  2. guardrails (broad-match drops, protected flows,       │
│     name resolution vs. live topology, session limits)    │
│  3. conflict detection (BOUNDED: exact + full-shadow)     │
│  4. dry-run mode (default ON in UI)                       │
│  5. audit log JSONL keyed on flow cookie + undo by cookie │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP (localhost:6653 REST / 8080 agent / 5100 lab)
┌──────────────────────────▼───────────────────────────────┐
│  os-ken controller (osken-manager):                       │
│    os_ken.app.ofctl_rest      → flow CRUD, stats          │
│    os_ken.app.rest_topology   → switches/links            │
│    netcopilot_app (os_ken)    → host tracking, baseline   │
│                                L2 flows (cookie 0, prio <100) │
└──────────────────────────┬───────────────────────────────┘
                           │ OpenFlow 1.3 (port CONTROLLER_PORT=6653, configurable)
┌──────────────────────────▼───────────────────────────────┐
│  Mininet (custom Topo, OVS or --switch user fallback)     │
│  + Lab Runner process (Flask on 127.0.0.1:5100):          │
│    /verify/{src}/{dst}?proto=icmp|tcp&port=N              │
│    /iperf/{src}/{dst} · /link/down/{a}/{b} · /link/up     │
│    ★ input validation + global request lock + list-arg    │
│      exec (no shell interpolation, see C.1)               │
└───────────────────────────────────────────────────────────┘
```

### B.2 Data flow — sample intent: "block 10.0.0.5 from reaching the DB server 10.0.0.20"

1. User sends intent → agent loop.
2. LLM returns `tool_call: install_flow({match: {ipv4_src: 10.0.0.5, ipv4_dst: 10.0.0.20}, action: drop})`.
3. **Safety**: pydantic validates → guardrails (match not too broad; hosts resolve against live topology) → conflict scan (bounded) → dry-run check.
4. Tool allocates `cookie = COOKIE_MAGIC | op_id` (magic = high 32 bits, monotonic op id = low 32 bits), executes `POST /stats/flowentry/add` (dpid, cookie, priority 150, match, actions=[] for drop) on os-ken.
5. Audit row written, keyed on cookie.
6. Agent calls `verify_connectivity(10.0.0.5, 10.0.0.20, proto=icmp)` → Lab Runner pings → unreachable (expected) → agent reports "blocked and verified".
7. UI renders each step.

**Verification semantics:** verification must match the intent's protocol — ICMP ping for L3 blocks, TCP port probe (`nc`/socket via Runner) for L4 blocks (`tcp_dst`). Never report "verified" on a protocol the intent did not touch.

### B.3 Key design decisions
1. **Plain function-calling loop** — no agent framework. One file, ~120 lines, fully explainable in interviews. Model-agnostic via OpenAI-compatible `/chat/completions` with `tools`.
2. **Safety as a hard gate** — the LLM *proposes*, the safety layer *disposes*. Guardrails are plain Python, not prompts. The design is injection-resistant at the core: worst case the LLM proposes evil, code rejects it.
3. **Lab Runner process** — Mininet host commands can only run inside the Mininet process; a Flask server inside the topology script exposes them over localhost HTTP. **The Runner is a trust boundary**: it validates every input against the known host set / `ipaddress`, executes with list args (never shell strings), and serializes requests with a global lock (single Mininet process — concurrent intents would otherwise race).
4. **Flow identity = OpenFlow cookie** (not priority band). Cookie = magic high bits (identifies "agent-installed", survives restarts, no in-memory state needed) + op id (low bits, monotonic). Undo = delete by cookie (order-independent). "Agent-installed?" = `cookie & mask == magic`. Audit rows key on cookie. Priority bands remain as *policy* only: baseline L2 flows `<100`, agent flows `100–200`, so a learned flow can never out-prioritize an agent drop.
5. **Deterministic testing via MockLLM** — loop tests use scripted tool-call sequences; no network or API keys needed for unit/CI tests.
6. **Dry-run default ON** — destructive ops (remove_flow, fail_link) additionally require confirmation when dry-run is off.

### B.4 Tool schemas (JSON function definitions)
| Tool | Parameters (validated by pydantic, extra=forbid) | Returns |
|---|---|---|
| `get_topology` | — | switches, links, hosts (MAC/IP/port) |
| `get_flows` | dpid? | flow table entries (cookie, priority, match, actions, counters) |
| `get_stats` | dpid, port? | byte/packet counters per port |
| `install_flow` | match: {eth_type?, ipv4_src?, ipv4_dst?, ip_proto?, tcp_dst?}, action: drop\|output, priority (100–200), idle_timeout? | cookie, installed rule |
| `remove_flow` | cookie (must be agent cookie: magic bits match) | removed rule |
| `verify_connectivity` | src (host name or IP), dst, proto: icmp\|tcp, port? (tcp only), count=2 | reachable/unreachable + rtt |
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
│   ├── Dockerfile          # privileged lab image: mininet + os-ken + runner
│   ├── compose.yml         # lab container, repo mounted, ports 6653/5100/8080
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
│   │   ├── guardrails.py   # LLM-independent policy checks
│   │   ├── conflicts.py    # BOUNDED overlap detection (see C.3)
│   │   └── audit.py        # JSONL audit keyed on cookie + undo store
│   ├── controller/
│   │   ├── app.py          # os-ken app (host tracking, baseline flows, OpenFlow13)
│   │   └── client.py       # HTTP client for ofctl_rest/rest_topology
│   └── ui/
│       └── app.py          # Chainlit UI
├── tests/
│   ├── unit/               # schema, guardrails, conflicts, audit, tools (no net)
│   ├── integration/        # requires lab container (tagged, local only)
│   └── eval_suite.py       # 20-intent gold set + metrics
└── scripts/
    ├── setup_env.sh        # uv 3.11 venv + deps (host, no root needed)
    ├── build_lab.sh        # build/start lab container
    └── spike_ofctl.sh      # Phase 0 gate 1: curl-a-flow against os-ken
```

---

## Part C — Implementation Plan

### Phase 0 — Environment bootstrap (days 1–3)
**Gate 0 (day 1, hardest unknown first — do NOT skip):** confirm lab strategy on the dev box:
1. Container runtime present and usable (Docker rootful, or rootless Podman).
2. Build the lab image; verify `mn --topo linear,2 --switch user` runs inside the container (OVS kernel module is present on host, but the container needs it passed through or userspace fallback — test both).
3. If the lab cannot run in a container: STOP and choose Fallback B (SSH lab on Ubuntu VM) before writing agent code.

**Gate 1 (day 1, controller spike):**
1. `uv venv --python 3.11` + `uv pip install os-ken`.
2. `osken-manager os_ken.app.ofctl_rest os_ken.app.rest_topology netcopilot/controller/app.py --ofp-tcp-listen-port 6653`.
3. `mn --controller remote,ip=127.0.0.1,port=6653 --switch ovs,protocols=OpenFlow13` (inside lab container).
4. `curl -X POST .../stats/flowentry/add` a test drop flow; `curl .../stats/flow/1` shows it. → **If this green-lights, the plan holds.**
5. Smoke: `verify` endpoint via Runner, ping from a host.

**Exit criteria:** OpenFlow 1.3 topology with REST flow install/delete working; os-ken chosen and proven; lab strategy confirmed; commands documented in README.

### Phase 1 — Lab + controller plumbing (days 4–8)
1. `lab/topo.py` — campus topology: 2 core + 2 access switches, 4 hosts (web, db, client, dmz), IP plan 10.0.0.0/24.
2. `lab/runner.py` — **hardened**: endpoints `/verify/{src}/{dst}?proto=icmp|tcp&port=N`, `/iperf`, `/link/down`, `/link/up`; every `src`/`dst` validated against the known host set OR `ipaddress.ip_address()` before use; host commands executed as **argument lists** (`h.cmd(["ping", "-c", str(count), str(dst)])` — no f-string shell); **global threading.Lock** serializing all host commands; binds 127.0.0.1:5100.
3. `netcopilot/controller/app.py` (os-ken) — host tracking (packet-in → MAC/IP/port map), baseline L2 learning flows with **cookie=0, priority <100** (never out-prioritize agent flows), exposes `/v1/hosts`.
4. `netcopilot/controller/client.py` — typed wrappers: `add_flow(cookie, ...)`, `delete_flow(cookie)`, `get_flows`, `get_topology`, `get_ports`; timeouts + controller-health check tool (controller-down failure mode).
5. `scripts/build_lab.sh`, `scripts/spike_ofctl.sh`.
**Exit:** curl can install/remove a flow and verify a ping via the Runner; host map populated; controller-down produces a clean tool error.

### Phase 2 — Agent loop (days 9–17)
**Tasks (TDD, MockLLM first):**
1. `config.py` + `.env.example` (provider, base_url, key, model, dry-run default, ports).
2. `agent/llm.py` — `LLMClient.chat(messages, tools) -> response`; OpenRouter-compatible (also covers Zen), Ollama, `MockLLM`.
3. `agent/tools.py` — registry: JSON schema per tool + handlers calling controller/lab clients; **cookie allocator** (monotonic op id under lock).
4. `agent/loop.py` — loop: system prompt → model → tool_calls → validate → guardrails → conflicts → execute → append result → repeat (≤6 rounds, ≤2 validation retries) → final message; timeouts, error propagation.
5. `netcopilot/cli.py` — `python -m netcopilot "block 10.0.0.5 from 10.0.0.20"`.
**Exit:** mock-LLM e2e test green (scripted install→verify sequence asserts the flow exists on the switch with the right cookie); CLI works on 3 real scenarios with a real LLM.

### Phase 3 — Safety layer + UI (days 18–27)
1. `safety/schema.py` — pydantic per tool, `extra=forbid`; validation errors fed back to LLM (bounded, 2 retries).
2. `safety/guardrails.py` — reject: wildcard/drop-all at high priority; unknown hosts/IPs (allowlist built from **resolved controller topology**, never from strings the LLM echoed); removal of non-agent flows (cookie check); >50 flow changes/session; management-plane targets. Machine-readable rejection reasons.
3. `safety/conflicts.py` — **BOUNDED**: (a) identical match + same priority + different action; (b) new flow fully shadows / is fully shadowed by an existing flow **on the supported match fields only** (the 5 in `install_flow`). Explicitly OUT OF SCOPE: general N-field geometric intersection (prefixes/port ranges/wildcards combos). Structured conflict report back to the LLM.
4. `safety/audit.py` — JSONL per operation keyed on cookie: intent, tool call, validation results, executed ops, verification; **undo = delete by cookie in any order**.
5. `ui/app.py` — Chainlit chat, tool-call cards, dry-run toggle, audit panel, topology render (networkx → PNG).
**Exit:** unit suite green (Part D); UI demonstrates all 5 demo scenarios; safety cases all rejected.

### Phase 4 — Evaluation, docs, packaging (days 28–40)
1. `tests/eval_suite.py` — 20-intent gold set (5 categories × 4: security, QoS (priority-marking only), observability, failure diagnosis, safety) with expected operations; metrics: op success rate, safety rejection rate (100% on safety cases), rounds, latency, token cost.
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
| Unit (schema, guardrails, conflicts, audit, config) | pytest | nothing | ✅ |
| Loop e2e with MockLLM | pytest + fake controller client | nothing | ✅ |
| Integration (real Mininet + os-ken in lab container) | pytest, tagged `integration` | lab container running | ❌ (local) |
| Real-LLM smoke + eval suite | script + CSV output | API key (.env) | ❌ (manual) |
| Lint | ruff | — | ✅ |

Key tests: guardrail table-driven cases (≥15, incl. prompt-injection attempts as intents), conflict matrix (≥10: exact, full-shadow both directions, non-overlapping), cookie identity (magic mask survives restart simulation), undo correctness (order-independent), dry-run blocks all writes, audit completeness, Runner input validation (injection strings rejected), Runner serialization (parallel requests).

---

## Part E — Risks & Open Questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| E.1 | Lab container can't run Mininet on dev box (privileges, userspace OVS) | Medium | Phase 0 Gate 0 on day 1; Fallback B (SSH lab to Ubuntu VM) decided before any agent code |
| E.2 | os-ken/eventlet quirks on Python 3.11 (details from memory — verify in Gate 1) | Medium | Gate 1 spike on day 1: if ofctl_rest works against mn, risk retired; pin eventlet version in venv |
| E.3 | Small/free LLMs unreliable at tool-calling | Medium | Bounded retries + validation feedback; structured JSON fallback; cheap paid models as backup |
| E.4 | Scope creep (UI polish, OVS queues, general conflict solver, O5) | High | Explicit non-goals (A.5); phases gated by exit criteria; conflicts bounded by design |
| E.5 | API cost during dev | Low | MockLLM for tests; small models for dev; token budget logging |
| E.6 | Concurrent eval intents race on single Mininet process | Medium | Runner global lock (C.1) |

Open questions: container runtime availability on dev box (Gate 0); exact topology size; QoS demo = priority-marking only (decided: yes, no OVS queue config in v1).

---

## Part F — REVIEW BRIEF (for the reviewing agent)

> **You are reviewing an implementation plan, not executing it.** Read `PLAN.md` (this file) and `proposal.md` in the repo root. Do NOT modify any files. Produce a review report and write it to `REVIEW.md` in the repo root (or return it in full in your final message if you cannot write files).

### F.1 Context you need
- Author: 4th-year CS student (minor: networking), **8 weeks**, dev box = CachyOS/Arch with **sudo blocked** (no_new_privs), host Python 3.14 (uv provides 3.11 venvs), **no GPU**, minimal API budget. Mininet runs inside a container (strategy in A.4). CI = GitHub Actions (Ubuntu, rootless unit tests only).
- Differentiator = safety layer + demo quality; the core loop (NL intent → validated OpenFlow rule on a live emulated network) must demonstrably work by end of Phase 2 (~day 17) or the plan is failing.
- This is v2: a prior review (Part G) already produced must-fixes that are folded in. Review the CURRENT state, not the history.

### F.2 Review dimensions — check ALL explicitly
1. **Feasibility & environment**: Would anything fail on the stated environment? Sanity-check: Mininet-in-container (privileged vs rootless, OVS userspace), os-ken module paths (`os_ken.app.ofctl_rest`, `osken-manager`), OpenFlow 1.3 port 6653 config on both sides, `--switch ovs,protocols=OpenFlow13` flags, cookie semantics in ofctl_rest (`cookie` on add; `cookie`/`cookie_mask` on delete), uv-on-Arch without root.
2. **Architecture correctness**: Does the data flow in B.2 work? Can the safety layer be bypassed by (a) malicious prompt-injected intent, (b) hallucinating LLM emitting valid-JSON-but-evil tool calls, (c) attacker-controlled strings in tool output (host names/MACs) fed back into the prompt? Is cookie-as-identity sound (magic bits, restart survival, undo)? Is the priority policy (baseline <100, agent 100–200) sufficient alongside cookies? Is the Runner trust boundary complete (validation, list-arg exec, lock)?
3. **Completeness**: Missing tools, endpoints, failure modes (controller down ✅ in plan, LLM timeout, Mininet host crash, port-in-use 6653/5100/8080, concurrent sessions ✅ lock)? Is verification-protocol-matching (B.2) fully specified? Anything in the eval suite unmeasurable?
4. **Scope control**: Any task that will silently eat >2 weeks? Are non-goals (A.5) sufficient? Should anything be cut or deferred?
5. **Testability**: MockLLM strategy sound? CI unit suite passable without root/Mininet? Exit criteria objectively checkable?
6. **Security**: `.env` gitignored ✅; Runner localhost + validated inputs ✅; root-in-container implications; prompt-injection surface (user intents and tool results are untrusted) — is the design defensible end-to-end?
7. **Sequencing**: Are phases ordered to de-risk the hardest unknowns first (Gates 0 and 1 on day 1)? Anything blocking that should move earlier/later?

### F.3 What to keep in mind while reviewing
- **Correctness of claims > politeness**: if a command, endpoint, flag, or API behavior looks wrong, verify it (docs/web) or flag as "verify" — do not assume the plan is right.
- **Proportion**: 8-week UG project, not a PhD thesis. Reject over-engineering (multi-controller, k8s, LangChain, vector DBs) as much as under-specification.
- **Constraints**: no GPU, minimal spend, no root on dev host, explainable code matters more than clever code.
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

**Review v1 (external agent): VERDICT — GO / APPROVE WITH CHANGES.** All findings ACCEPTED and incorporated in v2:

| Finding | Severity | Disposition | Where |
|---|---|---|---|
| Ryu dead on modern Python; use os-ken (not ONOS) | BLOCKER | Accepted — os-ken primary, ONOS removed | A.4, B.1, C.0 Gate 1, E.2 |
| Flow ownership via priority band ≠ identity; use OpenFlow cookie | MAJOR | Accepted — cookie magic+op_id, undo by cookie | B.2, B.3, B.4, C.2/3 |
| Lab Runner command injection | MAJOR | Accepted — input validation, list-arg exec, allowlist from resolved topology | B.1, C.1, D |
| Conflict detection scope creep; bound it | MAJOR | Accepted — exact + full-shadow on supported fields only | A.5, B.3, C.3 |
| Port 6633 vs 6653 — make configurable | MINOR | Accepted — CONTROLLER_PORT=6653, both sides | A.4, B.1 |
| Concurrent access to Mininet process (eval races) | MINOR | Accepted — global lock in Runner | C.1, E.6 |
| Verification must match intent protocol (ping vs port probe) | NIT | Accepted — proto parameter + semantics note | B.2, B.4 |
| Pull controller spike into Phase 0 day 1 | NIT | Accepted — Gate 1 | C.0 |
| Env mismatch (Arch, no sudo, py3.14) — decide dev target | — | Accepted — dev = local box, containerized lab | A.4 |

*End of plan. Reviewer: see Part F. Author: reply to every finding with accept/fix/reject-reason before implementation starts.*
