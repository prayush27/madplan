# constraints.py
# Detects time conflicts between courses

def has_time_conflict(course1, course2):
    """
    Returns True if two courses conflict in time.
    Two courses conflict if they share a day AND their times overlap.
    """
    shared_days = set(course1.days) & set(course2.days)
    if not shared_days:
        return False
    if course1.start_time < course2.end_time and course2.start_time < course1.end_time:
        return True
    return False


def is_valid_schedule(course_list):
    """
    Checks every pair of courses for time conflicts.
    Returns True if the entire schedule is conflict-free.
    """
    for i in range(len(course_list)):
        for j in range(i + 1, len(course_list)):
            if has_time_conflict(course_list[i], course_list[j]):
                return False
    return True