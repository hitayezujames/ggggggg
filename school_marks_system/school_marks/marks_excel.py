"""Marks sheets in Excel: one workbook per course (a subject in a class, for one term).

The teacher (or the admin) downloads the sheet, types the marks in Excel, and uploads it again.

Sheet "Marks":
    row 1   school name
    row 2   MARKS SHEET - subject - class - term
    row 3   teacher and a short instruction
    row 5   No | Student code | Student name | Test 1 | Test 2 | ... | (empty columns for new tests)
    row 6   "Out of"                         |  20    |  30    | ...
    row 7+  one student per row
A hidden sheet "_system" says which course and term the file belongs to, and which test of the
system each column is. Students are found again by their student code, so sorting the rows in
Excel does no harm.
"""
import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

MARK = "SCHOOL-MARKS-SHEET 1"          # the first files: one course, keys and values
MARK2 = "SCHOOL-MARKS-SHEET 2"         # one row per tab: tab, class, subject, term, columns
HEAD_ROW, MAX_ROW, FIRST_ROW = 5, 6, 7
FIRST_COL = 4                       # column D: the first test
NEW_COLS = 3                        # empty columns where a teacher may add new tests
MAX_SIZE = 5 * 1024 * 1024

GREY = PatternFill("solid", fgColor="E7EAF0")
BLUE = PatternFill("solid", fgColor="1D4F91")
EXAM = PatternFill("solid", fgColor="FDF0E1")
NEWF = PatternFill("solid", fgColor="F3F7EC")
THIN = Side(style="thin", color="B9C2D0")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _num(v):
    return int(v) if float(v).is_integer() else v


def _tab_name(text, used):
    name = re.sub(r"[\[\]:*?/\\']", "", text).strip()[:28] or "Course"
    base, n = name, 2
    while name.lower() in used or name.lower() in ("summary", "instructions", "_system"):
        name = f"{base[:25]} {n}"
        n += 1
    used.add(name.lower())
    return name


