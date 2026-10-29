# app.py
import os
import json
from flask import Flask, render_template, request, session, redirect, url_for
from course import Course, LectureGroup, CourseEntry
from scheduler import generate_schedules
from scoring import rank_schedules, max_possible_score, credit_load_warning

app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.secret_key = "smartplanner_secret_key_2024"

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def format_time(t):
    h = t // 100
    m = t % 100
    period = "AM" if h < 12 else "PM"
    if h > 12:
        h -= 12
    if h == 0:
        h = 12
    return f"{h}:{m:02d} {period}"

app.jinja_env.globals['format_time'] = format_time
app.jinja_env.globals['credit_load_warning'] = credit_load_warning

def get_user_file():
    if 'user_id' not in session:
        session['user_id'] = os.urandom(16).hex()
    return os.path.join(DATA_DIR, f"courses_{session['user_id']}.json")

def load_user_courses():
    filepath = get_user_file()
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        return json.load(f)

def save_user_courses(courses):
    filepath = get_user_file()
    with open(filepath, 'w') as f:
        json.dump(courses, f, indent=2)

def time_str_to_int(time_str):
    h, m = time_str.split(":")
    return int(h) * 100 + int(m)

def raw_to_course_entries(raw_list):
    """Converts JSON data back into CourseEntry objects."""
    entries = []
    for raw in raw_list:
        lecture_groups = []
        for lg in raw['lecture_groups']:
            lecture_data = lg['lecture']
            lecture = Course(
                raw['name'],
                lecture_data['section'],
                lecture_data['days'],
                lecture_data['start_time'],
                lecture_data['end_time'],
                raw['credits'],
                section_type="lecture"
            )
            discussions = []
            for d in lg.get('discussions', []):
                disc = Course(
                    raw['name'],
                    d['section'],
                    d['days'],
                    d['start_time'],
                    d['end_time'],
                    0,  # discussions don't add credits
                    section_type="discussion"
                )
                discussions.append(disc)
            lecture_groups.append(LectureGroup(lecture, discussions))

        entries.append(CourseEntry(
            raw['name'],
            raw['credits'],
            raw['has_discussion'],
            lecture_groups
        ))
    return entries

@app.route("/", methods=["GET"])
def index():
    user_courses = load_user_courses()
    return render_template("index.html", user_courses=user_courses)

@app.route("/add_course", methods=["POST"])
def add_course():
    name           = request.form.get("name", "").strip()
    credits        = int(request.form.get("credits", 3))
    has_discussion = request.form.get("has_discussion") == "yes"

    # Collect lecture sections
    lecture_sections  = request.form.getlist("lecture_section")
    lecture_starts    = request.form.getlist("lecture_start")
    lecture_ends      = request.form.getlist("lecture_end")

    if not name or not lecture_sections:
        return redirect(url_for('index'))

    lecture_groups = []

    for i, lsec in enumerate(lecture_sections):
        lecture_days = request.form.getlist(f"lecture_days_{i}")
        if not lecture_days:
            continue

        lecture = {
            "section":    lsec.strip(),
            "days":       lecture_days,
            "start_time": time_str_to_int(lecture_starts[i]),
            "end_time":   time_str_to_int(lecture_ends[i]),
        }

        discussions = []
        if has_discussion:
            # Discussion sections are named disc_{i}_{j}
            disc_sections = request.form.getlist(f"disc_section_{i}")
            disc_starts   = request.form.getlist(f"disc_start_{i}")
            disc_ends     = request.form.getlist(f"disc_end_{i}")

            for j, dsec in enumerate(disc_sections):
                disc_days = request.form.getlist(f"disc_days_{i}_{j}")
                if not disc_days:
                    continue
                discussions.append({
                    "section":    dsec.strip(),
                    "days":       disc_days,
                    "start_time": time_str_to_int(disc_starts[j]),
                    "end_time":   time_str_to_int(disc_ends[j]),
                })

        lecture_groups.append({
            "lecture":     lecture,
            "discussions": discussions
        })

    if not lecture_groups:
        return redirect(url_for('index'))

    user_courses = load_user_courses()
    user_courses.append({
        "name":           name,
        "credits":        credits,
        "has_discussion": has_discussion,
        "lecture_groups": lecture_groups
    })
    save_user_courses(user_courses)
    return redirect(url_for('index'))

@app.route("/delete_course/<int:index>", methods=["POST"])
def delete_course(index):
    user_courses = load_user_courses()
    if 0 <= index < len(user_courses):
        user_courses.pop(index)
        save_user_courses(user_courses)
    return redirect(url_for('index'))

@app.route("/generate", methods=["POST"])
def generate():
    selected     = request.form.getlist("courses")
    raw_courses  = load_user_courses()
    all_entries  = raw_to_course_entries(raw_courses)

    selected_entries = [e for e in all_entries if e.name in selected]

    if not selected_entries:
        user_courses = load_user_courses()
        return render_template("index.html",
            user_courses=user_courses,
            error="Please select at least one course.")

    schedules = generate_schedules(selected_entries)
    ranked    = rank_schedules(schedules)
    max_score = max_possible_score(selected_entries)

    return render_template("results.html", ranked=ranked, max_score=max_score)

if __name__ == "__main__":
    app.run(debug=True)