# app.py
# Flask app: serves the planner page and a small JSON API it talks to

import os
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, render_template, request

import madgrades
import uw_api
from course import Course
from scheduler import generate_schedules, unschedulable_courses
from scoring import (DEFAULT_PREFS, credits_of, option_key, pick_backups,
                     rank_schedules, tight_walks)

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

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
        "instructors":  [{"name": n, "grades": madgrades.instructor_grades(grades, n)}
                         for n in c.instructors],
        "class_number": c.class_number,
        "open_seats":   c.open_seats,
        "capacity":     c.capacity,
        "waitlist":     c.waitlist,
        "status":       c.status,
    }


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


if __name__ == "__main__":
    app.run(debug=True, port=5001)
