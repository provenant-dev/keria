"""
main entry package

Entrypoint module, in case you use `python -m keria`.


Why does this file exist, and why __main__? For more info, read:

- https://www.python.org/dev/peps/pep-0338/
- https://docs.python.org/3/using/cmdline.html#cmdoption-m
"""

from keria.app.cli.keria import main

import cProfile

if __name__ == "__main__":
    cProfile.run("main.run()", "/reports/profile_data.prof")
