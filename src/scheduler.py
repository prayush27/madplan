# scheduler.py
# Generates all valid schedule combinations
# Now supports lecture + discussion linking

from itertools import product
from constraints import has_time_conflict, is_valid_schedule


def generate_schedules(course_entries):
    """
    course_entries: list of CourseEntry objects

    For each course, we generate every valid
    (lecture, discussion) pair — or just (lecture,) if lecture-only.
    Then we combine across all courses and filter conflicts.
    """

    # Step 1 — For each course, build a list of valid
    # (lecture + discussion) combos
    all_course_options = []

    for entry in course_entries:
        course_options = []

        for lg in entry.lecture_groups:
            if entry.has_discussion and lg.discussions:
                # Pair this lecture with each of its discussions
                for disc in lg.discussions:
                    # Check lecture and discussion don't conflict with each other
                    if not has_time_conflict(lg.lecture, disc):
                        course_options.append([lg.lecture, disc])
            else:
                # Lecture only
                course_options.append([lg.lecture])

        if course_options:
            all_course_options.append(course_options)

    if not all_course_options:
        return []

    # Step 2 — Generate every combination across all courses
    valid_schedules = []

    for combo in product(*all_course_options):
        # Flatten: combo is a tuple of lists, merge into one flat list
        flat = [course for group in combo for course in group]

        # Check entire flat schedule for conflicts
        if is_valid_schedule(flat):
            valid_schedules.append(flat)

    return valid_schedules