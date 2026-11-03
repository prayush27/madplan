# Picking classes for requirements. These run the real planner against a small fake
# catalog, so the answers are predictable and nothing calls UW or Madgrades.

import pytest

import madgrades
import suggest
import uw_api
from course import Course, CourseEntry

MAJOR = "Computer Sciences major"
GEN_ED = "University General Education: Breadth"


def course(name, credits=3, breadths=(), gen_ed="", prereq="", title=None, ethnic=""):
    subject, number = name.rsplit(" ", 1)
    return {
        "term": "1274", "name": name, "subject": subject, "subject_code": subject,
        "course_id": name.replace(" ", "-"), "catalog_number": number,
        "title": title or f"{name} lecture", "min_credits": credits, "max_credits": credits,
        "prerequisites": prereq, "breadth_codes": list(breadths), "gen_ed_code": gen_ed,
        "ethnic_studies": ethnic,
    }


CATALOG = [
    course("COMP SCI 407"), course("COMP SCI 506"), course("COMP SCI 536"),
    course("COMP SCI 537", credits=4), course("COMP SCI 564"),
    course("HISTORY 101", breadths="H"), course("ENGL 207", breadths="L"),
    course("PHILOS 101", breadths="H", credits=4), course("ART HIST 201", breadths="H"),
    course("SOC 134", breadths="S"), course("ECON 101", breadths="S", credits=4),
    course("ASTRON 103", breadths="P"), course("ZOOLOGY 101", breadths="B"),
    course("MUSIC 113", breadths="H", credits=1),
    course("COMP SCI 640", prereq="Graduate/professional standing"),
    course("COMP SCI 520", prereq="COMP SCI 400"),
    course("COMP SCI 699", title="Directed Study"),
    course("ENGL 100", gen_ed="COM A"),
]


