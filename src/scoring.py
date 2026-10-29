# scoring.py
# Scores and ranks valid schedules against the student's preferences

import math

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

WALK_METERS_PER_MIN = 80   # brisk walk across campus

DEFAULT_PREFS = {
    "earliest_start": 900,     # HHMM — classes before this are penalized
    "latest_end":     1700,    # HHMM — classes after this are penalized
    "days_off":       [],      # e.g. ["Fri"]
    "spacing":        "balanced",  # "compact" (fewer gaps), "balanced", "spread" (breathing room)
    "avoid_waitlist": True,
}


def to_minutes(hhmm):
    return (hhmm // 100) * 60 + hhmm % 100


def credits_of(schedule):
    return sum(c.credits for c in schedule)


def walk_minutes(a, b):
    """Walking time between two sections' buildings, or None if either location is unknown."""
    if None in (a.lat, a.lon, b.lat, b.lon):
        return None
    # Equirectangular approximation — plenty accurate across one campus
    dx = math.radians(b.lon - a.lon) * math.cos(math.radians((a.lat + b.lat) / 2))
    dy = math.radians(b.lat - a.lat)
    meters = math.hypot(dx, dy) * 6_371_000
    return meters / WALK_METERS_PER_MIN


def by_day(schedule):
    day_map = {}
    for course in schedule:
        for day in course.days:
            day_map.setdefault(day, []).append(course)
    for courses in day_map.values():
        courses.sort(key=lambda c: c.start_time)
    return day_map


def tight_walks(schedule):
    """
    Back-to-back classes where the walk takes longer than the gap.
    Returns a list of (day, from_course, to_course, gap_mins, walk_mins).
    """
    tight = []
    for day, courses in by_day(schedule).items():
        for a, b in zip(courses, courses[1:]):
            walk = walk_minutes(a, b)
            gap  = to_minutes(b.start_time) - to_minutes(a.end_time)
            if walk is not None and walk > gap:
                tight.append((day, a, b, gap, round(walk)))
    return tight


def score_schedule(schedule, prefs=None):
    """
    Returns (score, notes) where score is 0–100 and notes are short
    human-readable reasons, e.g. ["Fridays free", "1 tight walk"].
    """
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    score = 100.0
    notes = []

    # Early mornings / late evenings
    early = sum(len(c.days) for c in schedule if c.start_time < prefs['earliest_start'])
    late  = sum(len(c.days) for c in schedule if c.end_time > prefs['latest_end'])
    score -= early * 4 + late * 4
    if early:
        notes.append(f"{early} class{'es' if early > 1 else ''} before your start time")
    if late:
        notes.append(f"{late} class{'es' if late > 1 else ''} after your end time")

    # Requested days off
    day_map = by_day(schedule)
    for day in prefs['days_off']:
        if day in day_map:
            score -= 12
            notes.append(f"class on {day}")
        else:
            notes.append(f"{day} free")

    # Gaps between classes, per day
    gap_total = 0
    for courses in day_map.values():
        for a, b in zip(courses, courses[1:]):
            gap = to_minutes(b.start_time) - to_minutes(a.end_time)
            gap_total += gap
            if prefs['spacing'] == "spread" and gap < 15:
                score -= 3
            elif prefs['spacing'] == "compact" and gap > 60:
                score -= (gap - 60) / 30
            elif prefs['spacing'] == "balanced" and gap > 120:
                score -= (gap - 120) / 30
    if prefs['spacing'] == "compact":
        # Fewer days on campus is the point of a compact schedule
        score -= len(day_map) * 2

    # Walking between buildings
    walks = tight_walks(schedule)
    score -= len(walks) * 8
    if walks:
        notes.append(f"{len(walks)} tight walk{'s' if len(walks) > 1 else ''}")

    # Seats
    waitlisted = {c.name for c in schedule if c.status in ("WAITLISTED", "CLOSED")}
    if prefs['avoid_waitlist']:
        score -= len(waitlisted) * 15
    if waitlisted:
        notes.append(f"waitlist: {', '.join(sorted(waitlisted))}")

    free_days = [d for d in WEEKDAYS if d not in day_map]
    if free_days and not prefs['days_off']:
        notes.append(f"{'/'.join(free_days)} free")

    return max(0, round(score)), notes


def option_key(prefs=None):
    """Sort key for one course's section options: best fit to the preferences first."""
    return lambda sections: -score_schedule(sections, prefs)[0]


def credit_load_warning(schedule):
    """Returns True if the schedule exceeds 18 credits (UW's normal max without approval)."""
    return credits_of(schedule) > 18


def rank_schedules(schedules, prefs=None):
    """Returns [(score, notes, schedule)] best first."""
    scored = [(*score_schedule(s, prefs), s) for s in schedules]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def pick_backups(ranked, count=3):
    """
    Plan A plus backups that don't share every section with the plans before them,
    so if a section fills on enrollment day there's a real alternative.
    Returns a list of indices into ranked.
    """
    chosen = []
    for i, (_, _, schedule) in enumerate(ranked):
        sections = {(c.name, c.section) for c in schedule}
        if all(len(sections - {(c.name, c.section) for c in ranked[j][2]}) >= 2 for j in chosen):
            chosen.append(i)
        if len(chosen) == count:
            break
    return chosen
