# suggest.py
# "Fill the rest": picks courses that satisfy the student's remaining requirements,
# fit around their must-have classes, and rank well on easiness and interest

import re
from concurrent.futures import ThreadPoolExecutor

import dars
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
MAX_CREDITS = 18    # UW's normal per-term limit without a dean's approval
MIX         = "MMMGG"   # when not everything fits: about 3 major classes for every 2 gen eds


def _any(field, codes):
    return {"bool": {"should": [{"term": {field: c}} for c in codes]}}


def _breadth(*codes):
    return [_any("breadths.code", codes)], lambda c: bool(set(codes) & set(c['breadth_codes']))


def _gen_ed(code):
    return [{"term": {"generalEd.code": code}}], lambda c: c['gen_ed_code'] == code


# kind -> (label, filters, matcher). L&S counts Literature as Humanities. "N" is general natural
# science (most CS, math and stat): it counts toward Natural Science but not Biological or Physical.
REQUIREMENT_KINDS = {
    "humanities": ("Humanities",         *_breadth("H", "L")),
    "literature": ("Literature",         *_breadth("L")),
    "social":     ("Social Science",     *_breadth("S")),
    "natural":    ("Natural Science",    *_breadth("B", "P", "N")),
    "biological": ("Biological Science", *_breadth("B")),
    "physical":   ("Physical Science",   *_breadth("P")),
    "ethnic":     ("Ethnic Studies", [{"term": {"ethnicStudies.code": "ETHNIC ST"}}],
                   lambda c: bool(c['ethnic_studies'])),
    "comm_a":     ("Communication A",    *_gen_ed("COM A")),
    "comm_b":     ("Communication B",    *_gen_ed("COM B")),
    "qr_a":       ("Quant Reasoning A",  *_gen_ed("QR-A")),
    "qr_b":       ("Quant Reasoning B",  *_gen_ed("QR-B")),
    "elective":   ("Any elective", [], lambda c: True),
}


class Requirement:
    def __init__(self, label, filters, match, courses=None, count=1, group=None):
        self.label   = label
        self.filters = filters
        self.match   = match
        self.courses = courses   # explicit course names (DARS "SELECT FROM: MATH 320 OR 340 ...")
        self.count   = count     # how many courses it still needs
        self.group   = group     # a course counts once per group (e.g. once within the major)
        self.major   = False     # major/program requirement, as opposed to a gen ed or breadth


def build_requirement(term, spec):
    """
    spec: {"kind": "humanities"}, {"kind": "subject", "subject": "cs", "min": 300},
          or {"kind": "list", "courses": ["MATH 320", ...]}
          plus optional "label", "count" and "group" (set when the spec comes from DARS)
    Returns a Requirement, or None if the spec doesn't make sense for this term.
    """
    req = _build(term, spec)
    if req:
        req.label = spec.get("label") or req.label
        req.count = max(1, int(spec.get("count") or 1))
        req.group = spec.get("group")
        req.major = (not re.search(r"general education|breadth", req.group, re.I) if req.group
                     else spec.get("kind") in ("subject", "list"))
        if spec.get("credits"):
            # "Needs 3 credits" in one course means a 1-credit seminar won't do it
            per_course, match = min(3, float(spec["credits"]) / req.count), req.match
            req.match = lambda c: match(c) and c['min_credits'] >= per_course
    return req


def _build(term, spec):
    if spec.get("kind") == "list":
        names = [n.upper() for n in spec.get("courses", [])][:60]
        return Requirement("One of a list", [], lambda c: c['name'] in names, courses=names) if names else None

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


def allocate(reqs, courses, only=None):
    """
    How many of `courses` count toward each requirement, DARS-style: a course can count toward
    requirements in different groups, but only once within a group (e.g. once inside the major).
    Returns {requirement index: count}.
    """
    counts, used = {}, set()
    for i, r in enumerate(reqs):
        if only is not None and i not in only:
            continue
        for c in courses:
            key = (r.group, c['name']) if r.group else None
            if counts.get(i, 0) >= r.count or not r.match(c) or (key and key in used):
                continue
            counts[i] = counts.get(i, 0) + 1
            if key:
                used.add(key)
    return counts


