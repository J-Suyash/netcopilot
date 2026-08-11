# NetCopilot — Implementation Plan & Review Brief

**Project:** LLM-Powered Network Operations Agent for Software-Defined Networks
**Repo:** https://github.com/J-Suyash/netcopilot (local: `/home/ubuntu/hermes_workspace/projects/sdn_ai_copilot`)
**Author:** J-Suyash (4th-year CS, minor in networking)
**Document purpose:** This file contains (A) full project context, (B) architecture & design, (C) the implementation plan, (D) test/evaluation strategy, (E) risks, and (F) a self-contained **REVIEW BRIEF** for an independent agent to review the plan.

---

## Part A — Project Context

### A.1 What we are building
**NetCopilot**: a chat interface where an operator types network intents in natural language ("block all traffic from 10.0.0.5 to the DB VLAN except port 443") and an LLM agent plans, validates, installs, and verifies the corresponding OpenFlow rules on a live SDN (Mininet + Ryu). A hard safety layer sits between the LLM and the network so malformed, conflicting, or dangerous operations are rejected regardless of what the model outputs.

### A.2 Goals (success criteria)
| # | Objective | Criterion |
|---|---|---|
| O1 | LLM agent translates NL intents → structured SDN operations | ≥90% of test intents produce valid executable operations |
| O2 | Safety layer (schema validation, conflict detection, guardrails, dry-run) | 100% of malformed/conflicting/dangerous ops blocked before install |
| O3 | Live SDN integration (flow install/remove/query/verify) | Works end-to-end on Mininet + Ryu |
| O4 | Chat UI demo | 30-second demo: intent → validation → install → verified |
| O5 | (Optional research extension) fine-tuned small model vs API models on IBNBench | Accuracy/cost/latency comparison — only if phases 0–4 stay on schedule |

### A.3 Current state
- `proposal.md` exists (committed, signed, pushed).
- Nothing else. No code, no environment setup done yet.

### A.4 Environment (known)
- Linux VM, kernel 6.17.0-1018-oracle (Oracle Cloud), user `ubuntu` with sudo (assume; verify in Phase 0).
- No GPU. Internet available. GitHub via HTTPS + signed SSH commits.
- Python tooling preference: **uv** (not pip) for venvs and project management.
- LLM access: OpenRouter (OpenAI-compatible) and OpenCode Zen (`https://opencode.ai/zen/v1`, free `deepseek-v4-flash`), keys in `.env`. Ollama local as fallback.
- **Verify in Phase 0:** sudo/root availability, Open vSwitch kernel module (`modprobe openvswitch`), Mininet installability, Ryu compat with the available Python (see E.2).

### A.5 Non-goals (do NOT build)
- No ONOS/ODL/P4/DPDK, no multi-controller clusters, no production claims.
- No LangChain/LlamaIndex — a plain function-calling loop we can explain line-by-line.
- No LLM fine-tuning in v1 (O5 only if schedule allows).
- No real hardware, no internet-facing services (lab binds localhost only).

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
│  get_topology · get_flows · get_stats · install_flow      │
│  remove_flow · verify_connectivity · resolve_host ·       │
│  fail_link / heal_link (demo scenarios)                   │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  SAFETY LAYER (LLM-independent, in-process, mandatory)    │
│  1. pydantic schema validation (reject malformed args)    │
│  2. guardrails (broad-match drops, protected flows,       │
│     name resolution, session limits)                      │
│  3. conflict detection vs. installed flows                │
│  4. dry-run mode (default ON in UI)                       │
│  5. audit log JSONL + undo (rollback installed flows)     │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼───────────────────────────────┐
│  Ryu controller (ryu-manager):                            │
│    ryu.app.ofctl_rest      → flow CRUD, stats             │
│    ryu.app.rest_topology   → switches/links               │
│    netcopilot_ryu_app      → host tracking (MAC/IP),      │
│                              port stats, OpenFlow13       │
└──────────────────────────┬───────────────────────────────┘
                           │ OpenFlow 1.3 (port 6633)
