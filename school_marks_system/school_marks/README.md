# School Marks System

A web system for a school to record student marks and print them as PDF.

- **Admin** – adds, updates and deletes staff, classes, subjects and students; approves staff who
  register; chooses the class teacher of each class and the teacher of each subject; watches
  everyone's progress and the activity log; sets the school details and calculation rules.
- **Subject teacher** – creates their own account, sees the students of the classes they teach,
  adds tests and the exam, and types in each student's marks. A teacher sees **only their own
  subjects** – never the marks of another teacher.
- **Class teacher** – adds, corrects and deletes the students of their own class and writes their
  conduct and remarks. The marks of the other teachers and the report cards stay with the admin
  (unless the admin allows class teachers to see their own class's results).
- **PDFs** – termly report for every student in the school's own layout (logo, Subject / Max / Marks,
  Conduct, Core and Non Core subjects, Total, Percentage, Position), class marksheet, subject marksheet.
- **Levels** – a level is a list of subjects with their maximums and periods a week. Every class
  follows one level. The system starts with the seven lists of the school:

  | Level | Subjects | Report total |
  |---|---|---|
  | S1, S2, S3 | 15 | 480 |
  | S4, S5 - Math and Science Stream One | 15 (with the 4 projects) | 600 |
  | S4, S5 - Math and Science Stream Two | 11 | 480 |
  | S6 - MCB | 10 | 410 |
  | S6 - MEG | 10 | 410 |
  | S6 - PCB | 11 (with Subsidiary Mathematics) | 450 |
  | S6 - PCM | 10 | 410 |

  The first two follow the school's printed reports. Stream Two and S6 follow the courses and
  periods of the school's class pages; their maximum is **10 marks for every period a week**
  (7 periods → 70, 3 → 30, 2 → 20), the rule both printed reports follow. Check them against a
  printed report of those classes and change any maximum, group or subject under
  **Levels & subjects**. Add more levels for other combinations.
- **School name and logo** – printed on every report and marksheet. The school's logo and details are
  already in place; change them under Settings.
- **Log-in** – nobody sees anything without their own username and password.

## 1. Install (once)

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/
   (on Windows, tick **"Add Python to PATH"** during installation).
2. Open this folder and start the system:
   - **Windows:** double-click `start.bat`
   - **Mac / Linux:** run `./start.sh` in a terminal

The first start downloads three small packages (Flask, ReportLab, Waitress), so it needs internet once.

## 2. Open it

- On the same computer: http://localhost:5000
- From other computers or phones on the same school network (Wi-Fi/LAN):
  `http://IP-ADDRESS-OF-THIS-COMPUTER:5000` (find the IP with `ipconfig` on Windows). Windows may
  ask to allow Python through the firewall – choose **Allow**.

The computer running the system must stay on while teachers are working.

## 3. First steps

1. **Log in as the administrator.** The account is already in the database: name **HITAYEZU James**,
   username **`jameshitayezu@gmail.com`**, with the password you chose for it. The password is not
   written in any file: the database keeps only its one-way hash. Change it any time under **My account**.
2. Admin: **School Settings → Profile** → check the P.O. Box, email, phone number (**0788892027** is filled in), logo, and the head's name and title.
   They are printed on every report.
3. Admin: **School Settings → Academic years** → add the academic year, e.g. **2025-2026**. Its terms are created for you
   ("2025-2026 TERM 1", "TERM 2", "TERM 3") – this name is printed after "ACADEMIC YEAR:" on the report.
4. Admin: **School Settings → Levels & subjects** → check the two ready-made levels and their maximums. Change a maximum,
   group or order, add subjects, or add another level (e.g. another S4–S6 combination).
5. Admin: **Students → Import from Excel** → choose the school's student
   list workbook (the SDMS "STUDENT LIST" export, one sheet per class). You see a preview, then one
   click creates every class with its students, the right level for each class, and the academic
   year. Classes and students can also be added by hand: add a class, choose its level, then open it
   and add students one by one or paste a list.
6. Staff: open the system, click **"Register your account"** (once) and give their **email address
   and phone number**. Admin: **Staffs** → check the email and phone, **choose the role** (Teacher or
   Administrator) → **Approve**. Or the admin adds staff with **"Add a staff member"** – that person
   logs in with the password the admin typed and must then choose their own.
