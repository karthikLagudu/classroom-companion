from __future__ import annotations

import logging
import shlex
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import DomainError
from app.llm.service import LLMService
from app.models import (
    Assignment,
    ProcessedTelegramUpdate,
    StudentAssignmentState,
    User,
)
from app.services.assignment import AssignmentService
from app.services.invite import InviteService
from app.services.notification import NotificationService
from app.services.progress import ProgressService
from app.services.submission import SubmissionService
from app.telegram.client import TelegramClient
from app.telegram.files import TelegramFileService
from app.telegram.router import TelegramIntentRouter

logger = logging.getLogger(__name__)
HELP = (
    "Commands:\n/join CODE EMAIL - link your pre-created student account\n"
    "/assign CLASS_ID instructions and deadline - teacher creates work\n"
    "/ack ASSIGNMENT_ID\n/progress ASSIGNMENT_ID update\n/blocked ASSIGNMENT_ID reason\n"
    "/submit ASSIGNMENT_ID text (or attach a file with this caption)\n/status"
)


class TelegramService:
    def __init__(self, llm: LLMService, client: TelegramClient):
        self.llm = llm
        self.client = client
        self.notifications = NotificationService(client)
        self.assignments = AssignmentService(self.notifications)
        self.progress = ProgressService()
        self.submissions = SubmissionService()
        self.invites = InviteService()
        self.files = TelegramFileService(client.settings, client)
        self.intent_router = TelegramIntentRouter(llm, client, self.notifications)

    def process(self, db: Session, update: dict) -> dict[str, str | bool]:
        try:
            update_id = int(update.get("update_id", -1))
        except (TypeError, ValueError):
            update_id = -1
        if update_id < 0:
            return {"ok": True, "result": "ignored_missing_update_id"}
        if db.get(ProcessedTelegramUpdate, update_id):
            return {"ok": True, "duplicate": True}
        message = update.get("message") or update.get("edited_message")
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message", {})
            message["from"] = callback.get("from", {})
            action, _, value = callback.get("data", "").partition(":")
            if action in {"ack", "blocked"}:
                message["text"] = f"/{action} {value}"
            elif action == "help":
                message["text"] = f"/blocked {value} I need help"
            else:
                message["text"] = "/help"
        if not message:
            db.add(
                ProcessedTelegramUpdate(telegram_update_id=update_id, result_reference="ignored")
            )
            return {"ok": True, "result": "ignored"}
        sender = message.get("from", {})
        telegram_user_id = str(sender.get("id", ""))
        chat_id = str(message.get("chat", {}).get("id", telegram_user_id))
        text = (message.get("text") or message.get("caption") or "").strip()
        user = db.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        try:
            result = self._dispatch(db, user, telegram_user_id, chat_id, text, message, update_id)
        except DomainError as exc:
            self.client.send(db, user, chat_id, str(exc), kind="error")
            result = f"error:{type(exc).__name__}"
        except ValueError:
            self.client.send(
                db,
                user,
                chat_id,
                "Check the command format and use numeric assignment/class IDs. Send /help for examples.",
                kind="error",
            )
            result = "error:invalid_command_format"
        db.add(ProcessedTelegramUpdate(telegram_update_id=update_id, result_reference=result))
        logger.info("telegram_update_processed update_id=%s result=%s", update_id, result)
        return {"ok": True, "result": result}

    def _dispatch(
        self,
        db: Session,
        user: User | None,
        telegram_user_id: str,
        chat_id: str,
        text: str,
        message: dict,
        update_id: int,
    ) -> str:
        parts = shlex.split(text) if text.startswith("/") else []
        command = parts[0].lower() if parts else ""
        if command == "/join":
            if len(parts) != 3:
                self.client.send(
                    db, user, chat_id, "Use /join CODE your@email.example", kind="help"
                )
                return "join_help"
            target = db.scalar(select(User).where(User.email == parts[2].lower()))
            if not target:
                from app.exceptions import InviteError

                raise InviteError("The invite details could not be verified")
            classroom = self.invites.join(db, target, parts[1], telegram_user_id, chat_id)
            self.client.send(
                db, target, chat_id, f"Linked as {target.name} to {classroom.name}.", kind="joined"
            )
            return f"joined:{classroom.id}"
        if not user:
            self.client.send(
                db,
                None,
                chat_id,
                "Your Telegram account is not linked. Use /join CODE EMAIL.\n\n" + HELP,
                kind="help",
            )
            return "unlinked"
        user.telegram_chat_id = chat_id
        if command in {"/help", "/start"}:
            self.client.send(db, user, chat_id, HELP, kind="help")
            return "help"
        if command == "/assign":
            if len(parts) < 3:
                self.client.send(
                    db, user, chat_id, "Use /assign CLASS_ID task with deadline", kind="help"
                )
                return "assign_help"
            class_id = int(parts[1])
            natural_text = " ".join(parts[2:])
            classroom = __import__(
                "app.services.authorization", fromlist=["require_teacher_class"]
            ).require_teacher_class(db, user, class_id)
            parsed = self.llm.assignment(
                db,
                user.id,
                natural_text,
                classroom
                and db.get(
                    __import__("app.models", fromlist=["School"]).School, classroom.school_id
                ).timezone,
                datetime.now(UTC),
            )
            assignment = self.assignments.create(
                db,
                user,
                class_id,
                parsed.title,
                parsed.instructions,
                parsed.due_at,
                db.get(
                    __import__("app.models", fromlist=["School"]).School, classroom.school_id
                ).timezone,
                f"telegram:{update_id}:assignment",
            )
            self.client.send(
                db,
                user,
                chat_id,
                f"Created #{assignment.id}: {assignment.title}, due {assignment.due_at}.",
                kind="assignment_created",
            )
            return f"assignment:{assignment.id}"
        if command in {"/ack", "/progress", "/blocked"}:
            if len(parts) < 2:
                return "missing_assignment_id"
            assignment_id = int(parts[1])
            status = {"/ack": "acknowledged", "/progress": "in_progress", "/blocked": "blocked"}[
                command
            ]
            body = " ".join(parts[2:]) or status.replace("_", " ")
            percent = None
            if command == "/progress":
                interpreted = self.llm.progress(db, user.id, body)
                status, percent = interpreted.status, interpreted.progress_percent
            self.progress.update(db, user, assignment_id, status, body, percent)
            self.client.send(
                db,
                user,
                chat_id,
                f"Updated assignment #{assignment_id}: {status.replace('_', ' ')}.",
                kind="progress",
            )
            return f"progress:{assignment_id}:{status}"
        if command == "/submit":
            if len(parts) < 2:
                return "missing_assignment_id"
            assignment_id = int(parts[1])
            document = message.get("document")
            photos = message.get("photo") or []
            photo = photos[-1] if photos else None
            file_data = document or photo
            body = " ".join(parts[2:]) or None
            stored_path = None
            original_filename = None
            mime_type = None
            content = None
            if file_data:
                stored = self.files.download(
                    str(file_data.get("file_id")),
                    document.get("file_name") if document else "telegram-photo.jpg",
                    document.get("mime_type") if document else "image/jpeg",
                )
                stored_path = stored.path
                original_filename = stored.original_filename
                mime_type = stored.mime_type
                content = stored.content
            submission = self.submissions.submit(
                db,
                user,
                assignment_id,
                f"telegram:{update_id}:submission",
                text_content=body if not file_data else None,
                telegram_file_id=str(file_data.get("file_id")) if file_data else None,
                stored_file_path=stored_path,
                original_filename=original_filename,
                mime_type=mime_type,
                content=content,
            )
            self.client.send(
                db, user, chat_id, f"Submission #{submission.id} received.", kind="submission"
            )
            return f"submission:{submission.id}"
        if command == "/status":
            rows = db.execute(
                select(Assignment, StudentAssignmentState)
                .join(StudentAssignmentState, StudentAssignmentState.assignment_id == Assignment.id)
                .where(StudentAssignmentState.student_id == user.id)
            ).all()
            summary = (
                "\n".join(f"#{a.id} {a.title}: {s.status}, due {a.due_at}" for a, s in rows)
                or "No assignments."
            )
            self.client.send(db, user, chat_id, summary, kind="status")
            return "status"
        if command.startswith("/"):
            self.client.send(
                db, user, chat_id, "I did not recognize that command.\n\n" + HELP, kind="help"
            )
            return "unknown"
        return self.intent_router.route(db, user, chat_id, text, message, update_id)
