# MadPlan

A class planner for UW–Madison students. Upload your DARS and MadPlan works out what you still need, picks classes that count toward it, and builds a weekly schedule around your preferences, so you don't have to search Course Search & Enroll one class at a time.

Personal project. Not affiliated with or endorsed by the University of Wisconsin–Madison.

## What it does

- **Reads your DARS.** Upload the PDF export and it finds the courses you've taken, your year, and every requirement you have left. The file is parsed in memory and never saved.
- **Picks classes for you.** For each requirement you tick, it searches the live catalog and ranks the options by grade history (from Madgrades), your interests, and whether one class can cover several requirements at once. It skips classes you've taken, checks prerequisites against your record, and leaves out grad-only and honors-only courses.
- **Stays under a credit cap.** You set a target and a maximum (18 by default). If everything you ticked won't fit, it mixes about three major classes for every two gen eds and tells you what's left for later.
- **Builds the week.** A backtracking search tries every section combination and scores each schedule on your earliest and latest class, days off, gaps, and walking time between buildings. You get the best plan plus backups.
- **Shows live seats.** Open seats, waitlists and closed sections come straight from Course Search & Enroll.
- **Explains its picks.** Every suggested class shows what it counts for, its average GPA and share of A's, and the other options you could swap in.
- **Plans ahead.** A roadmap keeps classes for future terms and shows how your requirements fill up over time.
- **Gets you enrolled.** Copy the class numbers for enrollment, export the schedule to your calendar (.ics), or share a read-only link.

It works for any term the UW API lists, and understands shorthand like `cs400` or `stat 340`.

## How it works

| Part | File |
| --- | --- |
| Course Search & Enroll API: terms, catalog search, sections and seats, subject shorthand | `src/uw_api.py` |
| DARS PDF parsing and prerequisite checking (a small and/or parser) | `src/dars.py` |
| Requirement matching and class suggestions | `src/suggest.py` |
| Conflict-free schedule search | `src/scheduler.py` |
| Schedule scoring, walking times, backup plans | `src/scoring.py` |
| Grade distributions from Madgrades | `src/madgrades.py` |
| Flask JSON API | `src/app.py` |
| The whole front end: vanilla JS, no build step | `templates/index.html` |

API responses are cached on disk: catalog data for a day, seats for 10 minutes and grades for a week.

## Running it

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cd src && ../venv/bin/python app.py
```

Then open [localhost:5001](http://localhost:5001).

Grade data needs a free [Madgrades](https://api.madgrades.com) API token. Put it in a `.env` file at the project root, which git ignores:

```
MADGRADES_TOKEN=your-token
```

Without a token everything still works, just without GPAs.

## Limits

- Prerequisite and requirement matching are best-effort. Always check your plan against your actual DARS before enrolling.
- DARS parsing has only been tested on an L&S B.S. audit so far.
- Saved plans live in your browser's local storage.