7. Admin: **School Settings → Classes** → **Manage** on a class. Under **Teachers**, pick a teacher on
   each course (subject) row and confirm with **✓**. Under **Settings**, choose the **class teacher**.
   Every course of a class's level is printed on the class's reports, with or without a teacher.
8. Teachers log in, open a subject, **add a test** (name + what it is marked out of), type the marks,
   press **Save marks**. Add more tests any time, and the **exam** at the end of the term.
9. Admin: **Reports & monitoring** → see which teachers have entered their marks and which have not,
   and which classes are ready. Click **Report cards PDF** next to a class to generate its reports.
   The class teacher types conduct and remarks for their own class under **Conduct & remarks**;
   these are printed on the report cards the admin generates.

### Try it with sample data first (optional)

Before step 1, run `python seed_demo.py` in this folder. It creates a sample school with marks.
Log in as `admin` or as teacher `n.zulu`, password `school1234`. The sample school is invented
(it has classes S1 A, S2 A and S4 MSC A) and prints no logo.
Do this only on a computer that has no real data yet. To go back to an empty system, stop the
program and delete the file `school.db` in the `SchoolMarksData` folder.

## 4. How marks are calculated

Every subject counts out of its own **maximum** (Mathematics 60, English 50, …). Teachers can mark
their tests and exam out of anything (20, 50, 100 …); the system converts.

For one subject, one student, one term:

- **Tests %** = all test marks added together ÷ the total those tests were out of × 100
- **Exam %** = exam mark ÷ exam total × 100
- **Final %** = Tests % × 40% + Exam % × 60%  (the admin can change the 40/60 under **School Settings → Profile**)
- **Mark on the report** = Final % of the subject's maximum. Example: 86 % in Mathematics (60) = **51.6**

On the report:

- **TOTAL** = all subject marks added together.
- **PERCENTAGE** = TOTAL ÷ the maximums of all the class's subjects (480 for S1–S3, 600 for S4–S5
  Math and Science), rounded to one decimal. A subject without a mark counts as nothing scored.
- **POSITION** = place in the class by the printed percentage; equal percentages share a position
  (1, 2, 2, 4). It is printed as "2 / 42" (42 = students in the class). A student with no marks at
  all has no percentage and no position.
- **Conduct** is out of 40 and is printed but **not added** to the total. It starts at 40; the class
  teacher types a lower number when needed.

These rules were checked against the school's own reports for S1 A (42 students) and S4 Math and
Science (45 students): total, percentage and position came out the same for every student.

Rules worth knowing:

- An **empty box** means the student did not write that test – it is left out. Type **0** for a real zero.
- A subject has a mark only when the student has at least one test mark **and** the exam mark.
  For a report from tests only (before the exam), set "Tests count for" to **100** under Settings.
- Settings → "Also show the Test and Exam parts" adds two columns to the report (Max, Test, Exam, Total).

## 5. Adding and removing things

Everything is done on the screens – nobody needs to open the database.

| What | Who | Where |
|---|---|---|
| Add staff | Admin | **Staffs** → "Add a staff member" with email, phone and role (or the person registers once and the admin approves and chooses the role) |
| Update staff, change the role | Admin | **Staffs** → **Edit**: name, email, phone, username, role, or a new password. **Reset password** gives a temporary one. |
| Give, change or take away a teacher's subjects or class | Admin | **Staffs** → **Edit / subjects** → "What … teaches": give a subject, **Change** it to another class or subject, **Remove** it, make or stop being class teacher. |
| Add or remove a subject at a class | Admin | **Classes** → **Manage** → **Teachers** tab: pick a teacher on the row and press **✓** to add it, the pencil to change the teacher, the red **trash** button to take it out (marks stay stored), or "Add a new subject" for one the level does not have yet. |
| Remove staff | Admin | **Staffs** → **Delete**. Marks they entered are kept; their subjects and class have no teacher until the admin chooses a new one. **Disable** only stops the log-in. |
| Add / change / remove a subject | Admin | **Levels & subjects**. Deleting a subject deletes its marks in every class of that level and every term. |
| Add / change an academic year or term | Admin only | **Academic years**: add a year (its terms are created), change its name, change one term, **Make current**, **Lock**, delete an empty term. |
| Switch academic year / term | Everyone (teachers can only do this) | **Academic year** at the top of the menu. Each person chooses the term they are working in; it lasts until they log out. A notice shows when it is not the school's current term. |
| Add a level | Admin | **Levels & subjects** → "Add another level", then add its subjects. |
| Remove a subject from one class only | Admin | **Classes** → **Manage** → red trash button on the subject's row (marks stay stored). |
| Periods per week of a subject | Admin | **Levels & subjects** → "Periods / week" column. Only shown on Manage Class; it never changes a mark. |
| Find any student of the school | Admin | **Students → All students**: search by name or student code, or choose a class. |
| Add / remove a class | Admin | **School Settings → Classes** (delete under **Manage** → **Settings**). Deleting a class deletes its students and all their marks. |
| Add / edit / delete students | Admin, and the **class teacher** for their own class | Admin: **Classes** → **Manage** → **Students** tab. Class teacher: **My classes** → **Students**. |
| Import all classes and students from Excel | Admin | **Students → Import from Excel**. Safe to repeat: students already in the system are not added twice, and nothing is ever deleted. A newer list moves students to their new class if you tick that box. |

