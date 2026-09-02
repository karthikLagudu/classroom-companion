# Reproducible demo

## 1. Start clean

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m scripts.seed
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Keep `LLM_MODE=fake` and `TELEGRAM_MODE=log` for a fully deterministic local demo. To demonstrate real model interpretation, set `LLM_MODE=real`, `OPENAI_API_KEY`, and an available `OPENAI_MODEL`, then restart.

## 2. Establish actors

Sign in as `teacher@sim.school` / `DemoPass123!`. The dashboard shows Grade 8 Science, two students, a seeded assignment, one linked/in-progress student, and one unlinked/silent student.

The second student can link Telegram by sending:

```text
/join SIM8SCI student2@sim.school
```

For the silent-student portion, leave that account unlinked. The delivery log will record a skipped notification rather than pretending delivery occurred.

## 3. Create with a relative deadline

On the teacher dashboard select Grade 8 Science and submit:

```text
Complete questions 1-5 from the energy worksheet and upload a photo by next Friday at 6 PM
```

Open the created assignment. Confirm the title, instructions, resolved aware deadline, and two independent `assigned` states. Refreshing or repeating the same browser POST operation key cannot create a duplicate.

Equivalent Telegram command (replace class ID if the seed ID differs):

```text
/assign 1 Complete questions 1-5 from the energy worksheet and upload a photo by next Friday at 6 PM
```

## 4. Change the deadline and clarify

On assignment detail choose a new future local deadline and click **Update deadline**. Save a clearer instruction such as “Show your calculations and include units.” Pending schedule rows are superseded and replacement rows are created.

## 5. Progress, blocker, and silence

As the linked student in Telegram:

```text
/ack 1
/progress 1 I have completed 60%
/blocked 1 I cannot open the reference spreadsheet
```

Or sign in as `student1@sim.school` and use the progress form. Do nothing as student 2.

## 6. Run reminder policy

Click **Run reminder processing now** or run:

```powershell
python -m scripts.run_reminders
```

Expected results:

- student 1: `blocked_support` with supportive blocker wording;
- student 2: `silent_checkin`, recorded as skipped while unlinked;
- the teacher risk panel distinguishes blocked and no-response states;
- running again the same UTC day creates no duplicate policy reminder.

## 7. Submit

As student 1, open the assignment and submit text or a small file/photo. Telegram equivalent:

```text
/submit 1 My observations and calculations are attached.
```

For a document/photo, attach it with `/submit 1` in the caption. The UI moves to `submitted` and lists the submission. Replaying the Telegram update or form operation key creates no duplicate.

## 8. Review and feedback

Return to the teacher assignment page, write feedback, and choose **Needs revision** or **Complete**. The feedback is persisted, state changes through the central transition map, and log mode records the Telegram message. Sign in as `student1@sim.school`; the student UI shows the feedback and final status.

## 9. Prove access boundaries

Sign in as `teacher@northstar.school` / `DemoPass123!` and manually request `/teacher/classes/1`: the server returns 403. As student 1, requesting a submission belonging to student 2 through `/api/student/submissions/{id}` returns 403. Automated coverage:

```powershell
python -m pytest -q tests/test_authorization.py
```

## 10. Prove persistence and quality

Stop and restart Uvicorn. The assignment, student states, reminders, submissions, feedback, activity, and delivery log remain. Run:

```powershell
python -m pytest -q
python -m ruff check app scripts tests
```

Expected repository verification at delivery: 16 passed, 0 failed; Ruff clean.

