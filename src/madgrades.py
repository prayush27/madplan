# madgrades.py
# Grade distributions from the Madgrades API (https://api.madgrades.com)
# Used to show how "easy" a course is: average GPA and share of A's

import json
import os
import urllib.parse
import urllib.request

from uw_api import CACHE_DIR, _cache_path

import time

BASE_URL  = "https://api.madgrades.com/v1"
ENV_PATH  = os.path.join(os.path.dirname(__file__), '..', '.env')
TTL       = 7 * 24 * 3600   # grade data only changes once a term

GRADE_POINTS = {"aCount": 4.0, "abCount": 3.5, "bCount": 3.0, "bcCount": 2.5,
                "cCount": 2.0, "dCount": 1.0, "fCount": 0.0}


def _token():
    token = os.environ.get("MADGRADES_TOKEN")
    if token or not os.path.exists(ENV_PATH):
        return token
    with open(ENV_PATH) as f:
        for line in f:
            key, _, value = line.strip().partition("=")
            if key == "MADGRADES_TOKEN":
                return value
    return None


def enabled():
    return bool(_token())


def _get(url):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError:
        pass
    path = _cache_path("madgrades:" + url)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL:
        with open(path) as f:
            return json.load(f)

    req = urllib.request.Request(url, headers={"Authorization": f"Token token={_token()}",
                                               "User-Agent": "madplan/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    try:
        with open(path, 'w') as f:
            json.dump(result, f)
    except OSError:
        pass   # caching is a speed-up only
    return result


def summarize(counts):
    """Turns raw grade counts into {gpa, pct_a, pct_ab_or_better, graded}."""
    graded = sum(counts.get(k, 0) for k in GRADE_POINTS)
    if graded == 0:
        return None
    points = sum(counts.get(k, 0) * v for k, v in GRADE_POINTS.items())
    return {
        "gpa":    round(points / graded, 2),
        "pct_a":  round(100 * counts.get("aCount", 0) / graded),
        "pct_ab": round(100 * (counts.get("aCount", 0) + counts.get("abCount", 0)) / graded),
        "graded": graded,
    }


def find_course_uuid(subject, catalog_number):
    """Matches a UW course (e.g. 'COMP SCI', '400') to its Madgrades id."""
    query = urllib.parse.quote(f"{subject} {catalog_number}")
    data  = _get(f"{BASE_URL}/courses?query={query}&per_page=20")
    for r in data.get('results', []):
        if str(r['number']) == str(catalog_number) and \
           any(s['abbreviation'] == subject for s in r['subjects']):
            return r['uuid']
    return None


def course_grades(subject, catalog_number):
    """
    Returns grade stats for a course, or None if Madgrades has nothing:
    {
      "overall":     {gpa, pct_a, pct_ab, graded},
      "recent":      same but for the last 6 terms taught,
      "instructors": {"FIRST LAST": {gpa, pct_a, pct_ab, graded}, ...}
    }
    """
    if not enabled():
        return None
    try:
        uuid = find_course_uuid(subject, catalog_number)
        if not uuid:
            return None
        data = _get(f"{BASE_URL}/courses/{uuid}/grades")
    except OSError:
        return None

    offerings = sorted(data.get('courseOfferings', []), key=lambda o: o['termCode'], reverse=True)
    recent = {}
    for o in offerings[:6]:
        for k, v in o['cumulative'].items():
            recent[k] = recent.get(k, 0) + v

    by_instructor = {}
    for o in offerings:
        for sec in o['sections']:
            for inst in sec['instructors']:
                totals = by_instructor.setdefault(inst['name'], {})
                for k in GRADE_POINTS:
                    totals[k] = totals.get(k, 0) + sec.get(k, 0)

    instructors = {}
    for name, totals in by_instructor.items():
        s = summarize(totals)
        if s and s['graded'] >= 20:
            instructors[name] = s

    return {
        "overall":     summarize(data.get('cumulative', {})),
        "recent":      summarize(recent),
        "instructors": instructors,
    }


def instructor_grades(grades, instructor_name):
    """Looks up one current instructor (e.g. 'Shayne Wadle') in a course_grades() result."""
    if not grades:
        return None
    return grades['instructors'].get(instructor_name.upper())
