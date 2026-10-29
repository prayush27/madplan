# course.py
# Core data models for the Smart Academic Planner

class Course:
    """Represents a single meeting of a section (lecture, discussion, lab, ...)."""
    def __init__(self, name, section, days, start_time, end_time, credits, section_type="lecture",
                 building=None, room=None, lat=None, lon=None, instructors=None,
                 class_number=None, open_seats=None, capacity=None, waitlist=None, status=None):
        self.name         = name
        self.section      = section
        self.days         = days          # e.g. ["Mon", "Wed"]
        self.start_time   = start_time    # HHMM int, e.g. 1100
        self.end_time     = end_time
        self.credits      = credits
        self.section_type = section_type  # "lecture", "discussion", "lab", ...

        # Live info from Course Search & Enroll (None for hand-entered courses)
        self.building     = building
        self.room         = room
        self.lat          = lat
        self.lon          = lon
        self.instructors  = instructors or []
        self.class_number = class_number
        self.open_seats   = open_seats
        self.capacity     = capacity
        self.waitlist     = waitlist
        self.status       = status        # "OPEN", "WAITLISTED", "CLOSED"

    def __repr__(self):
        return (f"{self.name} ({self.section_type.upper()} {self.section}) | "
                f"{self.days} | {self.start_time}-{self.end_time} | {self.credits} credits")


class CourseEntry:
    """
    The top-level object for a course a student wants to take.
    packages: every enrollable combination of sections for this course, as
              dicts of {"id", "sections": [Course], "status", "open_seats", "online"}
    """
    def __init__(self, name, credits, packages, subject_code=None, course_id=None):
        self.name         = name
        self.credits      = credits
        self.packages     = packages
        self.subject_code = subject_code
        self.course_id    = course_id
