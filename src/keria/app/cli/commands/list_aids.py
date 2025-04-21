# -*- encoding: utf-8 -*-
"""
KERIA
keria.cli.keria.commands module

"""
import argparse

from hio.base import doing
from keria.db import basing
from keri import kering

from keri.db import basing as keribasing
from keri.db import koming


def handler(args):
    """
    List existing identifiers of the agents

    Args:
        args(Namespace): arguments object from command line
    """
    list_aids = ListIdentifiersDoDoer(args)
    return [list_aids]


parser = argparse.ArgumentParser(description='List AIDs of the agents')
parser.set_defaults(handler=handler,
                    transferable=True)
parser.add_argument('--base', '-b', help='additional optional prefix to file location of KERI keystore',
                    required=False, default="")
parser.add_argument('--caids', nargs='+', help='Optional list of CAIDs to filter and list', required=False)


class ListIdentifiersDoDoer(doing.DoDoer):
    def __init__(self, args):
        self.args = args
        self.adb = basing.AgencyBaser(name="TheAgency", base=args.base, reopen=True, temp=False)
        doers = [doing.doify(self.listIdentifiersDo)]

        super(ListIdentifiersDoDoer, self).__init__(doers=doers)

    def listIdentifiersDo(self, tymth, tock=0.0):
        """ For each agent in the agency, add a migrate doer

        Parameters:
            tymth (function): injected function wrapper closure returned by .tymen() of
                Tymist instance. Calling tymth() returns associated Tymist .tyme.
            tock (float): injected initial tock value

        """
        self.wind(tymth)
        self.tock = tock
        _ = (yield self.tock)

        
        caids = []
        if self.args.caids:
            caids = self.args.caids
            print(f"📌 CAIDs provided via argument: {', '.join(caids)}")
            print("─────────────────────────────")
        else:
            print("📋 Listing AIDs of all agents in the agency...")
            print("─────────────────────────────")

            caids = []
            for ((caid,), _) in self.adb.agnt.getItemIter():
                caids.append(caid)


        print(f"Total agents to be inspected: {len(caids)}")
        print("─────────────────────────────")

        for caid in caids:
            print(f"\n🚀 Listing identifiers for agent: {caid}")
            
            db = keribasing.Baser(
                name=caid,
                base=self.args.base,
                temp=False,
                reopen=False
            )

            try:
                db.reopen()
            except kering.DatabaseError as ex:
                print(f"\t⚠️ Skipping {caid} — unable to reopen DB: {ex}")
                continue

            habs = koming.Komer(db=db, subkey='habs.', schema=dict)

            for (name,), habord in habs.getItemIter():
                print(f"\n🔹 Identifier Name: {name}")
                # print("─────────────────────────────")

                hid = habord.get("hid") or '—'
                mid = habord.get("mid") or '—'
                sid = habord.get("sid") or '—'
                prefix = habord.get("prefix")  # Legacy field from HabitatRecordV0_6_7

                # print(f"\tHID    : {hid}")
                # print(f"\tMID    : {mid}")
                # print(f"\tSID    : {sid}")

                print(f"\tprefix               : {hid}")
                print(f"\tgroup-member-prefix  : {mid}")
                print(f"\tsignify-prefix       : {sid}")


                if prefix:
                    print(f"\tPrefix : {prefix}  ← legacy field (v0.6.7)")

            # print(f"\n✅ Finished listing identifiers for agent: {caid}")
            print()
            print("───────────────────────────────────────────────────────────────────────────────────────")
            print()


        print("\n✅ Completed listing AIDs of agents.")
        print("─────────────────────────────")

 
    
