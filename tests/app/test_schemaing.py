# -*- encoding: utf-8 -*-
"""
KERIA
keria.app.schemaing module

Testing the agency-wide schema cache
"""

import http.client
import json
import threading
import time
import urllib.error
from unittest import mock

import falcon
from hio.base import doing
from keri import core
from keri.app import configing, habbing
from keri.app.oobiing import Result
from keri.core import coring, scheming

from keria.app import agenting, schemaing
from keria.testing.testing_helper import SCRIPTS_DIR


def makeSchema(title):
    """Build a small, valid, self-addressed JSON schema."""
    sad = {
        "$id": "",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": title,
        "type": "object",
        "properties": {"d": {"type": "string"}},
    }
    _, sad = coring.Saider.saidify(sad, label=coring.Saids.dollar)
    return scheming.Schemer(sed=sad)


def oobiFor(schemer, host="http://127.0.0.1:7723"):
    return f"{host}/oobi/{schemer.said}"


def fakeResponse(raw=None, error=None, reads=None):
    """A fake urlopen response: one body, an error on read, or a scripted list of reads."""
    rep = mock.MagicMock()
    if error is not None:
        rep.read.side_effect = error
    elif reads is not None:
        rep.read.side_effect = reads
    else:
        rep.read.side_effect = [raw, b""]
    rep.__enter__.return_value = rep
    return rep


def test_said_from_oobi():
    schemer = makeSchema("said-from-oobi")
    assert schemaing.saidFromOobi(oobiFor(schemer)) == schemer.said
    assert schemaing.saidFromOobi(oobiFor(schemer) + "?name=x") == schemer.said

    # controller, witness and blinded introductions are not schema OOBIs
    assert (
        schemaing.saidFromOobi("http://127.0.0.1:5642/oobi/EAID/witness/BEID") is None
    )
    assert schemaing.saidFromOobi("http://127.0.0.1:5642/oobi?name=alias") is None
    assert schemaing.saidFromOobi("http://127.0.0.1:5642/") is None
    assert schemaing.saidFromOobi("not a url") is None


def test_schema_cache_add_and_raw():
    cache = schemaing.SchemaCache()
    assert len(cache) == 0

    schemer = makeSchema("add")
    assert cache.add(schemer) is True
    assert cache.add(schemer) is False  # already there
    assert len(cache) == 1
    assert schemer.said in cache
    assert cache.get(schemer.said) is schemer
    assert cache.saids == [schemer.said]

    # raw round trip
    other = makeSchema("raw")
    loaded = cache.addRaw(other.raw, said=other.said, source="test")
    assert loaded is not None
    assert loaded.said == other.said
    assert len(cache) == 2

    # str input is accepted too
    third = makeSchema("raw-str")
    assert cache.addRaw(third.raw.decode("utf-8")) is not None
    assert len(cache) == 3

    # tampered schema: SAID no longer matches content
    tampered = dict(other.sed)
    tampered["title"] = "tampered"
    assert cache.addRaw(json.dumps(tampered).encode("utf-8")) is None

    # schema whose SAID is not the one the caller expected
    assert cache.addRaw(other.raw, said="ENotTheSaid") is None

    # not even JSON
    assert cache.addRaw(b"{not json", source="garbage") is None
    assert len(cache) == 3


def test_schema_cache_load_dir(tmp_path):
    cache = schemaing.SchemaCache()
    assert cache.loadDir(None) == 0
    assert cache.loadDir("") == 0
    assert cache.loadDir(str(tmp_path / "missing")) == 0

    one = makeSchema("dir-one")
    two = makeSchema("dir-two")
    (tmp_path / "one.json").write_bytes(one.raw)
    (tmp_path / "two.json").write_bytes(two.raw)
    (tmp_path / "broken.json").write_bytes(b"{broken")
    (tmp_path / "notes.txt").write_bytes(one.raw)  # wrong extension, ignored

    assert cache.loadDir(str(tmp_path)) == 2
    assert one.said in cache
    assert two.said in cache
    assert len(cache) == 2

    # loading again is idempotent
    assert cache.loadDir(str(tmp_path)) == 0
    assert len(cache) == 2


