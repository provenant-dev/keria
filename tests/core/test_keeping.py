"""External keepers persist public key metadata without storing signing secrets."""

import pytest
from keri.app.keeping import Algos
from keri.core import coring
from keri.db import dbing

from keria.core import keeping


@pytest.fixture
def remote_keeper(tmp_path):
    with dbing.openLMDB(
        cls=keeping.RemoteKeeper, name="extern", temp=False, headDirPath=str(tmp_path)
    ) as rb:
        yield rb


def test_extern_incept_and_reload(remote_keeper, helpers, mockHelpingNowIso8601):
    serder, _ = helpers.inceptExtern()
    manager = keeping.RemoteManager(hby=None, rb=remote_keeper)
    keeper = manager.get(algo=Algos.extern)
    keeper.incept(
        pre=serder.pre,
        verfers=serder.verfers,
        digers=serder.ndigers,
        pidx=7,
        transferable=True,
        stem="external-provider",
    )

    assert remote_keeper.pres.get(serder.pre) == keeping.Prefix(
        pidx=7, algo=Algos.extern
    )
    situation = remote_keeper.sits.get(serder.pre)
    assert situation.new.pubs == serder.ked["k"]
    assert situation.nxt.pubs == serder.ked["n"]
    assert situation.new.dt == situation.nxt.dt == "2021-06-27T21:26:21.233257+00:00"

    # Reopening the database must retain the metadata needed by GET /identifiers.
    remote_keeper.close(clear=False)
    remote_keeper.reopen()
    reloaded = keeping.RemoteManager(hby=None, rb=remote_keeper).get(pre=serder.pre)
    assert isinstance(reloaded, keeping.ExternKeeper)
    assert reloaded.params(serder.pre) == {"extern": {"pidx": 7}}
    assert remote_keeper.sits.get(serder.pre) == situation
    assert list(remote_keeper.prxs.getItemIter()) == []
    assert list(remote_keeper.nxts.getItemIter()) == []


def test_extern_duplicate_inception_preserves_existing_state(remote_keeper, helpers):
    serder, _ = helpers.inceptExtern()
    other, _ = helpers.inceptExtern()
    keeper = keeping.ExternKeeper(remote_keeper)
    keeper.incept(pre=serder.pre, verfers=serder.verfers, digers=serder.ndigers)
    original = remote_keeper.sits.get(serder.pre)

    with pytest.raises(ValueError, match="Already incepted pre="):
        keeper.incept(
            pre=serder.pre, verfers=other.verfers, digers=other.ndigers, pidx=9
        )

    assert remote_keeper.sits.get(serder.pre) == original
    assert keeper.params(serder.pre) == {"extern": {"pidx": 0}}


def test_extern_rotation_updates_public_keys(remote_keeper, helpers):
    serder, _ = helpers.inceptExtern()
    rotated, _ = helpers.inceptExtern()
    keeper = keeping.ExternKeeper(remote_keeper)
    keeper.incept(pre=serder.pre, verfers=serder.verfers, digers=serder.ndigers, pidx=3)
    keeper.rotate(
        pre=serder.pre,
        verfers=rotated.verfers,
        digers=rotated.ndigers,
        transferable=True,
    )

    situation = remote_keeper.sits.get(serder.pre)
    assert situation.new.pubs == rotated.ked["k"]
    assert situation.nxt.pubs == rotated.ked["n"]
    assert keeper.params(serder.pre) == {"extern": {"pidx": 3}}


@pytest.mark.parametrize("algo", [None, Algos.salty, Algos.randy, Algos.group])
def test_extern_rejects_missing_or_other_algorithm(remote_keeper, helpers, algo):
    serder, _ = helpers.inceptExtern()
    if algo is not None:
        remote_keeper.pres.put(serder.pre, keeping.Prefix(algo=algo))
    keeper = keeping.ExternKeeper(remote_keeper)

    with pytest.raises(ValueError, match="nonexistent or invalid pre="):
        keeper.params(serder.pre)
    with pytest.raises(ValueError, match="nonexistent or invalid pre="):
        keeper.rotate(
            pre=serder.pre,
            verfers=serder.verfers,
            digers=serder.ndigers,
            transferable=True,
        )
    assert remote_keeper.sits.get(serder.pre) is None


def test_extern_nontransferable_identifier(remote_keeper):
    verfer = coring.Verfer(raw=bytes(range(32)), code=coring.MtrDex.Ed25519N)
    keeper = keeping.ExternKeeper(remote_keeper)
    keeper.incept(pre=verfer.qb64, verfers=[verfer], digers=[], transferable=False)

    assert keeper.params(verfer.qb64) == {"extern": {"pidx": 0}}
    situation = remote_keeper.sits.get(verfer.qb64)
    assert situation.new.pubs == [verfer.qb64]
    assert situation.nxt.pubs == []
