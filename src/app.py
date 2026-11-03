# app.py
# Flask app: serves the planner page and a small JSON API it talks to

import os
import sys
import urllib.error
from concurrent.futures import ThreadPoolExecutor

# Let the sibling modules import by name however the app is started (locally or on Vercel)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import dars
import madgrades
import suggest
import uw_api
from course import Course
from constraints import has_time_conflict
from scheduler import generate_schedules, unschedulable_courses, usable_packages
from scoring import (DEFAULT_PREFS, credits_of, option_key, pick_backups,
                     rank_schedules, score_schedule, tight_walks)

app = Flask(__name__, template_folder=os.path.join(HERE, "..", "templates"),
            static_folder=os.path.join(HERE, "..", "static"))
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)


@app.errorhandler(Exception)
def api_error(err):
    """Answer API failures with JSON the page can show, and say when UW's course API is the problem."""
    if isinstance(err, HTTPException):
        return err
    app.logger.exception("Request failed")
    if isinstance(err, urllib.error.HTTPError):
        message = f"UW's course API refused the request (HTTP {err.code}). Try again in a minute."
    elif isinstance(err, urllib.error.URLError):
        message = "Couldn't reach UW's course API. Try again in a minute."
    else:
        message = "Something went wrong on our side. Try again."
    return jsonify(error=message), 500

pool = ThreadPoolExecutor(max_workers=8)


def with_grades(courses):
    """Attaches Madgrades stats to each course summary (fetched in parallel)."""
    grades = pool.map(lambda c: madgrades.course_grades(c['subject'], c['catalog_number']), courses)
    for course, g in zip(courses, grades):
        course['grades'] = {"overall": g['overall'], "recent": g['recent']} if g else None
    return courses


def serialize_section(c, grades):
    return {
        "name":         c.name,
        "section":      c.section,
        "type":         c.section_type,
        "days":         c.days,
        "start":        c.start_time,
        "end":          c.end_time,
        "credits":      c.credits,
        "building":     c.building,
        "room":         c.room,
        "lat":          c.lat,
        "lon":          c.lon,
        "instructors":  [{"name": n, "grades": madgrades.instructor_grades(grades, n)}
                         for n in c.instructors],
        "class_number": c.class_number,
        "open_seats":   c.open_seats,
        "capacity":     c.capacity,
        "waitlist":     c.waitlist,
        "status":       c.status,
    }


def sections_from(items):
    """Sections sent back by the page (as serialize_section made them) → Course objects."""
    return [Course(s['name'], s.get('section', ""), s['days'], int(s['start']), int(s['end']),
                   s.get('credits') or 0, section_type=s.get('type') or "lecture",
                   building=s.get('building'), lat=s.get('lat'), lon=s.get('lon'),
                   status=s.get('status')) for s in items or []]


def walks_of(schedule):
    return [{"day": d, "from": a.name, "to": b.name, "gap": gap, "walk": walk}
            for d, a, b, gap, walk in tight_walks(schedule)]


def parse_blocked(blocks):
    """Busy times from the preferences form → Course-like objects the conflict checker understands."""
    return [Course("Busy", "", b['days'], int(b['start']), int(b['end']), 0, section_type="busy")
            for b in blocks or [] if b.get('days')]


@app.route("/")
def index():
    return render_template("index.html", grades_enabled=madgrades.enabled())


@app.route("/api/terms")
def terms():
    return jsonify(terms=uw_api.get_terms(), default=uw_api.default_term())


@app.route("/api/subject")
def subject():
    """Resolves shorthand like 'cs' to {'code': '266', 'name': 'COMP SCI'}."""
    term = request.args.get("term") or uw_api.default_term()
    code, _ = uw_api.resolve_query(term, request.args.get("q", ""))
    if not code:
        return jsonify(error="Unknown subject"), 404
    return jsonify(code=code, name=uw_api.get_subject_name(term, code))


