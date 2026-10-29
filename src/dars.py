# dars.py
# Parses a UW–Madison DARS degree audit (PDF export) into what the planner needs:
# courses taken, class standing, and unmet requirements as suggestion specs.
# Only planning data is extracted — no name, student ID or advisor.

import io
import math
import re

import pypdf

DIVIDER   = re.compile(r'^-{20,}\s*$')
HEADER    = re.compile(r'^(OK|NO)\s+(.+?)\s*$')
SUBREQ    = re.compile(r'^\s*(?:IP|IN-P|PL)?\s*([+-])\s+(?:\d+\)\s*)?(.+?)\s*$')
COURSE    = re.compile(r'^\s*(?:>\w\s+)?([A-Z]{2}\d{2})\s(.{8})\s*(\d{2,4}[A-Z]?)\s+([\d.]+)\s+(\S+)\s+(.*?)\s*$')
NEEDS     = re.compile(r'NEEDS:\s+(?:([\d.]+)\s+CREDITS?)?\s*(?:(\d+)\s+COURSES?)?')
SELECT    = re.compile(r'SELECT FROM:\s*(.*?)\s*$')
COURSE_REF = re.compile(r'([A-Z][A-Z&]*(?:\s[A-Z&]+)*)?\s*(\d{3,4})')

BREADTH_KINDS = {
    "literature": "literature", "humanities": "humanities", "biological science": "biological",
    "physical science": "physical", "natural science": "natural", "social science": "social",
}
GEN_ED_KINDS = {"a": "comm_a", "b": "comm_b"}

# Titles of requirements that have no SELECT FROM line but map cleanly to a kind
TITLE_KINDS = [
    (re.compile(r"humanities/literature/arts", re.I), "humanities"),
    (re.compile(r"social (studies|sciences)", re.I), "social"),
    (re.compile(r"communication b", re.I), "comm_b"),
    (re.compile(r"ethnic studies", re.I), "ethnic"),
]

NOT_COURSEWORK = re.compile(r"gpa|residence|credits in the degree|las credits|total credits|"
                            r"intermediate/advanced|declared|declaration", re.I)


def pdf_text(data):
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_course_list(text):
    """'STAT 309 OR 311 OR MATH 331, COMP SCI 407,506' -> ['STAT 309', 'STAT 311', 'MATH 331', 'COMP SCI 407', 'COMP SCI 506']"""
    courses, subject = [], None
    for token in re.split(r',|\bOR\b|\bAND\b', text):
        m = re.fullmatch(r'\s*([A-Z][A-Z&]*(?:\s[A-Z&]+)*)?\s*(\d{3,4})\s*', token)
        if not m:
            continue
        subject = (m.group(1) or subject or "").strip()
        if subject:
            name = f"{subject} {m.group(2)}"
            if name not in courses:
                courses.append(name)
    return courses


def select_to_spec(select):
    """Turns a SELECT FROM description into a requirement spec, or None if it isn't course-specific."""
    breadth = re.search(r"W/\s*'([^']+)'\s*Breadth", select, re.I)
    if breadth:
        kind = BREADTH_KINDS.get(breadth.group(1).lower())
        return {"kind": kind} if kind else None
    gen_ed = re.search(r"Gen-Ed code of '(\w)'", select, re.I)
    if gen_ed:
        kind = GEN_ED_KINDS.get(gen_ed.group(1).lower())
        return {"kind": kind} if kind else None
    courses = parse_course_list(select)
    return {"kind": "list", "courses": courses} if courses else None


def courses_needed(credits, courses):
    if courses:
        return int(courses)
    if credits:
        return max(1, round(float(credits) / 3.5))
    return 1


