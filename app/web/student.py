from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import validate_csrf
from app.config import get_settings
from app.database import get_db
from app.models import Assignment, Feedback, ProgressEvent, StudentAssignmentState, Submission, User
from app.services.assignment import get_student_state
from app.services.authorization import require_student_assignment, require_student_submission
from app.services.progress import ProgressService
from app.services.submission import SubmissionService
from app.web.helpers import csrf, operation_key, render, web_user

router = APIRouter()


@router.get("/student", response_class=HTMLResponse)
def student_dashboard(
    request: Request, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    rows = db.execute(
        select(Assignment, StudentAssignmentState)
        .join(StudentAssignmentState, StudentAssignmentState.assignment_id == Assignment.id)
        .where(StudentAssignmentState.student_id == user.id)
        .order_by(Assignment.due_at)
    ).all()
    feedback = list(
        db.scalars(
            select(Feedback)
            .where(Feedback.student_id == user.id)
            .order_by(Feedback.created_at.desc())
            .limit(10)
        )
    )
    return render(
        request,
        "student/dashboard.html",
        user=user,
        rows=rows,
        feedback=feedback,
        csrf_token=csrf(request),
        message=request.query_params.get("message"),
    )


@router.get("/student/assignments/{assignment_id}", response_class=HTMLResponse)
def student_assignment(
    request: Request,
    assignment_id: int,
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    assignment = require_student_assignment(db, user, assignment_id)
    state = get_student_state(db, assignment_id, user.id)
    progress = list(
        db.scalars(
            select(ProgressEvent)
            .where(
                ProgressEvent.assignment_id == assignment_id,
                ProgressEvent.student_id == user.id,
            )
            .order_by(ProgressEvent.created_at.desc())
        )
    )
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.assignment_id == assignment_id, Submission.student_id == user.id)
            .order_by(Submission.created_at.desc())
        )
    )
    feedback = list(
        db.scalars(
            select(Feedback)
            .where(Feedback.assignment_id == assignment_id, Feedback.student_id == user.id)
            .order_by(Feedback.created_at.desc())
        )
    )
    return render(
        request,
        "student/assignment.html",
        user=user,
        assignment=assignment,
        state=state,
        progress=progress,
        submissions=submissions,
        feedback=feedback,
        csrf_token=csrf(request),
        idempotency_key=operation_key("submission"),
        message=request.query_params.get("message"),
    )


@router.post("/student/assignments/{assignment_id}/progress")
def student_progress(
    request: Request,
    assignment_id: int,
    status: str = Form(),
    message: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    ProgressService().update(db, user, assignment_id, status, message)
    db.commit()
    return RedirectResponse(
        f"/student/assignments/{assignment_id}?message=Progress+updated", status_code=303
    )


@router.post("/student/assignments/{assignment_id}/submit")
async def student_submit(
    request: Request,
    assignment_id: int,
    idempotency_key: str = Form(),
    text_content: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    settings = get_settings()
    content = None
    stored_path = None
    original_name = None
    mime_type = None
    if file and file.filename:
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "File is too large")
        original_name = Path(file.filename).name[:240]
        safe_suffix = Path(original_name).suffix.lower()[:10]
        root = settings.upload_dir.resolve()
        destination = (root / f"{uuid.uuid4().hex}{safe_suffix}").resolve()
        if root not in destination.parents:
            raise HTTPException(400, "Invalid filename")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        stored_path = str(destination)
        mime_type = file.content_type
    if not text_content.strip() and content is None:
        raise HTTPException(422, "Provide text or a file")
    submission = SubmissionService().submit(
        db,
        user,
        assignment_id,
        idempotency_key,
        text_content=(text_content.strip() or None) if content is None else None,
        stored_file_path=stored_path,
        original_filename=original_name,
        mime_type=mime_type,
        content=content,
    )
    db.commit()
    return RedirectResponse(
        f"/student/assignments/{assignment_id}?message=Submission+{submission.id}+received",
        status_code=303,
    )


@router.get("/api/student/assignments/{assignment_id}")
def api_student_assignment(
    assignment_id: int, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    item = require_student_assignment(db, user, assignment_id)
    state = get_student_state(db, assignment_id, user.id)
    return {"id": item.id, "title": item.title, "status": state.status, "due_at": item.due_at}


@router.get("/api/student/submissions/{submission_id}")
def api_student_submission(
    submission_id: int, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    item = require_student_submission(db, user, submission_id)
    return {"id": item.id, "assignment_id": item.assignment_id, "type": item.submission_type}
