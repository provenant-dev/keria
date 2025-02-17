"""
main entry package

Entrypoint module, in case you use `python -m keria`.


Why does this file exist, and why __main__? For more info, read:

- https://www.python.org/dev/peps/pep-0338/
- https://docs.python.org/3/using/cmdline.html#cmdoption-m
"""

from keria.app.cli.keria import main

import cProfile
import pstats
import threading
import time

def profile_app(interval=10, output_file="/reports/profile.prof"):
    profiler = cProfile.Profile()
    profiler.enable()

    def save_report():
        while True:
            time.sleep(interval)
            profiler.dump_stats(output_file)

    # Start background thread to save profile periodically
    threading.Thread(target=save_report, daemon=True).start()
    return profiler

if __name__ == "__main__":
    profiler = profile_app()
    main.run()
