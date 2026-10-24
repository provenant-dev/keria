# -*- encoding: utf-8 -*-
"""
KERIA
keria.app.schemaing module

Agency-wide cache of ACDC schemas shared by every agent.

Schemas are content addressed (their SAID is their identity), so once one has
been fetched and validated it is the same for every agent in the agency.  The
cache lets the agency hand a schema to an agent without resolving the schema
OOBI over the network again, which is what makes onboarding many controllers
at once cheap.

The cache is curated by the operator only: it holds what ``KERIA_SCHEMA_DIR``
and ``KERIA_SURLS`` load at startup and nothing else.  Agents never feed it, so
one controller cannot push data into the cache or into other agents' databases,
and every replica of the agency holds the same, declarative set of schemas.
Schemas are written into an agent database only when that agent asks for them,
either through ``POST /oobis`` or through the schema OOBIs of its own config.
"""

import concurrent.futures
import http.client
import logging
import os
import time
import urllib.error
import urllib.request
from urllib import parse

from keri import kering
from keri.app.oobiing import Result
from keri.core import scheming
from keri.db import basing
from keri.end.ending import DOOBI_RE
from keri.help import helping

logger = logging.getLogger("keria")

ALLOWED_SCHEMES = ("http", "https")


def saidFromOobi(url):
    """Return the SAID of a data OOBI URL (``.../oobi/{said}``) or None for any other URL."""
    try:
        purl = parse.urlparse(url)
    except ValueError:
        return None

    if (match := DOOBI_RE.match(purl.path)) is None:
        return None

    return match.group("said")


