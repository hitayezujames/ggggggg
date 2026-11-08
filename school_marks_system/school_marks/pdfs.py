"""PDF generation (ReportLab): termly report cards, class marksheet, subject marksheet.

The report card follows the school's own report layout: logo and school details on top, a boxed
title, the student's details, then one table  Subject | Max | Marks  with Conduct, Core subjects,
Non Core Subjects, TOTAL, PERCENTAGE and POSITION, and a signature box underneath.
"""
import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db import CATEGORIES

FONT, BOLD, ITALIC = "Times-Roman", "Times-Bold", "Times-Italic"
BLACK = colors.black
MAX_BG = colors.HexColor("#E9E7EC")      # the grey "Max" column of the school's report
MARK_BG = colors.HexColor("#F4EAF3")     # the pink "Marks" column
SIDE = 52                                 # left/right page margin of the report card, in points

LH_NAME = ParagraphStyle("lh_name", fontName=BOLD, fontSize=9.5, leading=13, alignment=TA_CENTER)
LH = ParagraphStyle("lh", fontName=FONT, fontSize=9, leading=11.5, alignment=TA_CENTER)
TITLE = ParagraphStyle("title", fontName=FONT, fontSize=12, leading=14, alignment=TA_CENTER)
CELL = ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=10)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName=BOLD)
NAME = ParagraphStyle("name", parent=CELL, fontSize=8, leading=9.5)          # student names in the marksheets
CELL_R = ParagraphStyle("cellr", parent=CELL_B, alignment=TA_RIGHT)
CELL_C = ParagraphStyle("cellc", parent=CELL_B, alignment=TA_CENTER)
INFO = ParagraphStyle("info", fontName=FONT, fontSize=9, leading=11)
INFO_B = ParagraphStyle("infob", parent=INFO, fontName=BOLD)
BOX = ParagraphStyle("box", fontName=FONT, fontSize=10, leading=12.5)
BOX_I = ParagraphStyle("boxi", parent=BOX, fontName=ITALIC)
HEAD_C = ParagraphStyle("headc", parent=BOX, alignment=TA_CENTER)
DATE_R = ParagraphStyle("date_r", parent=BOX, alignment=TA_RIGHT, rightIndent=10)
BODY = ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=12.5)
SMALL = ParagraphStyle("small", fontName=FONT, fontSize=8, leading=10, textColor=colors.HexColor("#333333"))
VHEAD = ParagraphStyle("vhead", fontName=BOLD, fontSize=7.5, leading=9, alignment=TA_CENTER)
VHEAD_S = ParagraphStyle("vhead_s", parent=VHEAD, fontSize=6.8, leading=8.5)      # narrow subject columns
SHEET_TITLE = ParagraphStyle("sheet_title", fontName=BOLD, fontSize=11.5, leading=15, alignment=TA_CENTER)
SHEET_SUB = ParagraphStyle("sheet_sub", fontName=FONT, fontSize=9.5, leading=12, alignment=TA_CENTER)


def pct(v):
    return "-" if v is None else f"{v:.1f}"


def num(v):
    """54.0 -> '54', 35.3 -> '35.3', None -> ''  (the way the school's report writes marks)."""
    if v is None:
        return ""
    v = round(float(v), 2)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def P(text, style=CELL):
    return Paragraph(escape(str(text if text is not None else "")), style)


def long_date(d=None):
    d = d or datetime.now()
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _logo(path, max_w, max_h):
    if not path or not os.path.exists(path):
        return ""
    try:
        w, h = ImageReader(path).getSize()
    except Exception:
        return ""
    scale = min(max_w / w, max_h / h)
    return Image(path, width=w * scale, height=h * scale)


def _letterhead(cfg, logo_path, width):
    """Logo on the left, school name and contact lines on the right - as on the school's report."""
    lines = [Paragraph(escape(cfg.get("school_name", "")).upper(), LH_NAME)]
    if cfg.get("school_pobox"):
        lines.append(Paragraph("P.O. Box: " + escape(cfg["school_pobox"]), LH))
    if cfg.get("school_email"):
        lines.append(Paragraph("Email: " + escape(cfg["school_email"]), LH))
    if cfg.get("school_tel"):
        lines.append(Paragraph("Tel : " + escape(cfg["school_tel"]), LH))
    logo = _logo(logo_path, 30 * mm, 21 * mm)
    if not logo:                                     # no logo uploaded: centre the school details on the page
        return Table([[lines]], colWidths=[width], style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)])
    t = Table([[logo, lines]], colWidths=[width * 0.36, width * 0.64])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "CENTER"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _title_bar(text, width):
    return Table([[Paragraph(escape(text), TITLE)]], colWidths=[width],
                 style=[("BOX", (0, 0), (-1, -1), 1.6, BLACK), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])


