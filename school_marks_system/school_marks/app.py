"""School Marks System - Flask application.

Run with:  python app.py     then open  http://localhost:5000
"""
import functools
import io
import os
import shutil
import socket
import time
from datetime import datetime
import re
import secrets
import sqlite3

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db as database
import importer
import mailer
import marks_excel
import pdfs
import results as R

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def choose_data_dir():
    """Where the database lives.

    It is kept in the folder  SchoolMarksData  in the user's home folder (for example
    C:\\Users\\James\\SchoolMarksData), NOT inside the program folder. So the program folder can be
    replaced by a newer version, moved or deleted, and the school's data stays where it is.
    A database found in the program's own data folder (older versions kept it there) is copied
    across the first time. Set MARKS_DATA_DIR to use another place."""
    chosen = os.environ.get("MARKS_DATA_DIR")
    if chosen:
        os.makedirs(chosen, exist_ok=True)
        return chosen
    local = os.path.join(BASE_DIR, "data")
    home = os.path.join(os.path.expanduser("~"), "SchoolMarksData")
    try:
        os.makedirs(home, exist_ok=True)
        probe = os.path.join(home, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError:
        os.makedirs(local, exist_ok=True)              # the home folder cannot be written: stay in the program folder
        return local
    if not os.path.exists(os.path.join(home, "school.db")) and os.path.exists(os.path.join(local, "school.db")):
        database.safe_copy(os.path.join(local, "school.db"), os.path.join(home, "school.db"))
        for name in ("secret_key", "logo.png"):
            if os.path.exists(os.path.join(local, name)):
                shutil.copy2(os.path.join(local, name), os.path.join(home, name))
        with open(os.path.join(local, "MOVED.txt"), "w") as f:
            f.write("The database was copied to " + home + " and is used from there now.\n"
                    "The school.db in this folder is an old copy and is no longer updated.\n")
    return home


DATA_DIR = choose_data_dir()
DB_PATH = os.path.join(DATA_DIR, "school.db")

app = Flask(__name__)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=64 * 1024 * 1024)


def _secret_key():
    path = os.path.join(DATA_DIR, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.secret_key = _secret_key()
BUNDLED_LOGO = os.path.join(BASE_DIR, "static", "school_logo.png")
database.init(DB_PATH, bundled_logo=BUNDLED_LOGO)
try:
    database.daily_backup(DB_PATH)                 # one automatic copy per day in SchoolMarksData/backups
except Exception:                                  # a failed backup must never stop the school from working
    pass

LOGO_PATH = os.path.join(DATA_DIR, "logo.png")
IMPORT_DIR = os.path.join(DATA_DIR, "imports")          # uploaded student lists wait here between preview and import
# A username is a short name (j.mokoena) or an email address (name@example.com).
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._+@-]{2,59}$")
USERNAME_HELP = "Username must be 3-60 characters: small letters, numbers, dot, dash or underscore, or an email address."
ADMIN_USERNAME = "jameshitayezu@gmail.com"      # suggested on the first-time setup page; the admin chooses the password there
MIN_PASSWORD = 8


# --------------------------------------------------------------------------- helpers
def hash_password(password):
    """Passwords are never stored. Only a salted one-way hash is kept in the database, so nobody -
    not the admin, and not someone who copies the database file - can read a password back.
    scrypt is used; pbkdf2-sha256 with 600 000 rounds if this Python was built without scrypt."""
    try:
        return generate_password_hash(password, method="scrypt")
    except (AttributeError, ValueError):
        return generate_password_hash(password, method="pbkdf2:sha256:600000")


def get_db():
    if "db" not in g:
        g.db = database.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def instance_id():
    row = get_db().execute("SELECT value FROM settings WHERE key = 'instance'").fetchone()
    return row["value"] if row else ""


def start_session(user):
    """Log this browser in as `user`. One browser is one person at a time."""
    session.clear()
    session["user_id"] = user["id"]
    session["inst"] = instance_id()


def lan_address():
    """This computer's address on the school network (nothing is sent anywhere)."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return None


def site_url():
    """The address staff should open, for the emails. The admin can fix it under Settings -> Email."""
    fixed = one("SELECT value FROM settings WHERE key = 'site_url'")
    if fixed and fixed["value"]:
        return fixed["value"]
    host, _, port = request.host.partition(":")
    if host in ("localhost", "127.0.0.1"):                     # the admin is on the server itself: staff need its network address
        return f"http://{lan_address() or host}" + (f":{port}" if port else "") + "/"
    return request.host_url


def notify(person, subject, lines):
    """Email a staff member about something the admin did for them. Quietly does nothing when
    email is switched off or the person has no email address. Never contains a password."""
    cfg = mailer.config(get_db())
    to = (person["email"] if person else "") or ""
    if not cfg["on"] or not to:
        return False
    school = one("SELECT value FROM settings WHERE key = 'school_name'")["value"]
    body = (f"Dear {person['full_name']},\n\n" + "\n".join(lines)
            + f"\n\nOpen the marks system here: {site_url()}\n\n{school}\n"
              "(This message was sent automatically by the school's marks system. It never contains a password.)\n")

    def done(error):
        con = database.connect(DB_PATH)
        con.execute("INSERT INTO activity (user_id, user_name, action, details, created_at) VALUES (NULL, 'System', ?, ?, ?)",
                    ("Email NOT sent" if error else "Email sent", f"to {to}: {subject}" + (f" - {error}" if error else ""), database.now()))
        con.commit()
        con.close()
    mailer.send_later(cfg, to, f"{school}: {subject}", body, school, done)
    return True


def notify_subjects(teacher_id, items):
    """Tell a teacher which subjects they were just given: items = [(subject name, class name, students)]."""
    teacher = one("SELECT * FROM users WHERE id = ?", (teacher_id,))
    if teacher is None or not items:
        return
    notify(teacher, "You have been given " + ("a subject" if len(items) == 1 else f"{len(items)} subjects"),
           ["The administrator has given you:"] + [f"  - {sub} in {cls} ({n} students)" for sub, cls, n in items]
           + ["", "Log in to see the students of each class and to enter their marks."])


def class_size(class_id):
    return one("SELECT COUNT(*) FROM students WHERE class_id = ?", (class_id,))[0]


def log(action, details="", user=None):
    user = user or g.get("user")
    get_db().execute(
        "INSERT INTO activity (user_id, user_name, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"] if user else None, user["full_name"] if user else "System", action, details, database.now()),
    )


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def before_request():
    g.user = None
    if request.endpoint == "static":
        return
    db = get_db()
    uid = session.get("user_id")
    if uid:
        if session.get("inst") == instance_id():
            g.user = db.execute("SELECT * FROM users WHERE id = ? AND status = 'active'", (uid,)).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "This page was opened before the last log-in or log-out on this browser, so it could not be saved. "
                       "Nothing was changed. Go back to the start and try again.")
    has_admin = db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if not has_admin and request.endpoint != "setup":
        return redirect(url_for("setup"))
    # An account made by the admin, or one whose password the admin reset, must choose its own
    # password before it can do anything else.
    if g.user is not None and g.user["must_change"] and request.endpoint not in ("account", "logout", "whoami"):
        return redirect(url_for("account"))


@app.context_processor
def inject():
    db = get_db()
    school = db.execute("SELECT value FROM settings WHERE key = 'school_name'").fetchone()
    pending = 0
    if g.user and g.user["role"] == "admin":
        pending = db.execute("SELECT COUNT(*) FROM users WHERE status = 'pending'").fetchone()[0]
    terms_info = {}
    if g.user:
        terms_info = {"nav_terms": all_terms(), "active_term": pick_term(), "school_term": school_term()}
    return {"user": g.user, "csrf_token": csrf_token, "school_name": school["value"] if school else "School",
            "pending_count": pending, "categories": database.CATEGORIES, "ct_results": class_teachers_see_results(), **terms_info}


@app.template_filter("pct")
def pct_filter(v):
    return "–" if v is None else f"{v:.1f}"


@app.template_filter("num")
def num_filter(v):
    return pdfs.num(v)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def is_admin():
    return g.user["role"] == "admin"


def one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def rows(sql, args=()):
    return get_db().execute(sql, args).fetchall()


TERM_ORDER = "year DESC, name COLLATE NOCASE"


def all_terms():
    return rows(f"SELECT * FROM terms ORDER BY {TERM_ORDER}")


def school_term():
    """The term the admin has made current for the whole school."""
    return one("SELECT * FROM terms ORDER BY is_current DESC, id DESC LIMIT 1")


def pick_term():
    """The academic year / term this person is working in.

    A term given in the address or form (?term=ID) wins; otherwise the one the person switched to
    (remembered until they log out); otherwise the school's current term."""
    for tid in (request.values.get("term", type=int), session.get("term_id")):
        term = one("SELECT * FROM terms WHERE id = ?", (tid,)) if tid else None
        if term is not None:
            return term
    return school_term()


def get_class(class_id):
    cls = one("""SELECT c.*, u.full_name AS class_teacher_name, l.name AS level_name FROM classes c
                 LEFT JOIN users u ON u.id = c.class_teacher_id LEFT JOIN levels l ON l.id = c.level_id
                 WHERE c.id = ?""", (class_id,))
    if cls is None:
        abort(404)
    return cls


def get_allocation(alloc_id):
    alloc = one("""SELECT a.*, c.name AS class_name, s.name AS subject_name, s.code AS subject_code,
                          s.max_mark AS subject_max, u.full_name AS teacher_name
                   FROM allocations a JOIN classes c ON c.id = a.class_id JOIN subjects s ON s.id = a.subject_id
                   JOIN users u ON u.id = a.teacher_id WHERE a.id = ?""", (alloc_id,))
    if alloc is None:
        abort(404)
    if not is_admin() and alloc["teacher_id"] != g.user["id"]:
        abort(403)
    return alloc


def require_class_access(cls):
    """The class itself (its students, conduct and remarks): the admin and the class teacher of this class."""
    if not is_admin() and cls["class_teacher_id"] != g.user["id"]:
        abort(403)


# ---- who may see marks -------------------------------------------------------------------------
# * The administrator sees and changes everything.
# * A teacher sees and changes only the marks of the subjects the admin gave them (get_allocation).
# * The class teacher manages the students, conduct and remarks of their own class. The marks the
#   OTHER teachers entered for that class - and everything worked out from them: totals, percentages,
#   positions, the class marksheet and the report cards - are for the administrator only, unless the
#   admin has ticked "class teachers may see the results of their own class" in the school profile.
def class_teachers_see_results():
    row = one("SELECT value FROM settings WHERE key = 'class_teacher_results'")
    return row is not None and row["value"] == "1"


def can_see_results(cls):
    if is_admin():
        return True
    return cls["class_teacher_id"] == g.user["id"] and class_teachers_see_results()


def require_results_access(cls):
    if not can_see_results(cls):
        abort(403, "The marks of a whole class, its marksheet and its report cards are for the administrator. "
                   "You can open the subjects you teach, and the students, conduct and remarks of your own class.")


def class_teacher_can():
    """One sentence for emails and hints: what a class teacher may do, following the school's choice."""
    if class_teachers_see_results():
        return "You can manage the students of the class, enter conduct and remarks, see the results of the class and print the report cards."
    return ("You can manage the students of the class and enter their conduct and remarks. "
            "The marks of the other teachers' subjects and the report cards are with the administrator.")


def can_edit_term(term):
    return is_admin() or not term["is_locked"]


