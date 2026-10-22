# -*- encoding: utf-8 -*-
"""
KERIA
keria.app.schemaing module

Testing the agency-wide schema cache
"""

import http.client
import json
import urllib.error
from unittest import mock

import falcon
from hio.base import doing
from keri import core
from keri.app import configing
from keri.app.oobiing import Result
from keri.core import coring, scheming

from keria.app import agenting, schemaing
from keria.core import longrunning


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

    responses = {
        oobiFor(good): good.raw,
        oobiFor(wrong): good.raw,  # served content does not match the SAID in the URL
        oobiFor(after): after.raw,
    }

    def fakeUrlopen(url, timeout=None):
        if url == oobiFor(truncated):
            # server closed the connection before sending the advertised Content-Length
            rep = mock.MagicMock()
            rep.read.side_effect = http.client.IncompleteRead(b"{", 100)
            rep.__enter__.return_value = rep
            return rep
        if url not in responses:
            raise urllib.error.URLError("connection refused")
        rep = mock.MagicMock()
        rep.read.return_value = responses[url]
        rep.__enter__.return_value = rep
        return rep

    urls = [
        oobiFor(good),
        oobiFor(wrong),
        oobiFor(down),
        oobiFor(truncated),
        "http://127.0.0.1:5642/oobi/EAID/witness/BEID",  # not a schema OOBI
        oobiFor(after),  # loading continues after every failure above
    ]
    with mock.patch(
        "keria.app.schemaing.urllib.request.urlopen", side_effect=fakeUrlopen
    ):
        assert cache.loadUrls(urls) == 2

    assert good.said in cache
    assert after.said in cache
    assert wrong.said not in cache
    assert down.said not in cache
    assert truncated.said not in cache
    assert len(cache) == 2


def test_load_schema_cache(tmp_path):
    one = makeSchema("load-one")
    (tmp_path / "one.json").write_bytes(one.raw)

    cache = schemaing.loadSchemaCache(schemaDir=str(tmp_path), surls=None)
    assert isinstance(cache, schemaing.SchemaCache)
    assert cache.saids == [one.said]

    cache = schemaing.loadSchemaCache()
    assert len(cache) == 0


def test_schema_cache_preload_resolve_harvest(helpers):
    with helpers.openKeria() as (agency, agent, app, client):
        db = agent.hby.db
        cache = schemaing.SchemaCache()

        cached = makeSchema("cached")
        cache.add(cached)

        # preload copies cached schemas into the agent database, once
        assert db.schema.get(keys=(cached.said,)) is None
        assert cache.preload(db) == 1
        assert db.schema.get(keys=(cached.said,)).said == cached.said
        assert cache.preload(db) == 0

        # preload also picks up schemas the agent already had (e.g. from config durls)
        owned = makeSchema("owned")
        db.schema.pin(keys=(owned.said,), val=owned)
        assert owned.said not in cache
        assert cache.preload(db) == 0
        assert owned.said in cache

        # and settles schema OOBIs still queued for the Oobiery when the schema is cached
        queued = oobiFor(cached)
        witness = "http://127.0.0.1:5642/oobi/EAID/witness/BEID"
        notCached = oobiFor(makeSchema("not-cached"))
        for u in (queued, witness, notCached):
            db.oobis.pin(
                keys=(u,),
                val=agenting.OobiRecord(date="2021-01-01T00:00:00.000000+00:00"),
            )
        assert cache.preload(db) == 0
        assert db.oobis.get(keys=(queued,)) is None
        assert db.roobi.get(keys=(queued,)).state == Result.resolved
        assert db.roobi.get(keys=(queued,)).said == cached.said
        assert (
            db.oobis.get(keys=(witness,)) is not None
        )  # not a schema OOBI, left alone
        assert db.oobis.get(keys=(notCached,)) is not None  # unknown schema, left alone
        for u in (witness, notCached):
            db.oobis.rem(keys=(u,))

        # resolve: not a schema OOBI
        assert (
            cache.resolve(db, "http://127.0.0.1:5642/oobi/EAID/witness/BEID") is False
        )

        # resolve: unknown schema, must go through the network
        unknown = makeSchema("unknown")
        assert cache.resolve(db, oobiFor(unknown)) is False
        assert db.roobi.get(keys=(oobiFor(unknown),)) is None

        # resolve: cached schema is recorded as resolved for the agent
        url = oobiFor(cached)
        assert cache.resolve(db, url) is True
        obr = db.roobi.get(keys=(url,))
        assert obr is not None
        assert obr.state == Result.resolved
        assert obr.said == cached.said
        assert obr.date is not None

        # resolving again does not rewrite the record
        assert cache.resolve(db, url) is True
        assert db.roobi.get(keys=(url,)).date == obr.date

        # resolve: the agent already has the schema but the cache does not, share it
        mine = makeSchema("mine")
        db.schema.pin(keys=(mine.said,), val=mine)
        assert mine.said not in cache
        assert cache.resolve(db, oobiFor(mine)) is True
        assert mine.said in cache
        assert db.roobi.get(keys=(oobiFor(mine),)).state == Result.resolved

        # harvest: schema resolved by the agent the usual way
        harvested = makeSchema("harvested")
        assert (
            cache.harvest(db, oobiFor(harvested)) is False
        )  # agent does not have it yet
        db.schema.pin(keys=(harvested.said,), val=harvested)
        assert cache.harvest(db, oobiFor(harvested)) is True
        assert cache.harvest(db, oobiFor(harvested)) is False  # already cached
        assert cache.harvest(db, "http://127.0.0.1:5642/oobi?name=alias") is False


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
        res = client.simulate_post(
            path="/oobis", body=json.dumps(dict(url=url)).encode("utf-8")
        )
        assert res.status == falcon.HTTP_202
        assert db.oobis.cntAll() == pending  # nothing queued for the Oobiery
        assert db.schema.get(keys=(cached.said,)).said == cached.said
        assert db.roobi.get(keys=(url,)).state == Result.resolved

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


