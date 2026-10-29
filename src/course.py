# course.py
# Core data models for the Smart Academic Planner

class Course:
    """Represents a single section (lecture or discussion)."""
    def __init__(self, name, section, days, start_time, end_time, credits, section_type="lecture"):
        self.name         = name
        self.section      = section
        self.days         = days
        self.start_time   = start_time
        self.end_time     = end_time
        self.credits      = credits
        self.section_type = section_type  # "lecture" or "discussion"

    def __repr__(self):
        return (f"{self.name} ({self.section_type.upper()} {self.section}) | "
                f"{self.days} | {self.start_time}-{self.end_time} | {self.credits} credits")


class LectureGroup:
    """
    A lecture section paired with its own discussion options.
    If the course is lecture-only, discussions will be empty.
    """
    def __init__(self, lecture, discussions=None):
        self.lecture     = lecture        # One Course object (the lecture)
        self.discussions = discussions or []  # List of Course objects (discussions)

    def is_lecture_only(self):
        return len(self.discussions) == 0


class CourseEntry:
    """
    The top-level object for a course a student wants to take.
    Contains multiple lecture groups (e.g. Lecture 001, Lecture 002).
    Each lecture group has its own discussions.
    has_discussion: whether this course requires a discussion section
    """
    def __init__(self, name, credits, has_discussion, lecture_groups):
        self.name            = name
        self.credits         = credits
        self.has_discussion  = has_discussion
        self.lecture_groups  = lecture_groups  # List of LectureGroup objects