def test_schema_cache_load_urls():
    cache = schemaing.SchemaCache()
    assert cache.loadUrls(None) == 0
    assert cache.loadUrls([]) == 0

    good = makeSchema("url-good")
    wrong = makeSchema("url-wrong-said")
    down = makeSchema("url-down")
    truncated = makeSchema("url-truncated")
    after = makeSchema("url-after-truncated")
    local = makeSchema("url-file-scheme")

    responses = {
        oobiFor(good): good.raw,
        oobiFor(wrong): good.raw,  # served content does not match the SAID in the URL
        oobiFor(after): after.raw,
        oobiFor(local, host="file:///tmp"): local.raw,  # must never be fetched
    }

    def fakeUrlopen(url, timeout=None):
        if url == oobiFor(truncated):
            # server closed the connection before sending the advertised Content-Length
            return fakeResponse(error=http.client.IncompleteRead(b"{", 100))
        if url not in responses:
            raise urllib.error.URLError("connection refused")
        return fakeResponse(raw=responses[url])

    urls = [
        oobiFor(good),
        oobiFor(wrong),
        oobiFor(down),
        oobiFor(truncated),
        "http://127.0.0.1:5642/oobi/EAID/witness/BEID",  # not a schema OOBI
        oobiFor(local, host="file:///tmp"),  # not http(s), skipped without fetching
        oobiFor(after),  # loading continues after every failure above
    ]
    with mock.patch(
        "keria.app.schemaing.urllib.request.urlopen", side_effect=fakeUrlopen
    ) as urlopen:
        assert cache.loadUrls(urls) == 2

    fetched = {call.args[0] for call in urlopen.call_args_list}
    assert oobiFor(local, host="file:///tmp") not in fetched
    assert "http://127.0.0.1:5642/oobi/EAID/witness/BEID" not in fetched

    assert good.said in cache
    assert after.said in cache
    assert wrong.said not in cache
    assert down.said not in cache
    assert truncated.said not in cache
    assert local.said not in cache
    assert len(cache) == 2


def test_schema_cache_load_urls_deadline():
    cache = schemaing.SchemaCache()
    fast = makeSchema("url-fast")
    slow = makeSchema("url-slow")
    release = threading.Event()

    def fakeUrlopen(url, timeout=None):
        if url == oobiFor(slow):
            release.wait(5.0)  # a server that never answers within the deadline
        return fakeResponse(raw=slow.raw if url == oobiFor(slow) else fast.raw)

    try:
        with mock.patch(
            "keria.app.schemaing.urllib.request.urlopen", side_effect=fakeUrlopen
        ):
            assert cache.loadUrls([oobiFor(slow), oobiFor(fast)], deadline=0.5) == 1
    finally:
        release.set()

    assert fast.said in cache
    assert slow.said not in cache


def test_schema_cache_load_urls_trickling_server_and_size_cap():
    """A server trickling bytes cannot keep a worker alive past the deadline, and huge bodies are dropped."""
    cache = schemaing.SchemaCache()
    trickle = makeSchema("url-trickle")
    huge = makeSchema("url-huge")
    reads = {"count": 0}

    def trickleRead(size=-1):
        # one byte every 50 ms, forever, always inside the socket timeout
        reads["count"] += 1
        time.sleep(0.05)
        return b"x"

    def fakeUrlopen(url, timeout=None):
        if url == oobiFor(trickle):
            return fakeResponse(reads=trickleRead)
        if url == oobiFor(huge):
            return fakeResponse(reads=[b"x" * (schemaing.MAX_SCHEMA_BYTES + 1), b""])
        raise urllib.error.URLError("unexpected url")

    before = threading.active_count()
    started = time.monotonic()
    with mock.patch(
        "keria.app.schemaing.urllib.request.urlopen", side_effect=fakeUrlopen
    ):
        assert cache.loadUrls([oobiFor(trickle), oobiFor(huge)], deadline=0.5) == 0
    elapsed = time.monotonic() - started

    assert len(cache) == 0
    assert elapsed < 2.0  # the deadline was honoured
    assert reads["count"] >= 2  # the trickling read did run and was cut short

    # the worker that was reading the trickle gives up right after the deadline
    for _ in range(40):
        if threading.active_count() <= before:
            break
        time.sleep(0.05)
    assert threading.active_count() <= before


def test_load_schema_cache(tmp_path):
    one = makeSchema("load-one")
    (tmp_path / "one.json").write_bytes(one.raw)

    cache = schemaing.loadSchemaCache(schemaDir=str(tmp_path), surls=None)
    assert isinstance(cache, schemaing.SchemaCache)
    assert cache.saids == [one.said]

    cache = schemaing.loadSchemaCache()
    assert len(cache) == 0


