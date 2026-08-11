# NetCopilot — Review Records

History of external technical reviews of `PLAN.md`. Each review was performed by an independent agent with access to the repo. All findings were dispositioned in the plan's Part G; this file preserves the raw review evidence.

---

## Review v1 (plan v1) — GO / APPROVE WITH CHANGES

**Environment landmine:** plan targeted Ubuntu VM; dev machine is CachyOS/Arch (pacman, Python 3.14 only, sudo broken via `no_new_privs`, OVS module present). Mininet dead on arrival natively; decide dev target.

**Findings (9):**
- BLOCKER: Ryu dead on modern Python (eventlet/distutils); use os-ken, not ONOS.
- MAJOR: priority band ≠ identity; use OpenFlow cookie.
- MAJOR: Lab Runner command injection (shell interpolation of dst).
- MAJOR: conflict detection scope-creep; bound to exact + full-shadow on supported fields.
- MINOR: port 6633 vs 6653; make configurable.
- MINOR: concurrent access to single Mininet process; global lock.
- NIT: verification must match intent protocol (ping vs port probe).
- NIT: pull controller spike into Phase 0 day 1.
- (env) Decide dev target.

---

## Review v2 (plan v2) — GO / APPROVE WITH CHANGES (empirical spikes)

**Empirical results on dev box:**
- Gate 0 PASSES: docker 29.7.2, daemon reachable, user in docker group (no sudo needed); `--privileged` netlink ops OK; podman not installed.
- Gate 1 FAILS: os-ken 4.2.1 wheel has no `ofctl_rest`, no `rest_topology`, no `wsgi`, no `osken-manager`, no entry_points (1437-file RECORD — not truncated). OpenStack strips REST apps; Neutron consumes os-ken as a library.
- Ryu retest: build fails on py3.9/3.10/3.11 (setuptools legacy); with setuptools<58 resolves to ryu 2.2 (2015) which fails on missing `oslo` module. Real Ryu 4.34 needs pinned old setuptools + eventlet in py3.9 image. Abandoning Ryu confirmed correct.

**Findings (11) — N1..N11:** N1 BLOCKER os-ken ships no REST apps/manager → Option A (own the REST layer); N2 MAJOR delete-by-cookie without cookie_mask wipes baseline; N3 MAJOR op-id counter resets on restart → seed from installed flows; N4 MAJOR no launcher + `--observe-links` default False; N5 MINOR idle_timeout on drop silently un-blocks; N6 MINOR "unreachable" ≠ "blocked" → pre/post probe pair; N7 MINOR QoS unmeasurable without queues; N8 MINOR Fallback B dead weight; N9 MINOR privileged container ≠ security boundary; N10 NIT lean on `os_ken.topology.switches` host tracking; N11 NIT eventlet compat retired empirically.

**Author Q&A:** Option A ✅ (ofctl_rest absent from upstream git too — verified by author); launcher in-repo manage.py; topology PNG cuttable; QoS = DSCP-marking, metric = flow present.

---

## Review v3 (plan v3) — GO / APPROVE WITH CHANGES (os-ken source-verified)

