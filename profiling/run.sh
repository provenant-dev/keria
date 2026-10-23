#!/usr/bin/env bash
# KERIA profiling orchestrator.
#
# Brings up the local stack (vlei + 6 witnesses + local-source KERIA + py-spy sidecar),
# then drives workload phases. Per phase it: resets KERIA to a clean LMDB, starts py-spy
# recording (on-CPU + idle), runs a signify-ts load generator against KERIA, stops
# py-spy, and snapshots CPU%/LMDB size. Flamegraphs land in ./profiles/<phase>.{cpu,idle}.json
# and per-op latencies in ./profiles/latency/latency.csv.
#
#   ./run.sh up                      # build + start the stack
#   ./run.sh smoke                   # one tiny end-to-end phase (proves the pipeline)
#   ./run.sh matrix                  # the default isolation + modest-contention sweep
#   ./run.sh phase <wl> <ixn|rot> <N> <W> <FLEET>   # one arbitrary phase
#   ./run.sh down                    # tear everything down
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
COMPOSE=(docker compose -f compose.profiling.yaml)
SIGNIFY="$HERE/../../signify-ts"
PROFILES="$HERE/profiles"
LATENCY="$PROFILES/latency/latency.csv"
export TEST_ENVIRONMENT=local

log() { printf '\n\033[1;36m[run] %s\033[0m\n' "$*"; }

up() {
    mkdir -p "$PROFILES/latency"
    "${COMPOSE[@]}" up -d --build
    wait_health
    log "stack up. KERIA on :3901/:3902/:3903, witnesses :5642-5647, vlei :7723"
}

down() { "${COMPOSE[@]}" down -v; }

wait_health() {
    log "waiting for KERIA health ..."
    for _ in $(seq 1 60); do
        if curl -fsS http://localhost:3903/health >/dev/null 2>&1; then
            log "KERIA healthy"; return 0
        fi
        sleep 2
    done
    echo "KERIA did not become healthy" >&2; exit 1
}

# Fresh KERIA LMDB between workload families so KEL/TEL state never leaks across phases.
reset_keria() {
    log "resetting KERIA (clean LMDB)"
    "${COMPOSE[@]}" rm -sf keria >/dev/null 2>&1 || true
    "${COMPOSE[@]}" up -d keria >/dev/null
    wait_health
    # profiler shares keria's pid namespace; recreate it so it re-attaches.
    "${COMPOSE[@]}" up -d --force-recreate profiler >/dev/null
    sleep 2
}

pyspy() { "${COMPOSE[@]}" exec -T profiler bash /usr/local/bin/pyspy.sh "$@"; }

# profile_phase <workload> <growth> <N> <W> <FLEET>
profile_phase() {
    local workload="$1" growth="$2" n="$3" w="$4" fleet="$5"
    local phase="${workload}_${growth}_n${n}_w${w}_f${fleet}"
    local spec="profiling/${workload}.test.ts"
    log "PHASE $phase"

    # Start py-spy recording in the background sidecar (runs until we SIGINT it).
    "${COMPOSE[@]}" exec -d profiler bash /usr/local/bin/pyspy.sh record "$phase" >/dev/null

    # Run the load generator on the host against KERIA.
    set +e
    ( cd "$SIGNIFY" && \
        PROFILE_WORKLOAD="$workload" PROFILE_GROWTH="$growth" \
        PROFILE_N="$n" PROFILE_W="$w" PROFILE_FLEET="$fleet" \
        PROFILE_PHASE="$phase" PROFILE_OUT="$LATENCY" \
        TEST_ENVIRONMENT=local \
        npx vitest -c vitest.integration.ts run "$spec" )
    local rc=$?
    set -e

    # Stop py-spy (flushes speedscope on SIGINT) and snapshot resource use.
    pyspy_stop
    "${COMPOSE[@]}" exec -T keria sh -c \
        'du -sh $(python -c "import keria" 2>/dev/null; echo /usr/local/var/keri 2>/dev/null) 2>/dev/null || true' \
        > "$PROFILES/${phase}.lmdb.txt" 2>/dev/null || true
    docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' \
        > "$PROFILES/${phase}.stats.txt" 2>/dev/null || true

    [ $rc -ne 0 ] && log "WARNING: phase $phase load generator exited $rc (profile still captured)"
    log "wrote $PROFILES/${phase}.cpu.json"
}

pyspy_stop() {
    "${COMPOSE[@]}" exec -T profiler pkill -INT -f py-spy >/dev/null 2>&1 || true
    # Wait for py-spy to actually finish flushing (large speedscope files on heavy
    # phases can take several seconds); poll instead of a fixed sleep so the next
    # reset_keria never kills the profiler mid-write (that left empty profiles before).
    for _ in $(seq 1 30); do
        "${COMPOSE[@]}" exec -T profiler pgrep -f py-spy >/dev/null 2>&1 || break
        sleep 1
    done
    sleep 1
}

smoke() {
    reset_keria
    profile_phase kel-verify ixn 20 1 1
    log "smoke done. Inspect $PROFILES/kel-verify_ixn_n20_w1_f1.cpu.json and $LATENCY"
}

# Default matrix: isolation baselines + modest contention.
#  - KEL size sweep at fixed W=3 (ixn and rot)
#  - Witness-fleet sweep at fixed N=300 (incl W=0 witnessless baseline)
#  - TEL churn sweep
#  - Delegation KEL-verify sweep (ixn only)
#  - Contention: kel-verify at fleets 5/10/25
matrix() {
    for growth in ixn rot; do
        for n in 100 200 300 400 500; do
            reset_keria; profile_phase kel-verify "$growth" "$n" 3 1
        done
    done
    for w in 0 1 2 3 4 5 6; do
        reset_keria; profile_phase kel-verify ixn 300 "$w" 1
    done
    for m in 25 50 100; do
        reset_keria; profile_phase tel-churn ixn "$m" 3 1
    done
    for n in 100 200 300 400 500; do
        reset_keria; profile_phase delegation ixn "$n" 3 1
    done
    for f in 5 10 25; do
        reset_keria; profile_phase kel-verify ixn 300 3 "$f"
    done
    log "matrix complete. Run analyze.mjs for percentile tables."
    node "$HERE/analyze.mjs" "$LATENCY" || true
}

case "${1:-}" in
    up) up ;;
    down) down ;;
    smoke) up; smoke ;;
    matrix) up; matrix ;;
    phase) shift; reset_keria; profile_phase "$@" ;;
    reset) reset_keria ;;
    *) echo "usage: $0 {up|down|smoke|matrix|phase <wl> <ixn|rot> <N> <W> <FLEET>|reset}" >&2; exit 1 ;;
esac
