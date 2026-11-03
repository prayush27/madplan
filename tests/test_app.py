# The Flask API the page talks to.

import io
import urllib.error

import pytest

import app as madplan
import dars
import uw_api
from conftest import fixture_text


@pytest.fixture
def client():
    return madplan.app.test_client()


def upload(client, data=b"%PDF-fake"):
    return client.post("/api/dars", data={"file": (io.BytesIO(data), "dars.pdf")})


def test_home_page(client, monkeypatch):
    r = client.get("/")
    assert r.status_code == 200 and b"MadPlan" in r.data


def test_dars_upload_returns_planning_data(client, monkeypatch):
    monkeypatch.setattr(dars, "pdf_text", lambda data: fixture_text("dars_engineering.txt"))
    body = upload(client).get_json()
    assert body["program"] == "Mechanical Engineering major"
    assert [r["label"] for r in body["requirements"]][:1] == ["Mechanics"]
    assert [u["label"] for u in body["unread"]] == ["Technical Electives", "Co-op or Internship Experience"]


def test_dars_upload_without_a_file(client):
    assert client.post("/api/dars").status_code == 400


def test_dars_upload_that_is_not_a_pdf(client):
    r = upload(client, b"just some text")
    assert r.status_code == 400 and "Couldn't read that PDF" in r.get_json()["error"]


def test_dars_upload_that_is_not_a_dars(client, monkeypatch):
    monkeypatch.setattr(dars, "pdf_text", lambda data: "A lovely recipe for soup.")
    r = upload(client)
    assert r.status_code == 400 and "doesn't look like a DARS" in r.get_json()["error"]


def test_dars_upload_that_is_too_big(client):
    r = upload(client, b"0" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 400 and "too big" in r.get_json()["error"]


def test_fill_needs_a_requirement(client):
    r = client.post("/api/fill", json={"term": "1274", "requirements": []})
    assert r.status_code == 400


def test_schedules_needs_a_course(client):
    r = client.post("/api/schedules", json={"term": "1274", "courses": []})
    assert r.status_code == 400


def test_search_with_no_query_returns_nothing(client):
    assert client.get("/api/search?term=1274&q=").get_json() == {"found": 0, "courses": []}


def test_uw_refusing_a_request_is_explained(client, monkeypatch):
    def refused(*args, **kwargs):
        raise urllib.error.HTTPError("https://public.enroll.wisc.edu", 403, "Forbidden", {}, None)
    monkeypatch.setattr(uw_api, "get_terms", refused)
    r = client.get("/api/terms")
    assert r.status_code == 500 and "HTTP 403" in r.get_json()["error"]


def test_uw_unreachable_is_explained(client, monkeypatch):
    def offline(*args, **kwargs):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(uw_api, "get_terms", offline)
    assert "Couldn't reach UW" in client.get("/api/terms").get_json()["error"]


def test_unknown_page_is_a_plain_404(client):
    assert client.get("/no-such-page").status_code == 404