def _load(course, term):
    try:
        return uw_api.load_course_entry({**course, "term": term})
    except OSError:
        return None


def _fits_any(options, base_schedules):
    return any(not any(has_time_conflict(s, b) for s in opt for b in base)
               for opt in options for base in base_schedules)


def fill(term, must, specs, prefs, blocked=None, include_closed=False, min_credits=None,
         interests=(), priority="balanced", skip_prereqs=True, standing=None, completed=None,
         programs=(), bonus_specs=(), max_credits=MAX_CREDITS, max_classes=None):
    """
    must:      course summaries the student is definitely taking
    specs:     requirement specs (see build_requirement)
    completed: course names already taken or in progress (from DARS); when given, prerequisites
               are checked against it instead of skipping every course that has one
    bonus_specs: other open requirements not asked for this term; extra credits go toward these first
    min_credits, max_credits: the credit range. Plans fill toward the top and never go over it;
               requirements that don't fit are left for later terms
    max_classes: optional cap on how many classes, must-haves included
    Returns a dict with plans, per-requirement alternatives and anything that couldn't be filled.
    """
    reqs  = [r for r in (build_requirement(term, s) for s in specs) if r]
    bonus = [r for r in (build_requirement(term, s) for s in bonus_specs) if r]
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

    completed = [c.upper() for c in completed] if completed is not None else None

    max_credits  = max(1, min(MAX_CREDITS, int(max_credits or MAX_CREDITS)))
    min_credits  = min(int(min_credits or 0), max_credits)
    max_classes  = int(max_classes) if max_classes else None
    must_credits = sum(c['min_credits'] for c in must)
    if must_credits > max_credits:
        return {"error": f"Your must-haves are already {must_credits:g} credits, over your max of {max_credits}."}
    if max_classes and len(must) > max_classes:
        return {"error": f"You have {len(must)} must-haves, more than your limit of {max_classes} classes."}

    # Must-haves count toward requirements first; covered_by names the one that finished a requirement
    must_alloc = allocate(reqs, must)
    need       = {i: max(0, r.count - must_alloc.get(i, 0)) for i, r in enumerate(reqs)}
    covered_by = {i: (next((c['name'] for c in must if r.match(c)), None) if not need[i] else None)
                  for i, r in enumerate(reqs)}

    # 2. Candidate courses from the catalog
    taken    = {c['name'] for c in must} | {c['course_id'] for c in must} | set(completed or [])
    catalog  = {}
    interest_of = {}

    def collect(hits, mark_interest=None):
        for c in hits:
            if c['name'] in taken or c['course_id'] in taken or not c['min_credits']:
                continue
            if completed is not None:
                if not dars.prereqs_met(c.get('prerequisites'), completed, standing, programs):
                    continue
            elif skip_prereqs and needs_prereqs(c):
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
        if r.courses:
            # Explicit list: look each course up directly instead of searching a category
            searches += [([], name, 5, None) for name in r.courses]
            continue
        searches.append((r.filters, "", POOL_SIZE, None))
        searches += [(r.filters, word, 60, word) for word in interests]
    searches += [([], word, 80, word) for word in interests]
    for r in bonus:
        searches += [([], name, 5, None) for name in r.courses] if r.courses else [(r.filters, "", 150, None)]
    results = pool.map(lambda a: uw_api.search_courses(term, a[1], filters=a[0] + [UNDERGRAD],
                                                       page_size=a[2])[1], searches)
    offered = {}   # everything the searches found, before screening; used to explain empty requirements
    for (_, _, _, word), hits in zip(searches, results):
        for c in hits:
            offered.setdefault(c['name'], c)
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
    for r in bonus:
        names |= set(sorted((n for n in catalog if r.match(catalog[n])), key=promise, reverse=True)[:PREFETCH // 3])
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
    unfilled   = [{"label": reqs[i].label, "reason": _why_unfilled(reqs[i], offered, catalog, options)}
                  for i, c in candidates.items() if not c]

    # 5. Combine: enough courses for each open requirement (a course can cover several), all fitting together
    checks = [0]

    def feasible(chosen):
        checks[0] += 1
        return bool(generate_schedules(must_entries + [entries[n] for n in chosen],
                                       include_closed, blocked, limit=1))

    def credits(chosen):
        return must_credits + sum(catalog[n]['min_credits'] for n in chosen)

    def too_big(chosen):
        """Over the credit range's top, or over the class limit."""
        return credits(chosen) > max_credits or bool(max_classes and len(must) + len(chosen) > max_classes)

    order = sorted((i for i, c in candidates.items() if c), key=lambda i: len(candidates[i]))
    covered_idx = set(order)
    sets  = []
    over_cap = []

    def search(k, chosen, start=0):
        if len(sets) >= MAX_SETS or checks[0] >= CHECK_LIMIT:
            return
        if k == len(order):
            sets.append(list(chosen))
            return
        i = order[k]
        if allocate(reqs, [catalog[n] for n in chosen], only=covered_idx).get(i, 0) >= need[i]:
            search(k + 1, chosen)
            return
        # Courses for one requirement are tried in rank order (start) so {A, B} isn't also tried as {B, A}
        pool_i = candidates[i][:BRANCH + need[i] - 1]
        for j in range(start, len(pool_i)):
            n = pool_i[j]
            if n in chosen:
                continue
            chosen.append(n)
            if too_big(chosen):
                checks[0] += 1    # count these too, so an impossible cap can't search forever
            elif feasible(chosen):
                search(k, chosen, j + 1)
            chosen.pop()

    search(0, [])
    if not sets and order:
        # Nothing covers everything at once — fill as many requirements as possible instead,
        # one class at a time, mixing major classes and gen eds (MIX) without going over the cap
        best, hit_cap = [], set()

        def done(i):
            return allocate(reqs, [catalog[x] for x in best], only=covered_idx).get(i, 0) >= need[i]

        def add_one(i):
            for n in candidates[i][:BRANCH + need[i]]:
                if n in best:
                    continue
                if too_big(best + [n]):
                    hit_cap.add(i)
                elif feasible(best + [n]):
                    best.append(n)
                    return True
            return False

        # One slot per class still needed, in the order the requirements were listed
        slots = {True: [], False: []}
        for i in sorted(order):
            slots[reqs[i].major] += [i] * need[i]
        stuck, turn = set(), 0
        while slots[True] or slots[False]:
            kind = MIX[turn % len(MIX)] == "M"
            turn += 1
            queue = slots[kind] or slots[not kind]
            i = queue.pop(0)
            if i in stuck or done(i):
                continue
            if not add_one(i):
                stuck.add(i)
        have = allocate(reqs, [catalog[x] for x in best], only=covered_idx)
        over_cap = [reqs[i].label + (f" ({have[i]} of {need[i]})" if have.get(i) else "")
                    for i in sorted(order) if i in hit_cap and not done(i)]
        sets = [best]

    # 6. Top up toward the top of the credit range with the best remaining courses

    def top_up(chosen):
        required = set(chosen)   # picked for a requirement; only classes added here get swapped
        credits = must_credits + sum(catalog[n]['min_credits'] for n in chosen)
        # Prefer courses that also knock out another open requirement, then interest, then quality
        bonus_have = allocate(bonus, [catalog[n] for n in chosen] + list(must))

        def bonus_gain(n):
            return sum(1 for j, r in enumerate(bonus) if bonus_have.get(j, 0) < r.count and r.match(catalog[n]))

        # Extra classes lean toward the major: about how students actually build a term.
        # The one exception is a plan with no breadth or gen ed at all, which gets one first.
        def kind(n):
            matched = [r for r in reqs + bonus if r.match(catalog[n])]
            if any(r.major for r in matched):
                return "major"
            return "breadth" if matched else "other"

        def needs_breadth():
            has_breadth = any(kind(n) == "breadth" for n in chosen) or \
                          any(not r.major and r.match(c) for c in must for r in reqs + bonus)
            return not has_breadth and any(not r.major for r in reqs + bonus)

        def rank(n, breadth_first):
            order = ("breadth", "major") if breadth_first else ("major", "breadth")
            k = kind(n)
            pref = 2 - order.index(k) if k in order else 0
            return (pref, bonus_gain(n) > 0, n in interest_of, quality[n])

        extras = [n for n in fits if n not in chosen]
        # First pass only takes full-size courses; second pass closes any small gap left
        # (small courses are rarer near the top of the ranking, so that pass looks further down)
        for min_size, depth in ((3, 40), (1, 200)):
            tried = set()
            while credits < max_credits:
                breadth_first = needs_breadth()
                ranked = sorted((n for n in extras if n not in chosen and n not in tried),
                                key=lambda n: rank(n, breadth_first), reverse=True)[:depth]
                for n in ranked:
                    tried.add(n)
                    c = catalog[n]['min_credits']
                    if c < min(min_size, max_credits - credits):
                        continue
                    if not too_big(chosen + [n]) and feasible(chosen + [n]):
                        chosen, credits = chosen + [n], credits + c
                        break
                else:
                    break

        # Still under the minimum: trade a class added above for a bigger one (3 credits -> 4)
        added = [n for n in chosen if n not in required]
        for old in reversed(added):
            if credits >= min_credits:
                break
            for n in sorted(extras, key=lambda n: rank(n, False), reverse=True)[:200]:
                gain = catalog[n]['min_credits'] - catalog[old]['min_credits']
                if n in chosen or gain <= 0:
                    continue
                swapped = [x for x in chosen if x != old] + [n]
                if not too_big(swapped) and feasible(swapped):
                    chosen, credits = swapped, credits + gain
                    break
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
            "picks":    [_explain(catalog[n], grades[n],
                                  [reqs[i].label for i in covers[n]] +
                                  [r.label for r in bonus if r.match(catalog[n])],
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
        "over_cap":     over_cap,
        "min_credits":  min_credits,
        "max_credits":  max_credits,
        "max_classes":  max_classes,
        "checked":      len(catalog),
        "fitting":      len(fits),
        "grades":       {n: grades[n] for n in fits},
    }


def _why_unfilled(req, offered, catalog, options):
    """The first thing that ruled out every class for a requirement, in the order the planner screens."""
    if not any(req.match(c) for c in offered.values()):
        return ("none of its classes are offered this term" if req.courses
                else "nothing that counts for it is offered this term")
    eligible_names = [n for n, c in catalog.items() if req.match(c)]
    if not eligible_names:
        return "none of its classes are open to you yet (prereqs, class standing, or already taken)"
    if not any(options.get(n) for n in eligible_names):
        return "every section is full or during your busy times"
    return "nothing open fits around your must-haves and busy times"


def why_not(term, query, completed=None, standing=None, programs=(), specs=(), bonus_specs=(),
            plan_sections=(), blocked=None, include_closed=False, skip_prereqs=True):
    """
    Explains whether one course could go in this term's plan, check by check, in plain words.
    completed:     course names from DARS (None if no DARS, so prereqs can't be checked)
    plan_sections: Course meetings in the current plan; the course has to fit around them
    Returns {"course", "checks": [{"ok": True/False/None, "label", "detail"}], "verdict", "can_add"},
    or {"error"} if the course can't be found. ok None is just information.
    """
    code, number = uw_api.resolve_query(term, query)
    _, hits = uw_api.search_courses(term, query, page_size=10) if code else (0, [])
    # Only an exact subject + number match counts; a nearby course isn't the one they asked about
    course = next((h for h in hits if h['subject_code'] == code and
                   (number is None or h['catalog_number'] == number)), None) if number else None
    if course is None:
        return {"error": f"{query.strip().upper()} isn't offered this term, or isn't a course name MadPlan recognizes."}

    checks = []

    def check(ok, label, detail=""):
        checks.append({"ok": ok, "label": label, "detail": detail})

    check(True, "Offered this term", f"{course['title']} · {course.get('credits') or course.get('min_credits')} cr")

    done = {c.upper() for c in completed} if completed is not None else None
    if done is not None:
        if course['name'].upper() in done:
            check(False, "Already on your record", "You've taken it or are taking it now.")
        else:
            check(True, "Not on your record yet")

    number_value = int(re.sub(r"\D", "", course['catalog_number']) or 0)
    if not is_real_class(course):
        check(False, "Not a regular class", "Independent study, research, internships and the like aren't suggested.")
    elif number_value < 100:
        check(False, "Below 100-level", "These generally don't count toward a degree.")
    elif not eligible(course, standing):
        check(False, "Not open to your class standing", course.get('prerequisites') or "")

    prereq = course.get('prerequisites') or ""
    taken = done is not None and course['name'].upper() in done
    if taken:
        pass   # prereqs don't matter for a class already on the record
    elif done is not None:
        if dars.prereqs_met(prereq, done, standing, programs):
            check(True, "Prereqs met", prereq)
        else:
            check(False, "Prereqs not met yet", prereq)
    elif skip_prereqs and needs_prereqs(course):
        check(False, "Has course prereqs", "Upload your DARS so MadPlan can check them, or turn off "
                                           "“Only suggest classes with no course prereqs”. " + prereq)
    elif prereq:
        check(None, "Prereqs not checked", "Upload your DARS to check them. " + prereq)

    reqs  = [r for r in (build_requirement(term, s) for s in specs) if r]
    bonus = [r for r in (build_requirement(term, s) for s in bonus_specs) if r]
    counts = [r.label for r in reqs if r.match(course)]
    later  = [r.label for r in bonus if r.match(course)]
    if counts:
        check(True, "Counts toward " + " + ".join(counts))
    elif later:
        check(None, "Counts toward " + " + ".join(later), "You haven't ticked that requirement for this term.")
    elif reqs or bonus:
        check(False, "Doesn't count toward your requirements", "It would only be an elective.")

    entry = _load(course, term)
    others = [s for s in plan_sections if s.name != course['name']]
    if entry is None or not entry.packages:
        check(False, "No sections listed yet")
    else:
        open_options = usable_packages(entry, include_closed, blocked)
        if not open_options:
            # Sections that would work if full ones were allowed: then fullness is the problem
            if usable_packages(entry, True, blocked):
                check(False, "Every section is full", "Turn on “Show full sections” to plan around a waitlist.")
            else:
                check(False, "Every section is during your busy times")
        else:
            fitting = [o for o in open_options if not any(has_time_conflict(s, b) for s in o for b in others)]
            if fitting:
                check(True, f"{len(fitting)} open section{'s fit' if len(fitting) > 1 else ' fits'} your week")
            else:
                clashes = {}
                for o in open_options:
                    for name in {b.name for s in o for b in others if has_time_conflict(s, b)}:
                        clashes[name] = clashes.get(name, 0) + 1
                worst = sorted(clashes, key=lambda n: -clashes[n])[:2]
                check(False, "Every open section clashes with your plan",
                      "Mostly with " + " and ".join(worst) + "." if worst else "")

    grades = madgrades.course_grades(course['subject'], course['catalog_number'])
    stats = grades and (grades['recent'] or grades['overall'])
    if stats:
        check(None, f"{stats['gpa']:.2f} average GPA", f"{stats['pct_a']}% A's")

    blocker = next((c for c in checks if c["ok"] is False), None)
    return {
        "course":  {**course, "grades": {"overall": grades['overall'], "recent": grades['recent']} if grades else None},
        "checks":  checks,
        "verdict": f"Not this term: {blocker['label'].lower()}." if blocker
                   else "It works. Add it as a must-have and MadPlan will rebuild your plan around it.",
        "can_add": blocker is None,
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
