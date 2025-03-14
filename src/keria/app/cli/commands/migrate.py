# -*- encoding: utf-8 -*-
"""
KERIA
keria.cli.keria.commands module

"""
import argparse

from hio.base import doing
from keria.db import basing

from hio.base import doing
from keri import help, kering
from keri.db import basing
from keria.db import basing as abase

logger = help.ogler.getLogger()

parser = argparse.ArgumentParser(description='Migrates for all agents')
parser.set_defaults(handler=lambda args: handler(args),
                    transferable=True)
parser.add_argument('--base', '-b', help='additional optional prefix to file location of KERI keystore',
                    required=False, default="")

def handler(args):
    kwa = dict(args=args)
    return [doing.doify(migrate, **kwa)]



def migrate(tymth, tock=0.0, **opts):
    _ = (yield tock)
    args = opts["args"]

    adb = abase.AgencyBaser(name="TheAgency", base=args.base, reopen=True, temp=False)

    caids = []
    for ((caid,), _) in adb.agnt.getItemIter():
        caids.append(caid)
    
    for caid in caids:
        print(f"Starting migration of agent {caid}...")
        db = basing.Baser(name=caid,
                          base=args.base,
                          temp=False,
                          reopen=False)
        try:
            db.reopen()
        except kering.DatabaseError as ex:
            print(f"\t skipping {caid}, unable reopen DB - {ex}")
            continue

        print(f"\t Migrating {caid}...")
        db.migrate()
        print(f"\t Finished migrating {caid}")
