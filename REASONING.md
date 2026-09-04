# Product reasoning

## Identity and onboarding

Application users are canonical identities; Telegram IDs are unique links to those accounts. Handles and display names are mutable and are not school evidence. The primary flow now uses a short-lived, high-entropy, one-time per-student deep-link token whose digest—not raw value—is stored. It is issued only through scoped class authorization and resolves directly to the internal user. `/join CODE EMAIL` remains a deliberate fallback combining a class capability with a pre-created identity. Invites enforce active state, expiry, use limits, school consistency, existing memberships, and Telegram uniqueness.

Coordinators and teachers can create a class inside a school where they already hold the corresponding school role. The creator receives a teacher class membership. Authorized teachers/coordinators can pre-create a student with a temporary password, add school/class memberships, and create or disable invites. This is enough for the interview scenario without turning the take-home into a school administration suite. Temporary passwords are entered by the operator and never displayed again.

## Roles, access, and privacy

Roles are scoped rows, not global flags. A person can coordinate one school, teach several classes, and share a class with another teacher. Coordinators inherit class access only inside their school; teachers require a class membership. Students require both a student class membership and assignment target. All paths—including Telegram references and file routes—apply the same server-side rules.

Candidate assignment queries are restricted before title matching. Explicit ID, normalized exact, prefix, and simple token overlap are used in that order. Zero matches and tied matches are non-mutating clarification outcomes. This avoids the dangerous pattern of choosing globally and filtering afterward.

`NotificationDelivery` carries school/class/assignment context. Teachers see only deliveries for assigned classes; coordinators can see their school scope. Bodies from another tenant never reach the result set.

## Lifecycle

`Assignment.status` controls shared draft/assigned/cancelled lifecycle, while every target has an independent `StudentAssignmentState`. One central map rejects invalid transitions. Submission safely walks an assigned/acknowledged state through `in_progress` to `submitted`; feedback moves submitted work to `needs_revision` or `completed`.

Overdue is evaluated immediately before a due reminder job. `assigned`, `acknowledged`, `in_progress`, `blocked`, and `needs_revision` become `overdue` after the deadline, with a `student_assignment_overdue` activity event. Submitted, completed, and cancelled states never become overdue. A blocker before the deadline receives supportive blocker handling; after the deadline the visible status is overdue while its stored blocker reason remains available to the teacher.

## Time and quiet hours

All stored instants are UTC; each school has an IANA timezone. Real LLM parsing receives the current aware instant and school timezone. Fake mode deterministically maps morning to 09:00 and evening to 18:00 and supports named weekdays. Deterministic validation rejects naive or past deadlines after interpretation.

Quiet hours default to 22:00-07:00 in the school timezone. No reminder is sent in that interval—even if already overdue—because predictable no-send behavior is safer for students than inventing an urgency exception. A due job is moved to the next local 07:00 and remains durable. This makes the eventual delay explicit in the job row.

## Reminder policy

Creating an assignment writes two durable jobs per target: 24 hours and 2 hours before the deadline. Creating work inside either window schedules that job immediately. A deadline edit increments the schedule version, cancels pending/deferred jobs, and creates versioned replacements. The database survives restarts and is the queue source of truth.

At execution, current state is re-read:

1. cancelled/completed/submitted -> suppress;
2. quiet hours -> defer;
3. eligible and past due -> transition to overdue;
4. blocked -> supportive `blocked_support`, frequency-limited to 12 hours;
5. overdue -> limited `overdue` wording;
6. within 2 hours -> high-priority `due_within_2h`;
7. within 24 hours with no activity -> `silent_due_soon`;
8. within 24 hours with activity -> `gentle_due_soon`;
9. farther away -> suppress.

The LLM only phrases a selected reminder. Provider failure uses a factual deterministic fallback and is logged. No daily silence message is generated when a deadline is far away.

## Conversation context

Informal references (“move it”) and separately arriving photos need short-lived state. `ConversationContext` is unique per user/chat and stores an active assignment, pending action, optional payload, and expiry. Thirty minutes is long enough for a normal chat exchange and short enough to reduce stale-action risk. Context is set when a linked student receives a new assignment and after an interaction. Every read checks expiry and every use re-authorizes the assignment. Ambiguous sensitive actions still require clarification.

“Here’s my homework” sets `pending_action=submission`; the following document/photo is downloaded and attached to that authorized assignment. A file without valid context is acknowledged and the student is asked to identify the assignment rather than silently discarding or misrouting it.

## Risk engine

Risk selection is deterministic. Blocked adds 70, overdue adds 80, due within 24 hours adds 30, no acknowledgement adds 25, and inactivity beyond two days adds 20; recent activity subtracts 10. Scores are clamped to 0-100, submitted/completed/cancelled states are excluded, and items below 25 are omitted. Levels are medium (25-59), high (60-79), and critical (80-100). Only these already-authorized structured facts go to the LLM for concise prose; the model never decides who is at risk.

## Duplicates and failure handling

Telegram update IDs, assignment operation keys, submission keys, feedback keys, delivery keys, reminder keys, targets, states, and memberships are database-unique. Deadline keys and `schedule_version` make repeated updates harmless. Callback buttons use the same deduplicated command path.

A valid domain operation is preserved when Telegram fails. The client records `failed`, error type/text, attempt count, and next attempt without recursively trying to report failure through the broken channel. The worker retries at 1, 5, and 15 minutes, capped by configuration. Unlinked recipients are `skipped`; log mode is explicitly `logged`. In production, notification intent belongs in a transactional outbox committed atomically with domain state, then delivered by a queue worker.

LLM calls record operation, actor, input, validated output, confidence, success, and a safe error type. Secrets are never included. Low confidence, invalid structure, or provider errors return clarification/factual fallback instead of mutation. Malformed or unsupported Telegram updates return safe 200-class ignored results so Telegram does not retry forever.

## File safety

Telegram files use `getFile`, bounded HTTP timeouts, a streamed byte limit, UUID storage names, path confinement, SHA-256 hashes, and transport idempotency. Original names are metadata only. Upload storage is not public static content: an authenticated route resolves a `Submission`, applies teacher/student authorization, rechecks that its stored path is below `UPLOAD_DIR`, and then serves it. A production version adds object storage, antivirus/content scanning, signed short-lived URLs, retention, and image transformations.

## SQLite and production migration

SQLite is appropriate for a single-process 2-3 day take-home: foreign keys, WAL, busy timeout, short transactions, and uniqueness constraints provide a runnable persistent slice. It is not a distributed scheduler or multi-writer production database. `create_all` is acceptable for fresh demo/test databases, but production must move to PostgreSQL with Alembic, an outbox, durable queue, leased/distributed workers, object storage, managed identity, secret management, and centralized observability.
