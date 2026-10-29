# scoring.py
# Scores and ranks valid schedules

from ml_model import train_model, schedule_workload_penalty

model = train_model()

def score_schedule(schedule):
    score = 0

    # Factor 1: Total credits (only count lectures to avoid double counting)
    lectures = [c for c in schedule if c.section_type == "lecture"]
    total_credits = sum(c.credits for c in lectures)
    score += total_credits * 10

    # Factor 2: Early morning penalty
    for course in schedule:
        if course.start_time < 900:
            score -= 15

    # Factor 3: Day spread (more unique days = better balance)
    all_days = []
    for course in schedule:
        all_days.extend(course.days)
    unique_days = len(set(all_days))
    score += unique_days * 5

    # Factor 4: Late night penalty
    for course in schedule:
        if course.end_time > 1800:
            score -= 10

    # Factor 5: Study time gaps
    # Reward schedules with breathing room between classes
    score += study_gap_bonus(schedule)

    # Factor 6: ML workload penalty
    ml_penalty = schedule_workload_penalty(model, lectures)
    score -= ml_penalty

    return round(score)


def study_gap_bonus(schedule):
    """
    Rewards schedules that have gaps between classes on the same day.
    Penalizes back-to-back marathons (gap < 10 min).
    """
    bonus = 0

    # Group courses by day
    day_map = {}
    for course in schedule:
        for day in course.days:
            if day not in day_map:
                day_map[day] = []
            day_map[day].append(course)

    for day, courses in day_map.items():
        # Sort by start time
        sorted_courses = sorted(courses, key=lambda c: c.start_time)

        for i in range(len(sorted_courses) - 1):
            current_end   = sorted_courses[i].end_time
            next_start    = sorted_courses[i + 1].start_time

            # Convert to minutes for accurate gap calculation
            end_mins   = (current_end // 100) * 60 + (current_end % 100)
            start_mins = (next_start // 100) * 60 + (next_start % 100)
            gap_mins   = start_mins - end_mins

            if gap_mins < 10:
                bonus -= 10   # Back to back — penalize
            elif gap_mins >= 30:
                bonus += 8    # Healthy gap — reward
            elif gap_mins >= 15:
                bonus += 4    # Decent gap — small reward

    return bonus


def credit_load_warning(schedule):
    """
    Returns True if the schedule exceeds 18 credits.
    Used in the template to show a warning badge.
    """
    lectures = [c for c in schedule if c.section_type == "lecture"]
    return sum(c.credits for c in lectures) > 18


def rank_schedules(schedules):
    scored = []
    for schedule in schedules:
        s = score_schedule(schedule)
        scored.append((s, schedule))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def max_possible_score(course_entries):
    """
    Theoretical max score based on selected courses.
    """
    best_credits = sum(entry.credits for entry in course_entries)
    best_day_spread = 5
    score = (best_credits * 10) + (best_day_spread * 5)
    return score