The system always shows what will be deleted (for example "8 students and 96 marks") and asks you to
confirm. **Deleting cannot be undone** – make a backup first (see below). Every add and delete is
written in the admin's **Activity log** with the name of the person who did it.

Limits on class teachers: only their own class; they cannot move a student to another class; and
they cannot delete a student who has marks in a term the admin has **locked**.

### Importing the student lists

- The student code in the file becomes the **registration number** on the report.
- The **Combination** line of each sheet decides the level: "ORDINARY LEVEL" → *S1, S2, S3*;
  "Math and Science Stream One" / "Stream Two" → the two *S4, S5 - Math and Science* levels;
  MCB, MEG, PCB, PCM → *S6 - MCB* … *S6 - PCM*. All of these come with their subjects. A combination
  the system does not know gets its own level, created **without subjects** – add its subjects and
  maximums under **Levels & subjects** before giving teachers subjects in those classes.
- A teacher sees all the students of a class as soon as the admin makes them the class teacher or
  gives them a subject in that class. Teachers never see classes they were not given.
- A student code that appears twice in a file is left out and reported, so the file can be corrected.
- Moving students up at the start of a new year: print last year's reports first. A moved student
  leaves the old class list, and old marks are no longer shown with the old class.

### Classes and class lists (admin)

**School Settings → Classes** shows all classes in one table: No., Combination, Grade, Classroom, Class
group, Size and Actions. Grade and combination come from the Excel student list (or you type them
when adding a class by hand).

- **Class list** – all the students of that class with their codes, and a **Class list PDF** to
  print. Only the administrator can open it; a class teacher sees the students of their own class
  under "Students".
- **Manage** – opens the **Manage Class** page. On top is a bar with the class, grade, level,
  classroom, class size and class teacher. Below it are three tabs:
  - **Teachers** – every course (subject) of the class with its type, periods per week, maximum and
    teacher. Pick a teacher on a row and press **✓** to assign; the pencil changes a teacher; the red
    trash button removes the course from this class. Click a teacher's name to see everything they
    teach.
  - **Students** – the students of the class; add, edit or delete them.
  - **Settings** – class name, grade, combination, level, class teacher, and deleting the class.

### The menu

The admin's menu is grouped like the school's other system: **Dashboard**, **School Settings**
(Profile, Classes, Levels & subjects, Academic years), **Students** (All students, Import from
Excel), **Staffs**, and **Reports** (Reports & monitoring, Activity log). The academic year switch
is at the top of the menu; My account and Log out are at the bottom. On a phone, press **☰ Menu**.

### The dashboard (admin)

The first page the admin sees shows the school in numbers and the drawing **Student Registration
by Classes**.

- **Total Students**, **Active Staff** and **Classes** are counted from the database every time
  the page is opened: add, move or delete a student, approve a staff member or add a class, and the
  numbers and the bars follow. *Active Staff* counts the accounts that are active (not the ones
  still waiting for approval or switched off).
- The drawing has one bar for every class with its number of students; the numbers add up to
  *Total Students*. Click a bar to open that class.
- **Classrooms** is not recorded anywhere in the marks system, so it cannot count it. Type the
  number once under **School Settings → Profile → The school in numbers**; until then the tile
  shows a dash.

### Class workbooks for Google Sheets (admin)

