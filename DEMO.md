# End-to-end interview demo

This runbook demonstrates the same service paths in real and offline modes. Use real OpenAI and Telegram when credentials are available; use the fallback only for deterministic local review.

## 1. Start from a clean demo database

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m scripts.seed
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> and verify <http://127.0.0.1:8000/health>.

Real mode in `.env`:

```dotenv
LLM_MODE=real
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
TELEGRAM_MODE=real
TELEGRAM_BOT_TOKEN=...
BASE_URL=https://your-public-tunnel.example
TELEGRAM_WEBHOOK_SECRET=a-long-random-value
```

Register the webhook with `python -m scripts.set_telegram_webhook`. For offline review, keep `LLM_MODE=fake` and `TELEGRAM_MODE=log`; delivery rows remain visible, but real Telegram binary downloads necessarily require real mode.

## 2. Establish the actors

Sign in as `teacher@sim.school` / `DemoPass123!`. Show Grade 8 Science, two students, the linked status, assignments, risk scores, activity, and scoped delivery log. Student 1 is linked; Student 2 is intentionally unlinked/silent.

Optionally prove live onboarding: create a class on the dashboard, open it, add two student records, generate an invite, inspect expiry/use count, then disable it. The seeded Student 2 can link by sending:

```text
/join SIM8SCI student2@sim.school
```

Leave Student 2 unlinked for the silent-recipient demonstration.

## 3. Assign naturally in Telegram

As the teacher, send:

```text
Give Grade 8 Science questions 1-10 from the energy worksheet by tomorrow evening.
```

Show the persisted `LLMInteraction`, assignment, two targets/states, four reminder jobs (24h and 2h for each student), and contextual deliveries. Student 1 receives a formatted message with buttons; Student 2 records `skipped` rather than pretending delivery.

Command fallback:

```text
/assign 1 Questions 1-10 from the energy worksheet by tomorrow evening
```

## 4. Change and clarify

Send:

```text
Move the energy worksheet to Friday at 6 PM.
Actually give them until Monday.
Clarify the energy worksheet: include a diagram and show units.
```

Show the old/new deadline activity, one schedule-version increment per real change, cancelled prior jobs, replacement jobs, and one deadline/clarification notification per student. The second sentence demonstrates persisted teacher context.

## 5. Student progress, blocker, and silence

As Student 1, send natural messages:

```text
Got it.
I've completed around 60%.
I'm stuck on question 4.
```

Show `acknowledged -> in_progress -> blocked`, the progress percentage and blocker reason. Do nothing as Student 2.

Commands remain available:

```text
/ack 2
/progress 2 I've completed 60%
/blocked 2 I'm stuck on question 4
```

## 6. Run reminders

Move the demo deadline within 24 hours if needed, then click **Run reminders** or execute:

```powershell
python -m scripts.run_reminders
```

Expected: due jobs only are considered; Student 1 receives `blocked_support`, Student 2 receives `silent_due_soon` (or records `skipped` while unlinked), final states are suppressed, and the teacher risk view distinguishes blocker/no-response. Run again to show unique jobs and delivery keys prevent duplicates. If local time is 22:00-07:00 Asia/Kolkata, show the jobs deferred to 07:00 instead of sent.

To demonstrate overdue, set a deadline just past, run processing, and show `student_assignment_overdue` before the overdue reminder.

## 7. Submit a separated photo

Student 1 sends:

```text
Here's my homework
```

Then sends an image without a caption. Show the 30-minute context, Bot API `getFile`/download, UUID path, hash, submission, `submitted` state, and teacher thumbnail through the authorized route. Replay the update to show there is still one submission. A file without context is retained by Telegram and receives a clarification prompt rather than being routed to the wrong assignment.

Offline fallback: sign in as `student1@sim.school` / `DemoPass123!` and upload an image in the student UI; it exercises the same storage and authorized-serving model without calling Telegram.

## 8. Feedback

On the teacher assignment page, enter specific feedback and choose **Needs revision** or **Complete**. Double-click/replay the POST: the unique feedback key keeps one row. Show the contextual Telegram delivery, then sign in as Student 1 to show feedback and final status.

## 9. Security demonstrations

- Sign in as `teacher@northstar.school` / `DemoPass123!` and request `/teacher/classes/1`: expect 403.
- As Student 1, request another student’s `/api/student/submissions/{id}` or `/submissions/{id}/file`: expect 403.
- Add a School B delivery containing recognizable private text; School A’s teacher dashboard does not contain it.
- Trigger reminders as School A’s teacher; School B jobs and deliveries remain untouched.

## 10. Persistence and verification

Stop and restart Uvicorn. The assignment, context, states, reminder jobs, submissions, feedback, activity, and delivery evidence remain. Run:

```powershell
python -m ruff check app scripts tests
python -m pytest -q
```

The expected result is whatever these commands actually print for the checked-out revision; do not rely on a hard-coded count in this document.