@app.route("/api/stats")
def stats():
    """Headline numbers for the masthead strip."""
    term = request.args.get("term") or uw_api.default_term()
    any_breadth = {"bool": {"should": [{"term": {"breadths.code": c}} for c in "HLSBPN"]}}
    classes, _ = uw_api.search_courses(term, "", page_size=1)
    breadth, _ = uw_api.search_courses(term, "", filters=[any_breadth], page_size=1)
    return jsonify(classes=classes, subjects=len(uw_api.subject_index_names(term)), breadth=breadth)


@app.route("/api/search")
def search():
    term  = request.args.get("term") or uw_api.default_term()
    query = request.args.get("q", "").strip()
    page  = int(request.args.get("page", 1))
    if not query:
        return jsonify(found=0, courses=[])
    found, courses = uw_api.search_courses(term, query, page=page, page_size=20)
    return jsonify(found=found, courses=with_grades(courses))


@app.route("/api/schedules", methods=["POST"])
def schedules():
    body    = request.get_json()
    term    = body.get("term") or uw_api.default_term()
    courses = body.get("courses", [])
    prefs   = {**DEFAULT_PREFS, **(body.get("prefs") or {})}
    include_closed = bool(body.get("include_closed"))
    blocked = parse_blocked(body.get("blocked"))

    if not courses:
        return jsonify(error="Add at least one course first."), 400

    entries = list(pool.map(lambda c: uw_api.load_course_entry({**c, "term": term}), courses))
    grades  = dict(zip([c['name'] for c in courses],
                       pool.map(lambda c: madgrades.course_grades(c['subject'], c['catalog_number']), courses)))

    found  = generate_schedules(entries, include_closed=include_closed, blocked=blocked,
                                option_key=option_key(prefs))
    ranked = rank_schedules(found, prefs)

    plans = []
    for i in pick_backups(ranked, count=3):
        score, notes, sched = ranked[i]
        plans.append({
            "score":   score,
            "notes":   notes,
            "credits": credits_of(sched),
            "sections": [serialize_section(c, grades.get(c.name)) for c in sched],
            "tight_walks": [{"day": d, "from": a.name, "to": b.name, "gap": gap, "walk": walk}
                            for d, a, b, gap, walk in tight_walks(sched)],
        })

    online = [e.name for e in entries if e.packages and all(p['online'] for p in e.packages)]

    return jsonify(
        plans=plans,
        total=len(found),
        unschedulable=[n for n in unschedulable_courses(entries, include_closed, blocked)
                       if n not in online],
        online=online,
    )


@app.route("/api/swap", methods=["POST"])
def swap():
    """
    Other sections of one course in the current plan that still fit around everything else,
    each scored as the whole week would be with it swapped in.
    body: {term, course: summary, sections: the plan's sections, blocked, include_closed, prefs}
    """
    body    = request.get_json()
    term    = body.get("term") or uw_api.default_term()
    course  = body.get("course") or {}
    prefs   = {**DEFAULT_PREFS, **(body.get("prefs") or {})}
    include_closed = bool(body.get("include_closed"))
    blocked = parse_blocked(body.get("blocked"))
    if not course.get("name"):
        return jsonify(error="Pick a class to swap."), 400

    plan    = sections_from(body.get("sections"))
    others  = [s for s in plan if s.name != course["name"]]
    current = sorted((s.section_type, s.section) for s in plan if s.name == course["name"])
    entry   = uw_api.load_course_entry({**course, "term": term})
    grades  = madgrades.course_grades(course.get("subject", ""), course.get("catalog_number", ""))

    usable  = usable_packages(entry, include_closed, blocked)
    options, clashing = [], 0
    for sections in usable:
        if sorted((s.section_type, s.section) for s in sections) == current:
            continue
        if any(has_time_conflict(s, o) for s in sections for o in others):
            clashing += 1
            continue
        week = others + sections
        score, notes = score_schedule(week, prefs)
        options.append({"score": score, "notes": notes, "tight_walks": walks_of(week),
                        "sections": [serialize_section(s, grades) for s in sections]})
    options.sort(key=lambda o: -o["score"])
    return jsonify(
        current_score=score_schedule(plan, prefs)[0] if plan else None,
        options=options[:12],
        clashing=clashing,
        unavailable=len(entry.packages) - len(usable),
    )