def test_schema_cache_resolve_and_settle(helpers):
    with helpers.openKeria() as (agency, agent, app, client):
        db = agent.hby.db
        cache = schemaing.SchemaCache()

        cached = makeSchema("cached")
        cache.add(cached)

        # resolve: not a schema OOBI
        assert (
            cache.resolve(db, "http://127.0.0.1:5642/oobi/EAID/witness/BEID") is False
        )

        # resolve: unknown schema, must go through the network
        unknown = makeSchema("unknown")
        assert cache.resolve(db, oobiFor(unknown)) is False
        assert db.roobi.get(keys=(oobiFor(unknown),)) is None

        # resolve: cached schema is written to the agent and recorded as resolved
        url = oobiFor(cached)
        assert db.schema.get(keys=(cached.said,)) is None
        assert cache.resolve(db, url, oobialias="vlei") is True
        assert db.schema.get(keys=(cached.said,)).said == cached.said
        obr = db.roobi.get(keys=(url,))
        assert obr is not None
        assert obr.state == Result.resolved
        assert obr.said == cached.said
        assert obr.oobialias == "vlei"
        assert obr.date is not None

        # resolving again does not rewrite the record
        assert cache.resolve(db, url) is True
        assert db.roobi.get(keys=(url,)).date == obr.date

        # resolve: the agent already has a schema the cache does not; the cache stays as is
        mine = makeSchema("mine")
        db.schema.pin(keys=(mine.said,), val=mine)
        assert cache.resolve(db, oobiFor(mine)) is True
        assert mine.said not in cache
        assert db.roobi.get(keys=(oobiFor(mine),)).state == Result.resolved

        # settle: queued schema OOBIs whose schema is cached are satisfied, the rest left alone
        second = makeSchema("cached-second")
        cache.add(second)
        queued = oobiFor(second)
        witness = "http://127.0.0.1:5642/oobi/EAID/witness/BEID"
        notCached = oobiFor(makeSchema("not-cached"))
        for u in (queued, witness, notCached):
            db.oobis.pin(
                keys=(u,),
                val=agenting.OobiRecord(
                    date="2021-01-01T00:00:00.000000+00:00", oobialias="cfg"
                ),
            )
        assert cache.settle(db) == 1
        assert db.oobis.get(keys=(queued,)) is None
        assert db.schema.get(keys=(second.said,)).said == second.said
        assert db.roobi.get(keys=(queued,)).state == Result.resolved
        assert db.roobi.get(keys=(queued,)).oobialias == "cfg"
        assert db.oobis.get(keys=(witness,)) is not None  # not a schema OOBI
        assert db.oobis.get(keys=(notCached,)) is not None  # unknown schema
        assert cache.settle(db) == 0
        for u in (witness, notCached):
            db.oobis.rem(keys=(u,))


def test_schema_cache_is_per_agent_on_read():
    """What one agent resolves never reaches the cache or another agent's database."""
    cache = schemaing.SchemaCache()
    shared = makeSchema("shared")
    cache.add(shared)

    with (
        habbing.openHby(name="agentA", temp=True) as hbyA,
        habbing.openHby(name="agentB", temp=True) as hbyB,
    ):
        # agent A resolves the shared schema and one of its own
        private = makeSchema("private-to-A")
        hbyA.db.schema.pin(keys=(private.said,), val=private)
        assert cache.resolve(hbyA.db, oobiFor(shared)) is True
        assert cache.resolve(hbyA.db, oobiFor(private)) is True

        # the cache only ever holds what the operator loaded
        assert cache.saids == [shared.said]

        # agent B sees nothing of A, and gets the shared schema only when it asks
        assert hbyB.db.schema.cntAll() == 0
        assert cache.settle(hbyB.db) == 0
        assert hbyB.db.schema.cntAll() == 0
        assert cache.resolve(hbyB.db, oobiFor(private)) is False
        assert cache.resolve(hbyB.db, oobiFor(shared)) is True
        assert [said for (said,), _ in hbyB.db.schema.getItemIter()] == [shared.said]


