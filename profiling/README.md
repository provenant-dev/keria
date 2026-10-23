# KERIA Profiling Framework

Repeatable local framework to find where KERIA spends CPU/latency as KEL size, TEL
churn, delegation, witness-fleet size, and client concurrency grow. Built on py-spy
(sampling flamegraphs, attach-only) with an opt-in cProfile/yappi deep-dive.

## Why

DEV e2e traces showed KERIA-mediated operations (`/operations/witness.*`,
`/operations/oobi.*`) dominated wall-clock while the witnesses themselves were fast,
and KERIA has no cross-agent KEL cache (each agent = its own `Habery` LMDB). This
framework reproduces those pressures locally and profiles the KERIA process directly.

## Topology

`compose.profiling.yaml` is self-contained and reuses signify-ts's config files:

- **vlei-server** (schemas) on :7723
- **witness-demo** — all 6 vanilla keripy witnesses (wan/wil/wes/wit/wub/wyz) on
  :5642–5647; KERIA already preloads all 6 OOBIs via `signify-ts/config/keria.json`
- **keria** — built from local `keria/`, with local `keria/` **and** `keripy/` source
  bind-mounted and put on `PYTHONPATH` so edits take effect on
  `docker compose restart keria` with **no rebuild**. `cap_add: SYS_PTRACE`.
- **profiler** — debian + py-spy sidecar sharing KERIA's PID namespace; attaches to
  the KERIA process and writes flamegraphs. KERIA's image stays pristine.

KERIA is a single-process, single-thread hio loop, so one py-spy flamegraph maps loop
time directly (no threads/GIL). Each phase captures two profiles: **on-CPU** (compute
hotspots: Ed25519 verify, blake3 SAID, CESR parse, LMDB) and **`--idle`** (adds parked
frames: where the loop blocks on witness-receipt polling / LMDB / escrow).

## Quick start

```bash
cd keria/profiling
./run.sh up        # build + start stack (first build pulls py-spy, builds keria)
./run.sh smoke     # one tiny phase end-to-end; proves the pipeline
./run.sh matrix    # full isolation + modest-contention sweep (long)
./run.sh down      # tear down (-v)
```

One-off phase: `./run.sh phase <workload> <ixn|rot> <N> <W> <FLEET>`, e.g.
`./run.sh phase kel-verify ixn 300 6 1`.

## Workloads (signify-ts load generators)

Live in `signify-ts/test-integration/profiling/*.test.ts` (co-located so they reuse the
existing integration utils + toolchain). Parameterized entirely by `PROFILE_*` env vars
(`profiling-util.ts`), so the orchestrator drives them with no code edits.

- **kel-verify** — grow an issuer AID to N events (ixn or rot), then fresh client(s)
  resolve its OOBI → KERIA fetches + verifies the full KEL. Primary op: `resolve-verify`.
- **tel-churn** — issue then revoke a credential for N cycles (two TEL events + two KEL
  anchors each). Ops: `issue`, `revoke`.
- **delegation** — delegator approves a delegate; grow delegate KEL to N (ixn); fresh
  client verifies delegate KEL + delegator KEL + anchor. Op: `resolve-verify`.

### Witness-fleet size (W)

Threaded through every workload. AIDs are created with **W ∈ {0..6}** witnesses:

- **W=0 — baseline floor**: witnessless controller (`wits:[]`, `toad=0`, the only legal
  toad=0 per keripy `eventing.py:597`). No witness receipting; operations gated only on
  the signify agent + KERIA. Every W≥1 result is overhead above this floor.
- **W≥1**: `toad = max(1, ceil(W/2))` (keripy default). toad=0 with witnesses is illegal,
  so W=0 is the only no-receipt case.

## Outputs

Everything lands in `./profiles/` (bind-mounted into keria + profiler):

