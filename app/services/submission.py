from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActivityEvent, Feedback, Submission, User
from app.services.assignment import get_student_state
from app.services.authorization import require_student_assignment, require_teacher_submission
from app.services.state_machine import transition


class SubmissionService:
    def submit(
        self,
        db: Session,
        student: User,
        assignment_id: int,
        idempotency_key: str,
        text_content: str | None = None,
        telegram_file_id: str | None = None,
        stored_file_path: str | None = None,
        original_filename: str | None = None,
        mime_type: str | None = None,
        content: bytes | None = None,
    ) -> Submission:
        assignment = require_student_assignment(db, student, assignment_id)
        existing = db.scalar(
            select(Submission).where(Submission.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.student_id != student.id or existing.assignment_id != assignment_id:
                from app.exceptions import AuthorizationError

                raise AuthorizationError("Idempotency key belongs to another operation")
            return existing
        submission_type = "text" if text_content else "file"
        digest_input = (
            content if content is not None else (text_content or telegram_file_id or "").encode()
        )
        submission = Submission(
            assignment_id=assignment_id,
            student_id=student.id,
            submission_type=submission_type,
            text_content=text_content,
            telegram_file_id=telegram_file_id,
            stored_file_path=stored_file_path,
            original_filename=original_filename,
            mime_type=mime_type,
            content_hash=hashlib.sha256(digest_input).hexdigest(),
            idempotency_key=idempotency_key,
        )
        db.add(submission)
        db.flush()
        state = get_student_state(db, assignment_id, student.id)
        now = datetime.now(UTC)
        if state.status in {"assigned", "acknowledged"}:
            transition(state, "in_progress", now)
        transition(state, "submitted", now)
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=student.id,
                event_type="submission_received",
                entity_type="submission",
                entity_id=submission.id,
                metadata_json={"type": submission_type},
            )
        )
        return submission


class FeedbackService:
    def create(
        self, db: Session, teacher: User, submission_id: int, message: str, complete: bool = False
    ) -> Feedback:
        submission = require_teacher_submission(db, teacher, submission_id)
        assignment = require_student_assignment(
            db, db.get(User, submission.student_id), submission.assignment_id
        )
        feedback = Feedback(
            submission_id=submission.id,
            assignment_id=submission.assignment_id,
            teacher_id=teacher.id,
            student_id=submission.student_id,
            message=message.strip(),
        )
        db.add(feedback)
        state = get_student_state(db, submission.assignment_id, submission.student_id)
        transition(state, "completed" if complete else "needs_revision", datetime.now(UTC))
        db.add(
            ActivityEvent(
                school_id=assignment.school_id,
                classroom_id=assignment.classroom_id,
                actor_user_id=teacher.id,
                event_type="feedback_sent",
                entity_type="feedback",
                entity_id=None,
                metadata_json={"submission_id": submission.id, "complete": complete},
            )
        )
        return feedback