def _write_course(ws, school, course, term, students, assessments, marks, width=None):
    """One course on one worksheet. Returns the number of mark columns (tests that exist + empty ones)."""
    n_cols = max(len(assessments) + NEW_COLS, width or 0)
    last_col = FIRST_COL + n_cols - 1
    ws["A1"] = school
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"MARKS SHEET - {course['subject_name']} - {course['class_name']} - {term['name']}"
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = (f"Teacher: {course.get('teacher_name') or '..........................'}.  Out of {_num(course['subject_max'])} on the report.  "
                "Type each student's mark in the white boxes. Leave a box empty if the student did not write. Do not change "
                "the student codes or this title. To add a test, type its name in a green column (row 5) and what it is "
                "marked out of (row 6); a name with the word Exam is the exam.")
    ws["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max(last_col, 8))
    ws.row_dimensions[3].height = 48

    for col, text in ((1, "No"), (2, "Student code"), (3, "Student name")):
        c = ws.cell(HEAD_ROW, col, text)
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), BLUE, BOX
        ws.cell(MAX_ROW, col, "Out of" if col == 3 else None).fill = GREY
        ws.cell(MAX_ROW, col).font = Font(bold=True)
        ws.cell(MAX_ROW, col).alignment = Alignment(horizontal="right")
        ws.cell(MAX_ROW, col).border = BOX
    for i, a in enumerate(assessments):
        col = FIRST_COL + i
        c = ws.cell(HEAD_ROW, col, a["name"])
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), BLUE, BOX
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        m = ws.cell(MAX_ROW, col, _num(a["max_mark"]))
        m.font, m.fill, m.border, m.alignment = Font(bold=True), GREY, BOX, Alignment(horizontal="center")
        dv = DataValidation(type="decimal", operator="between", formula1="0", formula2=str(a["max_mark"]), allow_blank=True,
                            showErrorMessage=True, errorTitle="Not a valid mark",
                            error=f"{a['name']} is marked out of {_num(a['max_mark'])}: type a number from 0 to {_num(a['max_mark'])}.")
        ws.add_data_validation(dv)
        if students:
            dv.add(f"{get_column_letter(col)}{FIRST_ROW}:{get_column_letter(col)}{FIRST_ROW + len(students) - 1}")
    for k in range(len(assessments), n_cols):
        col = FIRST_COL + k
        for row in (HEAD_ROW, MAX_ROW):
            c = ws.cell(row, col)
            c.fill, c.border = NEWF, BOX
        ws.cell(HEAD_ROW, col).alignment = Alignment(horizontal="center", wrap_text=True)
        ws.cell(MAX_ROW, col).alignment = Alignment(horizontal="center")

    for n, s in enumerate(students):
        row = FIRST_ROW + n
        for col, value in ((1, n + 1), (2, s["admission_no"]), (3, s["full_name"])):
            c = ws.cell(row, col, value)
            c.fill, c.border = GREY, BOX
        ws.cell(row, 2).number_format = "@"
        for k in range(n_cols):
            a = assessments[k] if k < len(assessments) else None
            score = marks.get((a["id"], s["id"])) if a else None
            c = ws.cell(row, FIRST_COL + k, _num(score) if score is not None else None)
            c.border, c.alignment = BOX, Alignment(horizontal="center")
            if a and a["kind"] == "exam":
                c.fill = EXAM

    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 34
    for col in range(FIRST_COL, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 11
    ws.row_dimensions[HEAD_ROW].height = 30
    ws.freeze_panes = ws.cell(FIRST_ROW, FIRST_COL)
    ws.print_title_rows = f"{HEAD_ROW}:{MAX_ROW}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    return n_cols


def _hidden(wb, entries):
    """The system's own information, on a hidden sheet: which course, term and test each tab and column is."""
    info = wb.create_sheet("_system")
    info.sheet_state = "veryHidden"
    info.cell(1, 1, "format")
    info.cell(1, 2, MARK2)
    for r, (tab, course, term, assessments) in enumerate(entries, start=2):
        for c, v in enumerate([tab, course["class_id"], course["subject_id"], term["id"], ",".join(str(a["id"]) for a in assessments)], start=1):
            info.cell(r, c, v)


def _save(wb):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build(school, alloc, term, students, assessments, marks):
    """The sheet of one course as an .xlsx file. assessments: rows with id, name, kind, max_mark;
    marks: {(assessment id, student id): score}."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Marks"
    _write_course(ws, school, alloc, term, students, assessments, marks)
    _hidden(wb, [("Marks", alloc, term, assessments)])
    return _save(wb)


def build_class(school, cls, term, students, courses):
    """One workbook for a whole class: a tab per course (the same layout as a course's own sheet) and a
    Summary tab that adds up the tests of every course, so the class can be checked before it is imported.
    courses: [(course, assessments, marks)] in report order."""
    wb = Workbook()
    intro = wb.active
    intro.title = "Summary"
    used = set()
    entries, tabs = [], []
    for course, assessments, marks in courses:
        tab = _tab_name(course.get("subject_code") or course["subject_name"], used)
        ws = wb.create_sheet(tab)
        n_cols = _write_course(ws, school, course, term, students, assessments, marks, width=8)
        entries.append((tab, course, term, assessments))
        tabs.append((tab, course, n_cols))
    _summary(intro, school, cls, term, students, tabs)
    _hidden(wb, entries)
    return _save(wb)


def _summary(ws, school, cls, term, students, tabs):
    """Formulas only (they work in Excel and in Google Sheets). Periodical: the tests of each course, no exam."""
    ws["A1"] = school
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"SUMMARY - {cls['name']} - {term['name']}"
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = ("Each teacher fills the tab of their own course (the tabs at the bottom). This page adds up the TESTS of every "
                "course (no exam), out of the course's maximum, to check the class before it is imported. Do not type here. "
                "The report cards themselves are made by the marks system after importing (a mark here can differ by 0.1 "
                "because of rounding).")
    ws["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    last = 3 + len(tabs) + 3
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max(last, 8))
    ws.row_dimensions[3].height = 48
    heads = ["No", "Student code", "Student name"] + [t for t, _, _ in tabs] + ["TOTAL", "%", "POSITION"]
    for col, text in enumerate(heads, start=1):
        c = ws.cell(HEAD_ROW, col, text)
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), BLUE, BOX
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        m = ws.cell(MAX_ROW, col)
        m.fill, m.border, m.font, m.alignment = GREY, BOX, Font(bold=True), Alignment(horizontal="center")
    ws.cell(MAX_ROW, 3, "Out of").alignment = Alignment(horizontal="right")
    for i, (tab, course, n_cols) in enumerate(tabs):
        ws.cell(MAX_ROW, 4 + i, _num(course["subject_max"]))
    total_col = 4 + len(tabs)
    total_max = sum(c["subject_max"] for _, c, _ in tabs)
    ws.cell(MAX_ROW, total_col, _num(total_max))
    ws.cell(MAX_ROW, total_col + 1, 100)
    n = len(students)
    end = FIRST_ROW + max(n, 1) - 1
    for k, s in enumerate(students):
        row = FIRST_ROW + k
        for col, value in ((1, k + 1), (2, s["admission_no"]), (3, s["full_name"])):
            c = ws.cell(row, col, value)
            c.fill, c.border = GREY, BOX
        ws.cell(row, 2).number_format = "@"
        for i, (tab, course, n_cols) in enumerate(tabs):
            q = "'" + tab.replace("'", "''") + "'"
            last_letter = get_column_letter(FIRST_COL + n_cols - 1)
            marks = f"INDEX({q}!$D$7:${last_letter}$600,MATCH($B{row},{q}!$B$7:$B$600,0),0)"
            tests = f"ISNUMBER({marks})*ISERROR(SEARCH(\"exam\",{q}!$D$5:${last_letter}$5))"
            got = f"SUMPRODUCT({tests},{marks})"
            out_of = f"SUMPRODUCT({tests},{q}!$D$6:${last_letter}$6)"
            c = ws.cell(row, 4 + i, f"=IFERROR(IF({out_of}=0,\"\",ROUND({got}/{out_of}*{get_column_letter(4 + i)}${MAX_ROW},1)),\"\")")
            c.border, c.alignment = BOX, Alignment(horizontal="center")
        first_l, last_l = get_column_letter(4), get_column_letter(3 + len(tabs))
        tl, pl = get_column_letter(total_col), get_column_letter(total_col + 1)
        ws.cell(row, total_col, f"=IF(COUNT({first_l}{row}:{last_l}{row})=0,\"\",SUM({first_l}{row}:{last_l}{row}))")
        ws.cell(row, total_col + 1, f"=IF({tl}{row}=\"\",\"\",ROUND({tl}{row}/{tl}${MAX_ROW}*100,1))")
        ws.cell(row, total_col + 2, f"=IF({pl}{row}=\"\",\"\",RANK({pl}{row},{pl}${FIRST_ROW}:{pl}${end}))")
        for col in (total_col, total_col + 1, total_col + 2):
            c = ws.cell(row, col)
            c.border, c.font, c.alignment = BOX, Font(bold=True), Alignment(horizontal="center")
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 34
    for col in range(4, total_col + 3):
        ws.column_dimensions[get_column_letter(col)].width = 9
    ws.row_dimensions[HEAD_ROW].height = 30
    ws.freeze_panes = ws.cell(FIRST_ROW, 4)
    ws.protection.sheet = True          # nobody types here: Excel and Google Sheets warn before the Summary is changed
    ws.protection.formatColumns = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0


def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _number(v):
    """A mark from a cell: a number, or text like '12,5'. None for an empty cell, False for something else."""
    if v is None or (isinstance(v, str) and v.strip() in ("", "-")):
        return None
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return float(v) if v == v and abs(v) != float("inf") else False
    try:
        x = float(str(v).strip().replace(",", "."))
    except ValueError:
        return False
    return x if x == x and abs(x) != float("inf") else False


def _read_tab(ws, hid):
    """One course tab. hid: what the hidden sheet says about it, or None. Returns (info, columns, rows)."""
    title = _text(ws["A2"].value)
    info, ids = {}, []
    if hid:
        try:
            info = {"class": int(float(hid[0])), "subject": int(float(hid[1])), "term": int(float(hid[2]))}
            ids = [int(float(x)) for x in str(hid[3] or "").split(",") if x.strip()]
        except (TypeError, ValueError, IndexError):
            info, ids = {}, []
    if not info:
        # the hidden part is gone (some programs drop it): the course is found by the names in the title,
        # and the tests by their names in row 5
        parts = title[len("MARKS SHEET - "):].split(" - ")
        if len(parts) < 3:
            raise ValueError("The title in cell A2 was changed. It must stay \"MARKS SHEET - course - class - term\".")
        info = {"subject_name": " - ".join(parts[:-2]), "class_name": parts[-2], "term_name": parts[-1]}
    columns = []
    for col in range(FIRST_COL, min(ws.max_column, 200) + 1):
        letter = get_column_letter(col)
        name = _text(ws.cell(HEAD_ROW, col).value)[:40]
        out_of = ws.cell(MAX_ROW, col).value
        known = ids[col - FIRST_COL] if col - FIRST_COL < len(ids) else None
        if known is None and name == "" and (out_of is None or _text(out_of) == ""):
            # an unused column; but marks typed under no heading are a mistake
            if any(_text(ws.cell(r, col).value) != "" for r in range(FIRST_ROW, ws.max_row + 1)):     # (a lone space is no mark)
                raise ValueError(f"Column {letter} has marks but no test name in row {HEAD_ROW}. Type the name of the test "
                                 f"in {letter}{HEAD_ROW} and what it is marked out of in {letter}{MAX_ROW}.")
            continue
        columns.append((letter, name, out_of, known))
    rows = []
    for r in range(FIRST_ROW, ws.max_row + 1):
        code = _text(ws.cell(r, 2).value)
        values = {letter: ws.cell(r, column_index_from_string(letter)).value for letter, *_ in columns}
        if code == "":
            if any(_text(v) != "" for k, v in values.items() if k != "_name"):
                raise ValueError(f"Row {r} has marks but no student code in column B.")
            continue
        values["_name"] = ws.cell(r, 3).value
        rows.append((r, code, values))
    return info, columns, rows


def read_all(data, class_only=False):
    """Every course tab of an uploaded file (a course's sheet, or a class workbook).
    Returns [(tab name, info, columns, rows, error or None)]; raises ValueError when the file is not a marks file at all."""
    if len(data) > MAX_SIZE:
        raise ValueError("The file is too big for a marks sheet.")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise ValueError("The file could not be read. Upload the Excel file (.xlsx) that was downloaded from the system "
                         "(from Google Sheets: File > Download > Microsoft Excel (.xlsx)).")
    if class_only and not any(_text(ws["A2"].value).upper().startswith("SUMMARY - ") for ws in wb.worksheets):
        raise ValueError("This is not a class workbook. Only the class workbook is accepted (Marks sheets → Class workbook (Excel)): "
                         "one Excel file per class, with a Summary tab and a tab for every course.")
    hidden = next((wb[n] for n in wb.sheetnames if n.lstrip("_").lower() == "system"), None)
    by_tab = {}
    if hidden is not None:
        cells = [list(r) for r in hidden.iter_rows(min_row=1, max_row=400, max_col=6, values_only=True)]
        if cells and cells[0][:2] == ["format", MARK2]:
            for r in cells[1:]:
                if r[0]:
                    by_tab[str(r[0])] = r[1:5]
        elif cells and cells[0][:2] == ["format", MARK]:                  # a sheet made by the first version
            kv = {str(r[0]): r[1] for r in cells if r[0]}
            by_tab["Marks"] = [kv.get("class"), kv.get("subject"), kv.get("term"), kv.get("columns")]
    out = []
    for ws in wb.worksheets:
        if ws is hidden or ws.sheet_state != "visible" and ws.title not in by_tab:
            continue
        if not _text(ws["A2"].value).upper().startswith("MARKS SHEET - ") or _text(ws.cell(HEAD_ROW, 2).value).lower() != "student code":
            continue
        try:
            info, columns, rows = _read_tab(ws, by_tab.get(ws.title))
            out.append((ws.title, info, columns, rows, None))
        except ValueError as e:
            out.append((ws.title, {}, [], [], str(e)))
    out = _marks_typed_in_summary(data, wb, out)
    if not out:
        raise ValueError("This is not a marks sheet of this system (no course sheet in it). Download the sheets from the "
                         "system, type the marks in them and upload that file.")
    return out


def _marks_typed_in_summary(data, wb, out):
    """Some teachers type their marks in the Summary tab instead of their course's tab. For a course whose own tab
    has no marks at all, numbers typed (not calculated) in its Summary column are taken as one test out of the
    course's maximum, named "Test 1" (the name of the course's first test, if it already has one)."""
    summary = next((ws for ws in wb.worksheets if _text(ws["A2"].value).upper().startswith("SUMMARY - ")), None)
    if summary is None:
        return out
    try:
        typed = load_workbook(io.BytesIO(data), data_only=False)[summary.title]      # to tell typed numbers from formulas
    except Exception:
        return out
    heads = {_text(summary.cell(HEAD_ROW, c).value): c for c in range(4, min(summary.max_column, 200) + 1)}
    result = []
    for tab, info, columns, rows, error in out:
        col = heads.get(tab)
        empty = not error and not any(any(_number(v.get(c[0])) is not None for v in [r[2] for r in rows]) for c in columns)
        if col is None or not empty:
            result.append((tab, info, columns, rows, error))
            continue
        letter = f"Summary!{get_column_letter(col)}"
        found = []
        for r in range(FIRST_ROW, summary.max_row + 1):
            code = _text(summary.cell(r, 2).value)
            raw = typed.cell(r, col).value
            if code == "" or raw is None or (isinstance(raw, str) and raw.startswith("=")) or not isinstance(typed.cell(r, col).value, (int, float, str)):
                continue
            if _number(summary.cell(r, col).value) is None:
                continue
            found.append((r, code, {letter: summary.cell(r, col).value, "_name": summary.cell(r, 3).value}))
        if not found:
            result.append((tab, info, columns, rows, error))
            continue
        out_of = summary.cell(MAX_ROW, col).value
        result.append((tab, dict(info, from_summary=True), [(letter, "", out_of, None)], found, None))
    return result


def read(data):
    """The first course of a file: (info, columns, rows). Raises ValueError with a plain message."""
    tab, info, columns, rows, error = read_all(data)[0]
    if error:
        raise ValueError(error)
    return info, columns, rows


def plan(info, columns, rows, alloc, term, students, assessments, marks):
    """Check everything and work out what the import would do. Returns (errors, changes)
    changes = {"new": [(letter, name, kind, max)], "set": [(assessment key, student id, score)], "clear": [...], ...}"""
    errors = []
    if "class" in info and (info["class"] != alloc["class_id"] or info["subject"] != alloc["subject_id"]):
        return [f"This sheet is for another course, not for {alloc['subject_name']} in {alloc['class_name']}."], None
    if info.get("term", term["id"]) != term["id"] or info.get("term_name", term["name"]).strip().lower() != term["name"].lower():
        return [f"This sheet was made for another term. You are working in {term['name']}: switch to the term of the "
                "sheet first, or download a new sheet for this term."], None
    by_id = {a["id"]: a for a in assessments}
    by_code = {s["admission_no"].strip().upper(): s for s in students}
    has_exam = any(a["kind"] == "exam" for a in assessments)
    used = {}
    new, cols = [], []
    for letter, name, out_of, known in columns:
        if known is not None and name and known in by_id and by_id[known]["name"].strip().lower() != name.lower():
            known = None                # columns were moved or renamed in the sheet: go by the name
        if known is not None:
            a = by_id.get(known)
            if a is None:
                errors.append(f"Column {letter} ({name or 'no name'}) is a test that was deleted in the system. Download a new sheet.")
                continue
            m = _number(out_of)
            if m is not None and m is not False and abs(m - a["max_mark"]) > 1e-9:
                errors.append(f"Column {letter}: {a['name']} is marked out of {_num(a['max_mark'])} in the system, but the sheet says "
                              f"{_text(out_of)}. Change what a test is marked out of in the system, not in the sheet.")
            cols.append((letter, ("old", a["id"]), a["name"], a["max_mark"]))
            continue
        same = next((a for a in assessments if a["name"].strip().lower() == name.lower()), None) if name else None
        if same is not None and same["id"] not in [c[1][1] for c in cols if c[1][0] == "old"]:
            m = _number(out_of)
            if m is not None and m is not False and abs(m - same["max_mark"]) > 1e-9:
                errors.append(f"Column {letter}: {same['name']} is marked out of {_num(same['max_mark'])} in the system, but the sheet says "
                              f"{_text(out_of)}. Change what a test is marked out of in the system, not in the sheet.")
            cols.append((letter, ("old", same["id"]), same["name"], same["max_mark"]))
            continue
        if name == "":
            # a maximum in row 6 but no name in row 5: with no marks under it, the column is simply not used;
            # with marks, it becomes the next test ("Test 1", "Test 2", ...)
            if not any(_number(values.get(letter)) is not None for _, _, values in rows):
                continue
            if info.get("from_summary"):
                first = next((a for a in assessments if a["kind"] == "test"), None)
                if first is not None and _number(out_of) not in (None, False) and abs(_number(out_of) - first["max_mark"]) < 1e-9:
                    cols.append((letter, ("old", first["id"]), first["name"], first["max_mark"]))
                    continue
            k = sum(1 for a in assessments if a["kind"] == "test") + sum(1 for c in new if c[2] == "test") + 1
            taken = {a["name"].strip().lower() for a in assessments} | set(used)
            while f"test {k}" in taken:
                k += 1
            name = f"Test {k}"
        m = _number(out_of)
        if m is None or m is False or m <= 0 or m > 1000:
            errors.append(f"Column {letter} ({name}): type what this test is marked out of in {letter}{MAX_ROW} (a number from 1 to 1000).")
            continue
        kind = "exam" if re.search(r"\bexam", name, re.I) else "test"
        if kind == "exam":
            if has_exam:
                errors.append(f"Column {letter} ({name}): this subject already has an exam for {term['name']}.")
                continue
            has_exam = True
        if name.lower() in used or any(name.lower() == a["name"].lower() for a in assessments):
            errors.append(f"Column {letter}: there is already a test named {name}.")
            continue
        used[name.lower()] = True
        key = ("new", len(new))
        new.append((letter, name, kind, m))
        cols.append((letter, key, name, m))

    seen = {}
    sets, clears = [], []
    unknown = []
    for r, code, values in rows:
        s = by_code.get(code.upper())
        if s is None:
            # a student who left the class (moved or removed in the system after the workbook was made):
            # the other students' marks still come in, and the row is listed for the admin
            if any(_number(values.get(c[0])) is not None for c in cols):
                unknown.append((r, code, _text(values.get("_name"))))
            continue
        if s["id"] in seen:
            errors.append(f"Row {r}: {s['full_name']} ({code}) is in the sheet twice (also row {seen[s['id']]}).")
            continue
        seen[s["id"]] = r
        for letter, key, name, mx in cols:
            v = _number(values.get(letter))
            if v is False or (v is not None and not 0 <= v <= mx):
                errors.append(f"Cell {letter}{r} ({s['full_name']}, {name}): \"{_text(values.get(letter))}\" is not a mark from 0 to {_num(mx)}.")
                continue
            old = marks.get((key[1], s["id"])) if key[0] == "old" else None
            if v is None:
                if old is not None:
                    clears.append((key, s["id"]))
            elif old is None or abs(old - v) > 1e-9:
                sets.append((key, s["id"], v))
    missing = [s for s in students if s["id"] not in seen]
    return errors, {"new": new, "set": sets, "clear": clears, "missing": missing, "students": len(seen), "unknown": unknown}