def parse_number(text):
    try:
        value = float(text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def parse_periods(text):
    """Periods (lessons) per week: empty -> None, a whole number from 0 to 60, anything else -> 'bad'."""
    text = (text or "").strip()
    if not text:
        return None
    return int(text) if text.isdigit() and int(text) <= 60 else "bad"


def logo_path():
    return LOGO_PATH if os.path.exists(LOGO_PATH) else None


def pdf_response(buf, filename):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("_")
    return send_file(buf, mimetype="application/pdf", as_attachment=request.args.get("download") == "1",
                     download_name=safe + ".pdf")


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def handle_error(err):
    titles = {400: "This page could not be saved", 403: "This page is not for your account", 404: "Page not found"}
    messages = {403: "You do not have permission to open this page.", 404: "That page was not found."}
    message = messages.get(err.code) or err.description
    if err.code == 403 and err.description and "permission to access the requested resource" not in err.description:
        message = err.description                       # a 403 raised with its own explanation
    return render_template("error.html", code=err.code, title=titles.get(err.code, "Something went wrong"),
                           message=message, roles=ROLES), err.code


@app.route("/whoami")
def whoami():
    """Who is this browser logged in as right now? Open pages ask this when they come back into view,
    so a page left open by one person notices when someone else has logged in on the same browser."""
    if g.user is None:
        return {"id": None}
    return {"id": g.user["id"], "name": g.user["full_name"], "role": ROLES.get(g.user["role"], g.user["role"])}


# --------------------------------------------------------------------------- accounts
ROLES = {"teacher": "Teacher", "admin": "Administrator"}
EMAIL_RE = re.compile(r"^[a-z0-9][a-z0-9._%+-]*@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def clean_role(text):
    return text if text in ROLES else "teacher"


def check_contact(email, phone, exclude_id=None):
    """Check an email address and a phone number. Returns (errors, email, phone) with both tidied up.

    The email must look like name@place.tld and the phone number must have 9 to 15 digits
    (spaces, dashes and brackets are ignored; one leading + is allowed). Neither may already
    belong to another account."""
    errors = []
    email = (email or "").strip().lower()
    phone = re.sub(r"[\s().-]", "", phone or "")
    other = exclude_id or 0
    if not email:
        errors.append("Enter the email address.")
    elif len(email) > 120 or not EMAIL_RE.match(email) or ".." in email:
        errors.append("The email address is not valid. Example: name@gmail.com")
    elif one("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, other)):
        errors.append("That email address is already used by another account.")
    if not phone:
        errors.append("Enter the phone number.")
    elif not re.fullmatch(r"\+?\d{9,15}", phone):
        errors.append("The phone number is not valid. Use digits only, for example 0788123456 or +250788123456.")
    elif one("SELECT 1 FROM users WHERE phone = ? AND id != ?", (phone, other)):
        errors.append("That phone number is already used by another account.")
    return errors, email, phone


def staff_user(uid):
    t = one("SELECT * FROM users WHERE id = ?", (uid,))
    if t is None:
        abort(404)
    return t


def validate_account(full_name, username, password, confirm):
    errors = []
    if len(full_name) < 3:
        errors.append("Enter your full name.")
    if not USERNAME_RE.match(username):
        errors.append(USERNAME_HELP)
    elif one("SELECT 1 FROM users WHERE username = ?", (username,)):
        errors.append("That username is already taken.")
    if len(password) < MIN_PASSWORD:
        errors.append(f"Password must be at least {MIN_PASSWORD} characters.")
    elif password != confirm:
        errors.append("The two passwords do not match.")
    return errors


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if one("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"):
        return redirect(url_for("login"))
    form = {k: request.form.get(k, "").strip() for k in ("school_name", "full_name", "username")}
    form["username"] = form["username"].lower()
    if request.method == "GET":
        form["username"] = ADMIN_USERNAME
    if request.method == "POST":
        errors = validate_account(form["full_name"], form["username"], request.form.get("password", ""),
                                  request.form.get("confirm", ""))
        if not form["school_name"]:
            errors.append("Enter the school name.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db = get_db()
            cur = db.execute(
                "INSERT INTO users (full_name, username, password_hash, role, status, created_at, email) VALUES (?, ?, ?, 'admin', 'active', ?, ?)",
                (form["full_name"], form["username"], hash_password(request.form["password"]), database.now(),
                 form["username"] if EMAIL_RE.match(form["username"]) else ""))
            db.execute("UPDATE settings SET value = ? WHERE key = 'school_name'", (form["school_name"],))
            admin = one("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))
            log("System set up", f"Admin account '{form['username']}' created", user=admin)
            db.commit()
            start_session(admin)
            flash("Welcome! The system is ready. Start by adding a term, subjects and classes.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("setup.html", form=form)


@app.route("/register", methods=["GET", "POST"])
def register():
    """New staff register once. They give their email and phone number; the admin then approves the
    account and decides the role."""
    form = {k: " ".join(request.form.get(k, "").split()) for k in ("full_name", "username", "email", "phone")}
    form["username"] = form["username"].lower()
    if request.method == "POST":
        errors = validate_account(form["full_name"], form["username"], request.form.get("password", ""),
                                  request.form.get("confirm", ""))
        contact_errors, email, phone = check_contact(form["email"], form["phone"])
        errors += contact_errors
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db = get_db()
            cur = db.execute(
                "INSERT INTO users (full_name, username, password_hash, role, status, created_at, email, phone) VALUES (?, ?, ?, 'teacher', 'pending', ?, ?, ?)",
                (form["full_name"], form["username"], hash_password(request.form["password"]), database.now(), email, phone))
            log("Staff registered", f"{form['full_name']} ({form['username']}, {email}, {phone}) is waiting for approval",
                user={"id": cur.lastrowid, "full_name": form["full_name"]})
            db.commit()
            notify({"full_name": form["full_name"], "email": email}, "Your registration was received",
                   ["Your registration in the school's marks system was received.",
                    f"Username: {form['username']}",
                    "", "You cannot log in yet. The administrator will check your details and give you your role.",
                    "You will get another email as soon as your account is approved."])
            for boss in rows("SELECT * FROM users WHERE role = 'admin' AND status = 'active' AND email != ''"):
                notify(boss, "A new staff registration is waiting",
                       [f"{form['full_name']} has registered and is waiting for your approval.",
                        f"Email: {email}", f"Phone: {phone}", f"Username: {form['username']}",
                        "", "Open Staff, check the email and phone number, choose the role and click Approve."])
            flash("Registration received. The admin must approve it and give you your role before you can log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    username = request.form.get("username", "").strip().lower()
    if request.method == "POST":
        u = one("SELECT * FROM users WHERE username = ?", (username,))
        if u is None or not check_password_hash(u["password_hash"], request.form.get("password", "")):
            flash("Wrong username or password.", "error")
        elif u["status"] == "pending":
            flash("Your account is still waiting for the admin to approve it.", "error")
        elif u["status"] == "disabled":
            flash("Your account has been disabled. Please speak to the admin.", "error")
        else:
            start_session(u)
            log("Logged in", user=u)
            get_db().commit()
            return redirect(url_for("home"))
    return render_template("login.html", username=username)


@app.route("/logout", methods=["POST"])
def logout():
    if g.user:
        log("Logged out")
        get_db().commit()
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    return redirect(url_for("admin_dashboard" if is_admin() else "teacher_dashboard"))


@app.route("/account/username", methods=["POST"])
@admin_required
def account_username():
    """The admin changes their own username. (Staff usernames are changed by the admin on the Staff page.)"""
    new = request.form.get("username", "").strip().lower()
    if not check_password_hash(g.user["password_hash"], request.form.get("current_password", "")):
        flash("Your password is wrong, so the username was not changed.", "error")
    elif not USERNAME_RE.match(new):
        flash(USERNAME_HELP, "error")
    elif one("SELECT 1 FROM users WHERE username = ? AND id != ?", (new, g.user["id"])):
        flash("That username is already used by someone else.", "error")
    elif new != g.user["username"]:
        get_db().execute("UPDATE users SET username = ? WHERE id = ?", (new, g.user["id"]))
        log("Changed admin username", f"{g.user['username']} -> {new}")
        get_db().commit()
        flash(f"Your username is now {new}. Use it the next time you log in.", "success")
    return redirect(url_for("account"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if not check_password_hash(g.user["password_hash"], request.form.get("current_password", "")):
            flash("Your current password is wrong.", "error")
        elif len(new) < MIN_PASSWORD:
            flash(f"The new password must be at least {MIN_PASSWORD} characters.", "error")
        elif new != request.form.get("confirm", ""):
            flash("The two new passwords do not match.", "error")
        elif check_password_hash(g.user["password_hash"], new):
            flash("Choose a new password that is different from the current one.", "error")
        else:
            first_time = g.user["must_change"]
            get_db().execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?",
                             (hash_password(new), g.user["id"]))
            log("Changed password")
            get_db().commit()
            flash("Password changed." + (" Your account is ready." if first_time else ""), "success")
            return redirect(url_for("home") if first_time else url_for("account"))
    return render_template("account.html")


# --------------------------------------------------------------------------- admin
def marks_progress(term_id, teacher_id=None):
    """One row per class/subject: how far the teacher is with entering marks this term."""
    sql = """
        SELECT a.id AS allocation_id, c.id AS class_id, c.name AS class_name, s.name AS subject_name,
               u.full_name AS teacher_name,
               (SELECT COUNT(*) FROM students st WHERE st.class_id = c.id) AS n_students,
               (SELECT COUNT(*) FROM assessments x WHERE x.class_id = a.class_id AND x.subject_id = a.subject_id
                    AND x.term_id = :t AND x.kind = 'test') AS n_tests,
               (SELECT COUNT(*) FROM assessments x WHERE x.class_id = a.class_id AND x.subject_id = a.subject_id
                    AND x.term_id = :t AND x.kind = 'exam') AS n_exams,
               (SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id
                    JOIN students st ON st.id = m.student_id AND st.class_id = a.class_id
                    WHERE x.class_id = a.class_id AND x.subject_id = a.subject_id AND x.term_id = :t) AS n_marks,
               (SELECT MAX(m.updated_at) FROM marks m JOIN assessments x ON x.id = m.assessment_id
                    WHERE x.class_id = a.class_id AND x.subject_id = a.subject_id AND x.term_id = :t) AS last_update
        FROM allocations a JOIN classes c ON c.id = a.class_id JOIN subjects s ON s.id = a.subject_id
        JOIN users u ON u.id = a.teacher_id
        WHERE (:teacher IS NULL OR a.teacher_id = :teacher)
        ORDER BY c.name COLLATE NOCASE, s.name COLLATE NOCASE"""
    out = []
    for r in get_db().execute(sql, {"t": term_id, "teacher": teacher_id}):
        d = dict(r)
        expected = d["n_students"] * (d["n_tests"] + d["n_exams"])
        d["expected"] = expected
        d["percent"] = round(d["n_marks"] / expected * 100) if expected else 0
        out.append(d)
    return out


def setting(name, default=""):
    """One value of the settings table (the school profile), or the default when it was never saved."""
    row = one("SELECT value FROM settings WHERE key = ?", (name,))
    return row["value"] if row else default


def class_chart(classes):
    """The drawing "students in each class" of the dashboard, worked out in whole numbers for a picture
    that is 1000 wide and 360 high. classes: id, name and n_students, in the order of the class list."""
    highest = max([c["n_students"] for c in classes] or [0])
    total = sum(c["n_students"] for c in classes)
    step = 1                                             # four steps on the side: 0, 15, 30, 45, 60
    for candidate in (1, 2, 5, 10, 15, 20, 25, 50, 100, 150, 200, 250, 500, 1000, 2500, 5000):
        step = candidate
        if candidate * 4 >= highest:
            break
    top = step * 4
    ticks = [{"value": step * k, "y": 270 - 60 * k} for k in range(5)]
    colours = ("#2f7ed8", "#3f4147", "#16a34a", "#ea580c", "#7c3aed", "#db2777", "#a16207", "#0d9488", "#dc2626", "#0891b2")
    n = len(classes)
    slot = 926 * 100 // n if n else 0                    # the room of one class, in hundredths
    width = max(8, min(46, slot * 42 // 10000))
    bars = []
    for i, c in enumerate(classes):
        count = c["n_students"]
        height = count * 240 // top
        if count > 0 and height < 2:
            height = 2
        middle = 64 + slot * (2 * i + 1) // 200
        bars.append({"id": c["id"], "name": c["name"], "count": count, "middle": middle, "x": middle - width // 2, "y": 270 - height,
                     "width": width, "height": height, "colour": colours[i % len(colours)]})
    return {"ticks": ticks, "bars": bars, "total": total, "classes": n}


@app.route("/admin")
@admin_required
def admin_dashboard():
    term = pick_term()
    # Every number on the dashboard is counted from the database at the moment the page is opened.
    by_class = rows("""SELECT c.*, NULL AS level_name, (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS n_students
                       FROM classes c ORDER BY c.name COLLATE NOCASE""")
    by_class = sorted((class_facts(c) for c in by_class), key=lambda c: (c["grade_no"], c["name"].lower()))
    counts = {
        "teachers": one("SELECT COUNT(*) FROM users WHERE status = 'active'")[0],
        "classes": one("SELECT COUNT(*) FROM classes")[0],
        "subjects": one("SELECT COUNT(*) FROM subjects")[0],
        "students": one("SELECT COUNT(*) FROM students")[0],
    }
    return render_template(
        "admin_dashboard.html", counts=counts, term=term, terms=all_terms(),
        progress=marks_progress(term["id"]) if term else [],
        pending=rows("SELECT * FROM users WHERE status = 'pending' ORDER BY id"), roles=ROLES,
        mine=marks_progress(term["id"], g.user["id"]) if term else [],          # an Administrator may teach too
        my_classes=rows("SELECT * FROM classes WHERE class_teacher_id = ? ORDER BY name COLLATE NOCASE", (g.user["id"],)),
        activity=rows("SELECT * FROM activity ORDER BY id DESC LIMIT 12"),
        chart=class_chart(by_class), school_name=setting("school_name"),
        facts={"classrooms": setting("school_classrooms")})


@app.route("/admin/activity")
@admin_required
def admin_activity():
    who = request.args.get("user", type=int)
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM activity WHERE 1 = 1", []
    if who:
        sql += " AND user_id = ?"
        args.append(who)
    if q:
        sql += " AND (action LIKE ? OR details LIKE ? OR user_name LIKE ?)"
        args += [f"%{q}%"] * 3
    return render_template("admin_activity.html", activity=rows(sql + " ORDER BY id DESC LIMIT 500", args),
                           people=rows("SELECT id, full_name FROM users ORDER BY full_name COLLATE NOCASE"), who=who, q=q)


@app.route("/admin/teachers", methods=["GET", "POST"])
@admin_required
def admin_teachers():
    form = {k: " ".join(request.form.get(k, "").split()) for k in ("full_name", "username", "email", "phone")}
    form["username"] = form["username"].lower()
    form["role"] = clean_role(request.form.get("role"))
    if request.method == "POST":
        # The admin creates a staff account directly, with its role. It is active at once (no approval needed).
        password = request.form.get("password", "")
        errors = validate_account(form["full_name"], form["username"], password, password)
        contact_errors, email, phone = check_contact(form["email"], form["phone"])
        errors += contact_errors
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            get_db().execute(
                """INSERT INTO users (full_name, username, password_hash, role, status, created_at, must_change, email, phone)
                   VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?)""",
                (form["full_name"], form["username"], hash_password(password), form["role"], database.now(), email, phone))
            log("Added staff member", f"{form['full_name']} ({form['username']}) as {ROLES[form['role']]}")
            get_db().commit()
            notify({"full_name": form["full_name"], "email": email}, "An account was created for you",
                   [f"The administrator has created an account for you in the school's marks system.",
                    f"Role: {ROLES[form['role']]}", f"Username: {form['username']}",
                    "", "The administrator will give you your first password in person.",
                    "The first time you log in, the system asks you to choose your own password."])
            flash(f"{form['full_name']} added as {ROLES[form['role']]}. They log in with username {form['username']} and the password you typed, "
                  "and the system then makes them choose their own password.", "success")
            return redirect(url_for("admin_teachers"))
    teachers = rows("""
        SELECT u.*,
               (SELECT GROUP_CONCAT(c.name, ', ') FROM classes c WHERE c.class_teacher_id = u.id) AS class_of,
               (SELECT COUNT(*) FROM allocations a WHERE a.teacher_id = u.id) AS n_subjects
        FROM users u
        ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, u.full_name COLLATE NOCASE""")
    return render_template("admin_teachers.html", teachers=teachers, form=form, roles=ROLES)


@app.route("/admin/teachers/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def admin_teacher_edit(uid):
    """The admin updates a staff member: name, username, email, phone, role, or a new password."""
    t = staff_user(uid)
    if t["id"] == g.user["id"]:
        return redirect(url_for("account"))             # your own account is changed under My account
    form = {"full_name": t["full_name"], "username": t["username"], "email": t["email"], "phone": t["phone"], "role": t["role"]}
    if request.method == "POST":
        form = {k: " ".join(request.form.get(k, "").split()) for k in ("full_name", "username", "email", "phone")}
        form["username"] = form["username"].lower()
        form["role"] = clean_role(request.form.get("role"))
        password = request.form.get("password", "")
        errors = []
        if len(form["full_name"]) < 3:
            errors.append("Enter the full name.")
        if not USERNAME_RE.match(form["username"]):
            errors.append(USERNAME_HELP)
        elif one("SELECT 1 FROM users WHERE username = ? AND id != ?", (form["username"], uid)):
            errors.append("That username is already used by someone else.")
        contact_errors, email, phone = check_contact(form["email"], form["phone"], exclude_id=uid)
        errors += contact_errors
        if password and len(password) < MIN_PASSWORD:
            errors.append(f"The new password must be at least {MIN_PASSWORD} characters (or leave it empty to keep the current one).")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db = get_db()
            new = {"name": form["full_name"], "username": form["username"], "email": email, "phone": phone, "role": form["role"]}
            old = {"name": t["full_name"], "username": t["username"], "email": t["email"], "phone": t["phone"], "role": t["role"]}
            changed = [k for k in new if new[k] != old[k]]
            db.execute("UPDATE users SET full_name = ?, username = ?, email = ?, phone = ?, role = ? WHERE id = ?",
                       (form["full_name"], form["username"], email, phone, form["role"], uid))
            if password:
                db.execute("UPDATE users SET password_hash = ?, must_change = 1 WHERE id = ?", (hash_password(password), uid))
                changed.append("password")
            log("Edited staff member", f"{t['full_name']} ({t['username']}) -> {form['full_name']} ({form['username']}); changed: {', '.join(changed) or 'nothing'}")
            if "role" in changed:
                log("Changed role", f"{form['full_name']}: {ROLES[t['role']]} -> {ROLES[form['role']]}")
            db.commit()
            if "role" in changed:
                notify({"full_name": form["full_name"], "email": email}, "Your role was changed",
                       [f"The administrator changed your role from {ROLES[t['role']]} to {ROLES[form['role']]}.",
                        "It applies the next time you open a page."])
            flash(f"{form['full_name']} saved." + (f" Role is now {ROLES[form['role']]}." if "role" in changed else "")
                  + (" They must choose their own password at the next log-in." if password else ""), "success")
            return redirect(url_for("admin_teachers"))
    return render_template("admin_teacher.html", t=t, form=form, roles=ROLES, **teacher_work(uid))


def teacher_work(uid):
    """What a staff member teaches, and everything they could be given, for the staff page."""
    order = R.SUBJECT_ORDER
    return {
        "allocs": rows(f"""SELECT a.id, c.id AS class_id, c.name AS class_name, s.id AS subject_id, s.name AS subject_name,
                                  (SELECT COUNT(*) FROM students st WHERE st.class_id = c.id) AS n_students,
                                  (SELECT COUNT(*) FROM assessments x WHERE x.class_id = c.id AND x.subject_id = s.id) AS n_assess
                           FROM allocations a JOIN classes c ON c.id = a.class_id JOIN subjects s ON s.id = a.subject_id
                           WHERE a.teacher_id = ? ORDER BY c.name COLLATE NOCASE, {order}""", (uid,)),
        # every subject of every class, with who teaches it now
        "pairs": rows(f"""SELECT c.id AS class_id, c.name AS class_name, s.id AS subject_id, s.name AS subject_name,
                                 a.teacher_id, u.full_name AS teacher_name
                          FROM classes c JOIN subjects s ON s.level_id = c.level_id
                          LEFT JOIN allocations a ON a.class_id = c.id AND a.subject_id = s.id
                          LEFT JOIN users u ON u.id = a.teacher_id
                          ORDER BY c.name COLLATE NOCASE, {order}"""),
        "classes": rows("""SELECT c.id, c.name, c.class_teacher_id, u.full_name AS class_teacher_name,
                                  (SELECT COUNT(*) FROM students st WHERE st.class_id = c.id) AS n_students
                           FROM classes c LEFT JOIN users u ON u.id = c.class_teacher_id ORDER BY c.name COLLATE NOCASE"""),
        "changing": request.args.get("change", type=int),
    }


def _class_subject(pair):
    """'12:34' -> (class row, subject row) when that subject belongs to the level of that class."""
    found = re.fullmatch(r"(\d+):(\d+)", pair or "")
    if not found:
        return None, None
    cls = one("SELECT * FROM classes WHERE id = ?", (int(found.group(1)),))
    sub = one("SELECT * FROM subjects WHERE id = ?", (int(found.group(2)),))
    if cls is None or sub is None or sub["level_id"] != cls["level_id"]:
        return None, None
    return cls, sub


def _give_subject(teacher, cls, sub):
    """Make `teacher` the teacher of `sub` in `cls` (taking it over if someone else has it)."""
    db = get_db()
    current = one("""SELECT a.*, u.full_name AS teacher_name FROM allocations a JOIN users u ON u.id = a.teacher_id
                     WHERE a.class_id = ? AND a.subject_id = ?""", (cls["id"], sub["id"]))
    if current is None:
        db.execute("INSERT INTO allocations (class_id, subject_id, teacher_id) VALUES (?, ?, ?)", (cls["id"], sub["id"], teacher["id"]))
        log("Assigned subject", f"{sub['name']} in {cls['name']} -> {teacher['full_name']}")
        notify_subjects(teacher["id"], [(sub["name"], cls["name"], class_size(cls["id"]))])
        return f"{teacher['full_name']} now teaches {sub['name']} in {cls['name']}."
    if current["teacher_id"] == teacher["id"]:
        return f"{teacher['full_name']} already teaches {sub['name']} in {cls['name']}."
    db.execute("UPDATE allocations SET teacher_id = ? WHERE id = ?", (teacher["id"], current["id"]))
    log("Changed subject teacher", f"{sub['name']} in {cls['name']} -> {teacher['full_name']} (was {current['teacher_name']})")
    notify_subjects(teacher["id"], [(sub["name"], cls["name"], class_size(cls["id"]))])
    return f"{teacher['full_name']} now teaches {sub['name']} in {cls['name']} instead of {current['teacher_name']}."


@app.route("/admin/teachers/<int:uid>/subjects", methods=["POST"])
@admin_required
def admin_teacher_subjects(uid):
    """From the staff page: give this teacher a subject, change one of their subjects, or take one away."""
    t = staff_user(uid)
    db = get_db()
    back = redirect(url_for("admin_teacher_edit", uid=uid) + "#work")
    action = request.form.get("action")
    mine = one("""SELECT a.*, c.name AS class_name, s.name AS subject_name FROM allocations a
                  JOIN classes c ON c.id = a.class_id JOIN subjects s ON s.id = a.subject_id
                  WHERE a.id = ? AND a.teacher_id = ?""", (request.form.get("allocation_id", type=int), uid))
    if action == "remove":
        if mine is None:
            abort(404)
        db.execute("DELETE FROM allocations WHERE id = ?", (mine["id"],))
        log("Removed subject from teacher", f"{mine['subject_name']} in {mine['class_name']} (was {t['full_name']})")
        db.commit()
        flash(f"{t['full_name']} no longer teaches {mine['subject_name']} in {mine['class_name']}. "
              "The subject has no teacher in that class now; marks already entered are kept.", "success")
        return back
    if action not in ("assign", "change") or (action == "change" and mine is None):
        abort(400, "Unknown action.")
    if t["status"] != "active":
        flash(f"{t['full_name']} cannot be given subjects because the account is {t['status']}.", "error")
        return back
    cls, sub = _class_subject(request.form.get("pair"))
    if cls is None:
        flash("Choose a class and subject from the list.", "error")
        return back
    message = _give_subject(t, cls, sub)
    if action == "change" and (mine["class_id"], mine["subject_id"]) != (cls["id"], sub["id"]):
        db.execute("DELETE FROM allocations WHERE id = ?", (mine["id"],))
        log("Removed subject from teacher", f"{mine['subject_name']} in {mine['class_name']} (was {t['full_name']})")
        message += f" {mine['subject_name']} in {mine['class_name']} has no teacher now."
    db.commit()
    flash(message, "success")
    return back


@app.route("/admin/teachers/<int:uid>/classes", methods=["POST"])
@admin_required
def admin_teacher_classes(uid):
    """From the staff page: make this teacher the class teacher of a class, or stop that."""
    t = one("SELECT * FROM users WHERE id = ?", (uid,))
    cls = one("""SELECT c.*, u.full_name AS class_teacher_name FROM classes c LEFT JOIN users u ON u.id = c.class_teacher_id
                 WHERE c.id = ?""", (request.form.get("class_id", type=int),))
    if t is None or cls is None:
        abort(404)
    db = get_db()
    back = redirect(url_for("admin_teacher_edit", uid=uid) + "#work")
    if request.form.get("action") == "remove":
        if cls["class_teacher_id"] == uid:
            db.execute("UPDATE classes SET class_teacher_id = NULL WHERE id = ?", (cls["id"],))
            log("Set class teacher", f"{cls['name']}: none (was {t['full_name']})")
            flash(f"{t['full_name']} is no longer the class teacher of {cls['name']}. The class has no class teacher now.", "success")
    elif t["status"] != "active":
        flash(f"{t['full_name']} cannot be a class teacher because the account is {t['status']}.", "error")
    elif cls["class_teacher_id"] != uid:
        db.execute("UPDATE classes SET class_teacher_id = ? WHERE id = ?", (uid, cls["id"]))
        log("Set class teacher", f"{cls['name']}: {t['full_name']}" + (f" (was {cls['class_teacher_name']})" if cls["class_teacher_name"] else ""))
        notify(t, f"You are now the class teacher of {cls['name']}",
               [f"The administrator has made you the class teacher of {cls['name']} ({class_size(cls['id'])} students).",
                class_teacher_can()])
        flash(f"{t['full_name']} is now the class teacher of {cls['name']}"
              + (f" instead of {cls['class_teacher_name']}." if cls["class_teacher_name"] else "."), "success")
    db.commit()
    return back


@app.route("/admin/teachers/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_teacher_action(uid, action):
    db = get_db()
    t = staff_user(uid)
    back = redirect(request.form.get("next") or url_for("admin_teachers"))
    if t["id"] == g.user["id"]:
        flash("You cannot do that to your own account.", "error")
        return back
    if t["role"] == "admin" and t["status"] == "active" and action in ("disable", "delete"):
        flash(f"{t['full_name']} is an Administrator. Change the role to Teacher first (Edit), then you can {action} the account.", "error")
        return back
    if action == "approve" and t["status"] == "pending":
        role = clean_role(request.form.get("role"))
        db.execute("UPDATE users SET status = 'active', role = ? WHERE id = ?", (role, uid))
        log("Approved staff", f"{t['full_name']} as {ROLES[role]}")
        notify(t, "Your account is approved",
               ["Your account in the school's marks system has been approved.",
                f"Role: {ROLES[role]}", f"Username: {t['username']}",
                "", "Log in with the password you chose when you registered."]
               + (["You will see your classes and subjects as soon as the administrator has given them to you."] if role == "teacher" else []))
        flash(f"{t['full_name']} is approved as {ROLES[role]} and can now log in."
              + (" Open “Edit / subjects” to give them subjects or a class." if role == "teacher" else ""), "success")
    elif action in ("approve", "enable"):
        db.execute("UPDATE users SET status = 'active' WHERE id = ?", (uid,))
        log("Enabled teacher", t["full_name"])
        flash(f"{t['full_name']} can now log in.", "success")
    elif action == "disable":
        db.execute("UPDATE users SET status = 'disabled' WHERE id = ?", (uid,))
        log("Disabled teacher", t["full_name"])
        flash(f"{t['full_name']} can no longer log in. Their marks are kept.", "success")
    elif action == "reject" and t["status"] == "pending":
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
        log("Rejected registration", t["full_name"])
        flash("Registration rejected and removed.", "success")
    elif action == "delete":
        n_sub = one("SELECT COUNT(*) FROM allocations WHERE teacher_id = ?", (uid,))[0]
        n_cls = one("SELECT COUNT(*) FROM classes WHERE class_teacher_id = ?", (uid,))[0]
        # Their subjects and classes become unassigned (database cascade). Marks belong to the
        # class and subject, not to the teacher, so every mark they entered is kept.
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
        log("Deleted teacher", f"{t['full_name']} ({t['username']}): {n_sub} subject(s) and {n_cls} class(es) left without a teacher")
        flash(f"{t['full_name']} was deleted. The marks they entered are kept."
              + (f" {n_sub} subject(s) now have no teacher – open the classes to choose a new one." if n_sub else "")
              + (f" {n_cls} class(es) now have no class teacher." if n_cls else ""), "success")
    elif action == "reset":
        temp = secrets.token_urlsafe(6)
        db.execute("UPDATE users SET password_hash = ?, must_change = 1 WHERE id = ?", (hash_password(temp), uid))
        log("Reset password", t["full_name"])
        notify(t, "Your password was reset",
               ["The administrator has reset your password.",
                "Ask the administrator for your temporary password (it is not sent by email).",
                "When you log in with it, the system asks you to choose your own new password."])
        flash(f"New temporary password for {t['full_name']}: {temp}  — give it to them. The system makes them choose their own password when they log in.", "success")
    else:
        abort(400, "Unknown action.")
    db.commit()
    return back


def clean_category(text):
    return text if text in {key for key, _ in database.CATEGORIES} else "core"


def all_levels():
    return rows("""SELECT l.*, (SELECT COUNT(*) FROM subjects s WHERE s.level_id = l.id) AS n_subjects,
                          (SELECT COALESCE(SUM(max_mark), 0) FROM subjects s WHERE s.level_id = l.id) AS total_max,
                          (SELECT COUNT(*) FROM classes c WHERE c.level_id = l.id) AS n_classes
                   FROM levels l ORDER BY l.sort_order, l.name COLLATE NOCASE""")


def next_subject_order(level_id):
    return one("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM subjects WHERE level_id = ?", (level_id,))[0]


def free_subject_code(name, level_id):
    """Make a short code from the name, e.g. 'Mathematics' -> 'MATH' (with a number if that is taken)."""
    base = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:4] or "SUBJ"
    code, n = base, 2
    while one("SELECT 1 FROM subjects WHERE level_id = ? AND code = ?", (level_id, code)):
        code, n = f"{base[:3]}{n}", n + 1
    return code


def subjects_url(level_id=None):
    return url_for("admin_subjects", level=level_id) if level_id else url_for("admin_subjects")


@app.route("/admin/subjects", methods=["GET", "POST"])
@admin_required
def admin_subjects():
    levels = all_levels()
    chosen = request.values.get("level", type=int)
    level = next((x for x in levels if x["id"] == chosen), levels[0] if levels else None)
    if request.method == "POST":
        name = " ".join(request.form.get("name", "").split())[:60]
        code = request.form.get("code", "").strip().upper()[:6]
        max_mark = parse_number(request.form.get("max_mark", ""))
        if level is None:
            flash("Add a level first.", "error")
        elif not name:
            flash("Enter the subject name.", "error")
        elif max_mark is None or not 0 < max_mark <= 1000:
            flash("Enter what the subject is marked out of on the report (its maximum), for example 30.", "error")
        elif parse_periods(request.form.get("periods")) == "bad":
            flash("Periods per week must be a whole number, for example 3 (or leave it empty).", "error")
        elif one("SELECT 1 FROM subjects WHERE level_id = ? AND name = ? COLLATE NOCASE", (level["id"], name)):
            flash(f"{level['name']} already has a subject with that name.", "error")
        else:
            try:
                get_db().execute("INSERT INTO subjects (level_id, name, code, category, max_mark, sort_order, periods) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (level["id"], name, code or free_subject_code(name, level["id"]),
                                  clean_category(request.form.get("category")), max_mark, next_subject_order(level["id"]),
                                  parse_periods(request.form.get("periods"))))
                log("Added subject", f"{name} (max {max_mark:g}) in {level['name']}")
                get_db().commit()
                flash(f"Subject {name} added to {level['name']}.", "success")
            except sqlite3.IntegrityError:
                flash(f"{level['name']} already has a subject with that short code.", "error")
        return redirect(subjects_url(level["id"] if level else None))
    subjects = []
    if level:
        subjects = rows(f"""SELECT s.*, (SELECT COUNT(*) FROM allocations a WHERE a.subject_id = s.id) AS n_classes,
                                   (SELECT COUNT(*) FROM assessments x WHERE x.subject_id = s.id) AS n_assess,
                                   (SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id
                                        WHERE x.subject_id = s.id) AS n_marks
                            FROM subjects s WHERE s.level_id = ? ORDER BY {R.SUBJECT_ORDER}""", (level["id"],))
    standard = []
    for item in database.STANDARD_LISTS:
        lv = next((x for x in levels if x["name"].lower() == item["name"].lower()), None)
        have = {r["name"].lower() for r in rows("SELECT name FROM subjects WHERE level_id = ?", (lv["id"],))} if lv else set()
        standard.append({"key": item["key"], "name": item["name"], "exists": lv is not None,
                         "missing": [x[0] for x in item["subjects"] if x[0].lower() not in have],
                         "count": len(item["subjects"]), "total": sum(x[3] for x in item["subjects"])})
    return render_template("admin_subjects.html", levels=levels, level=level, subjects=subjects, standard=standard,
                           total_max=sum(r["max_mark"] for r in subjects))


@app.route("/admin/subjects/save", methods=["POST"])
@admin_required
def admin_subjects_save():
    """Save the subjects table of one level at once: name, short code, group, maximum and order."""
    db = get_db()
    level_id = request.form.get("level", type=int)
    changes, errors = [], []
    for sub in rows("SELECT * FROM subjects WHERE level_id = ?", (level_id,)):
        k = str(sub["id"])
        if "name_" + k not in request.form:
            continue
        name = " ".join(request.form.get("name_" + k, "").split())[:60]
        code = request.form.get("code_" + k, "").strip().upper()[:6]
        max_mark = parse_number(request.form.get("max_" + k, ""))
        order = request.form.get("order_" + k, type=int)
        category = clean_category(request.form.get("category_" + k))
        periods = parse_periods(request.form.get("periods_" + k)) if "periods_" + k in request.form else sub["periods"]
        if periods == "bad":
            errors.append(f"{sub['name']}: periods per week must be a whole number (or empty).")
        elif not name or not code:
            errors.append(f"{sub['name']}: the name and the short code cannot be empty.")
        elif max_mark is None or not 0 < max_mark <= 1000:
            errors.append(f"{sub['name']}: the maximum must be a number greater than 0.")
        else:
            new = (name, code, category, max_mark, order if order is not None else sub["sort_order"], periods)
            if new != (sub["name"], sub["code"], sub["category"], sub["max_mark"], sub["sort_order"], sub["periods"]):
                changes.append((new, sub))
    if not errors:
        try:
            for new, sub in changes:
                db.execute("UPDATE subjects SET name = ?, code = ?, category = ?, max_mark = ?, sort_order = ?, periods = ? WHERE id = ?",
                           new + (sub["id"],))
                log("Edited subject", f"{sub['name']}: {new[0]} ({new[1]}), {dict(database.CATEGORIES)[new[2]]}, max {new[3]:g}")
        except sqlite3.IntegrityError:
            db.rollback()
            errors.append("Two subjects of the same level cannot have the same name or the same short code. Nothing was saved.")
    if errors:
        db.rollback()
        for e in errors:
            flash(e, "error")
    else:
        db.commit()
        flash(f"Subjects saved ({len(changes)} changed). Reports use the new values immediately.", "success")
    return redirect(subjects_url(level_id))


@app.route("/admin/levels", methods=["POST"])
@admin_required
def admin_level_add():
    name = " ".join(request.form.get("name", "").split())[:60]
    if not name:
        flash("Enter a name for the level, for example 'S4, S5 - Arts'.", "error")
        return redirect(subjects_url())
    if one("SELECT 1 FROM levels WHERE name = ? COLLATE NOCASE", (name,)):
        flash("A level with that name already exists.", "error")
        return redirect(subjects_url())
    order = one("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM levels")[0]
    new_id = get_db().execute("INSERT INTO levels (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
    log("Added level", name)
    get_db().commit()
    flash(f"Level {name} added. Now add its subjects with their maximums.", "success")
    return redirect(subjects_url(new_id))


@app.route("/admin/levels/<int:level_id>/<action>", methods=["POST"])
@admin_required
def admin_level_action(level_id, action):
    level = one("SELECT * FROM levels WHERE id = ?", (level_id,))
    if level is None:
        abort(404)
    db = get_db()
    if action == "rename":
        name = " ".join(request.form.get("name", "").split())[:60]
        if not name:
            flash("The level name cannot be empty.", "error")
        elif one("SELECT 1 FROM levels WHERE name = ? COLLATE NOCASE AND id != ?", (name, level_id)):
            flash("Another level already has that name.", "error")
        else:
            db.execute("UPDATE levels SET name = ? WHERE id = ?", (name, level_id))
            log("Renamed level", f"{level['name']} -> {name}")
            db.commit()
            flash("Level renamed.", "success")
        return redirect(subjects_url(level_id))
    if action == "delete":
        n_cls = one("SELECT COUNT(*) FROM classes WHERE level_id = ?", (level_id,))[0]
        n_sub = one("SELECT COUNT(*) FROM subjects WHERE level_id = ?", (level_id,))[0]
        if n_cls or n_sub:
            flash(f"{level['name']} still has {n_sub} subject(s) and {n_cls} class(es). Delete or move them first; "
                  "a level can only be deleted when it is empty.", "error")
            return redirect(subjects_url(level_id))
        db.execute("DELETE FROM levels WHERE id = ?", (level_id,))
        log("Deleted level", level["name"])
        db.commit()
        flash(f"Level {level['name']} deleted.", "success")
        return redirect(subjects_url())
    abort(400, "Unknown action.")


@app.route("/admin/subjects/standard/<key>", methods=["POST"])
@admin_required
def admin_subjects_standard(key):
    if key not in {x["key"] for x in database.STANDARD_LISTS}:
        abort(404)
    level_id, added, updated = database.add_standard_list(get_db(), key)
    name = one("SELECT name FROM levels WHERE id = ?", (level_id,))["name"]
    log("Loaded standard subjects", f"{name}: {added} added, {updated} already there (group, maximum and order reset)")
    get_db().commit()
    flash(f"{name}: {added} subject(s) added, {updated} were already there and now have the standard group, maximum and order.", "success")
    return redirect(subjects_url(level_id))


@app.route("/admin/subjects/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_subject_delete(sid):
    s = one("SELECT * FROM subjects WHERE id = ?", (sid,))
    if s is None:
        abort(404)
    n_cls = one("""SELECT COUNT(*) FROM (SELECT class_id FROM allocations WHERE subject_id = :s
                                         UNION SELECT class_id FROM assessments WHERE subject_id = :s)""", {"s": sid})[0]
    n_marks = one("SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id WHERE x.subject_id = ?", (sid,))[0]
    get_db().execute("DELETE FROM subjects WHERE id = ?", (sid,))     # cascade: allocations, tests, marks
    log("Deleted subject", f"{s['name']}: removed from {n_cls} class(es), {n_marks} marks deleted")
    get_db().commit()
    flash(f"{s['name']} was deleted" + (f" from {n_cls} class(es), together with {n_marks} marks." if (n_cls or n_marks) else "."), "success")
    return redirect(subjects_url(s["level_id"]))


@app.route("/switch-term", methods=["POST"])
@login_required
def switch_term():
    """Anyone who is logged in may change the academic year / term they are working in.
    Only the admin can add, rename or delete academic years (see below)."""
    term = one("SELECT * FROM terms WHERE id = ?", (request.form.get("term_id", type=int),))
    if term is None:
        abort(404)
    session["term_id"] = term["id"]
    current = school_term()
    flash(f"You are now working in {term['name']}."
          + ("" if term["is_current"] or current is None else f" The school's current term is {current['name']}."), "success")
    # Go back to the page the person was on, without any old ?term= in its address.
    target = request.form.get("next", "")
    parts = urlsplit(target)
    if not target.startswith("/") or target.startswith("//") or parts.netloc:
        return redirect(url_for("home"))
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "term"])
    return redirect(urlunsplit(("", "", parts.path, query, "")))


def clean_year(text):
    return " ".join((text or "").split())[:30]


@app.route("/admin/terms", methods=["GET", "POST"])
@admin_required
def admin_terms():
    db = get_db()
    if request.method == "POST":
        first = one("SELECT COUNT(*) FROM terms")[0] == 0
        if request.form.get("action") == "add_year":
            # A whole academic year at once: "2026-2027" -> 2026-2027 TERM 1, TERM 2, TERM 3
            year = clean_year(request.form.get("year"))
            count = min(4, max(1, request.form.get("count", type=int) or 3))
            names = [f"{year} TERM {n}" for n in range(1, count + 1)]
            if not year:
                flash("Enter the academic year, for example 2026-2027.", "error")
            elif one("SELECT 1 FROM terms WHERE year = ? COLLATE NOCASE", (year,)):
                flash(f"The academic year {year} already exists. Add a term to it below instead.", "error")
            elif any(one("SELECT 1 FROM terms WHERE name = ? COLLATE NOCASE", (n,)) for n in names):
                flash("A term with one of those names already exists.", "error")
            else:
                for i, name in enumerate(names):
                    db.execute("INSERT INTO terms (name, year, is_current) VALUES (?, ?, ?)", (name, year, 1 if first and i == 0 else 0))
                log("Added academic year", f"{year} with {count} term(s)")
                db.commit()
                flash(f"Academic year {year} added with {count} term{'s' if count != 1 else ''}. "
                      "Press “Make current” on a term when the school moves to it.", "success")
        else:
            name = " ".join(request.form.get("name", "").split())[:60]
            year = clean_year(request.form.get("year"))
            if not name:
                flash("Enter the term name as it must be printed, for example '2025-2026 TERM 1'.", "error")
            elif one("SELECT 1 FROM terms WHERE name = ? COLLATE NOCASE", (name,)):
                flash("That term already exists.", "error")
            else:
                db.execute("INSERT INTO terms (name, year, is_current) VALUES (?, ?, ?)", (name, year, 1 if first else 0))
                log("Added term", name)
                db.commit()
                flash(f"{name} added.", "success")
        return redirect(url_for("admin_terms"))
    terms = rows(f"""SELECT t.*, (SELECT COUNT(*) FROM assessments x WHERE x.term_id = t.id) AS n_assess,
                            (SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id WHERE x.term_id = t.id) AS n_marks
                     FROM terms t ORDER BY {TERM_ORDER}""")
    years = []
    for t in terms:
        if not years or years[-1]["year"] != t["year"]:
            years.append({"year": t["year"], "terms": []})
        years[-1]["terms"].append(t)
    return render_template("admin_terms.html", years=years)


@app.route("/admin/years/rename", methods=["POST"])
@admin_required
def admin_year_rename():
    """Change the name of an academic year; its terms are renamed with it."""
    db = get_db()
    old, new = clean_year(request.form.get("old")), clean_year(request.form.get("new"))
    terms = rows("SELECT * FROM terms WHERE year = ?", (old,))
    if not terms:
        abort(404)
    if not new:
        flash("The academic year cannot be empty.", "error")
    elif new.lower() != old.lower() and one("SELECT 1 FROM terms WHERE year = ? COLLATE NOCASE", (new,)):
        flash(f"The academic year {new} already exists.", "error")
    elif new != old:
        try:
            for t in terms:
                name = new + t["name"][len(old):] if old and t["name"].lower().startswith(old.lower()) else t["name"]
                db.execute("UPDATE terms SET year = ?, name = ? WHERE id = ?", (new, name, t["id"]))
            log("Changed academic year", f"{old or '(none)'} -> {new}")
            db.commit()
            flash(f"Academic year changed to {new}. Its terms and their reports now show the new name.", "success")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Not changed: a term with one of the new names already exists.", "error")
    return redirect(url_for("admin_terms"))


@app.route("/admin/terms/<int:tid>/<action>", methods=["POST"])
@admin_required
def admin_term_action(tid, action):
    db = get_db()
    t = one("SELECT * FROM terms WHERE id = ?", (tid,))
    if t is None:
        abort(404)
    if action == "current":
        db.execute("UPDATE terms SET is_current = (id = ?)", (tid,))
        session["term_id"] = tid
        log("Set current term", t["name"])
        flash(f"{t['name']} is now the school's current term. Everyone starts in it when they log in.", "success")
    elif action == "lock":
        db.execute("UPDATE terms SET is_locked = 1 - is_locked WHERE id = ?", (tid,))
        log("Unlocked term" if t["is_locked"] else "Locked term", t["name"])
    elif action == "edit":
        name = " ".join(request.form.get("name", "").split())[:60]
        year = clean_year(request.form.get("year"))
        if not name:
            flash("The term name cannot be empty.", "error")
        elif one("SELECT 1 FROM terms WHERE name = ? COLLATE NOCASE AND id != ?", (name, tid)):
            flash("Another term already has that name.", "error")
        else:
            db.execute("UPDATE terms SET name = ?, year = ? WHERE id = ?", (name, year, tid))
            log("Changed term", f"{t['name']} -> {name} (academic year {year or 'none'})")
            flash(f"Saved. Reports of this term now print “{name}”.", "success")
    elif action == "delete":
        if one("SELECT COUNT(*) FROM assessments WHERE term_id = ?", (tid,))[0]:
            flash("This term already has marks, so it cannot be deleted.", "error")
            return redirect(url_for("admin_terms"))
        db.execute("DELETE FROM terms WHERE id = ?", (tid,))
        if t["is_current"]:
            db.execute("UPDATE terms SET is_current = 1 WHERE id = (SELECT MAX(id) FROM terms)")
        log("Deleted term", t["name"])
        flash(f"{t['name']} deleted.", "success")
    else:
        abort(400, "Unknown action.")
    db.commit()
    return redirect(url_for("admin_terms"))


@app.route("/admin/classes", methods=["GET", "POST"])
@admin_required
def admin_classes():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        level = one("SELECT * FROM levels WHERE id = ?", (request.form.get("level_id", type=int),))
        if not name:
            flash("Enter the class name.", "error")
        elif level is None:
            flash("Choose the level of the class. The level decides which subjects are on its report.", "error")
        else:
            try:
                cur = get_db().execute("INSERT INTO classes (name, level_id, grade, combination) VALUES (?, ?, ?, ?)",
                                       (name, level["id"], " ".join(request.form.get("grade", "").split())[:40],
                                        " ".join(request.form.get("combination", "").split())[:80]))
                log("Added class", f"{name} ({level['name']})")
                get_db().commit()
                flash(f"Class {name} added. Now choose its class teacher, subjects and students.", "success")
                return redirect(url_for("admin_class", class_id=cur.lastrowid))
            except sqlite3.IntegrityError:
                flash("A class with that name already exists.", "error")
        return redirect(url_for("admin_classes"))
    classes = rows("""SELECT c.*, u.full_name AS class_teacher_name, l.name AS level_name,
                             (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS n_students,
                             (SELECT COUNT(*) FROM allocations a WHERE a.class_id = c.id) AS n_subjects
                      FROM classes c LEFT JOIN users u ON u.id = c.class_teacher_id LEFT JOIN levels l ON l.id = c.level_id
                      ORDER BY c.name COLLATE NOCASE""")
    classes = sorted((class_facts(c) for c in classes), key=lambda c: (c["grade_no"], c["name"].lower()))
    combos = list(dict.fromkeys(c["combination_shown"] for c in classes if c["combination_shown"]))
    return render_template("admin_classes.html", classes=classes, levels=all_levels(), combos=combos)


def class_facts(row):
    """A class with the grade and combination to show. Classes made by hand before these were stored
    get sensible values: 'S2 B' is SENIOR 2, and the combination is the name of its level."""
    c = dict(row)
    number = re.search(r"\d+", c["grade"] or "") or re.match(r"\s*S(?:ENIOR)?\s*(\d+)", c["name"], re.I)
    c["grade_no"] = int(number.group(number.lastindex or 0)) if number else 99
    c["grade_shown"] = c["grade"] or (f"SENIOR {c['grade_no']}" if number else "")
    c["combination_shown"] = c["combination"] or (c.get("level_name") or "")
    return c


@app.route("/admin/classes/<int:class_id>/list")
@admin_required
def admin_class_list(class_id):
    """The class list: every student of one class. For the administrator only."""
    cls = class_facts(get_class(class_id))
    return render_template("admin_class_list.html", cls=cls, students=R.class_students(get_db(), class_id), term=pick_term())


@app.route("/admin/classes/<int:class_id>/list.pdf")
@admin_required
def admin_class_list_pdf(class_id):
    cls = class_facts(get_class(class_id))
    term = pick_term()
    log("Generated PDF", f"Class list: {cls['name']}")
    get_db().commit()
    buf = pdfs.class_list(R.load_settings(get_db()), cls, R.class_students(get_db(), class_id), term["name"] if term else "", logo=logo_path())
    return pdf_response(buf, f"Class_list_{cls['name']}")


@app.route("/admin/classes/<int:class_id>")
@admin_required
def admin_class(class_id):
    """Manage Class: the facts of the class on top, then the tabs Teachers (courses), Students and Settings."""
    cls = class_facts(get_class(class_id))
    tab = request.args.get("tab", "teachers")
    if tab not in ("teachers", "students", "settings"):
        tab = "teachers"
    return render_template(
        "admin_class.html", cls=cls, tab=tab, students=R.class_students(get_db(), class_id),
        n_marks=one("SELECT COUNT(*) FROM marks m JOIN students s ON s.id = m.student_id WHERE s.class_id = ?", (class_id,))[0],
        teachers=rows("SELECT id, full_name FROM users WHERE status = 'active' ORDER BY full_name COLLATE NOCASE"),
        levels=all_levels(),
        in_use=one("SELECT (SELECT COUNT(*) FROM allocations WHERE class_id = :c) + (SELECT COUNT(*) FROM assessments WHERE class_id = :c)", {"c": class_id})[0],
        subjects=rows("""SELECT s.*, a.teacher_id, u.full_name AS teacher_name,
                                (SELECT COUNT(*) FROM assessments x WHERE x.subject_id = s.id AND x.class_id = :c) AS n_assess,
                                EXISTS (SELECT 1 FROM removed_courses r WHERE r.subject_id = s.id AND r.class_id = :c) AS removed
                         FROM subjects s LEFT JOIN allocations a ON a.subject_id = s.id AND a.class_id = :c
                              LEFT JOIN users u ON u.id = a.teacher_id
                         WHERE s.level_id = :l ORDER BY """ + R.SUBJECT_ORDER, {"c": class_id, "l": cls["level_id"]}))


@app.route("/admin/classes/<int:class_id>/update", methods=["POST"])
@admin_required
def admin_class_update(class_id):
    cls = get_class(class_id)
    db = get_db()
    name = request.form.get("name", "").strip() or cls["name"]
    teacher_id = request.form.get("class_teacher_id", type=int)
    teacher = one("SELECT * FROM users WHERE id = ? AND status = 'active'", (teacher_id,)) if teacher_id else None
    level_id = cls["level_id"]
    wanted = one("SELECT * FROM levels WHERE id = ?", (request.form.get("level_id", type=int),))
    if wanted is not None and wanted["id"] != cls["level_id"]:
        in_use = one("SELECT (SELECT COUNT(*) FROM allocations WHERE class_id = :c) + (SELECT COUNT(*) FROM assessments WHERE class_id = :c)", {"c": class_id})[0]
        if in_use:
            flash(f"The level was not changed: {cls['name']} already has subject teachers or marks for the subjects of its current level. "
                  "Set those subjects to “not taught” first (marks must be deleted by deleting the tests), or create a new class.", "error")
        else:
            level_id = wanted["id"]
            log("Changed class level", f"{cls['name']}: {wanted['name']}")
    grade = " ".join(request.form.get("grade", cls["grade"]).split())[:40]
    combination = " ".join(request.form.get("combination", cls["combination"]).split())[:80]
    try:
        db.execute("UPDATE classes SET name = ?, class_teacher_id = ?, level_id = ?, grade = ?, combination = ? WHERE id = ?",
                   (name, teacher["id"] if teacher else None, level_id, grade, combination, class_id))
    except sqlite3.IntegrityError:
        flash("Another class already has that name.", "error")
        return redirect(url_for("admin_class", class_id=class_id, tab="settings"))
    if (teacher["id"] if teacher else None) != cls["class_teacher_id"]:
        log("Set class teacher", f"{name}: {teacher['full_name'] if teacher else 'none'}")
        if teacher:
            notify(teacher, f"You are now the class teacher of {name}",
                   [f"The administrator has made you the class teacher of {name} ({class_size(class_id)} students).",
                    class_teacher_can()])
    if name != cls["name"]:
        log("Renamed class", f"{cls['name']} -> {name}")
    db.commit()
    flash("Class details saved.", "success")
    return redirect(url_for("admin_class", class_id=class_id, tab="settings"))


@app.route("/admin/classes/<int:class_id>/delete", methods=["POST"])
@admin_required
def admin_class_delete(class_id):
    cls = get_class(class_id)
    n_students = one("SELECT COUNT(*) FROM students WHERE class_id = ?", (class_id,))[0]
    n_marks = one("SELECT COUNT(*) FROM marks m JOIN students s ON s.id = m.student_id WHERE s.class_id = ?", (class_id,))[0]
    get_db().execute("DELETE FROM classes WHERE id = ?", (class_id,))  # cascade: students, marks, remarks, tests, allocations
    log("Deleted class", f"{cls['name']}: {n_students} students and {n_marks} marks deleted")
    get_db().commit()
    flash(f"{cls['name']} was deleted" + (f", together with its {n_students} students and {n_marks} marks." if n_students else "."), "success")
    return redirect(url_for("admin_classes"))


@app.route("/admin/classes/<int:class_id>/allocations", methods=["POST"])
@admin_required
def admin_class_allocations(class_id):
    cls = get_class(class_id)
    db = get_db()
    teachers = {t["id"]: t["full_name"] for t in rows("SELECT id, full_name FROM users WHERE status = 'active'")}
    changed = 0
    given = {}                                      # teacher id -> subjects they were just given, for one email each
    for sub in rows("SELECT * FROM subjects WHERE level_id = ?", (cls["level_id"],)):
        chosen = request.form.get(f"teacher_{sub['id']}", type=int)
        current = one("SELECT * FROM allocations WHERE class_id = ? AND subject_id = ?", (class_id, sub["id"]))
        if current is not None and chosen == current["teacher_id"]:
            continue                      # unchanged (also keeps a teacher who was disabled later)
        if chosen not in teachers:
            chosen = None
        if current is None and chosen:
            db.execute("INSERT INTO allocations (class_id, subject_id, teacher_id) VALUES (?, ?, ?)", (class_id, sub["id"], chosen))
            db.execute("DELETE FROM removed_courses WHERE class_id = ? AND subject_id = ?", (class_id, sub["id"]))
            log("Assigned subject", f"{sub['name']} in {cls['name']} -> {teachers[chosen]}")
            given.setdefault(chosen, []).append(sub["name"])
            changed += 1
        elif current is not None and chosen is None:
            if "teacher_%d" % sub["id"] in request.form:
                db.execute("DELETE FROM allocations WHERE id = ?", (current["id"],))
                log("Removed subject from class", f"{sub['name']} in {cls['name']}")
                changed += 1
        elif current is not None and chosen != current["teacher_id"]:
            db.execute("UPDATE allocations SET teacher_id = ? WHERE id = ?", (chosen, current["id"]))
            log("Changed subject teacher", f"{sub['name']} in {cls['name']} -> {teachers[chosen]}")
            given.setdefault(chosen, []).append(sub["name"])
            changed += 1
    db.commit()
    size = class_size(class_id)
    for teacher_id, names in given.items():
        notify_subjects(teacher_id, [(n, cls["name"], size) for n in names])
    flash(f"Subject teachers saved ({changed} change{'s' if changed != 1 else ''}).", "success")
    return redirect(url_for("admin_class", class_id=class_id))


@app.route("/admin/classes/<int:class_id>/subjects/<int:subject_id>/remove", methods=["POST"])
@admin_required
def admin_class_subject_remove(class_id, subject_id):
    """Take one course off this class (and its reports). Marks already entered stay stored."""
    cls = get_class(class_id)
    row = one("""SELECT a.id, s.name, s.level_id, u.full_name AS teacher_name FROM subjects s
                 LEFT JOIN allocations a ON a.subject_id = s.id AND a.class_id = ? LEFT JOIN users u ON u.id = a.teacher_id
                 WHERE s.id = ?""", (class_id, subject_id))
    if row is None or (row["id"] is None and (row["level_id"] != cls["level_id"] or one(
            "SELECT 1 FROM removed_courses WHERE class_id = ? AND subject_id = ?", (class_id, subject_id)))):
        abort(404)
    n_marks = one("""SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id
                     WHERE x.class_id = ? AND x.subject_id = ?""", (class_id, subject_id))[0]
    get_db().execute("DELETE FROM allocations WHERE class_id = ? AND subject_id = ?", (class_id, subject_id))
    get_db().execute("INSERT OR IGNORE INTO removed_courses (class_id, subject_id) VALUES (?, ?)", (class_id, subject_id))
    log("Removed subject from class", f"{row['name']} in {cls['name']} (teacher was {row['teacher_name'] or 'not chosen'})")
    get_db().commit()
    flash(f"{row['name']} was removed from {cls['name']}. It is no longer on this class's report."
          + (f" Its {n_marks} marks stay stored and come back if you add the subject again." if n_marks else ""), "success")
    return redirect(url_for("admin_class", class_id=class_id))


@app.route("/admin/classes/<int:class_id>/subjects", methods=["POST"])
@admin_required
def admin_class_subject_add(class_id):
    """Add a subject to this class in one step. A subject that does not exist yet is created."""
    cls = get_class(class_id)
    db = get_db()
    back = redirect(url_for("admin_class", class_id=class_id) + "#add-course")
    name = " ".join(request.form.get("name", "").split())[:60]
    code = request.form.get("code", "").strip().upper()[:6]
    teacher = one("SELECT * FROM users WHERE id = ? AND status = 'active'",
                  (request.form.get("teacher_id", type=int),))
    if not name:
        flash("Enter the subject name.", "error")
        return back
    if teacher is None:
        flash("Choose the teacher who will teach this subject.", "error")
        return back

    if cls["level_id"] is None:
        flash("Choose the level of this class first (under Class details).", "error")
        return back
    level_id = cls["level_id"]
    subject = one("SELECT * FROM subjects WHERE level_id = ? AND name = ? COLLATE NOCASE", (level_id, name))
    if subject is None:
        max_mark = parse_number(request.form.get("max_mark", ""))
        if max_mark is None or not 0 < max_mark <= 1000:
            flash(f"{name} is a new subject, so enter what it is marked out of on the report (its maximum), for example 30.", "error")
            return back
        if code and one("SELECT 1 FROM subjects WHERE level_id = ? AND code = ?", (level_id, code)):
            flash(f"The short code {code} is already used by another subject of this level. Choose a different one.", "error")
            return back
        periods = parse_periods(request.form.get("periods"))
        if periods == "bad":
            flash("Periods per week must be a whole number, for example 3 (or leave it empty).", "error")
            return back
        code = code or free_subject_code(name, level_id)
        new_id = db.execute("INSERT INTO subjects (level_id, name, code, category, max_mark, sort_order, periods) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (level_id, name, code, clean_category(request.form.get("category")), max_mark,
                             next_subject_order(level_id), periods)).lastrowid
        subject = one("SELECT * FROM subjects WHERE id = ?", (new_id,))
        log("Added subject", f"{name} (max {max_mark:g})")
        created = True
        # a course added on one class's page is for that class: the other classes of the level do not get it on their reports
        db.execute("INSERT OR IGNORE INTO removed_courses (class_id, subject_id) SELECT id, ? FROM classes WHERE level_id = ? AND id != ?",
                   (new_id, level_id, class_id))
    else:
        created = False

    if one("SELECT 1 FROM allocations WHERE class_id = ? AND subject_id = ?", (class_id, subject["id"])):
        flash(f"{subject['name']} is already a subject of {cls['name']}. Change its teacher in the list above.", "error")
        return back
    db.execute("INSERT INTO allocations (class_id, subject_id, teacher_id) VALUES (?, ?, ?)",
               (class_id, subject["id"], teacher["id"]))
    db.execute("DELETE FROM removed_courses WHERE class_id = ? AND subject_id = ?", (class_id, subject["id"]))
    log("Assigned subject", f"{subject['name']} in {cls['name']} -> {teacher['full_name']}")
    db.commit()
    notify_subjects(teacher["id"], [(subject["name"], cls["name"], class_size(class_id))])
    note = f" It was also added to the subject list of {cls['level_name']}." if created else ""
    flash(f"{subject['name']} added to {cls['name']} with {teacher['full_name']} as teacher.{note}", "success")
    return back


def next_admission_no(db):
    n = db.execute("SELECT COALESCE(MAX(id), 0) FROM students").fetchone()[0] + 1
    while True:
        candidate = f"S{n:04d}"
        if not db.execute("SELECT 1 FROM students WHERE admission_no = ?", (candidate,)).fetchone():
            return candidate
        n += 1


def clean_gender(text):
    text = (text or "").strip().upper()
    return {"M": "Male", "MALE": "Male", "BOY": "Male", "F": "Female", "FEMALE": "Female", "GIRL": "Female"}.get(text, "")


def _import_path(token):
    return os.path.join(IMPORT_DIR, token + ".xlsx") if re.fullmatch(r"[0-9a-f]{32}", token or "") else None


def _clear_old_imports(keep=None):
    if not os.path.isdir(IMPORT_DIR):
        return
    for name in os.listdir(IMPORT_DIR):
        path = os.path.join(IMPORT_DIR, name)
        if path != keep and time.time() - os.path.getmtime(path) > 3600:
            os.remove(path)


@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    """Step 1: the admin uploads the Excel student lists and sees what an import would do. Nothing is changed yet."""
    if request.method == "GET":
        return render_template("admin_import.html", plan=None)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose the Excel file with the student lists first.", "error")
        return redirect(url_for("admin_import"))
    if not upload.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("The file must be an Excel workbook (.xlsx). If you have an old .xls file, open it in Excel and use Save As → .xlsx.", "error")
        return redirect(url_for("admin_import"))
    os.makedirs(IMPORT_DIR, exist_ok=True)
    _clear_old_imports()
    token = secrets.token_hex(16)
    path = _import_path(token)
    upload.save(path)
    try:
        parsed = importer.parse_workbook(path)
    except Exception:
        os.remove(path)
        flash("The file could not be read. Make sure it is the Excel student list (.xlsx) and not open in another program.", "error")
        return redirect(url_for("admin_import"))
    if not parsed["classes"]:
        os.remove(path)
        flash("No student list was found in the file. Each sheet needs the columns “Student Code” and “Names”.", "error")
        return redirect(url_for("admin_import"))
    plan = importer.build_plan(get_db(), parsed)
    year_exists = bool(plan["year"]) and one("SELECT 1 FROM terms WHERE year = ? COLLATE NOCASE", (plan["year"],)) is not None
    return render_template("admin_import.html", plan=plan, token=token, filename=upload.filename, year_exists=year_exists)


@app.route("/admin/import/confirm", methods=["POST"])
@admin_required
def admin_import_confirm():
    """Step 2: carry out the import that was shown in the preview."""
    path = _import_path(request.form.get("token"))
    if path is None or not os.path.exists(path):
        flash("That upload is no longer available. Please choose the file again.", "error")
        return redirect(url_for("admin_import"))
    db = get_db()
    try:
        plan = importer.build_plan(db, importer.parse_workbook(path))
        done = importer.apply_plan(db, plan, move=request.form.get("move") == "1", rename=request.form.get("rename") == "1")
    except Exception:
        db.rollback()
        flash("The import failed and nothing was changed. Please try again.", "error")
        return redirect(url_for("admin_import"))
    year_note = ""
    year = plan["year"]
    if request.form.get("add_year") == "1" and year and not one("SELECT 1 FROM terms WHERE year = ? COLLATE NOCASE", (year,)):
        first = one("SELECT COUNT(*) FROM terms")[0] == 0
        for n in (1, 2, 3):
            if not one("SELECT 1 FROM terms WHERE name = ? COLLATE NOCASE", (f"{year} TERM {n}",)):
                db.execute("INSERT INTO terms (name, year, is_current) VALUES (?, ?, ?)", (f"{year} TERM {n}", year, 1 if first and n == 1 else 0))
        log("Added academic year", f"{year} with 3 term(s), from the student list import")
        year_note = f" Academic year {year} was added" + ("." if first else " (press “Make current” under Academic years when the school moves to it).")
    summary = (f"{done['students']} students added, {done['classes']} classes created, {done['levels']} levels created, "
               f"{done['moved']} students moved to their new class, {done['renamed']} names corrected")
    log("Imported student lists", f"{plan['totals']['classes']} classes in the file: {summary}")
    db.commit()
    os.remove(path)
    flash(f"Import finished: {summary}.{year_note}", "success")
    empty = [x["name"] for x in rows("SELECT l.name FROM levels l WHERE NOT EXISTS (SELECT 1 FROM subjects s WHERE s.level_id = l.id) "
                                     "AND EXISTS (SELECT 1 FROM classes c WHERE c.level_id = l.id) ORDER BY l.name")]
    if empty:
        flash("These levels have classes but no subjects yet – add their subjects under Levels & subjects before teachers can be given subjects: "
              + ", ".join(empty), "error")
    return redirect(url_for("admin_classes"))


def students_url(class_id):
    """Where the list of a class's students lives: the class page for the admin, 'My students' for a class teacher."""
    if is_admin():
        return url_for("admin_class", class_id=class_id, tab="students")
    return url_for("class_students", class_id=class_id)


@app.route("/class/<int:class_id>/students", methods=["GET", "POST"])
@login_required
def class_students(class_id):
    """List and add students. Allowed for the admin and for the class teacher of this class."""
    cls = get_class(class_id)
    require_class_access(cls)
    db = get_db()
    if request.method == "GET":
        if is_admin():
            return redirect(students_url(class_id))
        return render_template("class_students.html", cls=cls, students=R.class_students(db, class_id))

    added, skipped = 0, []
    if request.form.get("bulk") is not None:
        # One student per line: "Full name"  or  "Full name, M"  or  "Full name, F, AdmissionNo"
        for line in request.form["bulk"].splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0]:
                continue
            adm = parts[2] if len(parts) > 2 and parts[2] else next_admission_no(db)
            try:
                db.execute("INSERT INTO students (class_id, admission_no, full_name, gender) VALUES (?, ?, ?, ?)",
                           (class_id, adm, parts[0][:80], clean_gender(parts[1] if len(parts) > 1 else "")))
                added += 1
            except sqlite3.IntegrityError:
                skipped.append(parts[0])
    else:
        name = " ".join(request.form.get("full_name", "").split())[:80]
        adm = request.form.get("admission_no", "").strip() or next_admission_no(db)
        if not name:
            flash("Enter the student's name.", "error")
        else:
            try:
                db.execute("INSERT INTO students (class_id, admission_no, full_name, gender) VALUES (?, ?, ?, ?)",
                           (class_id, adm, name, clean_gender(request.form.get("gender"))))
                added = 1
            except sqlite3.IntegrityError:
                skipped.append(name)
    if added:
        log("Added students", f"{added} added to {cls['name']}")
        flash(f"{added} student{'s' if added != 1 else ''} added to {cls['name']}.", "success")
    if skipped:
        flash("Not added because the admission number is already used: " + ", ".join(skipped), "error")
    db.commit()
    return redirect(students_url(class_id))


@app.route("/admin/students")
@admin_required
def admin_students():
    """Every student of the school in one list, with a search box. For the administrator."""
    q = " ".join(request.args.get("q", "").split())[:60]
    class_id = request.args.get("class_id", type=int)
    where, args = [], []
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(s.full_name LIKE ? ESCAPE '\\' OR s.admission_no LIKE ? ESCAPE '\\')")
        args += [like, like]
    if class_id:
        where.append("s.class_id = ?")
        args.append(class_id)
    sql = """SELECT s.*, c.name AS class_name, c.grade, c.combination, l.name AS level_name
             FROM students s JOIN classes c ON c.id = s.class_id LEFT JOIN levels l ON l.id = c.level_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    found = [class_facts_of_student(r) for r in rows(sql + " ORDER BY s.full_name COLLATE NOCASE", args)]
    found.sort(key=lambda r: (r["grade_no"], r["class_name"].lower(), r["full_name"].lower()))
    page_size = 100
    page = max(request.args.get("page", 1, type=int), 1)
    pages = max((len(found) + page_size - 1) // page_size, 1)
    page = min(page, pages)
    classes = sorted((class_facts(c) for c in rows("SELECT c.*, NULL AS level_name FROM classes c")),
                     key=lambda c: (c["grade_no"], c["name"].lower()))
    return render_template("admin_students.html", students=found[(page - 1) * page_size: page * page_size], total=len(found),
                           everyone=one("SELECT COUNT(*) FROM students")[0], q=q, class_id=class_id, classes=classes,
                           page=page, pages=pages, first=(page - 1) * page_size)


def class_facts_of_student(row):
    r = dict(row)
    facts = class_facts({"name": r["class_name"], "grade": r["grade"], "combination": r["combination"], "level_name": r["level_name"]})
    r["grade_no"], r["grade_shown"] = facts["grade_no"], facts["grade_shown"]
    return r


@app.route("/students/<int:sid>", methods=["GET", "POST"])
@login_required
def student_edit(sid):
    """Edit or delete one student. Allowed for the admin and for the student's class teacher."""
    st = one("SELECT * FROM students WHERE id = ?", (sid,))
    if st is None:
        abort(404)
    cls = get_class(st["class_id"])
    require_class_access(cls)
    db = get_db()
    n_marks = one("SELECT COUNT(*) FROM marks WHERE student_id = ?", (sid,))[0]
    locked_marks = one("""SELECT COUNT(*) FROM marks m JOIN assessments x ON x.id = m.assessment_id
                          JOIN terms t ON t.id = x.term_id WHERE m.student_id = ? AND t.is_locked = 1""", (sid,))[0]
    can_delete = is_admin() or not locked_marks
    if request.method == "POST":
        if request.form.get("action") == "delete":
            if not can_delete:
                flash(f"{st['full_name']} has marks in a locked term, so only the admin can delete this student.", "error")
                return redirect(url_for("student_edit", sid=sid))
            db.execute("DELETE FROM students WHERE id = ?", (sid,))        # cascade: marks and remarks
            log("Deleted student", f"{st['full_name']} ({st['admission_no']}) from {cls['name']}, with {n_marks} marks")
            db.commit()
            flash(f"{st['full_name']} was deleted" + (f", together with {n_marks} marks." if n_marks else "."), "success")
            return redirect(students_url(st["class_id"]))
        name = " ".join(request.form.get("full_name", "").split())[:80]
        adm = request.form.get("admission_no", "").strip()
        # Only the admin may move a student to another class.
        new_class = request.form.get("class_id", type=int) if is_admin() else st["class_id"]
        if not name or not adm or not one("SELECT 1 FROM classes WHERE id = ?", (new_class,)):
            flash("Name, admission number and class are all required.", "error")
        else:
            try:
                db.execute("UPDATE students SET full_name = ?, admission_no = ?, gender = ?, class_id = ? WHERE id = ?",
                           (name, adm, clean_gender(request.form.get("gender")), new_class, sid))
                log("Edited student", f"{name} ({adm}) in {cls['name']}")
                db.commit()
                flash("Student saved.", "success")
                return redirect(students_url(new_class))
            except sqlite3.IntegrityError:
                flash("Another student already has that admission number.", "error")
    return render_template("student.html", st=st, cls=cls, n_marks=n_marks, can_delete=can_delete,
                           back=students_url(st["class_id"]),
                           classes=rows("SELECT * FROM classes ORDER BY name COLLATE NOCASE") if is_admin() else [])


TEXT_SETTINGS = ("school_name", "school_pobox", "school_email", "school_tel", "head_name", "head_title", "report_title")


def save_logo(upload):
    """Store the uploaded school logo as data/logo.png. Returns an error message, or None when it worked."""
    try:
        from PIL import Image
        img = Image.open(upload.stream)
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "P") else "RGB")
        img.thumbnail((700, 700))
        img.save(LOGO_PATH, "PNG")
    except Exception:
        return "The logo could not be read. Use a PNG or JPG picture."
    return None


@app.route("/logo.png")
@login_required
def logo():
    if not logo_path():
        abort(404)
    return send_file(LOGO_PATH, mimetype="image/png", max_age=0)


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        errors = []
        tw = parse_number(request.form.get("test_weight", ""))
        pm = parse_number(request.form.get("pass_mark", ""))
        cm = parse_number(request.form.get("conduct_max", ""))
        if tw is None or not 0 <= tw <= 100:
            errors.append("The tests weight must be a number from 0 to 100.")
        if pm is None or not 0 <= pm <= 100:
            errors.append("The pass mark must be a number from 0 to 100.")
        if cm is None or not 0 <= cm <= 1000:
            errors.append("The conduct maximum must be a number (use 0 to leave conduct off the report).")
        if not request.form.get("school_name", "").strip():
            errors.append("Enter the school name.")
        # A number for the dashboard that the system cannot count itself. Empty = not shown as a number.
        numbers = {}
        for key, what in (("school_classrooms", "classrooms"),):
            typed = request.form.get(key, "").strip().replace(" ", "").replace(",", "")
            if typed and not re.fullmatch(r"[0-9]{1,6}", typed):
                errors.append(f"The number of {what} must be a whole number (or leave it empty).")
            numbers[key] = str(int(typed)) if re.fullmatch(r"[0-9]{1,6}", typed) else ""
        upload = request.files.get("logo")
        if not errors and upload and upload.filename:
            problem = save_logo(upload)
            if problem:
                errors.append(problem)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            if request.form.get("remove_logo") and not (upload and upload.filename) and logo_path():
                os.remove(LOGO_PATH)
            for key in TEXT_SETTINGS:
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, request.form.get(key, "").strip()[:120]))
            for key, value in numbers.items():
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            see = "1" if request.form.get("class_teacher_results") == "1" else "0"
            was = "1" if class_teachers_see_results() else "0"
            for key, value in (("test_weight", f"{tw:g}"), ("pass_mark", f"{pm:g}"), ("conduct_max", f"{cm:g}"),
                               ("report_detail", "1" if request.form.get("report_detail") == "1" else "0"),
                               ("class_teacher_results", see)):
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            log("Changed settings", f"Tests {tw:g}% / Exam {100 - tw:g}%, pass mark {pm:g}%, conduct out of {cm:g}")
            if see != was:
                log("Changed access", "Class teachers may now see the results of their own class and print its report cards" if see == "1"
                    else "Class teachers can no longer see other teachers' marks: results and report cards are for the administrator only")
            db.commit()
            flash("Settings saved.", "success")
            return redirect(url_for("admin_settings"))
    mail = mailer.config(db)
    mail["has_password"] = bool(mail.pop("mail_password"))        # the password itself is never shown again
    return render_template("admin_settings.html", cfg=R.load_settings(db), has_logo=bool(logo_path()), dbinfo=database_info(),
                           mail=mail, guess_url=site_url())


@app.route("/admin/settings/email", methods=["POST"])
@admin_required
def admin_settings_email():
    """Save the email account the system sends FROM, or send a test email."""
    db = get_db()
    back = redirect(url_for("admin_settings") + "#email")
    if request.form.get("action") == "test":
        cfg = mailer.config(db)
        to = (request.form.get("to") or g.user["email"] or "").strip()
        if not cfg["mail_host"]:
            flash("Save the email settings first.", "error")
        elif not EMAIL_RE.match(to.lower()):
            flash("Type the email address the test should go to.", "error")
        else:
            try:
                school = one("SELECT value FROM settings WHERE key = 'school_name'")["value"]
                mailer.send(cfg, to, f"{school}: test email", "This is a test from the school's marks system.\n"
                            f"If you can read this, email notifications work.\n\nThe system is at: {site_url()}\n", school)
                log("Email sent", f"to {to}: test email")
                db.commit()
                flash(f"The test email was sent to {to}. Check the inbox (and the spam folder).", "success")
            except Exception as problem:
                flash(f"The test email could NOT be sent: {problem}. Check the server, port, username and password, and that this computer has internet.", "error")
        return back
    port = request.form.get("mail_port", "").strip()
    security = request.form.get("mail_security")
    sender = request.form.get("mail_from", "").strip().lower()
    site = request.form.get("site_url", "").strip()
    errors = []
    if port and not (port.isdigit() and 0 < int(port) < 65536):
        errors.append("The port must be a number, for example 587.")
    if sender and not EMAIL_RE.match(sender):
        errors.append("The 'from' address is not a valid email address.")
    if site and not re.match(r"^https?://[^\s/]+", site):
        errors.append("The address of the system must start with http:// , for example http://192.168.1.20:5000/")
    if errors:
        for e in errors:
            flash(e, "error")
        return back
    values = {"mail_enabled": "1" if request.form.get("mail_enabled") == "1" else "0",
              "mail_host": request.form.get("mail_host", "").strip()[:120], "mail_port": port,
              "mail_security": security if security in ("starttls", "ssl", "none") else "starttls",
              "mail_user": request.form.get("mail_user", "").strip()[:120], "mail_from": sender,
              "site_url": (site.rstrip("/") + "/") if site else ""}
    if request.form.get("mail_password"):                       # an empty box keeps the password that is saved
        values["mail_password"] = request.form["mail_password"]
    for key, value in values.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    log("Changed email settings", f"{'on' if values['mail_enabled'] == '1' else 'off'}, server {values['mail_host'] or '-'}")
    db.commit()
    flash("Email settings saved." + (" Use “Send a test email” to check that they work." if values["mail_enabled"] == "1" else ""), "success")
    return back


@app.route("/admin/reports")
@admin_required
def admin_reports():
    """Every class with how far its teachers are, and the buttons to generate its reports."""
    term = pick_term()
    classes, teachers = [], {}
    if term:
        cfg = R.load_settings(get_db())
        saved = {p["allocation_id"]: p for p in marks_progress(term["id"])}       # tests, exam, marks typed, last save
        for cls in rows("""SELECT c.*, u.full_name AS class_teacher_name, l.name AS level_name FROM classes c
                           LEFT JOIN users u ON u.id = c.class_teacher_id LEFT JOIN levels l ON l.id = c.level_id
                           ORDER BY c.name COLLATE NOCASE"""):
            res = R.class_results(get_db(), cls["id"], term["id"], cfg)
            total = len(res["students"])
            waiting = []
            for sub in res["subjects"]:
                have = res["per_subject"][sub["id"]]["ranked"]             # students who have a mark in this subject
                info = saved.get(sub["allocation_id"], {})
                typed = info.get("n_marks", 0)
                item = {"name": sub["name"], "class_name": cls["name"], "teacher": sub["teacher_name"], "have": have,
                        "students": total, "allocation_id": sub["allocation_id"], "tests": info.get("n_tests", 0),
                        "exam": bool(info.get("n_exams")), "typed": typed, "last": info.get("last_update"),
                        "state": "complete" if total and have == total else ("started" if typed else "nothing")}
                if have < total:
                    waiting.append(item)
                # the same facts, grouped by teacher: who has entered marks and who has not
                t = teachers.setdefault(sub["teacher_id"], {"id": sub["teacher_id"], "name": sub["teacher_name"], "subjects": []})
                if total and sub["teacher_id"] is not None:
                    t["subjects"].append(item)
            done = len(res["subjects"]) - len(waiting)
            if not total:
                state = "no students"
            elif not res["subjects"]:
                state = "no subjects"
            elif not waiting:
                state = "ready"
            elif done or any(w["typed"] for w in waiting):
                state = "in progress"
            else:
                state = "not started"
            classes.append({"cls": cls, "students": total, "subjects": len(res["subjects"]), "done": done,
                            "waiting": waiting, "state": state,
                            "percent": round(done / len(res["subjects"]) * 100) if res["subjects"] else 0})
    people = []
    for t in teachers.values():
        if not t["subjects"]:
            continue
        t["complete"] = sum(1 for x in t["subjects"] if x["state"] == "complete")
        t["started"] = sum(1 for x in t["subjects"] if x["state"] == "started")
        t["nothing"] = sum(1 for x in t["subjects"] if x["state"] == "nothing")
        t["last"] = max((x["last"] for x in t["subjects"] if x["last"]), default=None)
        t["state"] = ("all entered" if t["complete"] == len(t["subjects"])
                      else "nothing entered" if t["nothing"] == len(t["subjects"]) else "not finished")
        people.append(t)
    order = {"nothing entered": 0, "not finished": 1, "all entered": 2}
    people.sort(key=lambda t: (order[t["state"]], t["name"].lower()))
    return render_template("admin_reports.html", term=term, classes=classes, people=people,
                           ready=sum(1 for c in classes if c["state"] == "ready"),
                           counts={k: sum(1 for t in people if t["state"] == k) for k in order})


def database_info():
    folder = os.path.join(DATA_DIR, "backups")
    copies = sorted(f for f in os.listdir(folder) if f.endswith(".db")) if os.path.isdir(folder) else []
    count = lambda table: one(f"SELECT COUNT(*) FROM {table}")[0]
    return {"path": DB_PATH, "folder": DATA_DIR, "size_kb": max(1, os.path.getsize(DB_PATH) // 1024),
            "students": count("students"), "marks": count("marks"), "staff": count("users"), "classes": count("classes"),
            "backups": len(copies), "last_backup": copies[-1] if copies else None, "backup_folder": folder}


@app.route("/admin/backup")
@admin_required
def admin_backup():
    """Download a complete copy of the database (one file) to keep somewhere safe."""
    temp = os.path.join(DATA_DIR, "download-" + secrets.token_hex(8) + ".tmp")
    try:
        database.safe_copy(DB_PATH, temp)
        with open(temp, "rb") as f:
            data = io.BytesIO(f.read())
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    log("Downloaded backup", f"{len(data.getvalue()) // 1024} KB")
    get_db().commit()
    return send_file(data, mimetype="application/octet-stream", as_attachment=True,
                     download_name="school-backup-" + datetime.now().strftime("%Y-%m-%d-%H%M") + ".db")


@app.route("/admin/restore", methods=["POST"])
@admin_required
def admin_restore():
    """Replace the whole database by an uploaded school.db / backup file. The present database is
    saved in backups/ first, so the restore itself can be undone."""
    upload = request.files.get("file")
    back = redirect(url_for("admin_settings") + "#database")
    if not upload or not upload.filename:
        flash("Choose the database file (school.db or a backup) first.", "error")
        return back
    if request.form.get("confirm") != "yes":
        flash("Tick the box to confirm that the present data may be replaced.", "error")
        return back
    temp = os.path.join(DATA_DIR, "restore-" + secrets.token_hex(8) + ".tmp")
    upload.save(temp)
    try:
        problem = database.check_database_file(temp)
        if problem:
            flash("Not restored. " + problem, "error")
            return back
        folder = os.path.join(DATA_DIR, "backups")
        os.makedirs(folder, exist_ok=True)
        saved = os.path.join(folder, "before-restore-" + datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".db")
        who = g.user["full_name"]
        con = g.pop("db", None)
        if con is not None:
            con.close()
        database.safe_copy(DB_PATH, saved)                      # 1. keep what is there now
        database.safe_copy(temp, DB_PATH)                       # 2. put the uploaded database in its place
        database.init(DB_PATH)                                  # 3. bring an older database up to date
        db = get_db()
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance', ?)", (secrets.token_hex(16),))
        db.execute("INSERT INTO activity (user_id, user_name, action, details, created_at) VALUES (NULL, ?, 'Restored database', ?, ?)",
                   (who, f"from the file {upload.filename}; the previous data was kept as backups/{os.path.basename(saved)}", database.now()))
        db.commit()
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    session.clear()                                             # the accounts are now those of the restored database
    flash("The database was restored. Log in with an account of the restored database. "
          f"The data from before was kept as {os.path.basename(saved)} in the backups folder.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- teachers
@app.route("/teacher")
@login_required
def teacher_dashboard():
    if is_admin():
        return redirect(url_for("admin_dashboard"))
    term = pick_term()
    my_classes = rows("""SELECT c.*, (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS n_students,
                                (SELECT COUNT(*) FROM allocations a WHERE a.class_id = c.id) AS n_subjects
                         FROM classes c WHERE c.class_teacher_id = ? ORDER BY c.name COLLATE NOCASE""", (g.user["id"],))
    return render_template("teacher_dashboard.html", term=term, terms=all_terms(), my_classes=my_classes,
                           progress=marks_progress(term["id"], g.user["id"]) if term else [])


def marks_context(alloc, term):
    db = get_db()
    cfg = R.load_settings(db)
    students = R.class_students(db, alloc["class_id"])
    sr = R.subject_results(db, alloc["class_id"], alloc["subject_id"], alloc["subject_max"], term["id"], students, cfg)
    return cfg, students, sr


@app.route("/marks/<int:alloc_id>", methods=["GET", "POST"])
@login_required
def marks(alloc_id):
    alloc = get_allocation(alloc_id)
    term = pick_term()
    if term is None:
        flash("The admin has not created a term yet, so marks cannot be entered.", "error")
        return redirect(url_for("home"))
    db = get_db()
    cfg, students, sr = marks_context(alloc, term)
    editable = can_edit_term(term)
    bad = {}

    if request.method == "POST":
        if not editable:
            abort(403)
        saved = cleared = 0
        for a in sr["tests"] + ([sr["exam"]] if sr["exam"] else []):
            for s in students:
                field = f"m_{a['id']}_{s['id']}"
                if field not in request.form:
                    continue
                raw = request.form[field].strip()
                old = sr["marks"].get((a["id"], s["id"]))
                if raw == "":
                    if old is not None:
                        db.execute("DELETE FROM marks WHERE assessment_id = ? AND student_id = ?", (a["id"], s["id"]))
                        cleared += 1
                    continue
                value = parse_number(raw)
                if value is None or value < 0 or value > a["max_mark"]:
                    bad[field] = raw
                    continue
                if old is None or abs(old - value) > 1e-9:
                    db.execute(
                        """INSERT INTO marks (assessment_id, student_id, score, updated_by, updated_at) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT (assessment_id, student_id) DO UPDATE SET score = excluded.score,
                               updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                        (a["id"], s["id"], value, g.user["id"], database.now()))
                    saved += 1
        if saved or cleared:
            log("Saved marks", f"{alloc['subject_name']} - {alloc['class_name']} - {term['name']}: "
                               f"{saved} entered/changed, {cleared} cleared")
        db.commit()
        if bad:
            flash(f"{len(bad)} mark(s) were NOT saved because they are not a number between 0 and the maximum. "
                  "They are shown in red below.", "error")
        if saved or cleared or not bad:
            flash(f"Marks saved: {saved} entered or changed, {cleared} cleared.", "success")
        if not bad:
            return redirect(url_for("marks", alloc_id=alloc_id, term=term["id"]))
        cfg, students, sr = marks_context(alloc, term)

    return render_template("marks.html", alloc=alloc, term=term, terms=all_terms(), cfg=cfg, students=students,
                           sr=sr, editable=editable, bad=bad)


@app.route("/marks/<int:alloc_id>/assessments", methods=["POST"])
@login_required
def assessment_add(alloc_id):
    alloc = get_allocation(alloc_id)
    term = pick_term()
    if term is None or not can_edit_term(term):
        abort(403)
    kind = "exam" if request.form.get("kind") == "exam" else "test"
    name = request.form.get("name", "").strip()[:40]
    max_mark = parse_number(request.form.get("max_mark", ""))
    back = redirect(url_for("marks", alloc_id=alloc_id, term=term["id"]))
    key = (alloc["class_id"], alloc["subject_id"], term["id"])
    if max_mark is None or max_mark <= 0 or max_mark > 1000:
        flash("'Marked out of' must be a number greater than 0.", "error")
        return back
    if kind == "exam":
        if one("SELECT 1 FROM assessments WHERE class_id = ? AND subject_id = ? AND term_id = ? AND kind = 'exam'", key):
            flash("This subject already has an exam for this term.", "error")
            return back
        name = name or "Exam"
    else:
        n = one("SELECT COUNT(*) FROM assessments WHERE class_id = ? AND subject_id = ? AND term_id = ? AND kind = 'test'", key)[0]
        name = name or f"Test {n + 1}"
    get_db().execute("INSERT INTO assessments (class_id, subject_id, term_id, name, kind, max_mark) VALUES (?, ?, ?, ?, ?, ?)",
                     key + (name, kind, max_mark))
    log("Added " + kind, f"{name} (out of {max_mark:g}) - {alloc['subject_name']} - {alloc['class_name']} - {term['name']}")
    get_db().commit()
    flash(f"{name} added. Type the marks in its column and press Save marks.", "success")
    return back


@app.route("/assessments/<int:aid>/<action>", methods=["POST"])
@login_required
def assessment_action(aid, action):
    a = one("SELECT * FROM assessments WHERE id = ?", (aid,))
    if a is None:
        abort(404)
    link = one("SELECT id FROM allocations WHERE class_id = ? AND subject_id = ?", (a["class_id"], a["subject_id"]))
    if link is None:
        abort(404)
    alloc = get_allocation(link["id"])          # also checks permission
    term = one("SELECT * FROM terms WHERE id = ?", (a["term_id"],))
    if not can_edit_term(term):
        abort(403)
    db = get_db()
    where = f"{alloc['subject_name']} - {alloc['class_name']} - {term['name']}"
    if action == "delete":
        n = one("SELECT COUNT(*) FROM marks WHERE assessment_id = ?", (aid,))[0]
        db.execute("DELETE FROM assessments WHERE id = ?", (aid,))
        log("Deleted " + a["kind"], f"{a['name']} with {n} marks - {where}")
        flash(f"{a['name']} and its {n} marks were deleted.", "success")
    elif action == "edit":
        name = request.form.get("name", "").strip()[:40] or a["name"]
        max_mark = parse_number(request.form.get("max_mark", ""))
        highest = one("SELECT MAX(score) FROM marks WHERE assessment_id = ?", (aid,))[0] or 0
        if max_mark is None or max_mark <= 0 or max_mark > 1000:
            flash("'Marked out of' must be a number greater than 0.", "error")
        elif max_mark < highest:
            flash(f"A student already has {highest:g} in {a['name']}, so it cannot be marked out of {max_mark:g}.", "error")
        else:
            db.execute("UPDATE assessments SET name = ?, max_mark = ? WHERE id = ?", (name, max_mark, aid))
            log("Edited " + a["kind"], f"{a['name']} -> {name} (out of {max_mark:g}) - {where}")
            flash("Saved.", "success")
    else:
        abort(400, "Unknown action.")
    db.commit()
    return redirect(url_for("marks", alloc_id=alloc["id"], term=term["id"]))


@app.route("/marks/<int:alloc_id>/marksheet.pdf")
@login_required
def subject_pdf(alloc_id):
    alloc = get_allocation(alloc_id)
    term = pick_term()
    if term is None:
        abort(404)
    cfg, students, sr = marks_context(alloc, term)
    buf = pdfs.subject_marksheet(sr, students, cfg, {"name": alloc["class_name"]}, {"name": alloc["subject_name"]},
                                 alloc["teacher_name"], term, logo=logo_path())
    log("Generated PDF", f"Subject marksheet: {alloc['subject_name']} - {alloc['class_name']} - {term['name']}")
    get_db().commit()
    return pdf_response(buf, f"{alloc['subject_name']}_{alloc['class_name']}_{term['name']}")


# --------------------------------------------------------------------------- marks sheets in Excel
def _sheet_name(*parts):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", "_".join(parts)).strip("_")


def course_sheet(alloc, term):
    db = get_db()
    students = R.class_students(db, alloc["class_id"])
    assessments = rows("SELECT * FROM assessments WHERE class_id = ? AND subject_id = ? AND term_id = ? ORDER BY kind = 'exam', id",
                       (alloc["class_id"], alloc["subject_id"], term["id"]))
    marks = {}
    for m in rows("""SELECT m.assessment_id, m.student_id, m.score FROM marks m JOIN assessments a ON a.id = m.assessment_id
                     WHERE a.class_id = ? AND a.subject_id = ? AND a.term_id = ?""", (alloc["class_id"], alloc["subject_id"], term["id"])):
        marks[(m["assessment_id"], m["student_id"])] = m["score"]
    school = one("SELECT value FROM settings WHERE key = 'school_name'")
    return students, assessments, marks, (school["value"] if school else "")


def course_of(info):
    """The course and term a tab of an uploaded file belongs to: (course, term) or an error message."""
    if "class" in info:
        cls = one("SELECT * FROM classes WHERE id = ?", (info["class"],))
        sub = one("SELECT * FROM subjects WHERE id = ?", (info["subject"],))
        term = one("SELECT * FROM terms WHERE id = ?", (info["term"],))
    else:
        cls = one("SELECT * FROM classes WHERE name = ? COLLATE NOCASE", (info["class_name"],))
        sub = one("SELECT * FROM subjects WHERE name = ? COLLATE NOCASE AND level_id = ?", (info["subject_name"], cls["level_id"])) if cls else None
        term = one("SELECT * FROM terms WHERE name = ? COLLATE NOCASE", (info["term_name"],))
    if cls is None or sub is None or term is None:
        return None, None, "The class, the course or the term of this sheet is no longer in the system."
    course = next((c for c in class_courses(cls["id"]) if c["subject_id"] == sub["id"]), None)
    if course is None:
        link = one("SELECT id FROM allocations WHERE class_id = ? AND subject_id = ?", (cls["id"], sub["id"]))
        if link is None:
            return None, None, f"{sub['name']} is not a course of {cls['name']} any more."
        course = dict(get_allocation(link["id"]))
    return course, term, None


def import_tab(info, columns, sheet_rows, course, term):
    """Bring the marks of one course tab into the system. Everything is checked first: if one mark is wrong,
    nothing of this course is saved. Returns (ok, [messages])."""
    label = f"{course['subject_name']} - {course['class_name']}"
    if not can_edit_term(term):
        return False, [f"{term['name']} is locked."]
    students, assessments, marks, _ = course_sheet(course, term)
    errors, changes = marks_excel.plan(info, columns, sheet_rows, course, term, students, assessments, marks)
    if errors:
        get_db().rollback()
        return False, errors
    db = get_db()
    new_ids = []
    key = (course["class_id"], course["subject_id"], term["id"])
    for letter, name, kind, mx in changes["new"]:
        cur = db.execute("INSERT INTO assessments (class_id, subject_id, term_id, name, kind, max_mark) VALUES (?, ?, ?, ?, ?, ?)",
                         key + (name, kind, mx))
        new_ids.append(cur.lastrowid)
        log("Added " + kind, f"{name} (out of {mx:g}, from Excel) - {label} - {term['name']}")
    aid = lambda k: k[1] if k[0] == "old" else new_ids[k[1]]
    for k, sid, score in changes["set"]:
        db.execute("""INSERT INTO marks (assessment_id, student_id, score, updated_by, updated_at) VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT (assessment_id, student_id) DO UPDATE SET score = excluded.score,
                          updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                   (aid(k), sid, score, g.user["id"], database.now()))
    for k, sid in changes["clear"]:
        db.execute("DELETE FROM marks WHERE assessment_id = ? AND student_id = ?", (aid(k), sid))
    n_set, n_clear = len(changes["set"]), len(changes["clear"])
    if n_set or n_clear or changes["new"]:
        log("Imported marks from Excel", f"{label} - {term['name']}: {n_set} entered/changed, {n_clear} cleared, {len(changes['new'])} new test(s)")
    db.commit()
    msgs = [f"{n_set} entered or changed, {n_clear} cleared, for {changes['students']} students."
            + (f" New: {', '.join(n for _, n, _, _ in changes['new'])}." if changes["new"] else "")]
    if changes["unknown"]:
        msgs.append("NOT in " + course["class_name"] + " in the system, so their marks were left out: "
                    + "; ".join(f"row {r} {name or ''} ({code})".replace("  ", " ") for r, code, name in changes["unknown"][:8])
                    + (" ..." if len(changes["unknown"]) > 8 else "")
                    + ". If such a student belongs to the class, add or move them under Students, then import the file again.")
    if changes["missing"]:
        names = ", ".join(s["full_name"] for s in changes["missing"][:5]) + (" ..." if len(changes["missing"]) > 5 else "")
        msgs.append(f"{len(changes['missing'])} student(s) of the class are not in the sheet, so their marks were not touched: {names}")
    return True, msgs


def import_file(data, alloc=None, term=None):
    """Every course tab of one uploaded Excel file. With `alloc`/`term` (a teacher's own course page) only the
    tab of that course is taken, and it must be for that term. Returns [(ok, label, [messages])]."""
    try:
        tabs = marks_excel.read_all(data, class_only=True)
    except ValueError as e:
        return [(False, "", [str(e)])]
    results = []
    for tab, info, columns, sheet_rows, error in tabs:
        if error:
            results.append((False, f"tab {tab}", [error]))
            continue
        if alloc is not None:
            same = (info["class"] == alloc["class_id"] and info["subject"] == alloc["subject_id"]) if "class" in info else \
                   (info["class_name"].lower() == alloc["class_name"].lower() and info["subject_name"].lower() == alloc["subject_name"].lower())
            if not same:
                continue
            course, course_term = dict(alloc), term
            if info.get("term", term["id"]) != term["id"] or info.get("term_name", term["name"]).strip().lower() != term["name"].lower():
                results.append((False, f"{alloc['subject_name']} - {alloc['class_name']}", [
                    f"This sheet was made for another term. You are working in {term['name']}: switch to the term of the sheet first, "
                    "or download a new sheet for this term."]))
                continue
        else:
            course, course_term, problem = course_of(info)
            if problem:
                results.append((False, f"tab {tab}", [problem]))
                continue
        if not columns:
            results.append((True, f"{course['subject_name']} - {course['class_name']}", ["no marks in it yet."]))
            continue
        ok, msgs = import_tab(info, columns, sheet_rows, course, course_term)
        if ok and info.get("from_summary"):
            msgs[0] = "taken from the Summary tab (its own tab was empty): " + msgs[0]
        results.append((ok, f"{course['subject_name']} - {course['class_name']}", msgs))
    if alloc is not None and not results:
        results.append((False, "", [f"This file is for another course: it has no sheet for {alloc['subject_name']} in {alloc['class_name']}. "
                                    "Upload the sheet of this course (or the class workbook of this class)."]))
    return results


def excel_only(upload):
    """Only Excel files (.xlsx) are taken. Returns a message when the file is something else."""
    if not upload.filename.lower().endswith(".xlsx"):
        return (f"{upload.filename}: not an Excel file. Only Excel files (.xlsx) are accepted. From Google Sheets: "
                "File > Download > Microsoft Excel (.xlsx).")
    return None


def class_courses(class_id):
    """Every course on the report of a class, with its teacher if one is chosen: as allocation-like rows."""
    found = rows("""SELECT a.id, c.id AS class_id, s.id AS subject_id, c.name AS class_name, s.name AS subject_name, s.code AS subject_code,
                          s.max_mark AS subject_max, u.full_name AS teacher_name
                   FROM classes c JOIN subjects s ON s.level_id = c.level_id
                        LEFT JOIN allocations a ON a.class_id = c.id AND a.subject_id = s.id LEFT JOIN users u ON u.id = a.teacher_id
                   WHERE c.id = ? AND NOT EXISTS (SELECT 1 FROM removed_courses r WHERE r.class_id = c.id AND r.subject_id = s.id)
                   ORDER BY """ + R.SUBJECT_ORDER, (class_id,))
    return [dict(r) for r in found]


@app.route("/admin/classes/<int:class_id>/workbook.xlsx")
@admin_required
def class_workbook(class_id):
    """The class workbook: one Excel file with a tab for every course of the class and a Summary tab."""
    cls = get_class(class_id)
    term = pick_term()
    if term is None:
        abort(404)
    courses, students, school = [], R.class_students(get_db(), class_id), ""
    for course in class_courses(class_id):
        students, assessments, marks, school = course_sheet(course, term)
        courses.append((course, assessments, marks))
    if not courses:
        flash(f"{cls['name']} has no courses yet (choose its level first).", "error")
        return redirect(url_for("admin_sheets"))
    buf = marks_excel.build_class(school, cls, term, students, courses)
    log("Downloaded marks sheets", f"Class workbook {cls['name']} - {term['name']}: {len(courses)} courses")
    get_db().commit()
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True,
                     download_name=_sheet_name("Marks", cls["name"], term["name"]) + ".xlsx")


@app.route("/admin/sheets")
@admin_required
def admin_sheets():
    """Class workbooks for the whole school: download them (for Excel or Google Sheets) and bring them back."""
    term = pick_term()
    classes = rows("""SELECT c.*, (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS n_students,
                             (SELECT COUNT(*) FROM subjects s WHERE s.level_id = c.level_id
                                 AND NOT EXISTS (SELECT 1 FROM removed_courses r WHERE r.class_id = c.id AND r.subject_id = s.id)) AS n_courses
                      FROM classes c ORDER BY c.name COLLATE NOCASE""")
    classes = sorted((class_facts(c) for c in classes), key=lambda c: (c["grade_no"], c["name"].lower()))
    return render_template("admin_sheets.html", term=term, terms=all_terms(), classes=classes)


@app.route("/admin/sheets/import", methods=["POST"])
@admin_required
def admin_sheets_import():
    """The admin brings back filled-in Excel files (class workbooks or course sheets), one or several at once."""
    uploads = [u for u in request.files.getlist("files") if u and u.filename]
    if not uploads:
        flash("Choose the filled-in Excel files (.xlsx) first.", "error")
        return redirect(url_for("admin_sheets"))
    done = failed = 0
    report = []
    for up in uploads:
        problem = excel_only(up)
        if problem:
            failed += 1
            report.append(("error", problem))
            continue
        for ok, label, msgs in import_file(up.read(marks_excel.MAX_SIZE + 1)):
            if ok:
                done += 1
                report.append(("success", f"{label}: " + " ".join(msgs)))
            else:
                failed += 1
                report.append(("error", f"{label or up.filename} - NOT imported: " + " | ".join(msgs[:6])
                                        + (f" | ... and {len(msgs) - 6} more" if len(msgs) > 6 else "")))
    flash(f"{done} course(s) imported, {failed} not imported." + (" Correct what is listed in Excel or Google Sheets and import the file again; "
          "each course is imported completely or not at all, and importing again only brings in what changed." if failed else ""),
          "success" if not failed else "error")
    for cat, text_ in report:
        flash(text_, cat)
    return redirect(url_for("admin_sheets"))


# --------------------------------------------------------------------------- class results & reports
def load_remarks(class_id, term_id, cfg=None):
    """Remarks per student. With `cfg`, a student with no conduct mark yet gets the full conduct mark
    (conduct starts at the maximum and the class teacher lowers it when needed)."""
    out = {r["student_id"]: dict(r) for r in rows(
        "SELECT r.* FROM remarks r JOIN students s ON s.id = r.student_id WHERE s.class_id = ? AND r.term_id = ?",
        (class_id, term_id))}
    if cfg is not None and cfg["conduct_max_num"] > 0:
        full = pdfs.num(cfg["conduct_max_num"])
        for st in R.class_students(get_db(), class_id):
            entry = out.setdefault(st["id"], {"student_id": st["id"], "conduct": "", "days_absent": "", "remark": ""})
            if not entry["conduct"]:
                entry["conduct"] = full
    return out


@app.route("/class/<int:class_id>/results")
@login_required
def class_results(class_id):
    cls = get_class(class_id)
    require_class_access(cls)
    term = pick_term()
    if term is None:
        flash("The admin has not created a term yet.", "error")
        return redirect(url_for("home"))
    db = get_db()
    if can_see_results(cls):
        res = R.class_results(db, class_id, term["id"])
        students, cfg, own = res["students"], res["cfg"], []
    else:
        # A class teacher: the students with their conduct and remarks. No mark of another teacher's
        # subject is read here at all, and so no total, percentage or position either.
        res, students, cfg = None, R.class_students(db, class_id), R.load_settings(db)
        own = rows("""SELECT a.id, s.name FROM allocations a JOIN subjects s ON s.id = a.subject_id
                      WHERE a.class_id = ? AND a.teacher_id = ? ORDER BY """ + R.SUBJECT_ORDER, (class_id, g.user["id"]))
    return render_template("class_results.html", cls=cls, term=term, terms=all_terms(), res=res, students=students, cfg=cfg,
                           own_subjects=own, remarks=load_remarks(class_id, term["id"]), editable=can_edit_term(term))


@app.route("/class/<int:class_id>/remarks", methods=["POST"])
@login_required
def class_remarks(class_id):
    cls = get_class(class_id)
    require_class_access(cls)
    term = pick_term()
    if term is None or not can_edit_term(term):
        abort(403)
    db = get_db()
    conduct_max = R.load_settings(db)["conduct_max_num"]
    old = load_remarks(class_id, term["id"])
    bad = []
    for s in R.class_students(db, class_id):
        if f"remark_{s['id']}" not in request.form:
            continue
        raw = request.form.get(f"conduct_{s['id']}", "").strip()
        conduct = ""
        if raw:
            value = parse_number(raw)
            if value is None or value < 0 or value > conduct_max:
                bad.append(s["full_name"])
                conduct = old[s["id"]]["conduct"] if s["id"] in old else ""     # keep what was there
            else:
                conduct = pdfs.num(value)
        db.execute("INSERT OR REPLACE INTO remarks (student_id, term_id, conduct, days_absent, remark) VALUES (?, ?, ?, ?, ?)",
                   (s["id"], term["id"], conduct, request.form.get(f"absent_{s['id']}", "").strip()[:6],
                    request.form.get(f"remark_{s['id']}", "").strip()[:300]))
    if bad:
        flash(f"Conduct must be a number from 0 to {conduct_max:g}. Not changed for: " + ", ".join(bad), "error")
    log("Saved class teacher remarks", f"{cls['name']} - {term['name']}")
    db.commit()
    flash("Conduct and remarks saved. They will appear on the report cards.", "success")
    return redirect(url_for("class_results", class_id=class_id, term=term["id"]) + "#remarks")


@app.route("/class/<int:class_id>/marksheet.pdf")
@login_required
def class_marksheet_pdf(class_id):
    cls = get_class(class_id)
    require_results_access(cls)
    term = pick_term()
    if term is None:
        abort(404)
    res = R.class_results(get_db(), class_id, term["id"])
    log("Generated PDF", f"Class marksheet: {cls['name']} - {term['name']}")
    get_db().commit()
    return pdf_response(pdfs.class_marksheet(res, cls, term, logo=logo_path()), f"Marksheet_{cls['name']}_{term['name']}")


@app.route("/class/<int:class_id>/reports.pdf")
@login_required
def report_cards_pdf(class_id):
    cls = get_class(class_id)
    require_results_access(cls)
    term = pick_term()
    if term is None:
        abort(404)
    res = R.class_results(get_db(), class_id, term["id"])
    only = request.args.get("student", type=int)
    ids = {s["id"] for s in res["students"]}
    label = f"all {len(ids)} students"
    name = f"Reports_{cls['name']}_{term['name']}"
    if only:
        if only not in ids:
            abort(404)
        student = next(s for s in res["students"] if s["id"] == only)
        ids, label = {only}, student["full_name"]
        name = f"Report_{student['full_name']}_{term['name']}"
    log("Generated PDF", f"Report card: {cls['name']} - {term['name']} - {label}")
    get_db().commit()
    return pdf_response(pdfs.report_cards(res, cls, term, ids, load_remarks(class_id, term["id"], res["cfg"]), logo=logo_path()), name)


def periodical_settings():
    """The periodical report counts the tests only (everything entered so far in the term), not the exam."""
    cfg = R.load_settings(get_db())
    cfg.update(test_weight_num=100.0, exam_weight_num=0.0, detail=False, report_title="PERIODIC REPORT ( All Periods )")    # the title of the school's own periodic report
    return cfg


@app.route("/class/<int:class_id>/periodical.pdf")
@login_required
def periodical_pdf(class_id):
    """Periodical report: each subject's tests of the term (no exam), out of the subject's maximum. For a class or one student."""
    cls = get_class(class_id)
    require_results_access(cls)
    term = pick_term()
    if term is None:
        abort(404)
    res = R.class_results(get_db(), class_id, term["id"], periodical_settings())
    only = request.args.get("student", type=int)
    ids = {s["id"] for s in res["students"]}
    label, name = f"all {len(ids)} students", f"Periodical_reports_{cls['name']}_{term['name']}"
    if only:
        if only not in ids:
            abort(404)
        student = next(s for s in res["students"] if s["id"] == only)
        ids, label, name = {only}, student["full_name"], f"Periodical_report_{student['full_name']}_{term['name']}"
    log("Generated PDF", f"Periodical report: {cls['name']} - {term['name']} - {label}")
    get_db().commit()
    return pdf_response(pdfs.report_cards(res, cls, term, ids, load_remarks(class_id, term["id"], res["cfg"]), logo=logo_path()), name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    address = lan_address() or "<this computer's IP address>"
    print("\n  School Marks System is running.")
    print(f"  On this computer open:                             http://localhost:{port}")
    print(f"  Other computers/phones on the same network open:   http://{address}:{port}")
    print(f"  The database is kept in:                           {DATA_DIR}")
    print("  Keep this window open. Close it (or press Ctrl+C) to stop.\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False)
