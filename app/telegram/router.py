from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.exceptions import AmbiguousReferenceError, LLMInterpretationError, NotFoundError
from app.llm.service import LLMService
from app.models import Assignment, ClassMembership, Classroom, School, SchoolMembership, User
from app.services.assignment import AssignmentService
from app.services.assignment_reference import AssignmentReferenceService, normalize
from app.services.notification import NotificationService
from app.services.progress import ProgressService
from app.services.risk import RiskService
from app.services.submission import SubmissionService
from app.telegram.client import TelegramClient
from app.telegram.conversation import ConversationService
from app.telegram.files import TelegramFileService


class TelegramIntentRouter:
    """Conversation-first router; model interpretation is followed by deterministic scope checks."""

    def __init__(
        self,
        llm: LLMService,
        client: TelegramClient,
        notifications: NotificationService,
    ):
        self.llm = llm
        self.client = client
        self.notifications = notifications
        self.assignments = AssignmentService(notifications)
        self.progress = ProgressService()
        self.submissions = SubmissionService()
        self.references = AssignmentReferenceService()
        self.risks = RiskService()
        self.context = ConversationService(client.settings.conversation_context_minutes)
        self.files = TelegramFileService(client.settings, client)

    def _teacher_classes(self, db: Session, user: User) -> list[Classroom]:
        coordinator_schools = select(SchoolMembership.school_id).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.role == "coordinator",
        )
        teacher_classes = select(ClassMembership.classroom_id).where(
            ClassMembership.user_id == user.id,
            ClassMembership.role == "teacher",
        )
        return list(
            db.scalars(
                select(Classroom).where(
                    or_(
                        Classroom.school_id.in_(coordinator_schools),
                        Classroom.id.in_(teacher_classes),
                    )
                )
            )
        )

    def _resolve_class(
        self, classes: list[Classroom], reference: str | None
    ) -> Classroom:
        if reference:
            clean = normalize(reference)
            matches = [
                item
                for item in classes
                if clean in normalize(f"{item.grade} {item.name}")
                or normalize(item.name) in clean
            ]
        else:
            matches = classes
        if not matches:
            raise NotFoundError("No authorized class matches that description")
        if len(matches) > 1:
            choices = ", ".join(f"{item.name} (class {item.id})" for item in matches)
            raise AmbiguousReferenceError(f"Which class do you mean? {choices}")
        return matches[0]

    def route(
        self,
        db: Session,
        user: User,
        chat_id: str,
        text: str,
        message: dict,
        update_id: int,
    ) -> str:
        classes = self._teacher_classes(db, user)
        if classes:
            return self._teacher(db, user, chat_id, text, update_id, classes)
        return self._student(db, user, chat_id, text, message, update_id)

    def _teacher(
        self,
        db: Session,
        user: User,
        chat_id: str,
        text: str,
        update_id: int,
        classes: list[Classroom],
    ) -> str:
        context = self.context.get(db, user, chat_id)
        school = db.get(School, classes[0].school_id)
        parsed = self.llm.teacher_intent(db, user.id, text, school.timezone, datetime.now(UTC))
        if parsed.intent == "create_assignment":
            classroom = self._resolve_class(classes, parsed.class_reference)
            school = db.get(School, classroom.school_id)
            if not parsed.title or not parsed.instructions or not parsed.due_at:
                raise LLMInterpretationError("What should students do, and when is it due?")
            assignment = self.assignments.create(
                db,
                user,
                classroom.id,
                parsed.title,
                parsed.instructions,
                parsed.due_at,
                school.timezone,
                f"telegram:{update_id}:assignment",
            )
            self.context.set(db, user, chat_id, assignment_id=assignment.id)
            self.client.send(
                db,
                user,
                chat_id,
                f"Created #{assignment.id}: {assignment.title}.",
                kind="teacher_confirmation",
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                assignment_id=assignment.id,
                idempotency_key=f"teacher-confirm:{update_id}",
            )
            return f"assignment:{assignment.id}"
        reference = parsed.assignment_reference
        if reference and normalize(reference) in {
            "assignment",
            "the assignment",
            "science assignment",
            "the science assignment",
            "homework",
        }:
            reference = None
        if not reference and context and context.active_assignment_id:
            reference = f"#{context.active_assignment_id}"
        if parsed.intent in {
            "update_assignment_deadline",
            "update_assignment_instructions",
            "cancel_assignment",
        }:
            if not reference:
                raise AmbiguousReferenceError("Which assignment do you mean?")
            assignment = self.references.resolve_for_teacher(db, user, reference)
            if parsed.intent == "update_assignment_deadline":
                if not parsed.due_at:
                    raise LLMInterpretationError("What is the new deadline?")
                self.assignments.update_deadline(
                    db,
                    user,
                    assignment.id,
                    parsed.due_at,
                    f"telegram:{update_id}:deadline",
                )
                response = f"Moved #{assignment.id} to {assignment.due_at}."
            elif parsed.intent == "update_assignment_instructions":
                if not parsed.instructions:
                    raise LLMInterpretationError("What clarification should students receive?")
                self.assignments.clarify(
                    db,
                    user,
                    assignment.id,
                    parsed.instructions,
                    f"telegram:{update_id}:instructions",
                )
                response = f"Clarified #{assignment.id}."
            else:
                self.assignments.cancel(
                    db, user, assignment.id, f"telegram:{update_id}:cancel"
                )
                response = f"Cancelled #{assignment.id}."
            self.context.set(db, user, chat_id, assignment_id=assignment.id)
            self.client.send(db, user, chat_id, response, kind="teacher_confirmation")
            return f"{parsed.intent}:{assignment.id}"
        if parsed.intent in {"risk_summary", "class_status"}:
            risks = self.risks.for_actor(db, user)
            facts = [
                f"{item.student_name}: {item.assignment_title}; {', '.join(item.reasons)}; risk {item.risk_score}/100."
                for item in risks
            ]
            summary = self.llm.risk_summary(db, user.id, facts)
            self.client.send(db, user, chat_id, summary, kind="risk_summary")
            return "risk_summary"
        self.client.send(db, user, chat_id, "Tell me what to assign, change, cancel, or review.", kind="help")
        return "teacher_help"

    def _active_assignments(self, db: Session, user: User) -> list[Assignment]:
        from app.models import AssignmentTarget, StudentAssignmentState

        return list(
            db.scalars(
                select(Assignment)
                .join(AssignmentTarget, AssignmentTarget.assignment_id == Assignment.id)
                .join(
                    StudentAssignmentState,
                    (StudentAssignmentState.assignment_id == Assignment.id)
                    & (StudentAssignmentState.student_id == user.id),
                )
                .where(
                    AssignmentTarget.student_id == user.id,
                    Assignment.status == "assigned",
                    StudentAssignmentState.status.not_in(["completed", "cancelled"]),
                )
                .order_by(Assignment.due_at)
            )
        )

    def _student_assignment(
        self, db: Session, user: User, chat_id: str, reference: str | None
    ) -> Assignment:
        if reference:
            return self.references.resolve_for_student(db, user, reference)
        context = self.context.get(db, user, chat_id)
        if context and context.active_assignment_id:
            return self.references.resolve_for_student(
                db, user, f"#{context.active_assignment_id}"
            )
        active = self._active_assignments(db, user)
        if len(active) == 1:
            return active[0]
        choices = ", ".join(f"#{item.id} {item.title}" for item in active[:5])
        raise AmbiguousReferenceError(f"Which assignment do you mean? {choices or 'None are active.'}")

    def _student(
        self,
        db: Session,
        user: User,
        chat_id: str,
        text: str,
        message: dict,
        update_id: int,
    ) -> str:
        document = message.get("document")
        photos = message.get("photo") or []
        file_data = document or (photos[-1] if photos else None)
        context = self.context.get(db, user, chat_id)
        if file_data:
            if not context or context.pending_action != "submission" or not context.active_assignment_id:
                self.client.send(
                    db,
                    user,
                    chat_id,
                    "I received the file. Which assignment is it for? Reply with the assignment title, then send it again.",
                    kind="clarification",
                )
                return "file_needs_context"
            assignment = self.references.resolve_for_student(
                db, user, f"#{context.active_assignment_id}"
            )
            file_id = str(file_data["file_id"])
            stored = self.files.download(
                file_id,
                document.get("file_name") if document else "telegram-photo.jpg",
                document.get("mime_type") if document else "image/jpeg",
            )
            submission = self.submissions.submit(
                db,
                user,
                assignment.id,
                f"telegram:{update_id}:submission",
                telegram_file_id=file_id,
                stored_file_path=stored.path,
                original_filename=stored.original_filename,
                mime_type=stored.mime_type,
                content=stored.content,
            )
            self.context.set(db, user, chat_id, assignment_id=assignment.id)
            self.client.send(db, user, chat_id, f"Submission #{submission.id} received.", kind="submission")
            return f"submission:{submission.id}"
        parsed = self.llm.student_intent(db, user.id, text)
        if parsed.intent == "status":
            rows = self._active_assignments(db, user)
            summary = "\n".join(f"#{item.id} {item.title}: due {item.due_at}" for item in rows) or "No active assignments."
            self.client.send(db, user, chat_id, summary, kind="status")
            return "status"
        assignment = self._student_assignment(db, user, chat_id, parsed.assignment_reference)
        if parsed.intent == "submit_text":
            if parsed.message.lower() in {"here's my homework", "here is my homework", "submit"}:
                self.context.set(
                    db, user, chat_id, assignment_id=assignment.id, pending_action="submission"
                )
                self.client.send(db, user, chat_id, "Great—send the file or photo next.", kind="submission_prompt")
                return f"pending_submission:{assignment.id}"
            submission = self.submissions.submit(
                db,
                user,
                assignment.id,
                f"telegram:{update_id}:submission",
                text_content=parsed.message,
            )
            result = f"submission:{submission.id}"
        else:
            status = {
                "acknowledge": "acknowledged",
                "progress_update": "in_progress",
                "blocked": "blocked",
                "ask_for_help": "blocked",
            }.get(parsed.intent)
            if not status:
                raise LLMInterpretationError("Are you sharing progress, asking for help, or submitting?")
            self.progress.update(
                db,
                user,
                assignment.id,
                status,
                parsed.block_reason or parsed.message or status,
                parsed.progress_percent,
            )
            result = f"progress:{assignment.id}:{status}"
        self.context.set(db, user, chat_id, assignment_id=assignment.id)
        self.client.send(db, user, chat_id, "Update saved.", kind="progress")
        return result
