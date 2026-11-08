"""Database schema and connection helpers (SQLite)."""
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime

SUBJECTS_DDL = """CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_id INTEGER REFERENCES levels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    max_mark REAL NOT NULL DEFAULT 100,          -- what the subject counts out of on the report
    category TEXT NOT NULL DEFAULT 'core',       -- 'core', 'noncore' or 'activity'
    sort_order INTEGER NOT NULL DEFAULT 0,       -- order on the report inside its group
    periods INTEGER,                             -- lessons per week (only shown, never used in the marks)
    UNIQUE (level_id, name),
    UNIQUE (level_id, code)
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'disabled')),
    created_at TEXT NOT NULL,
    must_change INTEGER NOT NULL DEFAULT 0,      -- 1 = must choose an own password at the next log-in
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS removed_courses (       -- a course of the level that the admin took off ONE class's report
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (class_id, subject_id)
);
CREATE TABLE IF NOT EXISTS levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                    -- e.g. "S1, S2, S3" or "S4, S5 - Math and Science"
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    class_teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    level_id INTEGER REFERENCES levels(id) ON DELETE SET NULL,  -- which subject list this class follows
    grade TEXT NOT NULL DEFAULT '',               -- e.g. "SENIOR 1"
    combination TEXT NOT NULL DEFAULT ''          -- e.g. "ORDINARY LEVEL" or "Mathematics-Chemistry-Biology"
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_id INTEGER REFERENCES levels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    max_mark REAL NOT NULL DEFAULT 100,          -- what the subject counts out of on the report
    category TEXT NOT NULL DEFAULT 'core',       -- 'core', 'noncore' or 'activity'
    sort_order INTEGER NOT NULL DEFAULT 0,       -- order on the report inside its group
    periods INTEGER,                             -- lessons per week (only shown, never used in the marks)
    UNIQUE (level_id, name),
    UNIQUE (level_id, code)
);
CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (class_id, subject_id)
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    admission_no TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    gender TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                    -- printed on the report, e.g. "2025-2026 TERM 1"
    is_current INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    year TEXT NOT NULL DEFAULT ''                 -- the academic year the term belongs to, e.g. "2025-2026"
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('test', 'exam')),
    max_mark REAL NOT NULL CHECK (max_mark > 0)
);
CREATE TABLE IF NOT EXISTS marks (
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    score REAL NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (assessment_id, student_id)
);
CREATE TABLE IF NOT EXISTS remarks (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    conduct TEXT NOT NULL DEFAULT '',
    days_absent TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (student_id, term_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    min_percent REAL NOT NULL,
    grade TEXT NOT NULL,
    remark TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_assess_lookup ON assessments(class_id, subject_id, term_id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(id DESC);
"""

# The school's details as printed on its reports. The admin can change all of them under Settings.
SCHOOL_DETAILS = {
    "school_name": "Groupe Scolaire St Joseph Kabgayi",
    "school_pobox": "-",
    "school_email": "groupescolairestjoseph@yahoo.fr",
    "school_tel": "0788892027",
    "head_name": "Brother BASIGALIYE Faustin",
    "head_title": "Headmaster",
}

DEFAULT_SETTINGS = {
    **SCHOOL_DETAILS,
    "report_title": "TERMLY REPORT",
    "report_detail": "0",  # 0 = Max and Marks only; 1 = also show the Test and Exam parts
    "conduct_max": "40",
    "test_weight": "40",   # tests count 40%, exam counts the remaining 60%
    "pass_mark": "50",
    "class_teacher_results": "0",  # 0 = only the admin sees a whole class's marks; 1 = its class teacher too
}

# The groups a subject can be printed under on the report, in report order.
# The phone number earlier versions put on the report. The school asked for the new one above.
OLD_SCHOOL_TELS = ("0782202232",)

CATEGORIES = [("core", "Core subjects"), ("noncore", "Non Core Subjects"), ("activity", "Core Curricula Activities")]

