# suggest.py
# "Fill the rest": picks courses that satisfy the student's remaining requirements,
# fit around their must-have classes, and rank well on easiness and interest

import re
from concurrent.futures import ThreadPoolExecutor

import madgrades
import uw_api
from constraints import has_time_conflict
from scheduler import generate_schedules, usable_packages
from scoring import option_key, rank_schedules

pool = ThreadPoolExecutor(max_workers=24)

UNDERGRAD   = {"range": {"catalogSort": {"lt": "00700"}}}
POOL_SIZE   = 400   # courses pulled from the catalog per requirement
PREFETCH    = 90    # of those, how many get live sections + grades looked up
BRANCH      = 8     # best candidates tried per requirement when combining
MAX_SETS    = 40    # complete course combinations to collect before ranking
CHECK_LIMIT = 800   # cap on fit checks so a hard request can't hang the server


def _any(field, codes):
    return {"bool": {"should": [{"term": {field: c}} for c in codes]}}


def _breadth(*codes):
    return [_any("breadths.code", codes)], lambda c: bool(set(codes) & set(c['breadth_codes']))


def _gen_ed(code):
    return [{"term": {"generalEd.code": code}}], lambda c: c['gen_ed_code'] == code


# kind -> (label, filters, matcher). L&S counts Literature as Humanities, and
# "N" (natural science) counts as either biological or physical.
REQUIREMENT_KINDS = {
    "humanities": ("Humanities",         *_breadth("H", "L")),
    "literature": ("Literature",         *_breadth("L")),
    "social":     ("Social Science",     *_breadth("S")),
    "natural":    ("Natural Science",    *_breadth("B", "P", "N")),
    "biological": ("Biological Science", *_breadth("B", "N")),
    "physical":   ("Physical Science",   *_breadth("P", "N")),
    "ethnic":     ("Ethnic Studies", [{"term": {"ethnicStudies.code": "ETHNIC ST"}}],
                   lambda c: bool(c['ethnic_studies'])),
    "comm_a":     ("Communication A",    *_gen_ed("COM A")),
    "comm_b":     ("Communication B",    *_gen_ed("COM B")),
    "qr_a":       ("Quant Reasoning A",  *_gen_ed("QR-A")),
    "qr_b":       ("Quant Reasoning B",  *_gen_ed("QR-B")),
    "elective":   ("Any elective", [], lambda c: True),
}


class Requirement:
    def __init__(self, label, filters, match):
        self.label   = label
        self.filters = filters
        self.match   = match


def build_requirement(term, spec):
    """
    spec: {"kind": "humanities"} or {"kind": "subject", "subject": "cs", "min": 300}
    Returns a Requirement, or None if the spec doesn't make sense for this term.
    """
    if spec.get("kind") == "subject":
        code, _ = uw_api.resolve_query(term, spec.get("subject", ""))
        if not code:
            return None
        minimum = int(spec.get("min") or 0)
        name    = uw_api.get_subject_name(term, code)
        label   = f"{name} {minimum}+" if minimum else f"{name} course"
        filters = [{"term": {"subject.subjectCode": code}},
                   {"range": {"catalogSort": {"gte": f"{minimum:05d}"}}}]
        return Requirement(label, filters, lambda c: c['subject_code'] == code and
                           int(re.sub(r'\D', '', c['catalog_number']) or 0) >= minimum)

    kind = REQUIREMENT_KINDS.get(spec.get("kind"))
    return Requirement(*kind) if kind else None


NOT_A_CLASS = re.compile(
    r"cooperative education|internship|independent (study|reading|research|work)|"
    r"directed (study|reading|research)|undergraduate research|^research\b|research in |"
    r"\bthesis\b|practicum|senior honors|field (work|experience|study)|study abroad|"
    r"teaching assistant|work experience|capstone project", re.I)


def is_real_class(course):
    """Filters out co-ops, internships, independent study and the like — not things to 'take'."""
    return not NOT_A_CLASS.search(course['title'])


STANDINGS = {"freshman": 1, "sophomore": 2, "junior": 3, "senior": 4}


def eligible(course, standing):
    """
    standing: 1 (freshman) – 4 (senior), or None to only screen out grad-only courses.
    Filters out grad-only and honors-only courses, and ones that need a higher class standing.
    """
    text = (course.get('prerequisites') or "").lower()
    if "honors program" in text and " or " not in text:
        return False
    if re.match(r"(graduate|grad)[/ a-z]*standing\s*$", text.strip()) or \
       text.startswith("graduate/professional standing") and " or " not in text:
        return False
    if not standing:
        return True
    needed = [rank for word, rank in STANDINGS.items() if f"{word} standing" in text]
    return not needed or min(needed) <= standing


def needs_prereqs(course):
    """True if the prerequisite text names a specific course (e.g. 'MATH 221'), not just standing."""
    return bool(re.search(r'\d{3}', course.get('prerequisites') or ""))