@pytest.fixture
def catalog(monkeypatch):
    """Serves CATALOG as the term's courses; every course gets its own time slot so all fit."""
    slot = {c["name"]: i for i, c in enumerate(CATALOG)}

    def search(term, query="", filters=None, page=1, page_size=50):
        hits = [c for c in CATALOG if c["name"] == query] if query else list(CATALOG)
        return len(hits), [dict(c) for c in hits]

    def load(c):
        i = slot.get(c["name"], len(slot))
        days = ["Mon", "Wed", "Fri"] if i % 2 == 0 else ["Tue", "Thu"]
        start = 800 + 100 * (i // 2)
        section = Course(c["name"], "001", days, start, start + 50, c["min_credits"])
        return CourseEntry(c["name"], c["min_credits"],
                           [{"id": "1", "sections": [section], "status": "OPEN", "online": False}])

    monkeypatch.setattr(uw_api, "search_courses", search)
    monkeypatch.setattr(uw_api, "load_course_entry", load)
    monkeypatch.setattr(madgrades, "course_grades", lambda subject, number: None)
    return {c["name"]: c for c in CATALOG}


def fill(specs, **kwargs):
    return suggest.fill("1274", kwargs.pop("must", []), specs, {}, **kwargs)


def software(count=1):
    return {"kind": "list", "courses": ["COMP SCI 407", "COMP SCI 506", "COMP SCI 536", "COMP SCI 537"],
            "count": count, "group": MAJOR, "label": "Software"}


def humanities(count=1, **extra):
    return {"kind": "humanities", "count": count, "group": GEN_ED, "label": "Humanities", **extra}


def picked(result, plan=0):
    return [p["name"] for p in result["plans"][plan]["picks"]]


# ── Building requirements ────────────────────────────────────

def test_list_requirement_matches_only_its_courses():
    req = suggest.build_requirement("1274", software())
    assert req.match(course("COMP SCI 407")) and not req.match(course("COMP SCI 300"))
    assert req.major and req.count == 1


def test_breadth_requirements():
    hum = suggest.build_requirement("1274", humanities())
    assert hum.match(course("X 101", breadths="L"))          # L&S counts Literature as Humanities
    assert not hum.match(course("X 101", breadths="S"))
    assert not hum.major


def test_general_natural_science_counts_only_for_natural_science():
    n_course = course("STAT 240", breadths="N")
    assert suggest.build_requirement("1274", {"kind": "natural"}).match(n_course)
    assert not suggest.build_requirement("1274", {"kind": "biological"}).match(n_course)
    assert not suggest.build_requirement("1274", {"kind": "physical"}).match(n_course)


def test_credit_requirement_needs_a_big_enough_class():
    req = suggest.build_requirement("1274", humanities(credits=3))
    assert req.match(course("HISTORY 101", breadths="H"))
    assert not req.match(course("MUSIC 113", breadths="H", credits=1))


def test_unknown_kind_is_ignored():
    assert suggest.build_requirement("1274", {"kind": "underwater basketry"}) is None


def test_a_course_counts_once_inside_a_group_but_across_groups():
    reqs = [suggest.build_requirement("1274", s) for s in (
        {"kind": "humanities", "group": "Breadth in the Degree: Humanities"},
        {"kind": "literature", "group": "Breadth in the Degree: Humanities"},
        {"kind": "humanities", "group": GEN_ED},
    )]
    assert suggest.allocate(reqs, [course("ENGL 207", breadths="L")]) == {0: 1, 2: 1}


def test_screening_helpers():
    assert not suggest.is_real_class(course("COMP SCI 699", title="Directed Study"))
    assert suggest.is_real_class(course("COMP SCI 400", title="Programming III"))
    assert not suggest.eligible(course("X 700", prereq="Graduate/professional standing"), 4)
    assert not suggest.eligible(course("X 300", prereq="Junior standing"), 2)
    assert suggest.eligible(course("X 300", prereq="Junior standing"), 3)


# ── Filling a semester ───────────────────────────────────────

def test_covers_each_requirement(catalog):
    result = fill([software(), humanities()], max_credits=6)
    names = picked(result)
    assert any(n.startswith("COMP SCI") for n in names)
    assert any("H" in catalog[n]["breadth_codes"] or "L" in catalog[n]["breadth_codes"] for n in names)
    assert result["unfilled"] == [] and result["over_cap"] == []


@pytest.mark.parametrize("max_credits", [6, 9, 12, 15, 18])
def test_never_goes_over_the_credit_cap(catalog, max_credits):
    result = fill([software(count=3), humanities(count=2)], min_credits=max_credits - 2,
                  max_credits=max_credits)
    assert result["plans"]
    assert all(p["credits"] <= max_credits for p in result["plans"])


def test_fills_toward_the_top_of_the_range(catalog):
    result = fill([software()], min_credits=12, max_credits=15)
    assert 12 <= result["plans"][0]["credits"] <= 15


def test_over_the_cap_reports_what_is_left_for_later(catalog):
    result = fill([software(count=4), humanities(count=3)], max_credits=9)
    assert result["plans"][0]["credits"] <= 9
    assert result["over_cap"]                      # not everything fits in 9 credits


def test_respects_the_class_limit(catalog):
    result = fill([software(count=2), humanities(count=2)], max_credits=18, max_classes=3)
    assert all(len(p["picks"]) <= 3 for p in result["plans"])


def test_skips_taken_grad_only_unmet_prereq_and_non_classes(catalog):
    result = fill([software(), humanities()], min_credits=15, max_credits=18,
                  completed=["COMP SCI 407", "HISTORY 101"], standing=3, programs=[MAJOR])
    names = {n for p in result["plans"] for n in picked(result, result["plans"].index(p))}
    assert names and not names & {"COMP SCI 407", "HISTORY 101", "COMP SCI 640",
                                  "COMP SCI 520", "COMP SCI 699"}


def test_must_haves_count_toward_requirements(catalog):
    result = fill([software(), humanities()], must=[catalog["COMP SCI 407"]], max_credits=9)
    assert {"label": "Software", "by": "COMP SCI 407"} in result["covered"]
    assert "COMP SCI 407" not in picked(result)
    assert result["plans"][0]["credits"] <= 9


def test_must_haves_over_the_cap_is_an_error(catalog):
    must = [catalog["COMP SCI 407"], catalog["COMP SCI 537"]]
    assert "over your max" in fill([humanities()], must=must, max_credits=6)["error"]


def test_more_must_haves_than_the_class_limit_is_an_error(catalog):
    must = [catalog["COMP SCI 407"], catalog["COMP SCI 506"]]
    assert "more than your limit" in fill([humanities()], must=must, max_classes=1)["error"]