def test_oobi_end_uses_agency_schema_cache(helpers):
    with helpers.openKeria() as (agency, agent, app, client):
        oobiColEnd = agenting.OOBICollectionEnd()
        app.add_route("/oobis", oobiColEnd)

        db = agent.hby.db
        pending = db.oobis.cntAll()  # entries queued from the test keria.json config

        cached = makeSchema("end-cached")
        agency.schemas.add(cached)
        assert db.schema.get(keys=(cached.said,)) is None

        # A schema OOBI the agency already knows: no network, operation done at once
        url = oobiFor(cached)
        body = json.dumps(dict(url=url, oobialias="vlei")).encode("utf-8")
        res = client.simulate_post(path="/oobis", body=body)
        assert res.status == falcon.HTTP_202
        assert db.oobis.cntAll() == pending  # nothing queued for the Oobiery
        assert db.schema.get(keys=(cached.said,)).said == cached.said
        assert db.roobi.get(keys=(url,)).state == Result.resolved
        assert db.roobi.get(keys=(url,)).oobialias == "vlei"

        status = agent.monitor.get(res.json["name"])
        assert status is not None
        assert status.done is True
        assert status.response == dict(oobi=url)

        # A schema OOBI nobody has seen: queued for the Oobiery as before
        unknown = makeSchema("end-unknown")
        res = client.simulate_post(
            path="/oobis", body=json.dumps(dict(url=oobiFor(unknown))).encode("utf-8")
        )
        assert res.status == falcon.HTTP_202
        assert db.oobis.cntAll() == pending + 1
        assert db.oobis.get(keys=(oobiFor(unknown),)) is not None
        status = agent.monitor.get(res.json["name"])
        assert status.done is False
        db.oobis.rem(keys=(oobiFor(unknown),))

        # A controller OOBI is never served from the schema cache
        ctrl = "http://127.0.0.1:5642/oobi/EAID/witness/BEID"
        res = client.simulate_post(
            path="/oobis", body=json.dumps(dict(url=ctrl)).encode("utf-8")
        )
        assert res.status == falcon.HTTP_202
        assert db.oobis.get(keys=(ctrl,)) is not None
        db.oobis.rem(keys=(ctrl,))


def test_agency_settles_config_durls_for_new_agents():
    salter = core.Salter(raw=b"0123456789abcdef")
    schemer = makeSchema("agency-settle")

    cache = schemaing.SchemaCache()
    cache.add(schemer)

    # schema durls, as rendered by the deployment from SCHEMA_URLS: one cached, one not
    cachedUrl = oobiFor(schemer)
    uncachedUrl = oobiFor(makeSchema("agency-uncached"))

    # a config with "dt" is what makes keripy queue the iurls/durls of a new Habery
    cf = configing.Configer(name="agency-schemas", temp=True, reopen=True, clear=True)
    cf.put(dict(dt="2025-01-03T16:08:30.123456+00:00", durls=[cachedUrl, uncachedUrl]))

    agency = agenting.Agency(
        name="agency",
        base="",
        bran=None,
        temp=True,
        cf=cf,
        schemas=cache,
    )
    assert agency.schemas is cache

    doist = doing.Doist(limit=1.0, tock=0.03125, real=True)
    doist.enter(doers=[agency])

    caid = "ELI7pg979AdhmvrjDeam2eAO2SR5niCgnjAJXJHtJose"
    agent = agency.create(caid, salt=salter.qb64)

    # config durls were queued by the Habery on open; the cached one is settled
    # from the cache instead of fetched, the unknown one is left for the Oobiery
    assert cachedUrl in agent.hby.cf.get()["durls"]
    assert agent.hby.db.oobis.get(keys=(cachedUrl,)) is None
    assert agent.hby.db.schema.get(keys=(schemer.said,)).said == schemer.said
    assert agent.hby.db.roobi.get(keys=(cachedUrl,)).state == Result.resolved
    assert agent.hby.db.oobis.get(keys=(uncachedUrl,)) is not None

    # only what the agent asked for through its config got written, nothing else
    assert [said for (said,), _ in agent.hby.db.schema.getItemIter()] == [schemer.said]

    agency.shut(agent)
    agency.adb.close(clear=True)


def test_agency_default_schema_cache():
    agency = agenting.Agency(
        name="agency", bran=None, temp=True, configFile="keria", configDir=SCRIPTS_DIR
    )
    assert isinstance(agency.schemas, schemaing.SchemaCache)
    assert len(agency.schemas) == 0
    agency.adb.close(clear=True)


def test_server_config_schema_fields():
    config = agenting.KERIAServerConfig(name="x", bran=None)
    assert config.schemaDir is None
    assert config.surls == []
    assert config.surlsTimeout == 30.0

    config = agenting.KERIAServerConfig(
        name="x",
        bran=None,
        schemaDir="/schemas",
        surls=["http://127.0.0.1:7723/oobi/ESAID"],
        surlsTimeout=5,
    )
    assert config.schemaDir == "/schemas"
    assert config.surls == ["http://127.0.0.1:7723/oobi/ESAID"]
    assert config.surlsTimeout == 5
