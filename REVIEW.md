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

**Findings (11) — N1..N11:**
- N1 BLOCKER: os-ken ships no REST apps/manager → own the REST layer (Option A) or vendor from Ryu (Option B). Option A recommended (net LOC down).
- N2 MAJOR: delete-by-cookie without cookie_mask (OF1.3 mask 0 = match every cookie) wipes baseline flows.
- N3 MAJOR: in-memory op-id counter resets on restart → cookie collision; seed from max(op_id) over installed flows.
- N4 MAJOR: no launcher without osken-manager; `--observe-links` defaults False → empty topology APIs.
- N5 MINOR: idle_timeout on drop flow → block silently lifts.
- N6 MINOR: "unreachable" ≠ "blocked"; pre/post probe pair.
- N7 MINOR: QoS intents unmeasurable without queues; state metric = "correct flow installed".
- N8 MINOR: Fallback B dead weight (Gate 0 passed); retarget Gate 0 at OVS-in-container.
- N9 MINOR: privileged container is not a security boundary; one honest line.
- N10 NIT: os_ken.topology.switches already tracks hosts; add only ARP-snoop IP learning.
- N11 NIT: eventlet compat retired empirically; replace E.2 with packaging gap as real controller risk.

**Author Q&A:** Q1 Option A (Q2: verified — ofctl_rest absent from upstream git too, vendoring impossible); Q3 launcher in-repo manage.py; Q4 topology PNG cuttable; Q5 QoS = DSCP-marking flows, metric = flow present.

---

## Review v3 (plan v3) — GO / APPROVE WITH CHANGES (os-ken source-verified)

**Verified from os-ken 4.2.1 source:**
- `AppManager.run_apps` usable (Ryu's wsgi.start_service removed — won't trip).
- `--observe-links` defaults False (topology/switches.py:48).
- `ofctl_v1_3.mod_flow_entry` at :1049; `cookie_mask` default 0 at :1050-1051 — the N2 footgun is one keystroke away.
- `OFP_TCP_PORT = 6653` default (ofproto_common.py:27); 6633 is OFP_TCP_PORT_OLD.
- `os_ken/lib/hub.py:169-180` imports eventlet.wsgi, exposes patch/spawn/listen.
- Caveat: Docker socket became permission-denied mid-session; N12 rests on documented NAT semantics — verify in Gate 0.

**Findings (9) — N12..N20:**
- N12 MAJOR: host/container wiring broken 3 ways (controller-on-host unreachable — OVS switches dial OUT, nothing listens in container on 6653; Runner loopback inside container never sees published DNAT traffic; 8080 published but nothing to publish). Fix: everything lab-side in container, listeners 0.0.0.0 inside, publish only loopback-pinned 5100/8081. Alternative --network host.
- N13 MAJOR: app list omits `os_ken.controller.ofp_handler` → no OF listener socket ever opens; silent failure. Verified: app_manager.py has zero references; socket comes from OFPHandler.start().
- N14 MAJOR: oslo.config ordering — ofp-* opts registered in controller.py:58-66, --observe-links in topology/switches.py:48; NOT in flags.py. Parse-before-import → unrecognized arguments. Import-before-parse exactly as ryu-manager did.
- N15 MINOR: flask_app.run() in eventlet-monkey-patched process = hang; serve via hub.spawn(eventlet.wsgi.server, eventlet.listen(...), flask_app).
- N16 MINOR: read path needs waiters/multipart-reply correlation dict; use shipped `os_ken.app.ofctl.api` (send_msg/get_datapath, OfctlService brick) instead.
- N17 NIT: 6653 already default; keep knob, delete "configurable" framing.
- N18 MINOR: eval QoS category unexecutable (no mark_dscp in install_flow schema) — add action or cut category.
- N19 NIT: hard_timeout has same silent-unblock property; forbid both on drops or OFPFF_SEND_FLOW_REM.
- N20 NIT: all-agent mask delete nukes other sessions' flows; session id in cookie bits.

**Launcher recipe (fixes N13+N14+N15 together):** hub.patch(thread=False) → import cfg/flags/controller/topology (noqa) → cfg.CONF(['--observe-links','--ofp-tcp-listen-port','6653'], project='netcopilot') → AppManager.run_apps(['os_ken.controller.ofp_handler','netcopilot.controller.app','os_ken.topology.switches']).

**Top 5 risks (v3):** N12 wiring (+ LAN-exposed exec endpoint if naively fixed), N13/N14 silent launcher failures, OVS-in-container, Phase 3 UI overrun, N18 QoS action gap.

**Author Q&A (v4 decisions):** Q1 controller inside container; Q2 agent uses published 127.0.0.1:8081 (REST) + 127.0.0.1:5100 (Runner); Q3 mark_dscp added (20 intents kept); Q4 both timeouts forbidden on drops.

---

*Append future reviews here (reviewer: date, plan version, verdict, findings, evidence).*
