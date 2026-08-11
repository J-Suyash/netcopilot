# Project Proposal

## LLM-Powered Network Operations Agent for Software-Defined Networks
### An Intent-Driven AI Copilot that Manages SDN Infrastructure through Natural Language

**Author:** [Your Name]
**Course:** B.Tech Computer Science (Minor: Computer Networking) — Final Year Major Project
**Guide:** [Guide Name]
**Date:** August 2026

---

## 1. Abstract

Software-Defined Networking (SDN) separates the control plane from the data plane, exposing network state and programmability through open interfaces. However, operators still interact with SDN controllers through low-level primitives (flow rules, REST API calls), requiring deep protocol expertise and creating an entry barrier for automation. This project proposes **NetCopilot**, an LLM-powered network operations agent that lets operators manage an SDN network using natural language. Users express high-level intents ("block all traffic from host 10.0.0.5 to the database VLAN") and the agent translates them into validated, conflict-checked flow rules, installs them on the controller, and verifies the outcome — all with a safety layer that prevents harmful or malformed operations.

The system is built on Mininet + Ryu, uses a function-calling LLM agent loop (no heavyweight frameworks), and exposes a chat UI for live demos. Beyond the working system, the project establishes a foundation for a research contribution: the intent-translation component can be fine-tuned on public benchmarks (IBNBench) to study cost-efficient intent translation, yielding a conference-paper-grade evaluation.

---

## 2. Motivation & Problem Statement

### 2.1 The problem

