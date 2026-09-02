# Product reasoning

## Identity

User identity is the application account; Telegram identity is a unique link to it. Handles and display names can change and do not need to match school records. A sender who messages before linking receives `/join CODE EMAIL` guidance. Email is included because an open class code alone cannot safely decide which pre-created student record to attach. Linking validates invite state, expiry/use limit, school boundary, current membership, and uniqueness of the Telegram ID. In a production rollout, the email would be replaced by a short-lived per-student token or authenticated deep link.

## Roles and access

Roles are rows at school/class scope. A person may be coordinator and teacher, teach several classes, or teach one class while being unable to read another. Coordinators inherit access only inside their school. Students pass both class membership and assignment-target checks. Returning a generic wrong-scope error avoids confirming unrelated data exists.

## Lifecycle

The assignment has a shared lifecycle (`draft/assigned/cancelled`), while each targeted student has an independent state. A central transition map rejects impossible jumps. Submission is an application operation that safely applies two legal transitions when necessary; feedback deterministically selects revision or completion. Overdue is a student-state condition, not a mutation of every classmate at once.

## Time

Schools have an IANA timezone; assignments store that timezone and an aware UTC deadline. The real model resolves phrases using the supplied current instant and school timezone. Demo mode supports tomorrow morning/evening, weekdays/next weekdays, and AM/PM. Deadline comparisons use UTC. Quiet-hour configuration is included, but deferral is left for the durable production worker because silently delaying the take-home demo would make manual verification confusing.

## Corrections

Teachers can revise instructions, move a deadline, or cancel. A deadline update cancels every pending schedule and creates versioned replacement keys. Cancellation closes non-final student states and pending reminders. Every correction creates an activity event; production would also send deterministic correction notifications.

## Duplicates

Telegram updates have a unique update ID and are marked only with their transaction. Submissions and assignment POSTs use unique operation keys; repeating a key returns the original result. Reminder keys encode policy, assignment, student, and day. This handles webhook retries and double taps. Cross-operation reuse is rejected. Real Telegram delivery at exactly-once semantics would require a transactional outbox; the current API boundary is at-least-once with persistent intent/delivery evidence.

## Privacy

Student queries always filter on authenticated `user.id`; teacher queries require a coordinator-school or teacher-class membership. The same authorization functions are used by web and Telegram. Hidden links are only presentation. Upload names are stripped, content is UUID-named within a resolved directory, and size-limited.

## Failures

- LLM: schema, aware-time, enum, length, and confidence validation; safe clarification; interaction log; no assignment write.
- Telegram: delivery status/error persisted and exception logged; log mode makes the demo observable without a token.
- Database: short caller-owned transactions, foreign keys, uniqueness constraints, WAL, and rollback on exceptions.
- Files: path confinement and byte limit; Telegram file metadata is preserved even when binary download is unavailable.
- Invalid commands/missing context: a helpful, non-leaking command guide.

## Reminders

Blocked and silent are different product problems. Blocked students receive acknowledgement/support language and appear as explicit teacher risk. Silent students receive a low-pressure request for a status or blocker. Submitted/completed/cancelled states are suppressed. A daily dedupe key is a simple anti-spam ceiling; deadline edits version baseline schedules. The model only phrases a reminder after deterministic policy selects the recipient/type/facts.

## Product and infrastructure trade-offs

The web UI is intentionally for visibility and light operations; Telegram remains the primary surface. SQLite, Jinja, signed sessions, log delivery mode, and an in-process worker keep a 2-3 day project runnable from a clean checkout. The boundaries—provider interface, services, transaction ownership, outbox-shaped delivery table—leave a clear migration to managed identity, PostgreSQL, queue workers, object storage, and production observability.

