# Scoring schedules against the student's preferences.

from course import Course
from scoring import credits_of, pick_backups, rank_schedules, score_schedule, tight_walks

# Two real campus spots about 1.3 km apart (~16 minutes' walk)
ENGINEERING = (43.0725, -89.4118)
VAN_HISE    = (43.0752, -89.3966)


def meeting(name, days, start, end, credits=3, section="001", where=None, status="OPEN"):
    lat, lon = where or (None, None)
    return Course(name, section, days, start, end, credits, lat=lat, lon=lon, status=status)


def test_a_comfortable_week_scores_full_marks():
    week = [meeting("A", ["Mon", "Wed"], 1000, 1050), meeting("B", ["Tue", "Thu"], 1100, 1215)]
    score, notes = score_schedule(week)
    assert score == 100
    assert "Fri free" in notes


def test_early_and_late_classes_cost_points():
    early = [meeting("A", ["Mon", "Wed"], 745, 835)]
    late  = [meeting("A", ["Mon"], 1600, 1815)]
    assert score_schedule(early)[0] < 100
    assert score_schedule(late)[0] < 100
    assert any("before your start time" in n for n in score_schedule(early)[1])


def test_requested_day_off():
    friday = [meeting("A", ["Fri"], 1000, 1050)]
    no_friday = [meeting("A", ["Thu"], 1000, 1050)]
    prefs = {"days_off": ["Fri"]}
    assert score_schedule(no_friday, prefs)[0] > score_schedule(friday, prefs)[0]
    assert "class on Fri" in score_schedule(friday, prefs)[1]


def test_tight_walk_between_far_buildings():
    week = [meeting("A", ["Mon"], 1000, 1050, where=ENGINEERING),
            meeting("B", ["Mon"], 1055, 1145, where=VAN_HISE)]
    walks = tight_walks(week)
    assert len(walks) == 1 and walks[0][3] == 5      # 5-minute gap
    assert walks[0][4] > 5                           # but a longer walk
    assert "1 tight walk" in score_schedule(week)[1]


def test_no_walk_warning_with_enough_time_or_unknown_rooms():
    roomy = [meeting("A", ["Mon"], 1000, 1050, where=ENGINEERING),
             meeting("B", ["Mon"], 1130, 1220, where=VAN_HISE)]
    unknown = [meeting("A", ["Mon"], 1000, 1050), meeting("B", ["Mon"], 1055, 1145)]
    assert tight_walks(roomy) == [] and tight_walks(unknown) == []


def test_waitlisted_sections_cost_points_and_are_named():
    week = [meeting("A", ["Mon"], 1000, 1050, status="WAITLISTED")]
    score, notes = score_schedule(week)
    assert score < 100 and "waitlist: A" in notes
    assert score_schedule(week, {"avoid_waitlist": False})[0] == 100


def test_rank_puts_the_best_first():
    good = [meeting("A", ["Mon"], 1000, 1050)]
    bad  = [meeting("A", ["Mon"], 700, 750)]
    ranked = rank_schedules([bad, good])
    assert ranked[0][2] is good


def test_backups_differ_by_at_least_two_sections():
    def plan(*sections):
        return (100, [], [meeting(n, ["Mon"], 900, 950, section=s) for n, s in sections])
    ranked = [
        plan(("A", "1"), ("B", "1"), ("C", "1")),
        plan(("A", "1"), ("B", "1"), ("C", "2")),   # only one section differs: not a real backup
        plan(("A", "2"), ("B", "2"), ("C", "1")),
    ]
    assert pick_backups(ranked, count=3) == [0, 2]


def test_credits_add_up():
    assert credits_of([meeting("A", ["Mon"], 900, 950, credits=3),
                       meeting("B", ["Tue"], 900, 950, credits=4)]) == 7