def parse(text):
    lines  = text.splitlines()
    result = {"program": None, "year": None, "taken": [], "requirements": []}

    for line in lines[:15]:
        m = re.search(r'CL/YR:\s*\w+\s+(\d)', line)
        if m:
            result["year"] = int(m.group(1))
        if re.search(r'\b(major|certificate)\s*$', line.strip(), re.I) and not result["program"]:
            result["program"] = line.strip()

    # Split into blocks on the dashed dividers
    blocks, current = [], []
    for line in lines:
        if DIVIDER.match(line):
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    blocks.append(current)

    seen_courses = {}
    for block in blocks:
        for line in block:
            m = COURSE.match(line)
            if m and m.group(5) not in ("HS",):
                name = f"{m.group(2).strip()} {m.group(3)}"
                seen_courses.setdefault(name, {
                    "term": m.group(1), "name": name, "credits": float(m.group(4)),
                    "grade": m.group(5), "in_progress": m.group(5) == "INP",
                })

        header = next((HEADER.match(l) for l in block if HEADER.match(l)), None)
        if not header or header.group(1) == "OK":
            continue
        group = header.group(2)
        if NOT_COURSEWORK.search(group):
            continue

        # Walk sub-requirements; each runs until the next sub-requirement line
        subs, sub = [], None
        for line in block:
            if COURSE.match(line):
                continue
            m = SUBREQ.match(line)
            if m and not line.strip().startswith(("NEEDS", "SELECT")):
                sub = {"done": m.group(1) == "+", "title": m.group(2), "needs": None, "select": ""}
                subs.append(sub)
                continue
            target = sub if sub else None
            n = NEEDS.search(line)
            if n and "GPA" not in line:
                if target is not None:
                    target["needs"] = n.groups()
                elif not subs:
                    subs.append({"done": False, "title": group, "needs": n.groups(), "select": ""})
                continue
            s = SELECT.search(line)
            if s:
                # Keep the first SELECT FROM; later ones describe optional add-ons (e.g. "Add'l Humanities")
                owner = target or (subs[-1] if subs else None)
                if owner is not None and not owner["select"]:
                    owner["select"] = s.group(1)
                continue
            # Continuation of a multi-line SELECT FROM list
            if target and target["select"] and re.match(r'^\s{10,}[A-Z0-9]', line) and not NEEDS.search(line):
                target["select"] += " " + line.strip()

        for sub in subs:
            if sub["done"] or NOT_COURSEWORK.search(sub["title"]):
                continue
            spec = select_to_spec(sub["select"]) if sub["select"] else None
            if not spec:
                kind = next((k for pattern, k in TITLE_KINDS if pattern.search(sub["title"]) or
                             pattern.search(group)), None)
                spec = {"kind": kind} if kind else None
            if not spec or sub["needs"] is None and not sub["select"]:
                continue
            credits, count = sub["needs"] or (None, None)
            title = sub["title"] if sub["title"] != group else group.split(":")[-1].strip()
            result["requirements"].append({
                "group": group,
                "label": title,
                "count": courses_needed(credits, count),
                "credits": float(credits) if credits and not count else None,
                "needs": f"{count} course{'s' if count and int(count) > 1 else ''}" if count
                         else f"{float(credits):g} credits" if credits else "",
                **spec,
            })

    result["taken"] = sorted(seen_courses.values(), key=lambda c: (c["term"][2:], c["term"][:2]))
    return result


def parse_pdf(data):
    return parse(pdf_text(data))


# ── Prerequisites ────────────────────────────────────────────

PREREQ_TOKEN = re.compile(
    r"\(|\)|\bAND\b|\bOR\b|,|"
    r"(?P<standing>(?:FRESHMAN|SOPHOMORE|JUNIOR|SENIOR|GRADUATE/PROFESSIONAL|GRADUATE) STANDING)|"
    r"(?P<course>(?:[A-Z][A-Z&]*(?:[ /][A-Z&]+)*\s)?\d{3,4})|"
    r"(?P<flag>TRUE_|FALSE_)|"
    r"(?P<word>[A-Z/&]+)")