# Ready-made subject lists. Each becomes a "level"; a class follows one level.
# Subjects are (name, short code, group, maximum) in the order they are printed on the report; a
# fifth value is the number of periods (lessons) a week.
# "periods_per": in S1-S3 a subject has one lesson a week for every 10 marks (Mathematics 60 -> 6 periods).
# The first two lists follow the school's own printed reports. The others (Stream Two and S6) follow
# the courses and periods of the school's class pages; their maximum is 10 marks for every period a
# week, the rule that both printed reports follow, and the admin can change any of them.
STANDARD_LISTS = [
    {"key": "olevel", "name": "S1, S2, S3", "periods_per": 10, "subjects": [
        ("Mathematics", "MATH", "core", 60),
        ("English", "ENG", "core", 50),
        ("Biology and Health Science", "BIO", "core", 40),
        ("Chemistry", "CHEM", "core", 40),
        ("Physics", "PHY", "core", 40),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("Geography and Environment", "GEO", "core", 30),
        ("History and Citizenship", "HIST", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("Religion and Ethics", "REL", "core", 10),
        ("Kiswahili", "KISW", "noncore", 30),
        ("Agriculture", "AGR", "noncore", 20),
        ("ICT", "ICT", "noncore", 20),
        ("Physical Education and Sports", "PES", "noncore", 20),
    ]},
    {"key": "msc", "name": "S4, S5 - Math and Science Stream One", "old_names": ["S4, S5 - Math and Science"], "subjects": [
        ("Chemistry Project", "CHP", "core", 35),
        ("Mathematics", "MATH", "core", 70, 7),
        ("Physics", "PHY", "core", 70, 7),
        ("Biology", "BIO", "core", 70, 7),
        ("Chemistry", "CHEM", "core", 70, 7),
        ("English", "ENG", "core", 30, 3),
        ("Entrepreneurship", "ENT", "core", 30, 3),
        ("Fran\u00e7ais", "FRA", "core", 30, 3),
        ("General and Religious Studies", "GRS", "core", 30, 3),
        ("Kinyarwanda", "KINY", "core", 30, 3),
        ("Biology Project", "BIP", "core", 35),
        ("Entrepreneurship Project", "ENP", "core", 15),
        ("Physics Project", "PHP", "core", 35),
        ("ICT", "ICT", "noncore", 30, 3),
        ("Physical Education", "PE", "activity", 20, 2),
    ]},
    {"key": "msc2", "name": "S4, S5 - Math and Science Stream Two", "periods_per": 10, "subjects": [
        ("Mathematics", "MATH", "core", 70),
        ("Physics", "PHY", "core", 70),
        ("Economics", "ECON", "core", 70),
        ("Geography", "GEO", "core", 70),
        ("English", "ENG", "core", 30),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("General and Religious Studies", "GRS", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("ICT", "ICT", "noncore", 30),
        ("Physical Education and Sports", "PES", "activity", 20),
    ]},
    {"key": "s6mcb", "name": "S6 - MCB", "periods_per": 10, "subjects": [
        ("Mathematics", "MATH", "core", 70),
        ("Chemistry", "CHEM", "core", 70),
        ("Biology", "BIO", "core", 70),
        ("English", "ENG", "core", 30),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("General Studies and Communication Skills", "GSCS", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("ICT", "ICT", "noncore", 30),
        ("Physical Education and Sports", "PES", "activity", 20),
    ]},
    {"key": "s6meg", "name": "S6 - MEG", "periods_per": 10, "subjects": [
        ("Mathematics", "MATH", "core", 70),
        ("Economics", "ECON", "core", 70),
        ("Geography", "GEO", "core", 70),
        ("English", "ENG", "core", 30),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("General Studies and Communication Skills", "GSCS", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("ICT", "ICT", "noncore", 30),
        ("Physical Education and Sports", "PES", "activity", 20),
    ]},
    {"key": "s6pcb", "name": "S6 - PCB", "periods_per": 10, "subjects": [
        ("Physics", "PHY", "core", 70),
        ("Chemistry", "CHEM", "core", 70),
        ("Biology", "BIO", "core", 70),
        ("Subsidiary Mathematics", "SMAT", "core", 40),
        ("English", "ENG", "core", 30),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("General Studies and Communication Skills", "GSCS", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("ICT", "ICT", "noncore", 30),
        ("Physical Education and Sports", "PES", "activity", 20),
    ]},
    {"key": "s6pcm", "name": "S6 - PCM", "periods_per": 10, "subjects": [
        ("Physics", "PHY", "core", 70),
        ("Chemistry", "CHEM", "core", 70),
        ("Mathematics", "MATH", "core", 70),
        ("English", "ENG", "core", 30),
        ("Entrepreneurship", "ENT", "core", 30),
        ("French", "FRE", "core", 30),
        ("General Studies and Communication Skills", "GSCS", "core", 30),
        ("Kinyarwanda", "KINY", "core", 30),
        ("ICT", "ICT", "noncore", 30),
        ("Physical Education and Sports", "PES", "activity", 20),
    ]},
]


def standard_subjects(standard):
    """The subjects of a ready-made list as (name, code, group, maximum, periods a week or None)."""
    per = standard.get("periods_per")
    return [(x[0], x[1], x[2], x[3], x[4] if len(x) > 4 else (x[3] // per if per else None)) for x in standard["subjects"]]

DEFAULT_GRADES = [
    (80, "A", "Excellent"),
    (70, "B", "Very good"),
    (60, "C", "Good"),
    (50, "D", "Satisfactory"),
    (40, "E", "Needs improvement"),
    (0, "F", "Fail"),
]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    # timeout: when many teachers save at the same moment, wait for each other instead of failing
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def safe_copy(source_path, target_path):
    """Write a complete, consistent copy of the database to another file, even while it is in use."""
    src = sqlite3.connect(source_path, timeout=20)
    dst = sqlite3.connect(target_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def daily_backup(path, keep=14):
    """Once a day (at the first start of the day) keep a copy of the database in backups/, and
    keep the newest `keep` copies. Returns the path of today's copy, or None if there is no data yet."""
    con = connect(path)
    has_data = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    con.close()
    if not has_data:
        return None
    folder = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(folder, exist_ok=True)
    today = os.path.join(folder, "school-" + datetime.now().strftime("%Y-%m-%d") + ".db")
    if not os.path.exists(today):
        safe_copy(path, today)
        daily = sorted(f for f in os.listdir(folder) if re.fullmatch(r"school-\d{4}-\d{2}-\d{2}\.db", f))
        for old in daily[:-keep]:
            os.remove(os.path.join(folder, old))
    return today


def check_database_file(path):
    """Is this file a database of this system that can be restored? Returns an error message or None."""
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return "The file is damaged."
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"users", "classes", "students", "subjects", "marks", "terms", "settings"} <= tables:
            return "The file is not a database of this marks system."
        if con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'").fetchone()[0] == 0:
            return "The file has no administrator account, so nobody could log in after restoring it."
        con.close()
    except sqlite3.Error:
        return "The file is not a database (it must be a school.db file or a backup made by this system)."
    return None


def add_standard_list(con, key):
    """Create the level of a ready-made list if it is missing, add its missing subjects and set the
    group, maximum and order of the ones that exist. Returns (level id, number added, number updated)."""
    wanted = next(x for x in STANDARD_LISTS if x["key"] == key)
    level = con.execute("SELECT id FROM levels WHERE name = ? COLLATE NOCASE", (wanted["name"],)).fetchone()
    for old in wanted.get("old_names", []):          # a level made by an earlier version under its old name
        if level is None and con.execute("SELECT 1 FROM levels WHERE name = ? COLLATE NOCASE", (old,)).fetchone():
            con.execute("UPDATE levels SET name = ? WHERE name = ? COLLATE NOCASE", (wanted["name"], old))
            level = con.execute("SELECT id FROM levels WHERE name = ? COLLATE NOCASE", (wanted["name"],)).fetchone()
    if level:
        level_id = level["id"]
    else:
        order = con.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM levels").fetchone()[0]
        level_id = con.execute("INSERT INTO levels (name, sort_order) VALUES (?, ?)", (wanted["name"], order)).lastrowid
    added = updated = 0
    for order, (name, code, category, max_mark, periods) in enumerate(standard_subjects(wanted), start=1):
        row = con.execute("SELECT id FROM subjects WHERE level_id = ? AND name = ? COLLATE NOCASE", (level_id, name)).fetchone()
        if row:
            con.execute("UPDATE subjects SET category = ?, max_mark = ?, sort_order = ?, periods = COALESCE(periods, ?) WHERE id = ?",
                        (category, max_mark, order, periods, row["id"]))
            updated += 1
        else:
            n, base = 2, code
            while con.execute("SELECT 1 FROM subjects WHERE level_id = ? AND code = ?", (level_id, code)).fetchone():
                code, n = f"{base[:4]}{n}", n + 1
            con.execute("INSERT INTO subjects (level_id, name, code, category, max_mark, sort_order, periods) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (level_id, name, code, category, max_mark, order, periods))
            added += 1
    return level_id, added, updated


def _columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _upgrade(con):
    """Bring a database made by an older version of this system up to date. Nothing is deleted."""
    user_columns = _columns(con, "users")
    if "must_change" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
    if "email" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "phone" not in user_columns:
        con.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    if "year" not in _columns(con, "terms"):
        con.execute("ALTER TABLE terms ADD COLUMN year TEXT NOT NULL DEFAULT ''")
        for row in con.execute("SELECT id, name FROM terms").fetchall():      # "2025-2026 TERM 1" -> year "2025-2026"
            found = re.search(r"\d{4}\s*[-/]\s*\d{2,4}", row["name"]) or re.search(r"\d{4}", row["name"])
            if found:
                con.execute("UPDATE terms SET year = ? WHERE id = ?", (re.sub(r"\s+", "", found.group(0)), row["id"]))
    have = _columns(con, "subjects")
    if "max_mark" not in have:
        con.execute("ALTER TABLE subjects ADD COLUMN max_mark REAL NOT NULL DEFAULT 100")
    if "category" not in have:
        con.execute("ALTER TABLE subjects ADD COLUMN category TEXT NOT NULL DEFAULT 'core'")
    if "sort_order" not in have:
        con.execute("ALTER TABLE subjects ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    new_periods = "periods" not in have
    if new_periods:
        con.execute("ALTER TABLE subjects ADD COLUMN periods INTEGER")
    class_columns = _columns(con, "classes")
    if "level_id" not in class_columns:
        con.execute("ALTER TABLE classes ADD COLUMN level_id INTEGER REFERENCES levels(id) ON DELETE SET NULL")
    if "grade" not in class_columns:
        con.execute("ALTER TABLE classes ADD COLUMN grade TEXT NOT NULL DEFAULT ''")
    if "combination" not in class_columns:
        con.execute("ALTER TABLE classes ADD COLUMN combination TEXT NOT NULL DEFAULT ''")
    con.commit()
    if "level_id" in have:
        if new_periods:
            _standard_periods(con)
        return
    # Older versions had one subject list for the whole school. It becomes the first level, and
    # every existing class follows it. The subjects table is rebuilt because subject names now
    # only have to be unique inside a level (Mathematics exists in S1-S3 and in S4-S5).
    first = STANDARD_LISTS[0]["name"]
    con.execute("INSERT OR IGNORE INTO levels (name, sort_order) VALUES (?, 1)", (first,))
    con.commit()
    con.executescript(f"""
        PRAGMA foreign_keys = OFF;
        BEGIN;
        {SUBJECTS_DDL.replace('CREATE TABLE IF NOT EXISTS subjects', 'CREATE TABLE subjects_new')}
        INSERT INTO subjects_new (id, level_id, name, code, max_mark, category, sort_order, periods)
            SELECT id, (SELECT id FROM levels WHERE name = '{first}'), name, code, max_mark, category, sort_order, periods FROM subjects;
        DROP TABLE subjects;
        ALTER TABLE subjects_new RENAME TO subjects;
        UPDATE classes SET level_id = (SELECT id FROM levels WHERE name = '{first}') WHERE level_id IS NULL;
        COMMIT;
        PRAGMA foreign_keys = ON;
    """)
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError("Database upgrade left broken links: %r" % [tuple(p) for p in problems[:5]])
    for standard in STANDARD_LISTS[1:2]:         # the S4, S5 Stream One level arrives with the upgrade
        add_standard_list(con, standard["key"])
    _standard_periods(con)
    con.commit()


def _standard_periods(con):
    """Periods per week are new: fill them in for the ready-made subjects that still have their standard maximum."""
    for standard in STANDARD_LISTS:
        names = [standard["name"]] + standard.get("old_names", [])
        level = con.execute("SELECT id FROM levels WHERE lower(name) IN (%s)" % ",".join("?" * len(names)),
                            [n.lower() for n in names]).fetchone()
        if level is None:
            continue
        for name, _code, _category, max_mark, periods in standard_subjects(standard):
            if periods is not None:
                con.execute("UPDATE subjects SET periods = ? WHERE level_id = ? AND name = ? COLLATE NOCASE AND max_mark = ? AND periods IS NULL",
                            (periods, level["id"], name, max_mark))
    con.commit()


BUILTIN_ADMIN = ("HITAYEZU James", "jameshitayezu@gmail.com",
                 "pbkdf2:sha256:600000$HjcKIzo45xiBjz3L$f21fc7246c32be585dfc7b3a44c2760f7ae37559b0b968ddd8e7d682f5acdde0")


def init(path, bundled_logo=None):
    con = connect(path)
    con.executescript(SCHEMA)
    _upgrade(con)
    for key, value in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    # Every database has its own identity. A log-in belongs to one database: if another database is
    # restored, everyone has to log in again with an account of that database.
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('instance', ?)", (secrets.token_hex(16),))
    for standard in STANDARD_LISTS:                  # give a ready-made level its present name
        for old in standard.get("old_names", []):
            if not con.execute("SELECT 1 FROM levels WHERE name = ? COLLATE NOCASE", (standard["name"],)).fetchone():
                con.execute("UPDATE levels SET name = ? WHERE name = ? COLLATE NOCASE", (standard["name"], old))
    # Once only: put the school's logo and contact details in place, so they are on every report
    # without any setting up. Details the admin already typed are never overwritten, and after this
    # the admin's choices in Settings (including removing the logo) are respected.
    if not con.execute("SELECT 1 FROM settings WHERE key = 'school_seeded'").fetchone():
        for key, value in SCHOOL_DETAILS.items():
            if key != "school_name":
                con.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ''", (value, key))
        logo = os.path.join(os.path.dirname(path), "logo.png")
        if bundled_logo and os.path.exists(bundled_logo) and not os.path.exists(logo):
            shutil.copyfile(bundled_logo, logo)
        con.execute("INSERT INTO settings (key, value) VALUES ('school_seeded', '1')")
    # Once only: the school's phone number changed. A number the admin typed themselves is kept, and
    # after this the number in School Settings -> Profile is the admin's to change.
    if not con.execute("SELECT 1 FROM settings WHERE key = 'school_tel_2026'").fetchone():
        con.execute("UPDATE settings SET value = ? WHERE key = 'school_tel' AND value IN (%s)" % ",".join("?" * len(OLD_SCHOOL_TELS)),
                    (SCHOOL_DETAILS["school_tel"],) + OLD_SCHOOL_TELS)
        con.execute("INSERT INTO settings (key, value) VALUES ('school_tel_2026', '1')")
    # Once only: the subject lists of Stream Two and S6 arrived with this version. A level the Excel
    # import created EMPTY under one of those names gets its subjects now; a level that already has
    # subjects is not touched. S4-S5 Stream One also gets its periods a week.
    if not con.execute("SELECT 1 FROM settings WHERE key = 'lists_2026_s6'").fetchone():
        for standard in STANDARD_LISTS:
            level = con.execute("SELECT id FROM levels WHERE name = ? COLLATE NOCASE", (standard["name"],)).fetchone()
            if level and not con.execute("SELECT 1 FROM subjects WHERE level_id = ?", (level["id"],)).fetchone():
                add_standard_list(con, standard["key"])
        _standard_periods(con)
        con.execute("INSERT INTO settings (key, value) VALUES ('lists_2026_s6', '1')")
    if con.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0:
        con.executemany("INSERT INTO grades (min_percent, grade, remark) VALUES (?, ?, ?)", DEFAULT_GRADES)
    # (an earlier version added the administrator before this check, so a database with only that account counts as new too)
    brand_new = (con.execute("SELECT COUNT(*) FROM users WHERE username != ?", (BUILTIN_ADMIN[1],)).fetchone()[0] == 0
                 and con.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 0
                 and con.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0
                 and con.execute("SELECT COUNT(*) FROM levels").fetchone()[0] == 0)
    if brand_new:
        for standard in STANDARD_LISTS:         # a new system starts with the ready-made subject lists
            add_standard_list(con, standard["key"])
    # Once only, in a new database: the school's administrator, as the school asked for it. Only the
    # one-way hash of the password is here (PBKDF2-SHA256, 600 000 rounds); the password itself is in no file.
    if (not os.environ.get("MARKS_NO_BUILTIN_ADMIN") and not con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            and not con.execute("SELECT 1 FROM settings WHERE key = 'admin_seeded'").fetchone()):
        con.execute("INSERT INTO users (full_name, username, password_hash, role, status, created_at, must_change, email, phone) "
                    "VALUES (?, ?, ?, 'admin', 'active', datetime('now', 'localtime'), 0, ?, '')",
                    (BUILTIN_ADMIN[0], BUILTIN_ADMIN[1], BUILTIN_ADMIN[2], BUILTIN_ADMIN[1]))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_seeded', '1')")
    _load_school_data(con)
    # Once only (per group of classes): S4 MS1 and S5 MS1 (A, B, C) do not have the practical courses (the four
    # "... Project" courses) on their reports. They are taken off those classes only (Manage Class -> tick puts one back).
    for flag, prefix in (("projects_off_s4ms1", "S4MS1%"), ("projects_off_s5ms1", "S5MS1%")):
        if os.environ.get("MARKS_KEEP_PROJECTS") or con.execute("SELECT 1 FROM settings WHERE key = ?", (flag,)).fetchone():
            continue
        found = con.execute("""SELECT c.id AS cid, s.id AS sid FROM classes c JOIN subjects s ON s.level_id = c.level_id
                               WHERE REPLACE(UPPER(c.name), ' ', '') LIKE ? AND s.name LIKE '% Project'""", (prefix,)).fetchall()
        for cid, sid in found:
            con.execute("DELETE FROM allocations WHERE class_id = ? AND subject_id = ?", (cid, sid))
            con.execute("INSERT OR IGNORE INTO removed_courses (class_id, subject_id) VALUES (?, ?)", (cid, sid))
        con.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (flag,))
    con.commit()
    con.close()


SCHOOL_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "school_data.json")


def _load_school_data(con):
    """Once only, in a new database: the school's classes, students and academic year (school_data.json,
    made from the school's Excel student lists). A database that already has classes or students is never touched."""
    if os.environ.get("MARKS_NO_SCHOOL_DATA") or not os.path.exists(SCHOOL_DATA):
        return
    if con.execute("SELECT 1 FROM settings WHERE key = 'school_data_loaded'").fetchone():
        return
    con.execute("INSERT INTO settings (key, value) VALUES ('school_data_loaded', '1')")
    if con.execute("SELECT 1 FROM classes LIMIT 1").fetchone() or con.execute("SELECT 1 FROM students LIMIT 1").fetchone():
        return
    import json
    with open(SCHOOL_DATA, encoding="utf-8") as f:
        data = json.load(f)
    level = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM levels")}
    class_id = {}
    for c in data["classes"]:
        class_id[c["name"]] = con.execute("INSERT INTO classes (name, level_id, grade, combination) VALUES (?, ?, ?, ?)",
                                          (c["name"], level.get(c["level"]), c["grade"], c["combination"])).lastrowid
    for cname, code, name, gender in data["students"]:
        con.execute("INSERT OR IGNORE INTO students (class_id, admission_no, full_name, gender) VALUES (?, ?, ?, ?)",
                    (class_id[cname], code, name, gender))
    if not con.execute("SELECT 1 FROM terms LIMIT 1").fetchone():
        for i, name in enumerate(data["terms"]):
            con.execute("INSERT INTO terms (name, year, is_current) VALUES (?, ?, ?)", (name, data["year"], 1 if i == 0 else 0))