**Reports → Marks sheets** gives one Excel workbook per class: a tab for every course of the class
(named after the course, with the names and codes of its students) and a **Summary** tab that adds up
the tests of every course for every student (total, %, position) while the teachers are typing.

1. **Class workbook (Excel)** next to a class downloads its workbook (also on **Manage Class**).
2. On **drive.google.com**: **New → File upload** → choose the workbook, open it, **File → Save as
   Google Sheets**. **Share** it with the teachers of the class as **Editor** and send them the link.
   Each teacher types the marks in the tab of their own course. (To keep a teacher out of the other
   tabs: right-click a tab → **Protect sheet**.) Teachers do not need an account in the system.
3. When the teachers are done: **File → Download → Microsoft Excel (.xlsx)**, and on **Marks sheets**
   choose that file (several at once is fine) and press **Import marks**. Only class workbooks (Excel
   files made here) are accepted: no zip files, no other sheets.
4. Print the reports: **Periodical reports PDF** next to the class.

Every course tab is checked and imported completely or not at all; the page lists the courses that
came in and, for the others, the cells to correct. Importing the same workbook again later only brings
in what changed. A course with no teacher chosen in the system is still on the reports.

### Periodical report (admin)

The **periodical report** counts the **tests only** (every test entered so far in the term, no exam):
each subject is shown out of its maximum, with the total, percentage and position, under the title
**PERIODIC REPORT ( All Periods )**, as on the school's own report. Only the administrator generates it:

- for a whole class: **Reports → Reports & monitoring → Periodical reports PDF**, or on the class's
  **Results** page;
- for one student: on the class's **Results** page, **Periodical** next to the student.

The termly report (tests and exam) is still there as **Termly report cards PDF**.

### Reports & monitoring (admin)

- **Teachers: who has entered marks** – every teacher with their number of subjects, how many are
  complete, started, or have nothing entered, a status (*all entered*, *not finished*, *nothing
  entered*) and when they last saved marks. Click a teacher to see each class and subject.
- **Classes: generate the reports** – every class with "x of y subjects complete", which subjects and
  teachers it is still waiting for, and the buttons **Report cards PDF** and **Marksheet PDF**.
  A class is *ready* when every subject has a mark for every student. Reports can be generated before
  that; subjects without a mark are then empty on the report.
- The page follows the term chosen under "Academic year" in the menu.

### Email to staff

Switch it on under **School Settings → Profile → Email to staff**. The system then emails a staff member when: their
registration is received; the admin approves it and gives the role; an account is created for them;
their role changes; they are given a subject or made class teacher; their password is reset.
Administrators get an email when someone registers. **No email ever contains a password.**

- The system needs an email account to send **from**, and the computer needs internet. With Gmail:
  server `smtp.gmail.com`, port `587`, STARTTLS, the Gmail address as username, and an **App password**
  (Google account → Security → 2-Step Verification → App passwords) – not the normal Gmail password.
- Press **Send a test email** after saving. If it fails, the reason is shown.
- If an email cannot be sent, the admin's action still works; "Email NOT sent" with the reason
  appears in the Activity log.
- The mail account's password has to be kept (readable) in the database so the system can log in to
  the mail server – use an app password made only for this.
- Staff without an email address simply get no email.

### Who can see what (access)

| Who | Can open |
|---|---|
| **Administrator** | Everything: all classes, students, marks, reports, staff and settings. |
| **Subject teacher** | Only the subjects the admin gave them, in the classes the admin gave them: the students of that class and their **own** marks, tests and exam, and their subject marksheet PDF. Every other subject is refused. |
| **Class teacher** | The students of their own class (add, correct, delete) and their conduct, days absent and remarks – plus their own subjects, like every teacher. Not the marks of the other teachers, and not the results, marksheet or report cards of the class. |

- **No teacher can see or change the marks of another teacher.** This is checked by the system on
  every page and every save, not only hidden from the menu: a teacher who types the address of
  someone else's page gets "This page is not for your account".
- Access follows what the admin sets: when a subject or a class is given to another teacher, the
  first teacher loses it at once and the new teacher gets it, with the marks already entered.
- **Optional:** under **School Settings → Profile → Who can see the marks** the admin can tick
  "Allow a class teacher to see the results of their own class". Class teachers can then see (not
  change) all subjects of their own class and print its marksheet and report cards. It is **off**
  unless the admin switches it on, and every change of this switch is written in the Activity log.

### Registration, roles and log-in