**Verified from os-ken 4.2.1 source:** `AppManager.run_apps` usable (Ryu's wsgi.start_service removed); `--observe-links` default False (topology/switches.py:48); `mod_flow_entry` at ofctl_v1_3.py:1049, `cookie_mask` default 0 at :1050-1051; `OFP_TCP_PORT=6653` default; `lib/hub.py:169-180` exports patch/spawn/listen + eventlet.wsgi. Caveat: Docker socket became permission-denied mid-session; N12 rests on documented NAT semantics — verify in Gate 0.

**Findings (9) — N12..N20:** N12 MAJOR host/container wiring broken 3 ways → everything lab-side in container, listeners 0.0.0.0 inside, publish only loopback-pinned 5100/8081 (never bare `-p`); N13 MAJOR app list omits `ofp_handler` → no OF listener, silent failure; N14 MAJOR oslo.config order — opts in controller.py/topology/switches.py, NOT flags.py; N15 MINOR flask_app.run() in eventlet-patched process → serve via hub.spawn(eventlet.wsgi.server, ...); N16 MINOR read path needs waiters machinery → use shipped `os_ken.app.ofctl.api`; N17 NIT 6653 already default; N18 MINOR QoS unexecutable → add mark_dscp; N19 NIT hard_timeout same silent-unblock property; N20 NIT all-agent mask delete nukes other sessions.

**Launcher recipe (fixes N13+N14+N15):** hub.patch(thread=False) → import cfg/flags/controller/topology → cfg.CONF([...]) → AppManager.run_apps([ofp_handler, app, topology.switches]).

---

## Review v4 (plan v4) — GO / APPROVE WITH CHANGES (os-ken source + repo state verified)

**Verified:** `OSKenApp.start()` returns None; `OFPHandler.start()` returns the ONLY long-lived thread handed to run_apps — omit it and the process exits silently rc 0 (worse than a missing socket); import-before-parse confirmed; cookie masks arithmetic checked (correct, non-overlapping with cookie 0); `mod_flow_entry` cookie_mask default 0 confirmed one keystroke away; wiring fix sound; **`.gitignore` absent, stray shell dotfiles untracked in repo root** (N25).

**Findings (9) — N21..N29:** N21 **MAJOR** `h.cmd()` always goes through the node's bash (`sendCmd` joins args into one string into the persistent bash session) → "list-arg exec" is false comfort; use `h.popen([...])` (shell=False via mnexec argv); validation stays THE control; `sendCmd` asserts `not self.waiting` → global lock is load-bearing for correctness. N22 MINOR attribution regression: topology ← `os_ken.topology.api`, flow stats ← `os_ken.app.ofctl.api`. N23 MINOR bare `SetField(ip_dscp)` blackholes (no implicit forwarding) → mark_dscp = [SetField, Output(resolved out_port)]. N24 MINOR ofctl.api requires OfctlService; require_app registers on importing frame → top-level import + explicit name in run_apps. N25 MINOR no .gitignore; create before .env. N26 NIT day 3 unassigned. N27 NIT timeout fields vanished ambiguously → no agent flow ever carries a timeout. N28 NIT session-id source → max(session)+1 from audit tail. N29 NIT os_ken.hub monkey-patches sockets → confine imports to controller/ + CI grep guard.

**Bonus:** TCP probe tooling — busybox `nc` has no `-z`; pin netcat-openbsd or use Python socket via popen (chosen: Python socket).

**Reviewer's note (adopted in v5):** "This is the last review that will earn its keep... Fix N21–N25 in the text, then go write manage.py." Plan text frozen unless a spike fails; future reviews review code.

**Empirical addendum (2026-08-12, author boot test — no switch needed):** running the launcher for real caught three bugs no paper review did: (1) `hub.eventlet` does not exist under the default NATIVE hub (`OSKEN_HUB_TYPE` unset) — N15's `eventlet.wsgi.server` recipe raised AttributeError at runtime; fixed with werkzeug `make_server` + `hub.spawn(srv.serve_forever)`; (2) `hub.semaphore()` → `hub.Semaphore()` (native hub API); (3) missing `netcopilot/__init__.py` + `controller/__init__.py` made Flask's `get_root_path` crash on the namespace package. After fixes: `/health` → `{"status":"ok","switches":0}`, `/switches` → `{"dpids":[]}`, OF listener confirmed on 0.0.0.0:6653, process stays alive (N13 guard holds). This validates the reviewer's claim that launcher correctness is provable WITHOUT Mininet; switch handshake + cookie round-trip remain for Gate 1 in the lab container.

---

*Append future reviews here (reviewer: date, plan version, verdict, findings, evidence).*
