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
"""

import http.client
import logging
import os
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

    Schemas get into the cache in three ways:

    - preloaded at startup from a directory of JSON files (``loadDir``),
    - preloaded at startup from a list of schema OOBI URLs (``loadUrls``),
    - harvested from an agent that resolved a schema OOBI the usual way (``harvest``).

    Agents read from the cache through ``preload`` (copy every cached schema into a
    new agent database) and ``resolve`` (satisfy a schema OOBI request without a
    network round trip).
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
        for name in sorted(os.listdir(path)):
            if not name.endswith(".json"):
                continue

            filepath = os.path.join(path, name)
            try:
                with open(filepath, "rb") as f:
                    raw = f.read()
            except OSError as ex:
                logger.warning("Could not read schema file %s: %s", filepath, ex)
                continue

            self.addRaw(raw, source=filepath)

        count = len(self.schemers) - start
        logger.info("Preloaded %d schemas from %s", count, path)
        return count

    def loadUrls(self, urls, timeout=10.0):
        """Fetch every schema OOBI URL once, synchronously, into the cache.

        Meant to run at agency startup, before the event loop is up. Failures are
        logged and skipped: a schema that could not be fetched here is simply
        resolved the usual way by the first agent that asks for it.

        Returns the number of schemas added.
        """
        if not urls:
            return 0

        start = len(self.schemers)
        for url in urls:
            said = saidFromOobi(url)
            if said is None:
                logger.warning("Skipping %s: not a schema (data) OOBI URL", url)
                continue

            try:
                with urllib.request.urlopen(url, timeout=timeout) as rep:
                    raw = rep.read()
            except (
                urllib.error.URLError,
                http.client.HTTPException,  # e.g. IncompleteRead on a truncated response
                OSError,
                ValueError,
            ) as ex:
                logger.warning("Could not fetch schema OOBI %s: %s", url, ex)
                continue

            self.addRaw(raw, said=said, source=url)

        count = len(self.schemers) - start
        logger.info("Preloaded %d schemas from %d schema OOBI URLs", count, len(urls))
        return count

    def preload(self, db):
        """Sync an agent database with the cache, in both directions.

        - Every cached schema missing from the agent database is copied into it.
        - Every schema the agent already has (for example resolved through its
          config ``durls`` before the cache existed) is added to the cache.
        - Every schema OOBI still queued in the agent ``oobis`` table (the config
          ``durls`` are queued again each time the Habery is opened) whose schema
          is cached is recorded as resolved and removed from the queue, so the
          Oobiery never fetches it.

        Returns the number of schemas copied into the agent database.
        """
        for said, schemer in db.schema.getItemIter():
            self.add(schemer)

        count = 0
        for said, schemer in self.schemers.items():
            if db.schema.get(keys=(said,)) is None:
                db.schema.pin(keys=(said,), val=schemer)
                count += 1

        for (url,), obr in list(db.oobis.getItemIter()):
            said = saidFromOobi(url)
            if said is None or said not in self.schemers:
                continue
            self._markResolved(db, url, said)
            db.oobis.rem(keys=(url,))

        return count

    @staticmethod
    def _markResolved(db, url, said):
        obr = db.roobi.get(keys=(url,))
        if obr is None or obr.state != Result.resolved:
            obr = basing.OobiRecord(
                said=said,
                date=helping.toIso8601(helping.nowUTC()),
                state=Result.resolved,
            )
            db.roobi.pin(keys=(url,), val=obr)

    def resolve(self, db, url):
        """Satisfy a schema OOBI for an agent without touching the network, when possible.

        Returns True when ``url`` is a schema OOBI whose schema is already known, either
        by the cache or by the agent itself. In that case the schema is stored in the
        agent database (if missing) and the OOBI is recorded as resolved, so the
        long running operation completes on its first status check. Returns False
        when the OOBI has to be resolved the usual way.
        """
        said = saidFromOobi(url)
        if said is None:
            return False

        schemer = self.get(said)
        if schemer is None:
            schemer = db.schema.get(keys=(said,))
            if schemer is None:
                return False
            self.add(schemer)  # the agent already had it, share it with the agency
        elif db.schema.get(keys=(said,)) is None:
            db.schema.pin(keys=(said,), val=schemer)

        self._markResolved(db, url, said)
        return True

    def harvest(self, db, url):
        """Copy into the cache the schema an agent resolved for ``url``. Returns True when something new was cached."""
        said = saidFromOobi(url)
        if said is None or said in self.schemers:
            return False

        schemer = db.schema.get(keys=(said,))
        if schemer is None:
            return False

        return self.add(schemer)


def loadSchemaCache(schemaDir=None, surls=None):
    """Build the agency schema cache from the configured directory and schema OOBI URLs."""
    cache = SchemaCache()
    cache.loadDir(schemaDir)
    cache.loadUrls(surls)
    return cache