def test_monitor_harvests_resolved_schema(helpers):
    with helpers.openKeria() as (agency, agent, app, client):
        db = agent.hby.db
        assert agent.monitor.schemas is agency.schemas

        # Simulate the Oobiery having resolved a schema OOBI for this agent
        schemer = makeSchema("monitor-harvest")
        url = oobiFor(schemer)
        db.schema.pin(keys=(schemer.said,), val=schemer)
        db.roobi.pin(
            keys=(url,),
            val=agenting.OobiRecord(
                said=schemer.said,
                state=Result.resolved,
                date="2021-01-01T00:00:00.000000+00:00",
            ),
        )
        assert schemer.said not in agency.schemas

        # submit returns the operation status, which already went through the monitor
        status = agent.monitor.submit(
            core.Salter().qb64, longrunning.OpTypes.oobi, metadata=dict(oobi=url)
        )
        assert status.done is True
        assert status.response == dict(oobi=url)

        # The schema is now available to every other agent of the agency
        assert schemer.said in agency.schemas
        assert agency.schemas.get(schemer.said).said == schemer.said


def test_agency_preloads_schemas_into_new_agents():
    salter = core.Salter(raw=b"0123456789abcdef")
    schemer = makeSchema("agency-preload")

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
    assert agent.hby.db.schema.get(keys=(schemer.said,)).said == schemer.said
    assert agent.monitor.schemas is cache

    # config durls were queued by the Habery on open; the cached one is settled
    # from the cache instead of fetched, the unknown one is left for the Oobiery
    assert cachedUrl in agent.hby.cf.get()["durls"]
    assert agent.hby.db.oobis.get(keys=(cachedUrl,)) is None
    assert agent.hby.db.roobi.get(keys=(cachedUrl,)).state == Result.resolved
    assert agent.hby.db.oobis.get(keys=(uncachedUrl,)) is not None

    agency.shut(agent)
    agency.adb.close(clear=True)


def test_agency_default_schema_cache():
    agency = agenting.Agency(name="agency", bran=None, temp=True)
    assert isinstance(agency.schemas, schemaing.SchemaCache)
    assert len(agency.schemas) == 0
    agency.adb.close(clear=True)


def test_server_config_schema_fields():
    config = agenting.KERIAServerConfig(name="x", bran=None)
    assert config.schemaDir is None
    assert config.surls == []

    config = agenting.KERIAServerConfig(
        name="x",
        bran=None,
        schemaDir="/schemas",
        surls=["http://127.0.0.1:7723/oobi/ESAID"],
    )
    assert config.schemaDir == "/schemas"
    assert config.surls == ["http://127.0.0.1:7723/oobi/ESAID"]
