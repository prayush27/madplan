# scheduler.py
# Generates valid schedule combinations from each course's enrollment packages
# Uses backtracking so conflicting branches are pruned early instead of
# enumerating every combination (a single course can have 80+ packages)

from constraints import has_time_conflict, is_valid_schedule

MAX_SCHEDULES = 5000


def usable_packages(entry, include_closed=False, blocked=None):
    """
    Filters a course's packages down to the ones worth scheduling.
    include_closed: keep packages that are full / waitlisted
    blocked:        list of Course-like time blocks the student is busy (work, practice, ...)
    """
    options = []
    for pkg in entry.packages:
        sections = pkg['sections']
        if not include_closed and pkg.get('status') == 'CLOSED':
            continue
        if not is_valid_schedule(sections):
            continue
        if blocked and any(has_time_conflict(s, b) for s in sections for b in blocked):
            continue
        options.append(sections)
    return options


def generate_schedules(course_entries, include_closed=False, blocked=None, limit=MAX_SCHEDULES,
                       option_key=None):
    """
    course_entries: list of CourseEntry objects
    option_key:     optional function(sections) -> sort key; options that sort first are
                    tried first, so the best schedules are found before the limit is hit
    Returns a list of schedules, each a flat list of Course objects.
    Courses with no usable packages are skipped (see unschedulable_courses).
    """
    all_course_options = [usable_packages(e, include_closed, blocked) for e in course_entries]
    all_course_options = [opts for opts in all_course_options if opts]
    if option_key:
        for opts in all_course_options:
            opts.sort(key=option_key)
    if not all_course_options:
        return []

    # Most-constrained course first prunes the search the fastest
    all_course_options.sort(key=len)

    valid_schedules = []

    def backtrack(index, current):
        if len(valid_schedules) >= limit:
            return
        if index == len(all_course_options):
            valid_schedules.append(list(current))
            return
        for sections in all_course_options[index]:
            if any(has_time_conflict(s, c) for s in sections for c in current):
                continue
            current.extend(sections)
            backtrack(index + 1, current)
            del current[-len(sections):]

    backtrack(0, [])
    return valid_schedules


def unschedulable_courses(course_entries, include_closed=False, blocked=None):
    """Names of courses that have no open, conflict-free section at all."""
    return [e.name for e in course_entries if not usable_packages(e, include_closed, blocked)]