def _level_penalty(course):
    """500/600-level courses are often grad-heavy; nudge suggestions toward lower levels."""
    number = int(re.sub(r'\D', '', course['catalog_number']) or 0)
    return 0 if number < 500 else 8 if number < 600 else 16


def course_quality(course, grades, interest_hit, covers, priority):
    """0–100ish: how good a pick this course is, before thinking about the schedule."""
    stats = grades and (grades['recent'] or grades['overall'])
    ease  = 50 if not stats else max(0, min(100, (stats['gpa'] - 2.6) / 1.3 * 100))
    weights = {"easy": (0.8, 0.2), "interesting": (0.3, 0.7)}.get(priority, (0.55, 0.45))
    score = ease * weights[0] + (100 if interest_hit else 0) * weights[1]
    return round(score + 12 * max(0, covers - 1) - _level_penalty(course))


def _load(course, term):
    try:
        return uw_api.load_course_entry({**course, "term": term})
    except OSError:
        return None


def _fits_any(options, base_schedules):
    return any(not any(has_time_conflict(s, b) for s in opt for b in base)
               for opt in options for base in base_schedules)


def fill(term, must, specs, prefs, blocked=None, include_closed=False, target_credits=None,
         interests=(), priority="balanced", skip_prereqs=True, standing=None):
    """
    must:  course summaries the student is definitely taking
    specs: requirement specs (see build_requirement)
    Returns a dict with plans, per-requirement alternatives and anything that couldn't be filled.
    """
    reqs = [r for r in (build_requirement(term, s) for s in specs) if r]
    interests = [i.strip() for i in interests if i.strip()][:4]

    # 1. Best arrangements of the must-haves — suggestions have to fit around one of these
    must_entries = list(pool.map(lambda c: _load(c, term), must))
    if any(e is None for e in must_entries):
        return {"error": "Couldn't load sections for one of your must-have courses. Try again."}
    if must_entries:
        found = generate_schedules(must_entries, include_closed, blocked, limit=2000,
                                   option_key=option_key(prefs))
        if not found:
            return {"error": "Your must-have courses don't fit together. Remove one or allow full sections."}
        base = [s for _, _, s in rank_schedules(found, prefs)[:15]]
    else:
        base = [[]]

    # Requirements a must-have already covers don't need another course
    covered_by = {i: next((c['name'] for c in must if r.match(c)), None) for i, r in enumerate(reqs)}

    # 2. Candidate courses from the catalog
    taken    = {c['name'] for c in must} | {c['course_id'] for c in must}
    catalog  = {}
    interest_of = {}

    def collect(hits, mark_interest=None):
        for c in hits:
            if c['name'] in taken or c['course_id'] in taken or not c['min_credits']:
                continue
            if skip_prereqs and needs_prereqs(c):
                continue
            if not is_real_class(c) or not eligible(c, standing):
                continue
            # Sub-100 courses (ensembles, remedial) generally don't count toward a degree
            if int(re.sub(r'\D', '', c['catalog_number']) or 0) < 100:
                continue
            # Cross-listed courses show up once per subject — keep the first
            if any(o['course_id'] == c['course_id'] and o['name'] != c['name'] for o in catalog.values()):
                continue
            catalog.setdefault(c['name'], c)
            if mark_interest:
                interest_of.setdefault(c['name'], mark_interest)

    searches = []
    for i, r in enumerate(reqs):
        if covered_by[i]:
            continue
        searches.append((r.filters, "", POOL_SIZE, None))
        searches += [(r.filters, word, 60, word) for word in interests]
    if target_credits:
        searches += [([], word, 80, word) for word in interests]
    results = pool.map(lambda a: uw_api.search_courses(term, a[1], filters=a[0] + [UNDERGRAD],
                                                       page_size=a[2])[1], searches)
    for (_, _, _, word), hits in zip(searches, results):
        collect(hits, word)

    # Shortlist on catalog data alone (requirements covered, interest match, level) so we only
    # pull live sections and grades for courses with a real chance of being picked
    def promise(n):
        c = catalog[n]
        covered = sum(1 for i, r in enumerate(reqs) if not covered_by[i] and r.match(c))
        return (covered, n in interest_of, -_level_penalty(c))

    names = set(interest_of)
    for i, r in enumerate(reqs):
        if not covered_by[i]:
            names |= set(sorted((n for n in catalog if r.match(catalog[n])), key=promise, reverse=True)[:PREFETCH])
    names = list(names)

    # 3. Live sections; keep courses with an open section that fits around the must-haves
    entries = dict(zip(names, pool.map(lambda n: _load(catalog[n], term), names)))
    options = {n: usable_packages(e, include_closed, blocked) for n, e in entries.items() if e}
    fits    = [n for n, opts in options.items() if opts and _fits_any(opts, base)]

    # 4. Grades + how good each course is on its own
    grades  = dict(zip(fits, pool.map(
        lambda n: madgrades.course_grades(catalog[n]['subject'], catalog[n]['catalog_number']), fits)))
    covers  = {n: [i for i, r in enumerate(reqs) if not covered_by[i] and r.match(catalog[n])] for n in fits}
    quality = {n: course_quality(catalog[n], grades[n], n in interest_of, len(covers[n]), priority) for n in fits}

    candidates = {i: sorted((n for n in fits if i in covers[n]), key=lambda n: -quality[n])
                  for i in range(len(reqs)) if not covered_by[i]}
    unfilled   = [reqs[i].label for i, c in candidates.items() if not c]

    # 5. Combine: one course per open requirement (a course can cover several), all fitting together
    checks = [0]

    def feasible(chosen):
        checks[0] += 1
        return bool(generate_schedules(must_entries + [entries[n] for n in chosen],
                                       include_closed, blocked, limit=1))

    order = sorted((i for i, c in candidates.items() if c), key=lambda i: len(candidates[i]))
    sets  = []

    def search(k, chosen):
        if len(sets) >= MAX_SETS or checks[0] >= CHECK_LIMIT:
            return
        if k == len(order):
            sets.append(list(chosen))
            return
        i = order[k]
        if any(i in covers[n] for n in chosen):
            search(k + 1, chosen)
            return
        for n in candidates[i][:BRANCH]:
            if n in chosen:
                continue
            chosen.append(n)
            if feasible(chosen):
                search(k + 1, chosen)
            chosen.pop()

    search(0, [])
    if not sets and order:
        # Nothing covers everything at once — fill as many requirements as possible instead
        best = []
        for i in order:
            for n in candidates[i][:BRANCH]:
                if n not in best and feasible(best + [n]):
                    best.append(n)
                    break
        sets = [best]

    # 6. Top up to the credit target with the best remaining courses
    must_credits = sum(c['min_credits'] for c in must)

    def top_up(chosen):
        credits = must_credits + sum(catalog[n]['min_credits'] for n in chosen)
        if not target_credits:
            return chosen
        extras = sorted((n for n in fits if n not in chosen),
                        key=lambda n: (n in interest_of, quality[n]), reverse=True)
        # First pass only takes full-size courses; second pass closes any small gap left
        for min_size in (3, 1):
            for n in extras[:40]:
                if credits >= target_credits:
                    return chosen
                c = catalog[n]['min_credits']
                if n in chosen or c < min(min_size, target_credits - credits):
                    continue
                if credits + c <= target_credits + 1 and feasible(chosen + [n]):
                    chosen, credits = chosen + [n], credits + c
        return chosen

    # 7. Turn the best course sets into actual schedules and rank them
    sets.sort(key=lambda s: -sum(quality[n] for n in s))
    plans, seen = [], []
    for chosen in sets[:8]:
        chosen = top_up(chosen)
        if set(chosen) in seen:
            continue
        seen.append(set(chosen))
        found = generate_schedules(must_entries + [entries[n] for n in chosen], include_closed, blocked,
                                   limit=3000, option_key=option_key(prefs))
        if not found:
            continue
        sched_score, notes, schedule = rank_schedules(found, prefs)[0]
        on_calendar = {c.name for c in schedule}
        avg_quality = sum(quality[n] for n in chosen) / len(chosen) if chosen else 70
        plans.append({
            "score":    round(0.5 * sched_score + 0.5 * min(100, avg_quality)),
            "notes":    notes,
            "credits":  must_credits + sum(catalog[n]['min_credits'] for n in chosen),
            "schedule": schedule,
            "picks":    [_explain(catalog[n], grades[n], [reqs[i].label for i in covers[n]],
                                  interest_of.get(n), online=n not in on_calendar) for n in chosen],
        })
    plans.sort(key=lambda p: -p['score'])

    alternatives = [{
        "label":   reqs[i].label,
        "options": [_explain(catalog[n], grades[n], [reqs[j].label for j in covers[n]],
                             interest_of.get(n), online=not any(options[n])) for n in candidates[i][:8]],
    } for i in order]

    return {
        "plans":        plans[:3],
        "alternatives": alternatives,
        "covered":      [{"label": reqs[i].label, "by": name} for i, name in covered_by.items() if name],
        "unfilled":     unfilled,
        "checked":      len(catalog),
        "fitting":      len(fits),
        "grades":       {n: grades[n] for n in fits},
    }


def _explain(course, grades, req_labels, interest, online=False):
    """A pick plus the reasons it was chosen, for the UI."""
    stats = grades and (grades['recent'] or grades['overall'])
    return {
        **course,
        "grades":      {"overall": grades['overall'], "recent": grades['recent']} if grades else None,
        "satisfies":   req_labels,
        "interest":    interest,
        "ease":        stats,
        "online":      online,
    }
