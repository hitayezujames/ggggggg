"""Read the school's student lists from an Excel workbook (the SDMS "STUDENT LIST" export).

Each sheet is one class. Above the table the sheet says  Grade:  Combination:  Class:  and the
table has the columns  #, Student Code, Names.  Nothing is written to the database here; this
module only reads the file and works out what an import would do.
"""
import re

from openpyxl import load_workbook

import db as database

MAX_STUDENTS = 20000


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():      # a code typed as a number: 1044250252.0 -> "1044250252"
        value = int(value)
    return " ".join(str(value).split())


def parse_workbook(path):
    """Return {"year", "school", "classes": [{"sheet", "name", "grade", "combination", "students": [(code, name)]}], "problems"}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    out = {"year": "", "school": "", "classes": [], "problems": []}
    seen_classes, total = set(), 0
    for ws in wb.worksheets:
        meta, students, columns = {}, [], None
        for row in ws.iter_rows(values_only=True):
            cells = [_text(v) for v in row]
            if not any(cells):
                continue
            if columns is None:
                lowered = [c.lower() for c in cells]
                if "student code" in lowered and any(c in ("names", "name") for c in lowered):
                    columns = (lowered.index("student code"), next(i for i, c in enumerate(lowered) if c in ("names", "name")))
                    continue
                label = cells[0].rstrip(":").lower()
                value = next((c for c in cells[1:] if c), "")
                if label in ("school", "grade", "combination", "class"):
                    meta[label] = value
                for c in cells:
                    found = re.search(r"academic year:?\s*(\d{4}\s*[/-]\s*\d{2,4})", c, re.I)
                    if found:
                        meta["year"] = re.sub(r"\s*[/-]\s*", "-", found.group(1))
                continue
            code = cells[columns[0]] if columns[0] < len(cells) else ""
            name = cells[columns[1]] if columns[1] < len(cells) else ""
            if not code and not name:
                continue
            if not code or not name:
                if re.search(r"printed on|government", " ".join(cells), re.I):
                    continue                                    # the footer line of the export
                out["problems"].append(f"Sheet {ws.title}: a row with only {'a name' if name else 'a code'} was skipped ({name or code}).")
                continue
            students.append((code[:30], name[:80]))
        if columns is None:
            if any(meta.values()):
                out["problems"].append(f"Sheet {ws.title}: no table with 'Student Code' and 'Names' was found, so it was skipped.")
            continue
        name = meta.get("class") or _text(ws.title)
        key = re.sub(r"\s+", "", name).lower()
        if key in seen_classes:
            out["problems"].append(f"Sheet {ws.title}: the class {name} appears twice in the file; the second sheet was skipped.")
            continue
        seen_classes.add(key)
        total += len(students)
        if total > MAX_STUDENTS:
            raise ValueError("The file has too many students.")
        out["classes"].append({"sheet": ws.title, "name": name[:40], "grade": meta.get("grade", ""),
                               "combination": meta.get("combination", ""), "students": students})
        out["year"] = out["year"] or meta.get("year", "")
        out["school"] = out["school"] or meta.get("school", "")
    wb.close()
    # A student code that is in the file more than once cannot be trusted: it is left out everywhere
    # (that student is neither added nor moved) and reported, so the school can correct the file.
    places = {}
    for c in out["classes"]:
        for code, student in c["students"]:
            places.setdefault(code, []).append(f"{student} in {c['name']}")
    for code, where in places.items():
        if len(where) > 1:
            out["problems"].append(f"Student code {code} is used {len(where)} times ({'; '.join(where)}). "
                                   "These lines were left out; correct the file and import again.")
    for c in out["classes"]:
        c["students"] = [(code, student) for code, student in c["students"] if len(places[code]) == 1]
    return out


def tidy_combination(text):
    """'ORDINARY LEVEL (OL)' -> 'ORDINARY LEVEL';  'Math and Science Stream One (Math and Science Stream One)'
    -> 'Math and Science Stream One'. A bracket that adds real information is kept."""
    text = " ".join((text or "").split())
    found = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    if found and (re.fullmatch(r"[A-Z0-9]{2,8}", found.group(2)) or found.group(2).lower() == found.group(1).lower()):
        return found.group(1)
    return text[:80]


def level_for(grade, combination):
    """Which level (subject list) a class of this grade and combination follows.

    Returns ("standard", key) for one of the ready-made lists, or ("named", level name) for a
    combination this system has no subject list for yet - that level is created empty and the
    admin adds its subjects."""
    comb = combination.lower()
    number = re.search(r"\d+", grade or "")
    if "ordinary" in comb or (not comb and number and int(number.group(0)) <= 3):
        return ("standard", "olevel")
    if "math and science" in comb and re.search(r"stream\s*(one|1)\b", comb):
        return ("standard", "msc")
    plain = re.sub(r"\s*\(.*?\)\s*", " ", combination).strip()
    short = re.search(r"\(([A-Z]{2,6})\)", combination)
    if "stream" in comb:
        name = f"S4, S5 - {plain}"[:60]
    else:
        grade_label = f"S{number.group(0)}" if number else (grade or "Other")
        name = f"{grade_label} - {short.group(1) if short else plain or 'no combination'}"[:60]
    for standard in database.STANDARD_LISTS:           # Stream Two, S6 MCB / MEG / PCB / PCM have a ready-made list
        if standard["name"].lower() == name.lower():
            return ("standard", standard["key"])
    return ("named", name)


def build_plan(con, parsed):
    """Compare the file with the database. Changes nothing."""
    standard_names = {x["key"]: x["name"] for x in database.STANDARD_LISTS}
    existing_classes = {re.sub(r"\s+", "", r["name"]).lower(): r for r in con.execute(
        "SELECT c.*, l.name AS level_name FROM classes c LEFT JOIN levels l ON l.id = c.level_id")}
    existing_levels = {r["name"].lower() for r in con.execute("SELECT name FROM levels")}
    where = {r["admission_no"]: r for r in con.execute(
        "SELECT s.id, s.admission_no, s.full_name, s.class_id, c.name AS class_name FROM students s JOIN classes c ON c.id = s.class_id")}
    plan = {"year": parsed["year"], "school": parsed["school"], "problems": parsed["problems"], "classes": [],
            "totals": {"classes": 0, "new_classes": 0, "students": 0, "new": 0, "same": 0, "moved": 0, "renamed": 0, "new_levels": []}}
    t = plan["totals"]
    for c in parsed["classes"]:
        row = existing_classes.get(re.sub(r"\s+", "", c["name"]).lower())
        kind, target = level_for(c["grade"], c["combination"])
        level_name = standard_names[target] if kind == "standard" else target
        if row is not None and row["level_name"]:
            level_name, level_new = row["level_name"], False            # an existing class keeps its level
        else:
            level_new = level_name.lower() not in existing_levels
        item = {"name": row["name"] if row else c["name"], "class_id": row["id"] if row else None, "grade": c["grade"],
                "combination": c["combination"], "level": level_name, "level_new": level_new,
                "level_empty": kind == "named" and level_new, "level_kind": kind, "level_target": target,
                "students": c["students"], "new": [], "same": 0, "moved": [], "renamed": []}
        for code, name in c["students"]:
            old = where.get(code)
            if old is None:
                item["new"].append((code, name))
            else:
                if row is not None and old["class_id"] == row["id"]:
                    item["same"] += 1
                else:
                    item["moved"].append((old["id"], name, old["class_name"]))
                if old["full_name"] != name:
                    item["renamed"].append((old["id"], old["full_name"], name))
        plan["classes"].append(item)
        t["classes"] += 1
        t["new_classes"] += row is None
        t["students"] += len(c["students"])
        t["new"] += len(item["new"])
        t["same"] += item["same"]
        t["moved"] += len(item["moved"])
        t["renamed"] += len(item["renamed"])
        if level_new and level_name not in t["new_levels"]:
            t["new_levels"].append(level_name)
    return plan


def apply_plan(con, plan, move=True, rename=True):
    """Carry out a plan made by build_plan. The caller commits. Returns a summary dict."""
    done = {"classes": 0, "levels": 0, "students": 0, "moved": 0, "renamed": 0}
    for item in plan["classes"]:
        class_id = item["class_id"]
        if class_id is None or item["level_new"]:
            if item["level_kind"] == "standard" and item["level_new"]:
                level_id = database.add_standard_list(con, item["level_target"])[0]
                done["levels"] += 1
            else:
                level = con.execute("SELECT id FROM levels WHERE name = ? COLLATE NOCASE", (item["level"],)).fetchone()
                if level is None:
                    order = con.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM levels").fetchone()[0]
                    level_id = con.execute("INSERT INTO levels (name, sort_order) VALUES (?, ?)", (item["level"], order)).lastrowid
                    done["levels"] += 1
                else:
                    level_id = level["id"]
            if class_id is None:
                class_id = con.execute("INSERT INTO classes (name, level_id, grade, combination) VALUES (?, ?, ?, ?)",
                                       (item["name"], level_id, item["grade"], tidy_combination(item["combination"]))).lastrowid
                done["classes"] += 1
            else:
                con.execute("UPDATE classes SET level_id = ? WHERE id = ? AND level_id IS NULL", (level_id, class_id))
        # a class that was made earlier without its grade or combination gets them from the file
        con.execute("UPDATE classes SET grade = ? WHERE id = ? AND grade = ''", (item["grade"], class_id))
        con.execute("UPDATE classes SET combination = ? WHERE id = ? AND combination = ''", (tidy_combination(item["combination"]), class_id))
        for code, name in item["new"]:
            con.execute("INSERT INTO students (class_id, admission_no, full_name, gender) VALUES (?, ?, ?, '')", (class_id, code, name))
            done["students"] += 1
        if move:
            for student_id, _name, _old in item["moved"]:
                con.execute("UPDATE students SET class_id = ? WHERE id = ?", (class_id, student_id))
                done["moved"] += 1
        if rename:
            for student_id, _old, new in item["renamed"]:
                con.execute("UPDATE students SET full_name = ? WHERE id = ?", (new, student_id))
                done["renamed"] += 1
    return done