- SDN controllers (Ryu, ONOS, ODL) expose programmability via REST APIs and OpenFlow flow rules — powerful but **low-level and error-prone**. A single misformatted flow rule can blackhole traffic or bypass a security policy.
- Network operations today are manual: engineers translate business intent into device-specific commands, a process that is slow, requires specialized expertise, and is the leading source of misconfiguration (the industry's #1 cause of outages).
- Intent-Based Networking (IBN) promises to automate this, but prior rule-based translators are rigid and cannot handle the variety of natural-language intents.

### 2.2 Why now (2026 context)

Large Language Models (LLMs) have demonstrated strong natural-language understanding and tool-use capability. Industry leaders have already shown this works in production: **Confucius** (Meta, SIGCOMM 2025) runs multi-agent LLM network management at hyperscale; **NetIntent** (IEEE OJ-COMS 2025) demonstrated end-to-end LLM-driven intent deployment on ONOS/ODL with a public benchmark (IBNBench); **NetLLM** (SIGCOMM 2024) showed LLMs can be adapted cheaply (0.31% trainable parameters) for networking tasks. The gap: these are either production-internal systems or research prototypes targeting large controllers — there is no **open, minimal, safety-first reference implementation** that a student can build, understand line-by-line, and extend. This project fills that slot.

### 2.3 Target users

- Network operators who want a "natural language interface to the network"
- Students/educators wanting a teaching-grade reference implementation of AI-driven network automation
- Researchers needing a baseline system for intent-translation evaluation

---

## 3. Objectives

| # | Objective | Success criterion |
|---|---|---|
| O1 | Build an LLM agent that translates natural-language intents into structured SDN operations | ≥ 90% of test intents produce valid, executable operations |
| O2 | Implement a safety/validation layer (schema validation, conflict detection, dry-run) | 100% of malformed or conflicting operations blocked before install |
| O3 | Integrate with a live SDN environment (Mininet + Ryu) for flow management, topology query, and verification | Live flow install/remove/verify on emulated network |
| O4 | Provide a chat UI demonstrating end-to-end operation | 30-second demo: request → validation → install → verified |
| O5 | (Research extension) Evaluate fine-tuned small models vs API models on intent translation | Accuracy/cost/latency comparison on IBNBench |

---

## 4. Background & Related Work

| Work | Venue/Year | Relevance to this project |
|---|---|---|
| NetIntent + IBNBench | IEEE OJ-COMS 2025 | End-to-end LLM IBN pipeline on ODL/ONOS; released the first public intent-translation benchmark (50 intents/dataset) |
| Confucius | SIGCOMM 2025 (Meta) | Production multi-agent LLM network management: DAG planning, RAG memory, validation + human approval — the industry template |
| INTA | IEEE 2025 | LLM-agent intent-based translation of network configurations across vendors (98% syntactic accuracy) |
| NetConfEval | SIGCOMM 2024 | Benchmark showing LLMs can generate formal specs, API calls, and low-level configs |
| NetLLM | SIGCOMM 2024 | LLM as foundation model for networking; low-rank adaptation cuts fine-tuning cost (0.31% params) |
| CEGS | NSDI 2025 | GNN+LLM configuration synthesis at 1094-device scale |

**Positioning:** Existing systems are production-internal (Confucius), target heavy controllers (NetIntent), or address config translation rather than live SDN control (INTA). **NetCopilot** is a minimal, open, safety-first reference system: a single-controller agent loop with an explicit validation layer, designed to be readable, demoable, and extendable.

---

## 5. Proposed System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Chat UI (Chainlit/Streamlit)              │
│              "Block 10.0.0.5 from the DB VLAN"               │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    LLM Agent (function-calling loop)          │
│  • system prompt: role, tool schema, safety rules            │
│  • plan → call tool → observe → iterate (max N rounds)       │
│  • provider: OpenRouter / OpenCode Zen / local (Ollama)      │
└──────────────────────────┬──────────────────────────────────┘
                           │ tool calls (JSON)
┌──────────────────────────▼──────────────────────────────────┐
│                      Tool Layer (Python)                      │
│  get_topology()  get_flows()  get_stats()                     │
│  install_flow()  remove_flow()  verify_flow()                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                  SAFETY & VALIDATION LAYER                    │
│  1. JSON schema validation  (reject malformed tool args)     │
│  2. Conflict detection      (vs. existing flow rules)         │
│  3. Policy guardrails       (block dangerous ops by default)  │
│  4. Dry-run mode            (preview before apply)            │
│  5. Post-install verification (reachability/ping checks)      │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST (Ryu app)
┌──────────────────────────▼──────────────────────────────────┐
│         Ryu SDN Controller  ←→  Mininet (campus/leaf-spine)   │
│         OpenFlow 1.3, flow tables, stats, topology            │
└───────────────────────────────────────────────────────────────┘
```

**Design principles:**
- **Minimal**: plain Python function-calling loop, no LangChain/LlamaIndex dependency
- **Safety-first**: validation is a hard gate between the LLM and the network, not an afterthought
- **Explainable**: every action is logged with the intent, generated operation, validation result, and verification outcome
- **Controller-portable**: tool layer abstracts Ryu REST; ONOS can be swapped in later

---

## 6. Methodology & Work Plan (8 weeks)

| Phase | Duration | Deliverable | Exit criterion |
|---|---|---|---|
| **P1: Core plumbing** | Week 1–2 | Mininet topology (leaf-spine, 4–8 switches) + Ryu controller with REST app | Flow rules installable/removable via curl |
| **P2: Agent loop** | Week 3–4 | LLM agent with tool calling; validate → apply → verify pipeline | "Block host X" end-to-end works via CLI |
| **P3: Safety + UI** | Week 5–6 | Validation layer complete; Chainlit/Streamlit chat UI | Malformed/conflicting ops blocked; UI demo works |
| **P4: Scenarios + hardening** | Week 7–8 | Demo scenarios, eval suite, README, architecture diagram, demo video | All O1–O4 criteria met |

**Demo scenarios (evaluation suite):**
1. **Security**: block/allow traffic between hosts and VLANs; detect conflicts with existing rules
2. **QoS**: prioritize a flow (queue/bandwidth), then verify via iperf throughput
3. **Observability**: ask topology/flow/stat questions in natural language
4. **Failure diagnosis**: link down → agent reads stats, localizes the failure, proposes a reroute
5. **Safety tests**: malformed input, conflicting intent, "block everything" — all rejected or dry-run gated

**Evaluation metrics:** intent-to-operation success rate, schema-validation pass rate, conflict-detection recall (on crafted cases), end-to-end latency per operation, and (O5) accuracy/cost/latency of the translation component.

---

## 7. Tech Stack

| Component | Choice | Why |
|---|---|---|
| Emulation | Mininet 2.3 | Standard SDN research emulator; runs on a laptop |
| Controller | Ryu (OpenFlow 1.3) + REST app | Python, minimal, widely used in academia |
| LLM access | OpenRouter / OpenCode Zen API; Ollama for local fallback | Already available; model-agnostic design |
| Agent loop | Plain Python function-calling (OpenAI-compatible `tools` API) | No framework lock-in; explainable |
| UI | Chainlit or Streamlit | Fast, demo-friendly |
| Verification | Scapy / ping / iperf3 | Reachability and performance checks |

---

## 8. Expected Outcomes & Deliverables

1. **Working system**: NetCopilot — natural-language network management on a live emulated SDN (repo: GitHub, public)
2. **Documentation**: README with architecture diagram + demo video; API docs for the tool layer
3. **Project report** (thesis format) covering design, safety analysis, and evaluation results
4. **Research extension** (optional but planned): fine-tune a small open model (Qwen2.5-3B/7B, LoRA) on IBNBench intent-translation data; compare accuracy/cost/latency vs. API models (zero-shot) — target: IEEE/Springer conference paper
5. **Resume assets**: demo video, GitHub repo, LinkedIn writeup, hackathon entry

---

## 9. Risks & Mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM tool-calling errors / malformed JSON | High | Schema-validated tool layer; automatic retry loop; always-on dry-run default |
| LLM hallucinates nonexistent hosts/ports | Medium | Tool layer resolves names against live topology before install |
| Safety bypass (harmful intent slips through) | Medium | Policy guardrails as hard-coded checks independent of the LLM |
| Mininet/Ryu environment issues | Low | Containerized setup (Docker) with pinned versions |
| Scope creep | High | 8-week plan with explicit exit criteria; research extension is phase O5, optional |
| API cost during development | Low | Small models for dev; caching; local Ollama fallback |

---

## 10. Timeline Summary

```
Week 1-2  ████████  P1: Mininet + Ryu REST working
Week 3-4  ████████  P2: Agent loop end-to-end
Week 5-6  ████████  P3: Safety layer + UI
Week 7-8  ████████  P4: Scenarios, eval, docs, demo
[Optional] +4 weeks: O5 fine-tuning experiments → paper draft
```

---

## 11. References

1. T. Alam et al., "NetIntent: Leveraging Large Language Models for End-to-End Intent-Based SDN Automation," IEEE Open Journal of the Communications Society, 2025. doi:10.1109/OJCOMS.2025.3642642 (introduces IBNBench)
2. J. Liu et al., "Intent-Driven Network Management with Multi-Agent LLMs: The Confucius Framework," ACM SIGCOMM 2025. (Meta's production system)
3. S. Zhang et al., "INTA: Intent-Based Translation for Network Configuration with LLM Agents," IEEE 2025. arXiv:2501.08760
4. S. Yang et al., "NetConfEval: Can LLMs Facilitate Network Configuration?," ACM SIGCOMM 2024. doi:10.1145/3656296
5. Y. Wu et al., "NetLLM: Adapting Large Language Models for Networking," ACM SIGCOMM 2024. arXiv:2402.02338
6. J. Liu et al., "CEGS: Configuration Example Generalizing Synthesizer," USENIX NSDI 2025.
7. RFC 9315 — Intent-Based Networking Concepts and Definitions.