STANDING_RANK = {"FRESHMAN": 1, "SOPHOMORE": 2, "JUNIOR": 3, "SENIOR": 4}
NOT_SUBJECT   = {"AND", "OR", "OF", "IN", "THE", "WITH", "FOR", "CREDIT", "COMPLETION", "CONCURRENT",
                 "ENROLLMENT", "SATISFIED", "STANDING", "DECLARED", "MAJOR", "PLACEMENT", "GRADE"}


def prereqs_met(prereq_text, completed, standing=None, programs=()):
    """
    Checks a UW prerequisite string like
        "COMP SCI/E C E 354 and (COMP SCI 367 or 400) or graduate/professional standing"
    against completed course names and declared programs (e.g. "Computer Sciences major").
    Parses and/or with parentheses; commas act like "or",
    as in UW's "X, Y, or Z" lists. Unknown conditions (consent, declared in a program) count as unmet.
    """
    text = (prereq_text or "").upper().strip()
    # Only the first sentence is the requirement; later ones are notes like "Not open to ..."
    text = re.split(r"\.\s", text)[0]
    if not re.search(r"\d{3}|STANDING|CONSENT|DECLARED", text):
        return True
    completed = {c.upper() for c in completed}

    # "declared in the Computer Sciences major" -> met if it names one of the student's programs
    names = [re.sub(r"\s+(MAJOR|PROGRAM|CERTIFICATE)$", "", p.upper()).strip() for p in programs]
    def declared(m):
        return " TRUE_ " if any(n and n in m.group(1) for n in names) else " FALSE_ "
    text = re.sub(r"DECLARED IN (?:THE |AN? )?([A-Z &'-]+?)(?=\s+(?:MAJOR|PROGRAM|CERTIFICATE)|,|\)|$)",
                  declared, text)

    # Tokenize into atoms (True/False), operators and parentheses
    tokens, subject = [], None
    for m in PREREQ_TOKEN.finditer(text):
        tok = m.group(0)
        if m.group("flag"):
            tokens.append(tok == "TRUE_")
        elif m.group("standing"):
            rank = STANDING_RANK.get(tok.split()[0])
            tokens.append(bool(rank and standing and standing >= rank))
        elif m.group("course"):
            *words, number = tok.split()
            words = [w for w in " ".join(words).split() if w not in NOT_SUBJECT]
            if words:
                subject = " ".join(words)
            if subject:
                # "COMP SCI/E C E 354" is cross-listed: any listed subject counts
                names = {f"{part.strip()} {number}" for part in subject.split("/")}
                tokens.append(bool(names & completed))
        elif tok in ("(", ")", "AND", "OR", ","):
            tokens.append("OR" if tok == "," else tok)
        elif m.group("word") and tok not in NOT_SUBJECT and len(tok) > 2:
            # Words like CONSENT / INSTRUCTOR / DECLARED become an unmet condition
            if tok in ("CONSENT", "DECLARED", "MEMBER"):
                tokens.append(False)

    # Recursive descent: expr := term (OR term)* ; term := factor (AND factor)* ; factor := atom | ( expr )
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def factor():
        nonlocal pos
        tok = peek()
        if tok == "(":
            pos += 1
            value = expr()
            if peek() == ")":
                pos += 1
            return value
        if isinstance(tok, bool):
            pos += 1
            return tok
        pos += 1          # stray operator — skip it
        return None

    def term():
        values = [factor()]
        nonlocal pos
        while peek() == "AND":
            pos += 1
            values.append(factor())
        values = [v for v in values if v is not None]
        return all(values) if values else None

    def expr():
        values = [term()]
        nonlocal pos
        while peek() == "OR":
            pos += 1
            values.append(term())
        values = [v for v in values if v is not None]
        return any(values) if values else None

    result = None
    while pos < len(tokens):
        value = expr()
        if value is not None:
            result = value if result is None else (result or value)
        if peek() == ")":
            pos += 1
    return True if result is None else result