@app.route("/api/why_not", methods=["POST"])
def why_not():
    """Explains, check by check, whether one course could go in this term's plan."""
    body  = request.get_json()
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify(error="Type a course, like cs 540."), 400
    result = suggest.why_not(
        body.get("term") or uw_api.default_term(), query,
        completed=body.get("completed"),
        standing=int(body.get("standing") or 0) or None,
        programs=body.get("programs") or [],
        specs=body.get("requirements") or [],
        bonus_specs=body.get("bonus_requirements") or [],
        plan_sections=sections_from(body.get("sections")),
        blocked=parse_blocked(body.get("blocked")),
        include_closed=bool(body.get("include_closed")),
        skip_prereqs=body.get("skip_prereqs", True),
    )
    if "error" in result:
        return jsonify(error=result["error"]), 404
    return jsonify(result)


@app.route("/api/dars", methods=["POST"])
def upload_dars():
    """
    Parses an uploaded DARS PDF in memory (never written to disk) and returns only planning data:
    program, year, courses taken, and unmet requirements.
    """
    upload = request.files.get("file")
    if not upload:
        return jsonify(error="No file uploaded."), 400
    data = upload.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return jsonify(error="That file is too big to be a DARS report."), 400
    try:
        parsed = dars.parse_pdf(data)
    except Exception:
        return jsonify(error="Couldn't read that PDF. Export your DARS as a PDF and try again."), 400
    if not parsed["taken"] and not parsed["requirements"]:
        return jsonify(error="That doesn't look like a DARS report."), 400
    return jsonify(parsed)


@app.route("/api/requirements")
def requirement_kinds():
    return jsonify([{"kind": k, "label": v[0]} for k, v in suggest.REQUIREMENT_KINDS.items()])


@app.route("/api/fill", methods=["POST"])
def fill():
    body  = request.get_json()
    term  = body.get("term") or uw_api.default_term()
    must  = body.get("courses", [])
    specs = body.get("requirements", [])
    prefs = {**DEFAULT_PREFS, **(body.get("prefs") or {})}

    if not specs:
        return jsonify(error="Add at least one requirement, or use your must-haves alone."), 400

    result = suggest.fill(
        term, must, specs, prefs,
        blocked=parse_blocked(body.get("blocked")),
        include_closed=bool(body.get("include_closed")),
        min_credits=int(body.get("min_credits") or 0) or None,
        interests=body.get("interests") or [],
        priority=body.get("priority") or "balanced",
        skip_prereqs=body.get("skip_prereqs", True),
        standing=int(body.get("standing") or 0) or None,
        completed=body.get("completed"),
        programs=body.get("programs") or [],
        bonus_specs=body.get("bonus_requirements") or [],
        max_credits=int(body.get("max_credits") or 0) or suggest.MAX_CREDITS,
        max_classes=int(body.get("max_classes") or 0) or None,
    )
    if "error" in result:
        return jsonify(error=result["error"]), 400

    grades = dict(result["grades"])
    grades.update(zip([c['name'] for c in must],
                      pool.map(lambda c: madgrades.course_grades(c['subject'], c['catalog_number']), must)))

    plans = [{
        "score":    p["score"],
        "notes":    p["notes"],
        "credits":  p["credits"],
        "picks":    p["picks"],
        "sections": [serialize_section(c, grades.get(c.name)) for c in p["schedule"]],
        "tight_walks": [{"day": d, "from": a.name, "to": b.name, "gap": gap, "walk": walk}
                        for d, a, b, gap, walk in tight_walks(p["schedule"])],
    } for p in result["plans"]]

    return jsonify(
        plans=plans,
        alternatives=result["alternatives"],
        covered=result["covered"],
        unfilled=result["unfilled"],
        over_cap=result["over_cap"],
        min_credits=result["min_credits"],
        max_credits=result["max_credits"],
        max_classes=result["max_classes"],
        checked=result["checked"],
        fitting=result["fitting"],
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
