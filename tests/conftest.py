# Shared test setup. Tests never touch the network or the real on-disk cache:
# every UW / Madgrades call is either faked by the test or fails loudly.

import os
import sys
import urllib.request

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture_text(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    import madgrades
    import uw_api

    def no_network(*args, **kwargs):
        raise AssertionError("A test tried to reach the internet; fake that call instead.")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(uw_api, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(madgrades, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("MADGRADES_TOKEN", raising=False)
    monkeypatch.setattr(madgrades, "ENV_PATH", str(tmp_path / "no.env"))
