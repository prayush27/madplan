# DARS parsing: what the planner reads from a degree audit.
# The audits in fixtures/ are made up, laid out like a real UW DARS PDF export.

import os

import pytest

import dars
from conftest import fixture_text


def by_label(result):
    return {r["label"]: r for r in result["requirements"]}


# ── Small helpers ────────────────────────────────────────────

def test_course_list_carries_the_subject_forward():
    assert dars.parse_course_list("STAT 309 OR 311 OR MATH 331, COMP SCI 407,506") == [
        "STAT 309", "STAT 311", "MATH 331", "COMP SCI 407", "COMP SCI 506"]


def test_course_list_skips_duplicates_and_words():
    assert dars.parse_course_list("MATH 320 OR MATH 320 AND SEE ADVISOR") == ["MATH 320"]


@pytest.mark.parametrize("select, spec", [
    ("COURSES W/ 'Literature' Breadth", {"kind": "literature"}),
    ("COURSES W/ 'Social Science' Breadth", {"kind": "social"}),
    ("COURSES W/ 'Natural Science' Breadth", {"kind": "natural"}),
    ("COURSES W/ Gen-Ed code of 'b'", {"kind": "comm_b"}),
    ("MATH 320 OR 340", {"kind": "list", "courses": ["MATH 320", "MATH 340"]}),
    ("COURSES W/ 'Underwater Basketry' Breadth", None),
    ("SEE DEPARTMENT LIST", None),
])
def test_select_from_lines(select, spec):
    assert dars.select_to_spec(select) == spec


@pytest.mark.parametrize("credits, courses, expected", [
    (None, "2", 2),      # "2 COURSES" is exact
    ("6.00", None, 2),   # 6 credits is about two classes
    ("3.00", None, 1),
    ("1.00", None, 1),   # never less than one class
    (None, None, 1),
])
def test_courses_needed(credits, courses, expected):
    assert dars.courses_needed(credits, courses) == expected


# ── A full L&S B.S. audit ────────────────────────────────────

@pytest.fixture
def cs():
    return dars.parse(fixture_text("dars_cs_bs.txt"))


def test_reads_program_and_year(cs):
    assert cs["program"] == "Computer Sciences major"
    assert cs["year"] == 2


def test_reads_courses_taken_in_term_order(cs):
    assert [c["name"] for c in cs["taken"]] == [
        "PHYSICS 103", "ENGL 100", "MATH 221", "COMP SCI 300", "COMP SCI 400"]
    assert next(c for c in cs["taken"] if c["name"] == "COMP SCI 400")["in_progress"]


def test_high_school_credit_is_not_a_course_taken(cs):
    assert "SPANISH 102" not in [c["name"] for c in cs["taken"]]


def test_finds_every_open_course_requirement(cs):
    assert list(by_label(cs)) == [
        "Humanities/Literature/Arts", "Communication B", "Literature",
        "Social Sciences", "Linear Algebra", "Software & Hardware"]


def test_finished_requirements_are_left_out(cs):
    labels = by_label(cs)
    assert "Basic Computer Science" not in labels     # IP+ = done once in-progress work finishes
    assert "Natural Science" not in labels
    assert not any("Quantitative" in r["group"] for r in cs["requirements"])


def test_gpa_residence_and_credit_totals_are_not_class_requirements(cs):
    groups = {r["group"] for r in cs["requirements"]} | {u["group"] for u in cs["unread"]}
    assert not any("Residence" in g or "Total Credits" in g for g in groups)


def test_breadth_and_gen_ed_kinds(cs):
    labels = by_label(cs)
    assert labels["Humanities/Literature/Arts"]["kind"] == "humanities"
    assert labels["Communication B"]["kind"] == "comm_b"
    assert labels["Literature"]["kind"] == "literature"
    assert labels["Social Sciences"]["kind"] == "social"


def test_credit_requirements_become_class_counts(cs):
    literature = by_label(cs)["Literature"]
    assert (literature["count"], literature["credits"], literature["needs"]) == (2, 6.0, "6 credits")


def test_course_lists_including_wrapped_lines(cs):
    labels = by_label(cs)
    assert labels["Linear Algebra"]["courses"] == ["MATH 320", "MATH 340", "MATH 341"]
    software = labels["Software & Hardware"]
    assert software["courses"] == ["COMP SCI 407", "COMP SCI 506", "COMP SCI 536",
                                   "COMP SCI 537", "COMP SCI 538", "COMP SCI 564"]
    assert (software["count"], software["needs"]) == (2, "2 courses")


def test_everything_readable_means_no_unread_notice(cs):
    # Block totals like "--> NEEDS: 9.00 CREDITS" are covered by the sub-requirements under them
    assert cs["unread"] == []


# ── Other majors: what can't be read is reported, not dropped ─

def test_engineering_audit():
    result = dars.parse(fixture_text("dars_engineering.txt"))
    assert (result["program"], result["year"]) == ("Mechanical Engineering major", 3)
    labels = by_label(result)
    assert labels["Mechanics"]["courses"] == ["M E 240", "M E 340"]
    assert labels["Professional Skills"]["courses"] == ["INTEREGR 397", "E P D 397"]
    assert labels["Ethnic Studies"]["kind"] == "ethnic"
    assert result["unread"] == [
        {"group": "Mechanical Engineering: Required Courses", "label": "Technical Electives",
         "needs": "9 credits"},
        {"group": "Co-op or Internship Experience", "label": "Co-op or Internship Experience",
         "needs": ""},
    ]


def test_ba_audit():
    result = dars.parse(fixture_text("dars_ba.txt"))
    labels = by_label(result)
    assert labels["Communication A"]["kind"] == "comm_a"
    assert labels["Statistics"]["courses"] == ["SOC 360", "STAT 301", "STAT 371"]
    assert labels["Biological Science"]["kind"] == "biological"
    assert labels["Physical Science"]["kind"] == "physical"
    assert result["unread"] == [{"group": "Language for the B.A. degree",
                                 "label": "Fourth unit of a foreign language", "needs": "1 course"}]


def test_text_that_is_not_a_dars():
    result = dars.parse("Dear student,\nThanks for visiting.\n")
    assert result["taken"] == [] and result["requirements"] == [] and result["unread"] == []


def test_a_file_that_is_not_a_pdf_raises():
    with pytest.raises(Exception):
        dars.parse_pdf(b"definitely not a pdf")


# ── Optional: your own DARS PDF (never committed) ────────────

REAL_DARS = os.environ.get("MADPLAN_DARS")


@pytest.mark.skipif(not REAL_DARS, reason="set MADPLAN_DARS=/path/to/your/dars.pdf to run")
def test_real_dars_reads_fully():
    with open(REAL_DARS, "rb") as f:
        result = dars.parse_pdf(f.read())
    assert result["requirements"], "no requirements found"
    assert result["unread"] == [], f"couldn't read: {result['unread']}"
