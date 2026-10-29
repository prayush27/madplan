# uw_api.py
# Client for UW–Madison's public Course Search & Enroll API
# Results are cached on disk so we don't hit UW's servers on every request

import hashlib
import json
import os
import time
import urllib.request

from course import Course, CourseEntry

BASE_URL  = "https://public.enroll.wisc.edu/api/search/v1"
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')

# How long cached responses stay fresh (seconds)
TTL_CATALOG = 24 * 3600   # terms, subjects, course search
TTL_SEATS   = 10 * 60     # sections + live seat counts

DAY_NAMES = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri", "S": "Sat", "U": "Sun"}

SECTION_TYPES = {
    "LEC": "lecture",
    "DIS": "discussion",
    "LAB": "lab",
    "SEM": "seminar",
    "IND": "independent",
    "FLD": "field",
}


# ── HTTP + cache ─────────────────────────────────────────────

def _cache_path(key):
    digest = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.json")


def _fetch(url, body=None, ttl=TTL_CATALOG):
    """GET (or POST if body is given) a JSON endpoint, using the disk cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key  = url + (json.dumps(body, sort_keys=True) if body else "")
    path = _cache_path(key)

    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, 'r') as f:
            return json.load(f)

    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
    except OSError:
        # UW API is down or we're offline — fall back to a stale cache if we have one
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        raise

    with open(path, 'w') as f:
        json.dump(result, f)
    return result


# ── Terms + search ───────────────────────────────────────────

def get_terms():
    """Returns the terms currently open in Course Search & Enroll, e.g. [{'code': '1274', 'name': 'Spring 2027'}]."""
    data = _fetch(f"{BASE_URL}/aggregate")
    terms = []
    for t in data['terms']:
        season, year = t['longDescription'].split()[0], t['shortDescription'].split()[0]
        terms.append({"code": t['termCode'], "name": f"{season} {year}"})
    return terms


def default_term():
    """The furthest-out open term — the one students are planning for."""
    return get_terms()[-1]['code']


def search_courses(term, query="", filters=None, page=1, page_size=50):
    """
    Searches the course catalog for a term.
    filters: raw Elasticsearch-style filters understood by the UW API (optional)
    Returns (total_found, [course summary dicts]).
    """
    body = {
        "selectedTerm": term,
        "queryString":  query or "*",
        "filters":      filters or [],
        "page":         page,
        "pageSize":     page_size,
        "sortOrder":    "SCORE" if query else "SUBJECT",
    }
    data = _fetch(BASE_URL, body=body)
    return data.get('found', 0), [summarize_course(h) for h in data.get('hits', [])]


def summarize_course(hit):
    """Flattens a raw search hit into the fields the app actually uses."""
    return {
        "term":           hit['termCode'],
        "subject_code":   hit['subject']['subjectCode'],
        "course_id":      hit['courseId'],
        "name":           f"{hit['subject']['shortDescription']} {hit['catalogNumber']}",
        "subject":        hit['subject']['shortDescription'],
        "catalog_number": hit['catalogNumber'],
        "title":          hit['title'],
        "description":    hit.get('description') or "",
        "credits":        hit.get('creditRange') or "",
        "min_credits":    hit.get('minimumCredits') or 0,
        "max_credits":    hit.get('maximumCredits') or 0,
        "prerequisites":  hit.get('enrollmentPrerequisites') or "",
        "breadths":       [b['description'] for b in hit.get('breadths') or []],
        "gen_ed":         (hit.get('generalEd') or {}).get('description', ""),
        "ethnic_studies": (hit.get('ethnicStudies') or {}).get('description', ""),
        "levels":         [l['description'] for l in hit.get('levels') or []],
        "ls_credit":      (hit.get('lettersAndScienceCredits') or {}).get('description', ""),
        "honors":         hit.get('honors'),
        "typically_offered": hit.get('typicallyOffered') or "",
    }


# ── Sections ─────────────────────────────────────────────────

def ms_to_hhmm(ms):
    """UW sends times as ms after midnight UTC; Madison is UTC-6. Returns e.g. 1100 for 11:00 AM."""
    minutes = (ms // 60000 - 6 * 60) % (24 * 60)
    return (minutes // 60) * 100 + minutes % 60


def get_packages(term, subject_code, course_id):
    """Raw enrollment packages — each is one valid LEC/DIS/LAB combination you can enroll in."""
    url = f"{BASE_URL}/enrollmentPackages/{term}/{subject_code}/{course_id}"
    return _fetch(url, ttl=TTL_SEATS)


def package_to_sections(pkg, course_name, credits):
    """Converts one enrollment package into a list of Course objects (one per meeting time)."""
    status   = pkg.get('packageEnrollmentStatus') or {}
    sections = []
    credit_given = False

    for sec in pkg['sections']:
        section_type = SECTION_TYPES.get(sec['type'], sec['type'].lower())
        instructors  = [f"{i['name']['first']} {i['name']['last']}"
                        for i in sec.get('instructors') or [] if i.get('name')]
        seats        = sec.get('enrollmentStatus') or {}

        for m in sec['classMeetings']:
            if m['meetingType'] != 'CLASS' or not m.get('meetingDays') or m.get('meetingTimeStart') is None:
                continue
            building = m.get('building') or {}

            # Only the first lecture meeting carries the course's credits so totals don't double count
            section_credits = 0
            if section_type == "lecture" and not credit_given:
                section_credits, credit_given = credits, True

            sections.append(Course(
                course_name,
                sec['sectionNumber'],
                [DAY_NAMES[d] for d in m['meetingDays'] if d in DAY_NAMES],
                ms_to_hhmm(m['meetingTimeStart']),
                ms_to_hhmm(m['meetingTimeEnd']),
                section_credits,
                section_type=section_type,
                building=building.get('buildingName'),
                room=m.get('room'),
                lat=building.get('latitude'),
                lon=building.get('longitude'),
                instructors=instructors,
                class_number=(seats.get('classUniqueId') or {}).get('classNumber'),
                open_seats=seats.get('openSeats'),
                capacity=seats.get('capacity'),
                waitlist=seats.get('waitlistCurrentSize'),
                status=status.get('status'),
            ))

    # Lecture-less courses (e.g. seminars only) still need their credits counted somewhere
    if sections and not credit_given:
        sections[0].credits = credits
    return sections


def load_course_entry(course):
    """
    Builds a CourseEntry with live sections for one course.
    course: a summary dict from search_courses()
    Online/asynchronous packages with no meeting times are kept and flagged as online.
    """
    name, credits = course['name'], course['min_credits']

    packages = []
    for pkg in get_packages(course['term'], course['subject_code'], course['course_id']):
        sections = package_to_sections(pkg, name, credits)
        status   = pkg.get('packageEnrollmentStatus') or {}
        packages.append({
            "id":         pkg.get('id'),
            "sections":   sections,
            "status":     status.get('status'),
            "open_seats": status.get('availableSeats'),
            "online":     bool(pkg.get('onlineOnly') or pkg.get('isAsynchronous') or not sections),
        })

    return CourseEntry(name, credits, packages,
                       subject_code=course['subject_code'], course_id=course['course_id'])
