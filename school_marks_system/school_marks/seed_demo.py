"""Fill an EMPTY system with sample data so you can try it out.

    python seed_demo.py

It refuses to run if the system already has users. To start again with a clean
system afterwards, stop the program and delete the file data/school.db.
The sample school, teachers and students are invented.
"""
import os
import random

from werkzeug.security import generate_password_hash

import app as application
import db as database

PASSWORD = "school1234"
TEACHERS = ["Nomsa Zulu", "Peter Mokoena", "Aisha Patel", "David Nel"]
# class name: (level key, students)
CLASSES = {
    "S1 A": ("olevel", ["Thabo Nkosi,M", "Lerato Dlamini,F", "Sipho Khumalo,M", "Zanele Mthembu,F", "Kagiso Molefe,M", "Ayanda Ngcobo,F",
             "Bongani Sithole,M", "Naledi Mahlangu,F", "Tshepo Maseko,M", "Palesa Radebe,F"]),
    "S2 A": ("olevel", ["Mpho Tshabalala,M", "Nandi Cele,F", "Lwazi Zungu,M", "Refilwe Kgosi,F", "Sibusiso Ndlovu,M", "Thandiwe Mkhize,F",
             "Andile Gumede,M", "Karabo Phiri,F"]),
    "S4 MSC A": ("msc", ["Neo Mabuza,M", "Zinhle Shabalala,F", "Katlego Moloi,M", "Nomvula Zwane,F", "Lebo Sekgobela,M",
                         "Busisiwe Hadebe,F"]),
}


def main():
    con = database.connect(application.DB_PATH)
    if con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        raise SystemExit("The system already has accounts, so sample data was NOT added.")
    rnd = random.Random(7)
    now = database.now()
    ins_user = "INSERT INTO users (full_name, username, password_hash, role, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)"
    con.execute(ins_user, ("School Admin", "admin", generate_password_hash(PASSWORD), "admin", now))
    tids = []
    for name in TEACHERS:
        username = (name[0] + "." + name.split()[-1]).lower()
        tids.append(con.execute(ins_user, (name, username, generate_password_hash(PASSWORD), "teacher", now)).lastrowid)
    for key, value in {"school_name": "Sample Secondary School", "school_pobox": "123", "school_email": "info@sample-school.example",
                       "school_tel": "0700 000 000", "head_name": "Mr Sample HEAD", "head_title": "Headmaster"}.items():
        con.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    term = con.execute("INSERT INTO terms (name, year, is_current) VALUES ('2025-2026 TERM 1', '2025-2026', 1)").lastrowid
    for later in ("2025-2026 TERM 2", "2025-2026 TERM 3"):          # the rest of the academic year, still empty
        con.execute("INSERT INTO terms (name, year) VALUES (?, '2025-2026')", (later,))
    levels = {x["key"]: database.add_standard_list(con, x["key"])[0] for x in database.STANDARD_LISTS}   # already there on a new system
    n = 0
    for ci, (cname, (level_key, names)) in enumerate(CLASSES.items()):
        level_id = levels[level_key]
        subjects = con.execute("""SELECT * FROM subjects WHERE level_id = ?
                                  ORDER BY CASE category WHEN 'core' THEN 0 WHEN 'noncore' THEN 1 ELSE 2 END, sort_order""", (level_id,)).fetchall()
        cid = con.execute("INSERT INTO classes (name, class_teacher_id, level_id) VALUES (?, ?, ?)", (cname, tids[ci], level_id)).lastrowid
        students = []
        for entry in names:
            n += 1
            full, gender = entry.split(",")
            students.append(con.execute("INSERT INTO students (class_id, admission_no, full_name, gender) VALUES (?, ?, ?, ?)",
                                        (cid, f"S{n:04d}", full, "Male" if gender == "M" else "Female")).lastrowid)
        ability = {s: rnd.uniform(0.35, 0.92) for s in students}
        for si, sub in enumerate(subjects):
            teacher = tids[(si + ci) % len(tids)]
            con.execute("INSERT INTO allocations (class_id, subject_id, teacher_id) VALUES (?, ?, ?)", (cid, sub["id"], teacher))
            if cname == "S2 A" and si >= 12:
                continue                                     # leave three subjects unmarked to show "incomplete"
            plan = [("Test 1", "test", 30), ("Test 2", "test", 50), ("Assignment", "test", 20), ("Exam", "exam", 100)]
            for aname, kind, mx in plan:
                aid = con.execute("INSERT INTO assessments (class_id, subject_id, term_id, name, kind, max_mark) VALUES (?, ?, ?, ?, ?, ?)",
                                  (cid, sub["id"], term, aname, kind, mx)).lastrowid
                for s in students:
                    score = round(min(1, max(0.05, ability[s] + rnd.uniform(-0.15, 0.15))) * mx)
                    con.execute("INSERT INTO marks (assessment_id, student_id, score, updated_by, updated_at) VALUES (?, ?, ?, ?, ?)",
                                (aid, s, score, teacher, now))
        for s in students[:3]:                               # a few conduct marks below the maximum
            con.execute("INSERT INTO remarks (student_id, term_id, conduct, days_absent, remark) VALUES (?, ?, ?, '', '')",
                        (s, term, str(rnd.randint(34, 39))))
    if os.path.exists(application.LOGO_PATH):                # the sample school is invented, so it prints no real logo
        os.remove(application.LOGO_PATH)
    con.execute("INSERT INTO activity (user_id, user_name, action, details, created_at) VALUES (NULL, 'System', 'Sample data loaded', '', ?)", (now,))
    con.commit()
    con.close()
    print("Sample data added. Password for every sample account:", PASSWORD)
    print("  Admin:    admin")
    print("  Teachers: n.zulu (class teacher S1 A), p.mokoena (class teacher S2 A), a.patel (class teacher S4 MSC A), d.nel")


if __name__ == "__main__":
    main()
