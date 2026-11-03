# Time conflicts and the conflict-free schedule search.

from constraints import has_time_conflict, is_valid_schedule
from course import Course, CourseEntry
from scheduler import generate_schedules, unschedulable_courses, usable_packages


def meeting(name, days, start, end, credits=3, section="001"):
    return Course(name, section, days, start, end, credits)


def entry(name, *packages, credits=3):
    """packages: lists of sections, optionally (sections, status) to mark full ones."""
    pkgs = []
    for i, p in enumerate(packages):
        sections, status = p if isinstance(p, tuple) else (p, "OPEN")
        pkgs.append({"id": str(i), "sections": sections, "status": status, "online": not sections})
    return CourseEntry(name, credits, pkgs)


# ── Conflicts ────────────────────────────────────────────────

def test_overlap_on_a_shared_day_conflicts():
    assert has_time_conflict(meeting("A", ["Mon", "Wed"], 1000, 1050),
                             meeting("B", ["Wed"], 1030, 1120))


def test_same_time_on_different_days_is_fine():
    assert not has_time_conflict(meeting("A", ["Mon", "Wed"], 1000, 1050),
                                 meeting("B", ["Tue", "Thu"], 1000, 1050))


def test_back_to_back_classes_do_not_conflict():
    first, second = meeting("A", ["Mon"], 1000, 1050), meeting("B", ["Mon"], 1050, 1140)
    assert not has_time_conflict(first, second)
    assert not has_time_conflict(second, first)


def test_valid_schedule_checks_every_pair():
    a, b = meeting("A", ["Mon"], 900, 950), meeting("B", ["Mon"], 1000, 1050)
    c = meeting("C", ["Mon"], 920, 1010)
    assert is_valid_schedule([a, b])
    assert not is_valid_schedule([a, b, c])


# ── Which section options are usable ─────────────────────────

def test_full_sections_are_skipped_unless_asked_for():
    full = entry("CS 1", ([meeting("CS 1", ["Mon"], 900, 950)], "CLOSED"))
    assert usable_packages(full) == []
    assert len(usable_packages(full, include_closed=True)) == 1


def test_sections_during_busy_times_are_skipped():
    course = entry("CS 1", [meeting("CS 1", ["Mon"], 900, 950)], [meeting("CS 1", ["Tue"], 900, 950)])
    work = [meeting("Busy", ["Mon"], 800, 1200, credits=0)]
    options = usable_packages(course, blocked=work)
    assert [s[0].days for s in options] == [["Tue"]]


def test_a_package_whose_own_sections_clash_is_skipped():
    lecture = meeting("CS 1", ["Mon"], 900, 950)
    clashing_lab = meeting("CS 1", ["Mon"], 930, 1020, section="301")
    assert usable_packages(entry("CS 1", [lecture, clashing_lab])) == []


# ── Schedule search ──────────────────────────────────────────

def test_finds_only_conflict_free_combinations():
    a = entry("A", [meeting("A", ["Mon"], 900, 950)], [meeting("A", ["Mon"], 1000, 1050)])
    b = entry("B", [meeting("B", ["Mon"], 900, 950)])
    schedules = generate_schedules([a, b])
    assert len(schedules) == 1
    assert {(c.name, c.start_time) for c in schedules[0]} == {("A", 1000), ("B", 900)}
    assert all(is_valid_schedule(s) for s in schedules)


def test_every_combination_when_nothing_clashes():
    a = entry("A", [meeting("A", ["Mon"], 900, 950)], [meeting("A", ["Tue"], 900, 950)])
    b = entry("B", [meeting("B", ["Wed"], 900, 950)], [meeting("B", ["Thu"], 900, 950)])
    assert len(generate_schedules([a, b])) == 4


def test_limit_caps_the_search():
    a = entry("A", *[[meeting("A", ["Mon"], 800 + 100 * i, 850 + 100 * i)] for i in range(5)])
    b = entry("B", *[[meeting("B", ["Tue"], 800 + 100 * i, 850 + 100 * i)] for i in range(5)])
    assert len(generate_schedules([a, b], limit=3)) == 3


def test_online_section_keeps_the_other_classes():
    # Regression: an online package has no sections, and removing "the last 0 sections"
    # used to wipe the whole schedule being built
    online = entry("ONLINE 1", [])
    a = entry("A", [meeting("A", ["Mon"], 900, 950)])
    b = entry("B", [meeting("B", ["Tue"], 900, 950)])
    schedules = generate_schedules([a, online, b])
    assert schedules and all({c.name for c in s} == {"A", "B"} for s in schedules)


def test_online_or_in_person_choice_keeps_earlier_classes():
    # Same bug, harder case: after trying the online option, the in-person option
    # was tried on an emptied schedule and the plan came back without class A
    a = entry("A", [meeting("A", ["Mon"], 900, 950)])
    either = entry("B", [], [meeting("B", ["Tue"], 900, 950)])
    schedules = generate_schedules([a, either])
    assert len(schedules) == 2
    assert all("A" in {c.name for c in s} for s in schedules)


def test_preferred_options_are_tried_first():
    a = entry("A", [meeting("A", ["Mon"], 800, 850)], [meeting("A", ["Mon"], 1300, 1350)])
    first = generate_schedules([a], limit=1, option_key=lambda sections: -sections[0].start_time)
    assert first[0][0].start_time == 1300


def test_courses_with_no_usable_section():
    full = entry("FULL 1", ([meeting("FULL 1", ["Mon"], 900, 950)], "CLOSED"))
    ok = entry("OK 1", [meeting("OK 1", ["Tue"], 900, 950)])
    assert unschedulable_courses([full, ok]) == ["FULL 1"]
    assert all(c.name == "OK 1" for s in generate_schedules([full, ok]) for c in s)