def _page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(colors.HexColor("#555555"))
    w, _ = doc.pagesize
    canvas.drawString(doc.leftMargin + 6, 9 * mm, "Printed " + datetime.now().strftime("%d %b %Y %H:%M"))
    canvas.drawRightString(w - doc.rightMargin - 6, 9 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _build(story, pagesize, title, side, footer=None, top=12 * mm):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, title=title, author="School Marks System",
                            leftMargin=side - 6, rightMargin=side - 6, topMargin=top, bottomMargin=15 * mm)   # the frame adds 6pt itself
    if footer:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story)
    buf.seek(0)
    return buf


# --------------------------------------------------------------------------- termly report cards
def _report_table(res, s, conduct, width):
    cfg = res["cfg"]
    detail = cfg["detail"]
    summ = res["summary"][s["id"]]
    ncol = 5 if detail else 3
    widths = [width * 0.44] + [width * 0.14] * 4 if detail else [width * 0.5, width * 0.25, width * 0.25]
    last = ncol - 1
    data, st = [], []

    def add(row, *styles):
        data.append(row + [""] * (ncol - len(row)))
        r = len(data) - 1
        for name, c0, c1, *rest in styles:
            st.append((name, (c0, r), (c1, r), *rest))
        return r

    # header (two rows): "Subject" | "Marks" over the mark columns
    add([P("Subject", CELL_C), P("Marks", CELL_C)], ("SPAN", 1, last))
    if detail:
        tw, ew = cfg["test_weight_num"], cfg["exam_weight_num"]
        add(["", "Max", f"Test ({tw:g}%)", f"Exam ({ew:g}%)", "Total"], ("BACKGROUND", 1, 1, MAX_BG), ("BACKGROUND", 2, last, MARK_BG))
    else:
        add(["", "Max", "Marks"], ("BACKGROUND", 1, 1, MAX_BG), ("BACKGROUND", 2, last, MARK_BG))
    st.append(("SPAN", (0, 0), (0, 1)))
    st.append(("VALIGN", (0, 0), (0, 1), "MIDDLE"))

    # conduct (shown, but not part of the total)
    if cfg["conduct_max_num"] > 0:
        row = [P("Conduct", CELL_R), num(cfg["conduct_max_num"])] + [""] * (ncol - 3) + [conduct]
        add(row, ("BACKGROUND", 1, 1, MAX_BG), ("BACKGROUND", 2, last, MARK_BG))

    known = {key for key, _ in CATEGORIES}
    for category, title in CATEGORIES:                       # Core subjects, Non Core Subjects, Core Curricula Activities
        subjects = [x for x in res["subjects"] if (x["category"] if x["category"] in known else "core") == category]
        if not subjects:
            continue
        add([P(title, CELL_B)], ("SPAN", 0, last))
        for sub in subjects:
            r = res["per_subject"][sub["id"]]["rows"][s["id"]]
            row = [P(sub["name"].upper()), num(sub["max_mark"])]
            row += [num(r["test_part"]), num(r["exam_part"]), num(r["mark"])] if detail else [num(r["mark"])]
            add(row, ("BACKGROUND", 1, 1, MAX_BG), ("BACKGROUND", 2, last, MARK_BG))
    add([""], ("SPAN", 0, last))

    row = [P("TOTAL", CELL_B), num(res["total_max"])]
    row += [num(summ["test_total"]), num(summ["exam_total"]), num(summ["total"])] if detail else [num(summ["total"])]
    add(row, ("FONTNAME", 0, last, BOLD))
    # written as on the school's own report: "79 %", "78.9 %" (a whole number has no ".0")
    percentage = (num(summ["percentage"]) + " %") if summ["percentage"] is not None else "%"
    add([P("PERCENTAGE", CELL_B), percentage], ("SPAN", 1, last), ("FONTNAME", 0, last, BOLD))
    position = f"{summ['position'] or ''} / {len(res['students'])}".strip()
    add([P("POSITION", CELL_B), position], ("SPAN", 1, last), ("FONTNAME", 0, last, BOLD))

    t = Table(data, colWidths=widths, repeatRows=2)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5), ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ] + st + [
        ("GRID", (0, 0), (-1, -1), 1, BLACK), ("BOX", (0, 0), (-1, -1), 1.6, BLACK),
    ]))
    return t