┌──────────────────────────▼───────────────────────────────┐
│  Mininet (custom Topo, OVS or user-switch fallback)       │
│  + Lab Runner process (Flask on 127.0.0.1:5100):          │
│    /verify/{src}/{dst} · /iperf/{src}/{dst}               │
│    /link/down/{a}/{b} · /link/up/{a}/{b}                  │
└───────────────────────────────────────────────────────────┘
```

### B.2 Data flow — sample intent: "block 10.0.0.5 from reaching the DB server 10.0.0.20"

1. User sends intent → agent loop.
2. LLM returns `tool_call: install_flow({match: {ipv4_src: 10.0.0.5, ipv4_dst: 10.0.0.20}, action: drop})`.
3. **Safety**: pydantic validates → guardrail checks (match not too broad; hosts exist) → conflict scan (no existing conflicting flow) → dry-run check.
4. Tool executes `POST /stats/flowentry/add` on Ryu (dpid, priority 100, match, actions=[] for drop).
5. Audit log entry written; flow tagged as agent-managed (priority band 100–200 reserved for agent flows).
6. Agent calls `verify_connectivity(10.0.0.5, 10.0.0.20)` → Lab Runner runs `h1 ping -c 2 h2` → fails (expected) → agent reports "blocked and verified".
7. UI renders each step.

### B.3 Key design decisions
1. **Plain function-calling loop** — no agent framework. One file, ~120 lines, fully explainable in interviews. Model-agnostic via OpenAI-compatible `/chat/completions` with `tools`.
2. **Safety as a hard gate** — the LLM *proposes*, the safety layer *disposes*. Guardrails are plain Python, not prompts. This is the differentiator and the interview talking point.
3. **Lab Runner process** — Mininet host commands (ping/iperf/link down) can only run inside the Mininet process; a Flask server inside the topology script exposes them over localhost HTTP so the agent/tools stay decoupled.
4. **Agent-flow priority band** — agent-installed flows use priorities 100–200; baseline connectivity flows (installed by the controller app at startup) use <100; protected flows are only removable by the agent if it installed them (tracked in audit/state store).
5. **Deterministic testing via MockLLM** — the loop is tested with scripted tool-call sequences, so unit/integration tests don't need network or API keys; real-LLM runs are manual smoke tests + eval suite.
6. **Dry-run default ON** — UI toggle; destructive ops (remove_flow, fail_link) additionally require confirmation when dry-run is off.

### B.4 Tool schemas (JSON function definitions)
| Tool | Parameters (all validated by pydantic) | Returns |
|---|---|---|
| `get_topology` | — | switches, links, hosts (MAC/IP/port) |
| `get_flows` | dpid? | flow table entries (priority, match, actions, counters) |
| `get_stats` | dpid, port? | byte/packet counters per port |
| `install_flow` | match: {eth_type?, ipv4_src?, ipv4_dst?, ip_proto?, tcp_dst?}, action: drop\|output, priority (100–200), idle_timeout? | flow id / installed rule |
| `remove_flow` | flow_id (must be agent-installed) | removed rule |
| `verify_connectivity` | src (host name or IP), dst, count=2 | ping result (reachable/unreachable, rtt) |
| `resolve_host` | name or IP | canonical host info or error |
| `fail_link` / `heal_link` | src_switch, dst_switch | link state change (demo/failure scenarios) |

### B.5 Repo layout (target)
```
sdn_ai_copilot/
├── proposal.md
├── PLAN.md                 # this file
├── README.md               # final docs (P4)
├── pyproject.toml          # uv-managed
├── .env.example            # LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, DRY_RUN_DEFAULT
├── .gitignore              # .env, venv, __pycache__, audit logs
├── netcopilot/
│   ├── __init__.py
│   ├── config.py           # env → dataclass config
│   ├── agent/
│   │   ├── loop.py         # function-calling loop
│   │   ├── llm.py          # LLMClient (OpenRouter/Zen/Ollama/Mock) + prompts.py
│   │   └── tools.py        # tool registry: schemas + handlers
│   ├── safety/
│   │   ├── schema.py       # pydantic models per tool
│   │   ├── guardrails.py   # LLM-independent policy checks
│   │   ├── conflicts.py    # flow overlap/priority conflict detection
│   │   └── audit.py        # JSONL audit + undo store
│   ├── controller/
│   │   ├── ryu_app.py      # custom Ryu app (host tracking, OpenFlow13)
│   │   └── client.py       # HTTP client for ofctl_rest/rest_topology
│   └── ui/
│       └── app.py          # Chainlit UI
├── lab/
│   ├── topo.py             # Mininet Topo class (campus/leaf-spine)
│   └── runner.py           # Flask lab server (verify/iperf/link down-up)
├── tests/
│   ├── unit/               # schema, guardrails, conflicts, tools (no network)
│   ├── integration/        # requires root + Mininet (tagged, local only)
│   └── eval_suite.py       # 20-intent gold set + metrics
└── scripts/
    ├── setup_env.sh        # uv venv, apt deps, mininet/ryu
    └── run_lab.sh          # start runner + ryu-manager + (optional) UI
