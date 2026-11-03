# Prerequisite checking against the courses on a student's record.

import pytest

from dars import prereqs_met

TAKEN = ["COMP SCI 300", "MATH 221", "MATH 222", "STAT 240"]


@pytest.mark.parametrize("prereq, expected", [
    ("", True),
    (None, True),
    ("None", True),
    ("COMP SCI 300", True),
    ("COMP SCI 400", False),
    ("MATH 221 and 222", True),                       # subject carries over to the bare number
    ("MATH 221 and 234", False),
    ("COMP SCI 400 or MATH 222", True),
    ("(COMP SCI 400 or STAT 240) and MATH 221", True),
    ("COMP SCI 400 or (STAT 340 and MATH 221)", False),
    ("COMP SCI 400, 354, or 300", True),              # commas read as "or"
    ("MATH 999 and (COMP SCI 400, 300)", False),      # ...and bind inside the parentheses
    ("E C E/COMP SCI 252", False),
    ("COMP SCI/E C E 300", True),                     # cross-listed: any listed subject counts
    ("Consent of instructor", False),
    ("COMP SCI 300 or consent of instructor", True),
    # Only the first sentence is the rule; later ones are notes
    ("MATH 221. Not open to students with credit for MATH 211", True),
])
def test_course_prereqs(prereq, expected):
    assert prereqs_met(prereq, TAKEN) is expected


@pytest.mark.parametrize("standing, expected", [(1, False), (2, False), (3, True), (4, True)])
def test_class_standing(standing, expected):
    assert prereqs_met("Junior standing", [], standing=standing) is expected


def test_standing_or_course():
    assert prereqs_met("COMP SCI 300 or junior standing", TAKEN, standing=1)
    assert not prereqs_met("COMP SCI 400 or junior standing", TAKEN, standing=1)


def test_grad_standing_is_never_met_by_undergrads():
    assert not prereqs_met("Graduate/professional standing", TAKEN, standing=4)
    assert prereqs_met("COMP SCI 300 or graduate/professional standing", TAKEN, standing=2)


def test_declared_major():
    rule = "Declared in the Computer Sciences major"
    assert prereqs_met(rule, [], programs=["Computer Sciences major"])
    assert not prereqs_met(rule, [], programs=["Sociology major"])
    assert not prereqs_met(rule, [])


def test_course_names_are_case_insensitive():
    assert prereqs_met("comp sci 300", ["Comp Sci 300"])
