"""All mark calculations live here, so the screens and the PDFs always agree.

Every subject counts out of its own maximum (for example Mathematics 60, English 50).
For one subject, one student, one term:

  tests %  = marks scored in all tests written / total marks of those tests x 100
  exam %   = exam mark / exam total x 100
  final %  = tests % x test weight + exam % x exam weight
  MARK     = final % x the subject's maximum          (e.g. 90 % of 60 = 54)

For the report:

  TOTAL      = the subject marks added together
  PERCENTAGE = TOTAL / the maximums of ALL the class's subjects added together x 100
               (a subject without a mark counts as nothing scored, as on the school's own reports)
  POSITION   = place in the class by the printed percentage (equal percentages share a place: 1, 2, 2, 4);
               every student with at least one subject mark gets a position

Conduct is shown on the report but is not counted in the total.
A blank mark means "not written" and is left out. Enter 0 for a real zero.
A subject has a mark only when both the tests and the exam have marks
(unless one of the two weights is set to 0).
"""

import math

SUBJECT_ORDER = "CASE s.category WHEN 'core' THEN 0 WHEN 'noncore' THEN 1 ELSE 2 END, s.sort_order, s.name COLLATE NOCASE"


def round1(x):
    """Round to one decimal, halves up - the same way the school's existing reports are rounded."""
    return math.floor(x * 10 + 0.5) / 10


def _number(text, default):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def load_settings(db):
    cfg = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    cfg["test_weight_num"] = min(100.0, max(0.0, _number(cfg.get("test_weight"), 40.0)))
    cfg["exam_weight_num"] = 100.0 - cfg["test_weight_num"]
    cfg["pass_mark_num"] = _number(cfg.get("pass_mark"), 50.0)
    cfg["conduct_max_num"] = max(0.0, _number(cfg.get("conduct_max"), 40.0))
    cfg["detail"] = cfg.get("report_detail") == "1"
    return cfg


def rank(values):
    """Competition ranking, highest first: 1, 2, 2, 4. `values` maps id -> number or None."""
    scored = sorted((v for v in values.values() if v is not None), reverse=True)
    first_place = {}
    for i, v in enumerate(scored, start=1):
        first_place.setdefault(v, i)
    return {k: (first_place[v] if v is not None else None) for k, v in values.items()}


def class_students(db, class_id):
    return db.execute(
        "SELECT * FROM students WHERE class_id = ? ORDER BY full_name COLLATE NOCASE", (class_id,)
    ).fetchall()


def subject_results(db, class_id, subject_id, max_mark, term_id, students, cfg):
    assessments = db.execute(
        "SELECT * FROM assessments WHERE class_id = ? AND subject_id = ? AND term_id = ? ORDER BY id",
        (class_id, subject_id, term_id),
    ).fetchall()
    tests = [a for a in assessments if a["kind"] == "test"]
    exam = next((a for a in assessments if a["kind"] == "exam"), None)
    marks = {}
    if assessments:
        ids = ",".join(str(a["id"]) for a in assessments)
        for m in db.execute(f"SELECT assessment_id, student_id, score FROM marks WHERE assessment_id IN ({ids})"):
            marks[(m["assessment_id"], m["student_id"])] = m["score"]

    tw, ew = cfg["test_weight_num"], cfg["exam_weight_num"]
    rows = {}
    for s in students:
        got = total = 0.0
        for t in tests:
            sc = marks.get((t["id"], s["id"]))
            if sc is not None:
                got += sc
                total += t["max_mark"]
        tests_pct = got / total * 100 if total else None
        exam_pct = None
        if exam is not None:
            sc = marks.get((exam["id"], s["id"]))
            if sc is not None:
                exam_pct = sc / exam["max_mark"] * 100
        final = mark = test_part = exam_part = None
        if not (tw > 0 and tests_pct is None) and not (ew > 0 and exam_pct is None):
            raw = ((tests_pct or 0) * tw + (exam_pct or 0) * ew) / 100          # final % before rounding
            final = round1(raw)
            mark = round1(raw * max_mark / 100)                                  # scaled to the subject's maximum
            test_part = round1((tests_pct or 0) * tw / 100 * max_mark / 100)
            exam_part = round(mark - test_part, 1)
        rows[s["id"]] = {"tests_pct": tests_pct, "exam_pct": exam_pct, "final": final, "mark": mark,
                         "test_part": test_part, "exam_part": exam_part}
    positions = rank({sid: r["mark"] for sid, r in rows.items()})
    for sid, r in rows.items():
        r["position"] = positions[sid]
    return {"tests": tests, "exam": exam, "marks": marks, "rows": rows, "max_mark": max_mark,
            "test_max": round(max_mark * tw / 100, 1), "exam_max": round(max_mark * ew / 100, 1),
            "ranked": sum(1 for r in rows.values() if r["mark"] is not None)}


def class_results(db, class_id, term_id, cfg=None):
    cfg = cfg or load_settings(db)
    students = class_students(db, class_id)
    subjects = db.execute(
        # every course of the class's level is on the report, whether or not a teacher is chosen for it
        # in the system (and a subject given to the class from elsewhere, if it has a teacher)
        f"""SELECT s.id, s.name, s.code, s.max_mark, s.category, u.full_name AS teacher_name, a.id AS allocation_id,
                   a.teacher_id
            FROM subjects s JOIN classes c ON c.id = ?
                 LEFT JOIN allocations a ON a.class_id = c.id AND a.subject_id = s.id LEFT JOIN users u ON u.id = a.teacher_id
            WHERE (s.level_id = c.level_id AND NOT EXISTS (SELECT 1 FROM removed_courses r WHERE r.class_id = c.id AND r.subject_id = s.id))
                  OR a.id IS NOT NULL ORDER BY {SUBJECT_ORDER}""",
        (class_id,),
    ).fetchall()
    per_subject = {sub["id"]: subject_results(db, class_id, sub["id"], sub["max_mark"], term_id, students, cfg)
                   for sub in subjects}
    total_max = sum(sub["max_mark"] for sub in subjects)

    summary = {}
    for s in students:
        done = [(per_subject[sub["id"]]["rows"][s["id"]], sub["max_mark"]) for sub in subjects
                if per_subject[sub["id"]]["rows"][s["id"]]["mark"] is not None]
        total = round(sum(r["mark"] for r, _ in done), 1) if done else None
        max_done = sum(m for _, m in done)
        exact = total / total_max * 100 if done and total_max else None
        complete = bool(subjects) and len(done) == len(subjects)
        summary[s["id"]] = {
            "total": total,
            "max_done": max_done,
            "test_total": round(sum(r["test_part"] for r, _ in done), 1) if done else None,
            "exam_total": round(sum(r["exam_part"] for r, _ in done), 1) if done else None,
            "exact": exact,
            "percentage": round1(exact) if exact is not None else None,
            "done": len(done),
            "complete": complete,
            "passed": (exact >= cfg["pass_mark_num"]) if (complete and exact is not None) else None,
        }
    # Ranked on the percentage as printed (one decimal), so two students whose reports show the
    # same percentage always share a position. A student with no marks at all has no position.
    positions = rank({sid: v["percentage"] for sid, v in summary.items()})
    for sid, v in summary.items():
        v["position"] = positions[sid]
    return {"cfg": cfg, "students": students, "subjects": subjects, "per_subject": per_subject,
            "summary": summary, "total_max": total_max,
            "ranked": sum(1 for p in positions.values() if p is not None)}