```

---

## Part C — Implementation Plan

### Phase 0 — Environment bootstrap (1–2 days)
**Tasks:**
1. Verify: `sudo -n true`, `modprobe openvswitch` (fallback: user-switch), available Pythons (`python3.11` preferable for Ryu).
2. Install Mininet (`apt install mininet openvswitch-switch` or source), Ryu in a uv venv (pin Python 3.10/3.11 if system is 3.12 — see E.2).
3. Smoke test: `sudo mn --topo linear,2 --controller remote` + `ryu-manager ryu.app.ofctl_rest ryu.app.rest_topology` → curl flowentry/add works.
**Exit:** bare OpenFlow 1.3 topology with REST flow install/delete working, commands documented in README.

### Phase 1 — Lab + controller plumbing (days 3–7)
**Tasks:**
1. `lab/topo.py` — campus topology: 2 core + 2 access switches, 4 hosts (web, db, client, dmz), VLAN-ish IP plan (10.0.0.0/24).
2. `lab/runner.py` — Flask endpoints `/verify/{src}/{dst}` (ping via `h.cmd`), `/iperf`, `/link/down`, `/link/up`; binds 127.0.0.1:5100.
3. `netcopilot/controller/ryu_app.py` — host tracking (packet-in → MAC/IP/port map), baseline forwarding (L2 learning, priority <100), exposes `/v1/hosts`.
4. `netcopilot/controller/client.py` — typed wrappers: `add_flow`, `delete_flow`, `get_flows`, `get_topology`, `get_ports`.
5. `scripts/run_lab.sh` — one command brings up Mininet + Ryu + Runner.
**Exit:** curl can install/remove a flow and verify a ping result via the Runner; host map is populated.

### Phase 2 — Agent loop (days 8–16)
**Tasks (TDD, MockLLM first):**
1. `config.py` + `.env.example` (provider, base_url, key, model, dry-run default).
2. `agent/llm.py` — `LLMClient.chat(messages, tools) -> response`; implementations: OpenRouter-compatible (works for Zen too), Ollama, and `MockLLM` (scripted tool calls).
3. `agent/tools.py` — registry: JSON schema per tool + handler that calls controller/lab clients.
4. `agent/loop.py` — loop: system prompt → model → if tool_calls: validate → execute → append result → repeat (≤6 rounds, ≤2 validation retries) → final message. Timeouts, error propagation.
5. `netcopilot/cli.py` — `python -m netcopilot "block 10.0.0.5 from 10.0.0.20"` (CLI harness before UI).
**Exit:** mock-LLM e2e test green (scripted install→verify sequence asserts Ryu actually has the flow); CLI works on 3 real scenarios with a real LLM.

### Phase 3 — Safety layer + UI (days 17–26)
**Tasks:**
1. `safety/schema.py` — pydantic models per tool; strict extra=forbid; validation errors fed back to LLM (bounded).
2. `safety/guardrails.py` — reject: drop-all / wildcard-drop at high priority; unknown hosts/IPs; removal of non-agent flows; >50 flow changes/session; management-plane targets. Return machine-readable rejection reasons.
3. `safety/conflicts.py` — overlap detection: exact-match conflicts, priority inversion (new flow shadows/gets shadowed), contradictory actions; output structured conflict report.
4. `safety/audit.py` — JSONL per operation: intent, tool call, validation results, executed ops, verification; undo = delete agent-installed flows in reverse order.
5. `ui/app.py` — Chainlit chat, tool-call cards, dry-run toggle, audit panel, topology render (networkx → PNG).
**Exit:** unit suite green (see Part D); UI demonstrates all 5 demo scenarios; safety cases all rejected.

### Phase 4 — Evaluation, docs, packaging (days 27–40)
**Tasks:**
1. `tests/eval_suite.py` — 20-intent gold set (5 categories × 4: security, QoS, observability, failure diagnosis, safety) with expected operations; metrics: operation success rate, safety rejection rate (must be 100% on safety cases), rounds, latency, token cost.
2. README: architecture diagram, setup, demo script, interview talking points.
3. Demo video (30–60s) + GitHub release.
4. Report skeleton (thesis format) — write up as you go in `docs/`.
**Exit:** all O1–O4 criteria met; repo public and polished.

### Optional Phase 5 — Research extension (only if Phases 0–4 done early)
Fine-tune Qwen2.5-3B (LoRA) on IBNBench intent→flow-rule pairs; compare accuracy/cost/latency vs. API zero-shot models using the same eval suite. → conference paper draft.

---

## Part D — Testing & Evaluation Strategy

| Layer | Tooling | Requires | Runs in CI |
|---|---|---|---|
| Unit (schema, guardrails, conflicts, audit, config) | pytest | nothing | ✅ |
| Loop e2e with MockLLM | pytest + fake controller client | nothing | ✅ |
| Integration (real Mininet + Ryu) | pytest, tagged `integration` | root, Mininet | ❌ (local) |
| Real-LLM smoke + eval suite | script + CSV output | API key (.env) | ❌ (manual) |
| Lint | ruff | — | ✅ |

Key tests: guardrail table-driven cases (≥15), conflict matrix (≥10), loop round/retry limits, undo correctness, dry-run prevents all writes, audit log completeness.

---

## Part E — Risks & Open Questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| E.1 | Mininet/OVS on Oracle Cloud VM (kernel modules, sudo) | Medium | Phase 0 first; user-switch fallback (`--switch user`); document |
| E.2 | Ryu (last release 4.34, 2021) incompatible with Python 3.12 (eventlet/msgpack) | Medium-High | uv venv with Python 3.10/3.11; if Ryu is a dead end, swap to a maintained controller (Ryu is academia-standard; ONOS via Docker is the fallback but heavier) |
| E.3 | Small/free LLMs unreliable at tool-calling | Medium | Bounded retries + validation feedback; structured JSON fallback mode; cheap paid models as backup |
| E.4 | Scope creep (UI polish, QoS queues, O5) | High | Explicit non-goals; phases gated by exit criteria |
| E.5 | API cost during dev | Low | MockLLM for tests; small models for dev; token budget logging |

Open questions for the reviewer/author to settle: exact topology size; whether QoS demo needs OVS queue config in Phase 3 or can be priority-marking only; Chainlit vs Streamlit if Chainlit fights the environment.

---

## Part F — REVIEW BRIEF (for the reviewing agent)

> **You are reviewing an implementation plan, not executing it.** Read `PLAN.md` (this file) and `proposal.md` in the repo root. Do NOT modify any files. Produce a review report and write it to `REVIEW.md` in the repo root (or return it in full in your final message if you cannot write files).

### F.1 Context you need
- Author: 4th-year CS student (minor: networking), **8 weeks**, one Linux VM (Oracle Cloud, Ubuntu user with sudo), **no GPU**, minimal API budget. Python tooling is **uv**. LLM access: OpenRouter + OpenCode Zen (free tier) via OpenAI-compatible APIs; Ollama fallback.
- The project's differentiator is the **safety layer** and **demo quality**; the core loop (NL intent → validated OpenFlow rule on a live emulated network) must demonstrably work by end of Phase 2 (~day 16) or the plan is failing.
- The author will likely feed your report back into implementation, so be specific and actionable, not generic.

### F.2 Review dimensions — check ALL of these explicitly
1. **Feasibility & environment**: Would anything here fail on the stated environment? Specifically sanity-check: Mininet on a cloud VM without nested virt (it does NOT need KVM — network namespaces suffice; confirm reasoning), OVS kernel module availability and the user-switch fallback, Ryu+Python-version compatibility and the stated fallback path, `--controller=remote,port=6633` + `--protocols=OpenFlow13` flags, ofctl_rest/rest_topology endpoint names, sudo usage.
2. **Architecture correctness**: Does the data flow in B.2 actually work with the described components? Can the safety layer be bypassed by (a) a malicious prompt-injected user intent, (b) a broken/hallucinating LLM emitting valid-JSON-but-evil tool calls, (c) tool output containing attacker-controlled strings (host names, MACs) that get fed back into the prompt? Is the priority-band scheme (baseline <100, agent 100–200) sound? Is the Lab Runner approach the right way to exec host commands, and are there simpler alternatives?
3. **Completeness**: Missing tools, endpoints, failure modes (controller down, LLM timeout, Mininet host crash, port conflicts, concurrent sessions)? Is the undo/rollback design correct? Is anything in the eval suite unmeasurable?
4. **Scope control**: Any task that will silently eat >2 weeks? Are the non-goals (A.5) sufficient? Should anything be cut or deferred?
5. **Testability**: Is the MockLLM strategy sound? Can the CI unit suite pass without root/Mininet? Are the exit criteria objectively checkable?
6. **Security**: API key handling (.env, gitignored), Lab Runner binding localhost-only, running as root implications, prompt-injection surface (user intents are untrusted input; tool results are untrusted input — is the design defensible?).
7. **Sequencing**: Are phases ordered to de-risk the hardest unknown first? Is anything blocking that should be moved earlier/later?

### F.3 What to keep in mind while reviewing
- **Correctness of claims > politeness**: if a command, endpoint, or flag looks wrong, verify it (docs/web) or flag it as "verify" — do not assume the plan is right.
- **Proportion**: this is an 8-week UG project, not a PhD thesis. Reject over-engineering (multi-controller, k8s, LangChain, vector DBs) as much as under-specification.
- **The student's constraints**: no GPU, minimal spend, explainable code matters more than clever code (interviews).
- **Prioritize**: rank findings by how much they threaten the "core loop works by day 16" milestone.
- **Verdict options**: APPROVE / APPROVE WITH CHANGES (list must-fix) / MAJOR REVISION (list reasons).

### F.4 Required output format
```
VERDICT: <APPROVE | APPROVE WITH CHANGES | MAJOR REVISION>

FINDINGS (table or list):
ID | SEVERITY (BLOCKER/MAJOR/MINOR/NIT) | LOCATION (section/task) | ISSUE | SUGGESTED FIX

ANSWERS TO REVIEW DIMENSIONS (F.2 items 1–7, one short paragraph each)

TOP 5 RISKS (ranked, with why)

QUESTIONS FOR THE AUTHOR (anything you could not determine)
```
Severity guide: BLOCKER = plan cannot succeed as written; MAJOR = should fix before/early in implementation; MINOR = fix during implementation; NIT = style/optional.

---

*End of plan. Reviewer: see Part F. Author: reply to every finding with accept/fix/reject-reason before implementation starts.*
