#!/usr/bin/env bash
# py-spy wrapper, run INSIDE the profiler sidecar (shares KERIA's PID namespace).
#
#   pyspy.sh record <phase> [seconds]   # on-CPU + idle speedscope + SVG for a phase
#   pyspy.sh record-bg <phase>          # start background record; prints PID to stop later
#   pyspy.sh dump                       # one stack snapshot (stuck/long-running frames)
#   pyspy.sh top                        # live view
#
# KERIA is a single-thread hio loop, so the on-CPU profile shows compute hotspots
# (CESR parse, Ed25519 verify, LMDB). `record` captures a SINGLE on-CPU profile
# (reliable); for a blocked/parked view of a phase use `record-idle` separately.
set -euo pipefail

OUT=/profiles
RATE="${PYSPY_RATE:-100}"

keria_pid() {
    # The KERIA process is the python interpreter running the `keria` entrypoint.
    pgrep -f 'keria' | head -1 || true
    # Fallback: first python process in the shared namespace that isn't us.
}

pid() {
    local p
    p="$(pgrep -f 'bin/keria' | head -1 || true)"
    [ -z "$p" ] && p="$(pgrep -f 'keria.app.cli' | head -1 || true)"
    [ -z "$p" ] && p="$(pgrep -x python3 | head -1 || true)"
    [ -z "$p" ] && p="$(pgrep -x python | head -1 || true)"
    if [ -z "$p" ]; then
        echo "pyspy.sh: could not find KERIA PID in namespace" >&2
        exit 2
    fi
    echo "$p"
}

cmd="${1:-}"; shift || true

case "$cmd" in
    record)
        phase="${1:?usage: pyspy.sh record <phase> [seconds]}"
        # Always bound the record with --duration so py-spy exits via its reliable
        # duration path and writes the file. Relying on a SIGINT flush of a huge buffer
        # on ultra-long phases (N=500 etc.) produced 0-byte files. A bounded window is
        # all a flamegraph needs — relative hotspot shape is duration-independent. The
        # orchestrator still SIGINTs short phases early (small buffer flushes fine).
        secs="${2:-0}"
        [ "$secs" = "0" ] && secs="${PYSPY_MAXDUR:-200}"
        p="$(pid)"
        dur=(--duration "$secs")
        # SINGLE on-CPU record. Running two py-spy on one PID concurrently raced at
        # attach and silently lost one output on long/busy phases (empty/missing files);
        # on-CPU is the primary view anyway. For a blocked/parked view of one phase, use
        # `record-idle` separately (not concurrently with this). stderr is logged so any
        # future failure is visible instead of silent.
        echo "pyspy: recording (on-cpu) phase=$phase pid=$p rate=$RATE"
        py-spy record --pid "$p" --rate "$RATE" --nonblocking \
            --format speedscope -o "$OUT/${phase}.cpu.json" "${dur[@]}" \
            2>"$OUT/${phase}.cpu.pyspy.log"
        echo "pyspy: wrote $OUT/${phase}.cpu.json"
        ;;
    record-idle)
        phase="${1:?usage: pyspy.sh record-idle <phase> [seconds]}"
        secs="${2:-0}"
        p="$(pid)"
        dur=()
        [ "$secs" != "0" ] && dur=(--duration "$secs")
        echo "pyspy: recording (idle-inclusive) phase=$phase pid=$p rate=$RATE"
        py-spy record --pid "$p" --rate "$RATE" --nonblocking --idle \
            --format speedscope -o "$OUT/${phase}.idle.json" "${dur[@]}" \
            2>"$OUT/${phase}.idle.pyspy.log"
        echo "pyspy: wrote $OUT/${phase}.idle.json"
        ;;
    dump)
        py-spy dump --pid "$(pid)"
        ;;
    top)
        py-spy top --pid "$(pid)"
        ;;
    pid)
        pid
        ;;
    *)
        echo "usage: pyspy.sh {record <phase> [seconds]|dump|top|pid}" >&2
        exit 1
        ;;
esac
