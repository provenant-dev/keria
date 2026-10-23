"""
Configure PyTest

Use this module to configure pytest
https://docs.pytest.org/en/latest/pythonpath.html

"""

import os

import pytest
from hio.base import filing
from hio.help import ogling
from keri.core import coring
from keri.db import dbing
from keri.help import helping
from keria.testing import testing_helper


@pytest.fixture(autouse=True)
def keriHeadDirPath(monkeypatch, tmp_path):
    """Give every test its own head directory for non-temp (persistent) stores

    keripy and hio resolve persistent paths from the class level .HeadDirPath
    (/usr/local/var), falling back to .AltHeadDirPath (~) when that is not
    writable.  So a test that builds a non-temp Habery, Agency or Configer
    writes LMDB state into a real user directory and leaks it into the next
    run, e.g. agency.create() then fails with "Already incepted pre=...".
    Pointing both head dirs at pytest's tmp_path makes those stores throwaway
    without deleting anything the developer owns.  Tests that pass temp=True
    are unaffected; they already get a tempfile directory of their own.
    """
    head = tmp_path / "keri-head"
    os.makedirs(head, exist_ok=True)
    for cls in (filing.Filer, dbing.LMDBer, ogling.Ogler):
        monkeypatch.setattr(cls, "HeadDirPath", str(head))
        monkeypatch.setattr(cls, "AltHeadDirPath", str(head))
    return head


@pytest.fixture
def helpers():
    return testing_helper.Helpers


@pytest.fixture
def seeder():
    return testing_helper.DbSeed


@pytest.fixture()
def mockHelpingNowUTC(monkeypatch):
    """
    Replace nowUTC universally with fixed value for testing
    """
    monkeypatch.setattr(helping, "nowUTC", testing_helper.Helpers.mockNowUTC)


@pytest.fixture()
def mockHelpingNowIso8601(monkeypatch):
    """
    Replace nowIso8601 universally with fixed value for testing
    """
    monkeypatch.setattr(helping, "nowIso8601", testing_helper.Helpers.mockNowIso8601)


@pytest.fixture()
def mockCoringRandomNonce(monkeypatch):
    """Replay randomNonce with fixed falue for testing"""
    monkeypatch.setattr(coring, "randomNonce", testing_helper.Helpers.mockRandomNonce)