def report_cards(res, cls, term, student_ids, remarks, logo=None):
    cfg = res["cfg"]
    width = A4[0] - 2 * SIDE
    title = cfg.get("report_title") or "TERMLY REPORT"
    story = []
    chosen = [s for s in res["students"] if s["id"] in student_ids]
    for i, s in enumerate(chosen):
        if i:
            story.append(PageBreak())
        summ = res["summary"][s["id"]]
        rm = remarks.get(s["id"])
        story += [_letterhead(cfg, logo, width), Spacer(1, 3), _title_bar(title, width), Spacer(1, 4)]

        info = Table(
            [[P("STUDENT:", INFO), P(s["full_name"], INFO_B)],
             [P("REG. NUMBER:", INFO), P(s["admission_no"], INFO)],
             [P("CLASS:", INFO), P(cls["name"], INFO)],
             [P("ACADEMIC YEAR:", INFO), P(term["name"], INFO)]],
            colWidths=[width * 0.21, width * 0.79], hAlign="LEFT",
            style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                   ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")])
        story += [info, Spacer(1, 10)]

        story.append(_report_table(res, s, (rm["conduct"] if rm else "") or "", width))
        remark = (rm["remark"] if rm else "") or ""
        absent = (rm["days_absent"] if rm else "") or ""
        first = [Paragraph("Class Teacher's Remarks and Signature", BOX)]
        if remark:
            first.append(Paragraph(escape(remark), BOX_I))
        if absent:
            first.append(Paragraph("Days absent: " + escape(absent), SMALL))
        head = []
        if cfg.get("head_name"):
            head.append(Paragraph("<b>" + escape(cfg["head_name"]) + "</b>", HEAD_C))
        head.append(Paragraph(escape(cfg.get("head_title") or "Headmaster"), HEAD_C))
        head.append(Paragraph("Stamp and Signature.", HEAD_C))
        bw = width * 0.8
        box = Table([[first, ""], [Paragraph("Parent's Signature", BOX), ""], [head, Paragraph(long_date(), DATE_R)]],
                    colWidths=[bw * 0.42, bw * 0.58], hAlign="LEFT")
        box.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("SPAN", (0, 1), (1, 1)),
                                 ("BOX", (0, 0), (-1, -1), 1.6, BLACK), ("LINEBELOW", (0, 0), (-1, 1), 1, BLACK),
                                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 2), (1, 2), "RIGHT"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
        story += [Spacer(1, 4), box]
    if not chosen:
        story += [_letterhead(cfg, logo, width), _title_bar(title, width), Spacer(1, 10), Paragraph("No students found.", BODY)]
    return _build(story, A4, f"{title} - {cls['name']} - {term['name']}", SIDE, top=14 * mm)