- `<phase>.cpu.json`, `<phase>.idle.json` — speedscope (open at https://speedscope.app)
- `<phase>.cpu.svg` — flamegraph (if a fixed duration was used)
- `latency/latency.csv` — per-op client latency (`op, growth, n, witnesses, toad, fleet, ms`)
- `<phase>.stats.txt`, `<phase>.lmdb.txt` — CPU%/mem and LMDB size snapshots

`node analyze.mjs` prints p50/p90/p99 tables per phase+op and a `resolve-verify` scaling
curve. Correlate client p90 (from the CSV) against the server flamegraph for the same phase.

Phase naming: `<workload>_<growth>_n<N>_w<W>_f<FLEET>` ties the CSV rows, the flamegraphs,
and the stats snapshots together.

## Deep-dive (opt-in, OFF by default)

When py-spy fingers a function, get deterministic per-function numbers with
`deepdive/sitecustomize.py` (prefers yappi, falls back to stdlib cProfile). Enable by
starting KERIA with the deepdive dir on `PYTHONPATH` and `KERIA_DEEPDIVE=1`, then
`kill -USR2` the KERIA pid to dump a callgrind/pstats snapshot to `./profiles/`. See the
header of `deepdive/sitecustomize.py` for the exact commands.

## Runtime base & config (validated, local-source quirks)

- **KERIA base image**: the local keri/keripy deps don't build on the only cached
  alpine (3.14 → `rpds-py` Rust build fails), so `keria` runs on the cached
  `weboftrust/keria:0.4.0` image (Python 3.12 + the keri 1.2.13 dep tree) with the
  **local keria+keripy source shadowed via `PYTHONPATH`** — verified at runtime that
  `keri`/`keria` import from `/src/...`. Swap to `keria.profiling.dockerfile` /
  `images/keria.dockerfile` to build from source if the 3.12 bases are available.
- **Agency config path**: this keripy branch's `Configer` (a) picks between the
  `--config-dir` path and `~/.keri` by which candidate files exist, and (b) `os.chmod`s
  the file at open. So the agency config (`keria-agency.json`, a local copy of
  signify-ts's `keria.json`) is mounted **read-write at both** `/keria/config/keri/cf/`
  and `/root/.keri/cf/`. Without this the agency loads empty curls/iurls → no agent
  endpoint (empty agent OOBI) and unknown witnesses. Verify with:
  `docker compose -f compose.profiling.yaml exec keria python -c "from keria.app.agenting import readConfigFile; print(readConfigFile('/keria/config','keria').get())"`
- **Witness OOBIs**: the load generators call `ensureWitnesses()` to resolve the first
  W witness controller OOBIs before creating witnessed AIDs (idempotent; guards against
  the iurl auto-resolution racing the AID create).
- **Verify OOBI role**: `getVerifyOobi()` uses the **witness**-role OOBI for W≥1 (forces
  KERIA to fetch the KEL from the witnesses — the behavior under study) and the
  **agent**-role OOBI for the W=0 witnessless baseline.

## Viewing flamegraphs (WSL-friendly)

speedscope's web/CLI viewers are awkward on WSL (no local browser). Convert to
self-contained SVGs that open directly in any browser:

```bash
python3 flamegraph.py --all          # profiles/*.json -> profiles/*.svg
cp profiles/*.svg /mnt/c/Users/<you>/Downloads/keria-flamegraphs/   # open from Windows
```

Hover a box for name + %; widest boxes = biggest cost (identical stacks are merged).
For full interactivity instead, open a `*.cpu.json` at speedscope.app from Windows via
`\\wsl.localhost\<distro>\...\profiles`.

## Healthcheck must be cheap (don't profile the healthcheck)

The container healthcheck uses the **boot-port `/health` (3903)**, not admin
`/spec.yaml`. `/spec.yaml` regenerates and YAML-serializes the whole OpenAPI spec on
every request; at a 2s interval that added ~20-30% `apispec`/`yaml` noise to CPU
flamegraphs. Latency measurements (client-side wall-clock) are unaffected either way —
only the CPU-sample attribution was polluted.

## Gotchas

- **py-spy symbols**: the sidecar produces python-level frames from interpreter memory;
  native (C-extension) frames aren't symbolized (we don't profile native). Fine for us.
- **Editable source**: `PYTHONPATH=/src/keria/src:/src/keripy/src` shadows the pip-installed
  packages. Verify with `docker compose -f compose.profiling.yaml exec keria python -c
  "import keri, keria; print(keri.__file__, keria.__file__)"` → should point under `/src`.
- **Determinism**: fixed witnesses but variable receipt timing; report distributions, and
  `run.sh` resets KERIA's LMDB between phases so state never leaks.
- **Host vs container paths**: load generators run on the host (vitest) talking to
  localhost:3901/3903; py-spy runs in the sidecar. Both write to the same host `./profiles`.