- **Email and phone are checked** when staff register or are added: the email must be written
  correctly (name@place.com), the phone number must have 9–15 digits, and neither may already belong
  to another account. The system cannot send a confirmation email or SMS – the admin sees both on
  the Staff page and confirms the person before approving.
- **Roles:** *Teacher* – enters marks for the subjects given to them and manages the students,
  conduct and remarks of a class if made its class teacher. *Administrator* – everything, including staff, classes, levels, academic years and
  settings. An Administrator can also be given subjects and a class.
- An Administrator cannot disable, delete or change the role of their own account, and another
  Administrator must be changed to Teacher before the account can be disabled or deleted, so the
  school is never left without an administrator.
- **Passwords are protected in the database.** A password is never stored: only a salted one-way
  hash (scrypt) is kept, so nobody – not the admin, and not someone who copies `school.db` – can read
  a password. Two people with the same password get different stored values. A forgotten password
  cannot be looked up; the admin gives a new temporary one.
- A username can be a short name or an email address. The admin changes their own username under
  **My account**.

## 6. The database: where it is, backups, restoring

- **Where:** all data is saved permanently in one SQLite database file, `school.db`, in the folder
  **`SchoolMarksData`** in your home folder (on Windows `C:\Users\<your name>\SchoolMarksData`).
  The exact place is shown when the program starts and under **Settings → Database**.
- **It is not inside the program folder.** You can replace the `school_marks` folder by a newer
  version, move it or delete it – the data stays. A database left in the `data` folder of an older
  copy of the program is carried over automatically the first time the new version starts from
  that folder; you can also bring it in with **Settings → Database → Restore**.
- **Automatic copies:** once a day, at the first start of the day, a copy is put in
  `SchoolMarksData\backups` (the newest 14 are kept).
- **Your own backups:** **Settings → Database → Download a backup now**. Keep that file on a USB
  stick or in the cloud – the automatic copies do not help if the laptop itself is lost.
- **Restoring:** **Settings → Database → Restore** → choose a backup (or an old `school.db`). The
  data from just before is kept in `backups` first, and everyone logs in again afterwards.
- Many people can work at the same time; saving at the same moment is safe.
- **Lock a term** (Admin → Academic years) when its reports are final; teachers can then no longer change
  marks, but can still switch to it to look at results and print reports.
- New academic year: Admin → Academic years → add it, then press **Make current** on its first term.
  Everyone starts in the current term when they log in. Marks of earlier years stay where they are.
- Forgotten password: Admin → Staff → **Reset password** gives a temporary one; the system then makes
  the staff member choose their own at the next log-in.
- If the admin forgets their own password there is no reset screen – keep it written down safely.

### One browser is one person at a time

Every page needs a log-in, and a browser remembers **one** logged-in person. If the admin is logged
in and a teacher then logs in **on the same browser** (even in another tab), the browser belongs to
the teacher from that moment: the admin's open page shows a notice and admin pages say "This page is
not for your account". **Nothing is lost** – the admin logs in again and has every right back.

- Normal use: each person on their **own** phone or computer. Then everyone works at the same time
  with their own rights.
- Two accounts on one computer at the same time: open the second in a **private / incognito
  window**, or in another browser (Chrome and Edge, for example).
- **Log out** when you leave a shared computer.

## 7. Good to know

- Made for use inside a school network. If you want to put it on the internet, host it behind
  HTTPS so that passwords are encrypted on the way.
- Names in the PDFs use standard Latin letters (including accents).
- Updating to a newer version of the program: unzip the new `school_marks` folder and start it. Your
  data is in `SchoolMarksData`, so there is nothing to copy. Coming from a very early version that
  kept `school.db` inside the program's `data` folder: copy that file into the new folder's `data`
  folder before the first start (or use Settings → Database → Restore). Older databases are brought
  up to date automatically.

## Files

| File | What it is |
|---|---|
| `app.py` | The web application (pages, log-in, permissions) |
| `results.py` | All calculations (percentages, grades, positions) |
| `pdfs.py` | PDF layouts |
| `db.py` | Database tables |
| `templates/`, `static/` | The screens and their styling |
| `importer.py` | Reads the Excel student lists |
| `mailer.py` | Sends the notification emails |
| `seed_demo.py` | Optional sample data |
| `data/` | Empty. The database is in `SchoolMarksData` in your home folder (see section 6) |