# --------------------------------------------------------------------------- marksheets
def _sheet_grid():
    return [("GRID", (0, 0), (-1, -1), 0.6, BLACK), ("BOX", (0, 0), (-1, -1), 1.2, BLACK),
            ("FONTNAME", (0, 0), (-1, -1), FONT), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("FONTNAME", (0, 0), (-1, 0), BOLD), ("BACKGROUND", (0, 0), (-1, 0), MAX_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]


def _sheet_header(cfg, logo, width, heading, sub_heading):
    return [_letterhead(cfg, logo, width), Spacer(1, 3), _title_bar(heading, width), Spacer(1, 3),
            Paragraph(escape(sub_heading), SHEET_SUB), Spacer(1, 6)]


def class_marksheet(res, cls, term, logo=None):
    cfg = res["cfg"]
    page = landscape(A4)
    side = 12 * mm
    usable = page[0] - 2 * side
    story = _sheet_header(cfg, logo, usable, f"CLASS MARKSHEET - {cls['name']}",
                          f"{term['name']}   |   Class teacher: {cls['class_teacher_name'] or '-'}")
    subjects = res["subjects"]
    head = ["#", "Student"] + [Paragraph(f"{escape(s['code'])}<br/>/{num(s['max_mark'])}", VHEAD_S) for s in subjects]
    head += [Paragraph(f"Total<br/>/{num(res['total_max'])}", VHEAD), "%", "Pos"]
    data = [head]
    order = sorted(res["students"], key=lambda s: (res["summary"][s["id"]]["position"] or 10 ** 6, s["full_name"].lower()))
    for i, s in enumerate(order, start=1):
        summ = res["summary"][s["id"]]
        row = [i, P(s["full_name"], NAME)]
        row += [num(res["per_subject"][sub["id"]]["rows"][s["id"]]["mark"]) or "-" for sub in subjects]
        row += [num(summ["total"]) or "-", pct(summ["percentage"]), summ["position"] or "-"]
        data.append(row)
    avg_row = ["", P("Class average", CELL_B)]
    for sub in subjects:
        vals = [r["mark"] for r in res["per_subject"][sub["id"]]["rows"].values() if r["mark"] is not None]
        avg_row.append(pct(sum(vals) / len(vals)) if vals else "-")
    totals = [v["total"] for v in res["summary"].values() if v["total"] is not None]
    pcts = [v["exact"] for v in res["summary"].values() if v["exact"] is not None]
    avg_row += [pct(sum(totals) / len(totals)) if totals else "-", pct(sum(pcts) / len(pcts)) if pcts else "-", ""]
    data.append(avg_row)

    fixed = 8 * mm + 60 * mm + 16 * mm + 13 * mm + 10 * mm
    sub_w = max(9 * mm, min(20 * mm, (usable - fixed) / max(1, len(subjects))))
    t = Table(data, colWidths=[8 * mm, 60 * mm] + [sub_w] * len(subjects) + [16 * mm, 13 * mm, 10 * mm], repeatRows=1)
    n = len(subjects)
    t.setStyle(TableStyle(_sheet_grid() + [("ALIGN", (2, 0), (-1, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "CENTER"),
                                           ("FONTSIZE", (0, 1), (-1, -1), 8), ("FONTNAME", (0, -1), (-1, -1), BOLD),
                                           ("TOPPADDING", (0, 1), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 1), (-1, -1), 2.5),
                                           ("FONTNAME", (n + 2, 1), (n + 2, -1), BOLD),
                                           ("LEFTPADDING", (2, 0), (n + 1, -1), 1), ("RIGHTPADDING", (2, 0), (n + 1, -1), 1),
                                           ("BACKGROUND", (0, -1), (-1, -1), MAX_BG)]))
    story += [t, Spacer(1, 5)]
    key = " &nbsp;&bull;&nbsp; ".join(f"<b>{escape(s['code'])}</b> {escape(s['name'])}" for s in subjects)
    if key:
        story.append(Paragraph("Subjects: " + key, SMALL))
    story.append(Paragraph(f"Each subject is shown out of its own maximum. Tests count {cfg['test_weight_num']:g}% and the exam "
                           f"{cfg['exam_weight_num']:g}% of a subject's mark. '-' means the subject has no mark yet.", SMALL))
    return _build(story, page, f"Class marksheet - {cls['name']} - {term['name']}", side, _page_number, top=9 * mm)


def subject_marksheet(sr, students, cfg, cls, subject, teacher_name, term, logo=None):
    tests, exam = sr["tests"], sr["exam"]
    n_marks = len(tests) + (1 if exam else 0)
    side = 14 * mm
    fixed = 8 * mm + 58 * mm + 24 * mm + 4 * 13 * mm + 10 * mm
    page = A4
    if (page[0] - 2 * side - fixed) / max(1, n_marks) < 19 * mm:      # test names would wrap badly: turn the page
        page = landscape(A4)
    usable = page[0] - 2 * side
    story = _sheet_header(cfg, logo, usable, f"SUBJECT MARKSHEET - {subject['name'].upper()}",
                          f"{cls['name']}   |   {term['name']}   |   Teacher: {teacher_name or '-'}")
    head = ["#", "Student", "Reg. no."]
    head += [Paragraph(f"{escape(t['name'])}<br/>/{num(t['max_mark'])}", VHEAD) for t in tests]
    head += [Paragraph("Tests<br/>%", VHEAD)]
    if exam:
        head.append(Paragraph(f"{escape(exam['name'])}<br/>/{num(exam['max_mark'])}", VHEAD))
    head += [Paragraph("Exam<br/>%", VHEAD), Paragraph("Final<br/>%", VHEAD),
             Paragraph(f"Mark<br/>/{num(sr['max_mark'])}", VHEAD), "Pos"]
    data = [head]
    for i, s in enumerate(students, start=1):
        r = sr["rows"][s["id"]]
        row = [i, P(s["full_name"], NAME), s["admission_no"]]
        row += [num(sr["marks"].get((t["id"], s["id"]))) for t in tests]
        row.append(pct(r["tests_pct"]))
        if exam:
            row.append(num(sr["marks"].get((exam["id"], s["id"]))))
        row += [pct(r["exam_pct"]), pct(r["final"]), num(r["mark"]) or "-", r["position"] or "-"]
        data.append(row)

    mark_w = max(10 * mm, min(24 * mm, (usable - fixed) / max(1, n_marks)))
    widths = [8 * mm, 58 * mm, 24 * mm] + [mark_w] * len(tests) + [13 * mm] + ([mark_w] if exam else []) + [13 * mm, 13 * mm, 13 * mm, 10 * mm]
    t = Table(data, colWidths=widths, repeatRows=1)
    mark_col = len(widths) - 2
    t.setStyle(TableStyle(_sheet_grid() + [("ALIGN", (2, 0), (-1, -1), "CENTER"), ("ALIGN", (0, 0), (0, -1), "CENTER"),
                                           ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                                           ("FONTNAME", (mark_col, 1), (mark_col, -1), BOLD),
                                           ("BACKGROUND", (mark_col, 1), (mark_col, -1), MARK_BG)]))
    story += [t, Spacer(1, 5)]
    finals = [r["final"] for r in sr["rows"].values() if r["final"] is not None]
    if finals:
        passed = sum(1 for f in finals if f >= cfg["pass_mark_num"])
        story.append(Paragraph(f"<b>Class average:</b> {sum(finals) / len(finals):.1f}% &nbsp;&bull;&nbsp; "
                               f"<b>Highest:</b> {max(finals):.1f}% &nbsp;&bull;&nbsp; <b>Lowest:</b> {min(finals):.1f}% "
                               f"&nbsp;&bull;&nbsp; <b>{cfg['pass_mark_num']:g}% or more:</b> {passed} of {len(finals)}", BODY))
    story.append(Paragraph(f"Final % = Tests % x {cfg['test_weight_num']:g}% + Exam % x {cfg['exam_weight_num']:g}%. "
                           f"Mark = Final % of {num(sr['max_mark'])}, the maximum for this subject on the report. "
                           "Blank marks are not counted.", SMALL))
    story += [Spacer(1, 10 * mm), Paragraph("Teacher's signature: ______________________ &nbsp;&nbsp;&nbsp; Date: ______________", BODY)]
    return _build(story, page, f"{subject['name']} - {cls['name']} - {term['name']}", side, _page_number, top=9 * mm)


# --------------------------------------------------------------------------- class list
def class_list(cfg, cls, students, term_name, logo=None):
    """The list of all students of one class, for the administrator."""
    side = 18 * mm
    usable = A4[0] - 2 * side
    story = [_letterhead(cfg, logo, usable), Spacer(1, 3), _title_bar("STUDENT LIST", usable), Spacer(1, 5)]
    info = Table(
        [[P("Grade:", INFO), P(cls.get("grade_shown") or "-", INFO_B), P("Class:", INFO), P(cls["name"], INFO_B)],
         [P("Combination:", INFO), P(cls.get("combination_shown") or "-", INFO_B), P("Academic year:", INFO), P(term_name or "-", INFO_B)],
         [P("Class teacher:", INFO), P(cls.get("class_teacher_name") or "-", INFO_B), P("Students:", INFO), P(len(students), INFO_B)]],
        colWidths=[usable * 0.16, usable * 0.42, usable * 0.17, usable * 0.25],
        style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
               ("VALIGN", (0, 0), (-1, -1), "TOP")])
    story += [info, Spacer(1, 8)]
    with_gender = any(s["gender"] for s in students)
    head = ["#", "Student Code", "Names"] + (["Gender"] if with_gender else []) + ["Signature / note"]
    data = [head]
    for i, s in enumerate(students, start=1):
        data.append([i, s["admission_no"], P(s["full_name"], CELL)] + ([s["gender"] or ""] if with_gender else []) + [""])
    widths = [10 * mm, 32 * mm, 0, 40 * mm] if not with_gender else [10 * mm, 32 * mm, 0, 18 * mm, 32 * mm]
    widths[2] = usable - sum(widths)
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(_sheet_grid() + [("ALIGN", (0, 0), (1, -1), "CENTER"), ("FONTSIZE", (0, 1), (-1, -1), 9),
                                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F5F8")])]))
    story.append(table if students else Paragraph("This class has no students yet.", BODY))
    return _build(story, A4, f"Student list - {cls['name']}", side, _page_number, top=10 * mm)