class SchemaCache:
    """In-memory, agency-wide cache of validated ACDC schemas keyed by SAID.

    Schemas get into the cache only at startup, from a directory of JSON files
    (``loadDir``) or from a list of schema OOBI URLs (``loadUrls``).

    Agents read from the cache through ``resolve`` (satisfy a schema OOBI posted
    to ``/oobis`` without a network round trip) and ``settle`` (satisfy the schema
    OOBIs queued from the agent config without a network round trip).
    """

    def __init__(self):
        self.schemers = dict()

    def __len__(self):
        return len(self.schemers)

    def __contains__(self, said):
        return said in self.schemers

    @property
    def saids(self):
        return list(self.schemers.keys())

    def get(self, said):
        return self.schemers.get(said)

    def add(self, schemer):
        """Add a validated Schemer. Returns True when the schema was not cached yet."""
        if schemer.said in self.schemers:
            return False

        self.schemers[schemer.said] = schemer
        return True

    def addRaw(self, raw, said=None, source=None):
        """Parse, validate and cache a raw JSON schema.

        Parameters:
            raw (bytes | str): serialized JSON schema
            said (str | None): SAID the schema is expected to have, if known
            source (str | None): where the schema came from, only used for logging

        Returns:
            Schemer | None: the cached schemer, or None when the schema is invalid
        """
        if isinstance(raw, str):
            raw = raw.encode("utf-8")

        try:
            schemer = scheming.Schemer(raw=bytearray(raw))
        except (
            kering.ValidationError,
            kering.DeserializeError,
            ValueError,
            KeyError,
        ) as ex:
            logger.warning("Ignoring invalid schema from %s: %s", source, ex)
            return None

        if said is not None and schemer.said != said:
            logger.warning(
                "Ignoring schema from %s: SAID %s does not match expected %s",
                source,
                schemer.said,
                said,
            )
            return None

        self.add(schemer)
        return schemer

    def loadDir(self, path):
        """Load every ``*.json`` file in ``path`` into the cache. Returns the number of schemas added."""
        if not path:
            return 0

        if not os.path.isdir(path):
            logger.warning(
                "Schema directory %s does not exist, nothing preloaded", path
            )
            return 0

        start = len(self.schemers)
        names = sorted(name for name in os.listdir(path) if name.endswith(".json"))
        for name in names:
            filepath = os.path.join(path, name)
            try:
                with open(filepath, "rb") as f:
                    raw = f.read()
            except OSError as ex:
                logger.warning("Could not read schema file %s: %s", filepath, ex)
                continue

            self.addRaw(raw, source=filepath)

        count = len(self.schemers) - start
        logger.info("Preloaded %d schemas from %d files in %s", count, len(names), path)
        return count

    def loadUrls(self, urls, timeout=10.0, deadline=30.0, workers=8):
        """Fetch every schema OOBI URL once into the cache, concurrently, under a total deadline.

        Meant to run at agency startup, before the event loop is up. Only ``http``
        and ``https`` URLs are fetched. Failures are logged and skipped: a schema
        that could not be fetched here is simply resolved the usual way by the
        first agent that asks for it. Fetches still pending when ``deadline``
        seconds have elapsed are abandoned so startup is never held up for long.

        Returns the number of schemas added.
        """
        if not urls:
            return 0

        start = len(self.schemers)
        pending = {}
        for url in urls:
            said = saidFromOobi(url)
            if said is None:
                logger.warning("Skipping %s: not a schema (data) OOBI URL", url)
                continue
            if parse.urlparse(url).scheme not in ALLOWED_SCHEMES:
                logger.warning(
                    "Skipping %s: only http(s) schema OOBI URLs are fetched", url
                )
                continue
            pending[url] = said

        if pending:
            started = time.monotonic()
            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(workers, len(pending))
            )
            futures = {
                executor.submit(self._fetch, url, timeout): url for url in pending
            }
            try:
                for future in concurrent.futures.as_completed(
                    futures, timeout=deadline
                ):
                    url = futures[future]
                    try:
                        raw = future.result()
                    except (
                        urllib.error.URLError,
                        http.client.HTTPException,  # e.g. IncompleteRead on a truncated response
                        OSError,
                        ValueError,
                    ) as ex:
                        logger.warning("Could not fetch schema OOBI %s: %s", url, ex)
                        continue

                    self.addRaw(raw, said=pending[url], source=url)
            except concurrent.futures.TimeoutError:
                unfinished = [url for f, url in futures.items() if not f.done()]
                logger.warning(
                    "Schema warm-up deadline of %.0fs reached after %.1fs, %d URLs abandoned: %s",
                    deadline,
                    time.monotonic() - started,
                    len(unfinished),
                    ", ".join(unfinished),
                )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        count = len(self.schemers) - start
        log = logger.info if count == len(pending) else logger.warning
        log(
            "Preloaded %d of %d schemas from %d schema OOBI URLs",
            count,
            len(pending),
            len(urls),
        )
        return count

    @staticmethod
    def _fetch(url, timeout):
        with urllib.request.urlopen(url, timeout=timeout) as rep:
            return rep.read()

    def resolve(self, db, url, oobialias=None):
        """Satisfy a schema OOBI posted by an agent without touching the network, when possible.

        Returns True when ``url`` is a schema OOBI whose schema is cached or already
        held by the agent. In that case the schema is stored in the agent database
        (if missing) and the OOBI is recorded as resolved, so the long running
        operation completes on its first status check. Returns False when the OOBI
        has to be resolved the usual way.
        """
        said = saidFromOobi(url)
        if said is None:
            return False

        schemer = self.get(said)
        if schemer is None:
            if db.schema.get(keys=(said,)) is None:
                return False
        elif db.schema.get(keys=(said,)) is None:
            db.schema.pin(keys=(said,), val=schemer)

        self._markResolved(db, url, said, oobialias=oobialias)
        return True

    def settle(self, db):
        """Satisfy the cached schema OOBIs queued in an agent ``oobis`` table.

        The agent config ``durls`` are queued again every time the Habery is opened.
        Every queued schema OOBI whose schema is cached gets the schema written to
        the agent database, is recorded as resolved and is removed from the queue,
        so the Oobiery never fetches it. Other queued OOBIs are left alone.

        Returns the number of OOBIs settled.
        """
        count = 0
        for (url,), obr in list(db.oobis.getItemIter()):
            said = saidFromOobi(url)
            if said is None or (schemer := self.get(said)) is None:
                continue

            if db.schema.get(keys=(said,)) is None:
                db.schema.pin(keys=(said,), val=schemer)
            self._markResolved(db, url, said, oobialias=obr.oobialias)
            db.oobis.rem(keys=(url,))
            count += 1

        return count

    @staticmethod
    def _markResolved(db, url, said, oobialias=None):
        obr = db.roobi.get(keys=(url,))
        if obr is None or obr.state != Result.resolved:
            obr = basing.OobiRecord(
                oobialias=oobialias,
                said=said,
                date=helping.toIso8601(helping.nowUTC()),
                state=Result.resolved,
            )
            db.roobi.pin(keys=(url,), val=obr)


def loadSchemaCache(schemaDir=None, surls=None, deadline=30.0):
    """Build the agency schema cache from the configured directory and schema OOBI URLs."""
    cache = SchemaCache()
    cache.loadDir(schemaDir)
    cache.loadUrls(surls, deadline=deadline)
    return cache
