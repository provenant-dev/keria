"""Opt-in line/function-level deep-dive profiler for KERIA. OFF by default.

py-spy (sampling) tells you WHERE the loop spends time; once it fingers a function,
this gives deterministic per-function numbers for it. It auto-loads only when the
KERIA process is started with this directory on PYTHONPATH AND an enabling env var —
otherwise it does nothing, so it is safe to leave mounted.

Enable (see README "Deep-dive"):
  - PYTHONPATH must include /src/deepdive (prepended) so Python auto-imports this.
  - KERIA_DEEPDIVE=1 starts the profiler at interpreter startup.
  - Send SIGUSR2 to the KERIA process to dump a snapshot and keep profiling:
        docker compose -f compose.profiling.yaml exec -T keria \
            sh -c 'kill -USR2 $(pgrep -f bin/keria | head -1)'
  - Output: /profiles/deepdive-<engine>-<n>.{callgrind|pstats}

Prefers yappi (callgrind, wall+cpu, lower overhead on the hio loop) if installed;
falls back to cProfile (stdlib, always available in the KERIA venv, higher overhead).
"""
import os
import signal

if os.environ.get("KERIA_DEEPDIVE") == "1":
    _OUT = os.environ.get("KERIA_DEEPDIVE_OUT", "/profiles")
    _count = {"n": 0}

    def _dump_yappi(yappi):
        def handler(signum, frame):
            _count["n"] += 1
            stats = yappi.get_func_stats()
            path = f"{_OUT}/deepdive-yappi-{_count['n']}.callgrind"
            stats.save(path, type="callgrind")
            print(f"[deepdive] wrote {path}", flush=True)
        return handler

    def _dump_cprofile(prof):
        def handler(signum, frame):
            _count["n"] += 1
            path = f"{_OUT}/deepdive-cprofile-{_count['n']}.pstats"
            prof.disable()
            prof.dump_stats(path)
            prof.enable()
            print(f"[deepdive] wrote {path}", flush=True)
        return handler

    try:
        import yappi  # type: ignore

        yappi.set_clock_type("wall")
        yappi.start()
        signal.signal(signal.SIGUSR2, _dump_yappi(yappi))
        print("[deepdive] yappi profiling ON (SIGUSR2 to dump)", flush=True)
    except Exception:
        import cProfile

        _prof = cProfile.Profile()
        _prof.enable()
        signal.signal(signal.SIGUSR2, _dump_cprofile(_prof))
        print("[deepdive] cProfile profiling ON (SIGUSR2 to dump)", flush=True)
