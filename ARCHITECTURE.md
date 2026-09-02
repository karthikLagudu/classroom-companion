# Architecture

## Boundaries

The application is split by responsibility:

- `app/web`: HTTP forms, templates, cookies, CSRF, serialization.
- `app/telegram`: Telegram update parsing and Bot API delivery.
- `app/services`: authorization, assignments, progress, submissions, feedback, invites, reminders, state transitions.
- `app/llm`: provider interface, OpenAI adapter, deterministic demo/test adapter, Pydantic schemas, confidence/error containment.
- `app/models.py` and `app/database.py`: relational persistence, SQLite configuration, and transaction primitives.
- `app/reminders`: periodic runner; the same service is callable from CLI and teacher UI.

Handlers do not implement business rules. Each entrypoint resolves an authenticated user, passes supplied IDs through authorization, then calls a service. Services use a caller-owned SQLAlchemy transaction so multi-row operations commit or roll back together.

## Telegram flow

```text
Telegram
  -> secret webhook adapter
  -> persisted update-ID dedupe check
  -> TelegramService parses transport shape
  -> LLMService only when natural language is required
  -> validated structured intent + confidence threshold
  -> class/school authorization
  -> domain service and central state machine
  -> SQLite transaction (domain rows + activity + processed update)
  -> TelegramClient
  -> Bot API in real mode OR persisted delivery log in demo mode
```

Unknown Telegram identities can only request help or run the explicit invite-link flow. Telegram display names are never identity evidence. Commands and callback data use the same path.

## Reminder flow

```text
FastAPI lifespan loop / CLI / teacher button
  -> ReminderService
  -> active per-student assignment states from SQLite
  -> deterministic ReminderDecision
  -> unique daily dedupe lookup
  -> LLM wording from policy-selected facts
  -> TelegramClient
  -> reminder + notification delivery persisted
```

The model words a policy-approved message; it does not decide who receives it. Restart recovery comes from querying persistent active states and unique reminder keys, rather than depending on an in-memory job list.

## Browser flow

```text
Browser
  -> FastAPI/Jinja route
  -> signed HTTP-only cookie + CSRF validation
  -> current database user
  -> school/class/student authorization service
  -> domain service/state machine
  -> SQLite transaction
  -> redirect-after-POST to server-rendered status
```

Every state-changing form uses POST plus a synchronizer token stored inside the signed session. Assignment and submission forms also carry operation UUIDs for double-click safety.

## Transactions and consistency

- Assignment creation: assignment, all targets, all student states, baseline reminder schedules, activity event, and idempotency record.
- Submission: submission, student state transition, and activity event.
- Feedback: feedback, review outcome transition, activity event, and delivery log.
- Deadline update: assignment timestamp, cancellation of pending reminders, replacement schedules, and activity event.
- Telegram update: domain operation, outbound delivery records, and processed-update marker.

SQLAlchemy parameterizes SQL. Foreign keys and delete behavior protect references. Unique constraints protect Telegram updates, invites, targets/states, operation keys, submission retries, and reminder retries.

## LLM safety boundary

Allowed: intent classification, field/date extraction, progress interpretation, factual summarization, supportive wording.

Forbidden: authorization, arbitrary IDs, SQL, direct writes, transition decisions, membership creation, or arbitrary delivery. Parsed data must satisfy Pydantic types, aware deadlines, allowed literals, length limits, and confidence. Errors become clarification without domain mutation.

## Observability

HTTP responses include `x-request-id`; Telegram logs include `update_id`. Domain activity, LLM input/output metadata, reminder reason/status, and delivery status/error are persistent. Logs name important failures while avoiding secrets and password contents.

## Deployment shape

The take-home runs one FastAPI process and one SQLite database. With multiple replicas, disable the in-process loop and move policy evaluation to a durable single-consumer worker. The production shape uses PostgreSQL, an outbox, a queue, idempotent workers, object storage, secret management, rate limits, and centralized telemetry.

