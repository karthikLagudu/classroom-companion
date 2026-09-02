from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.security import current_session, current_user, validate_csrf, verify_password
from app.config import get_settings
from app.database import get_db
from app.exceptions import LLMInterpretationError
from app.models import (
    ActivityEvent,
    Assignment,
    ClassMembership,
    Classroom,
    Feedback,
    NotificationDelivery,
    ProgressEvent,
    School,
    SchoolMembership,
    StudentAssignmentState,
    Submission,
    User,
)
from app.services.assignment import AssignmentService, get_student_state
from app.services.authorization import (
    require_student_assignment,
    require_student_submission,
    require_teacher_assignment,
    require_teacher_class,
)
from app.services.progress import ProgressService
from app.services.submission import FeedbackService, SubmissionService

router = APIRouter()
logger = logging.getLogger(__name__)


def render(request: Request, template: str, **context):
    return request.app.state.templates.TemplateResponse(
        request=request, name=template, context={"request": request, **context}
    )


def web_user(request: Request, db: Session = Depends(get_db)) -> User:
    return current_user(request, db)


def csrf(request: Request) -> str:
    return current_session(request).csrf_token


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    try:
        user = current_user(request, db)
    except HTTPException:
        return RedirectResponse("/login", status_code=303)
    teacher = db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.role.in_(["teacher", "coordinator"]),
        )
    )
    return RedirectResponse("/teacher" if teacher else "/student", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
def login(
    request: Request, email: str = Form(), password: str = Form(), db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", error="Invalid email or password")
    token, _ = request.app.state.sessions.create(user.id)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        request.app.state.sessions.cookie_name,
        token,
        httponly=True,
        secure=request.app.state.sessions.secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form()):
    validate_csrf(request, csrf_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(request.app.state.sessions.cookie_name)
    return response


def teacher_classes(db: Session, user: User) -> list[Classroom]:
    coordinator_school_ids = select(SchoolMembership.school_id).where(
        SchoolMembership.user_id == user.id, SchoolMembership.role == "coordinator"
    )
    teacher_class_ids = select(ClassMembership.classroom_id).where(
        ClassMembership.user_id == user.id, ClassMembership.role == "teacher"
    )
    return list(
        db.scalars(
            select(Classroom)
            .where(
                or_(
                    Classroom.school_id.in_(coordinator_school_ids),
                    Classroom.id.in_(teacher_class_ids),
                )
            )
            .order_by(Classroom.name)
        )
    )


@router.get("/teacher", response_class=HTMLResponse)
def teacher_dashboard(
    request: Request, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    classes = teacher_classes(db, user)
    if not classes:
        raise HTTPException(403, "Teacher or coordinator access required")
    class_ids = [item.id for item in classes]
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.classroom_id.in_(class_ids))
            .order_by(Assignment.created_at.desc())
        )
    )
    risks = db.execute(
        select(StudentAssignmentState, User, Assignment)
        .join(User, User.id == StudentAssignmentState.student_id)
        .join(Assignment, Assignment.id == StudentAssignmentState.assignment_id)
        .where(
            Assignment.classroom_id.in_(class_ids),
            StudentAssignmentState.status.in_(["assigned", "blocked", "overdue"]),
        )
    ).all()
    risk_facts = [
        f"{student.name} is {state.status.replace('_', ' ')} on {assignment.title}."
        for state, student, assignment in risks
    ]
    risk_summary = request.app.state.llm_service.risk_summary(db, user.id, risk_facts)
    db.commit()
    activity = list(
        db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.classroom_id.in_(class_ids))
            .order_by(ActivityEvent.created_at.desc())
            .limit(12)
        )
    )
    deliveries = list(
        db.scalars(
            select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(10)
        )
    )
    return render(
        request,
        "teacher/dashboard.html",
        user=user,
        classes=classes,
        assignments=assignments,
        risks=risks,
        risk_summary=risk_summary,
        activity=activity,
        deliveries=deliveries,
        csrf_token=csrf(request),
        idempotency_key=str(uuid.uuid4()),
        message=request.query_params.get("message"),
    )


@router.get("/teacher/classes/{class_id}", response_class=HTMLResponse)
def teacher_class(
    request: Request, class_id: int, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    classroom = require_teacher_class(db, user, class_id)
    students = list(
        db.scalars(
            select(User)
            .join(ClassMembership, ClassMembership.user_id == User.id)
            .where(ClassMembership.classroom_id == class_id, ClassMembership.role == "student")
        )
    )
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.classroom_id == class_id)
            .order_by(Assignment.due_at)
        )
    )
    return render(
        request,
        "teacher/class.html",
        user=user,
        classroom=classroom,
        students=students,
        assignments=assignments,
        csrf_token=csrf(request),
    )


@router.post("/teacher/assignments")
def create_assignment(
    request: Request,
    classroom_id: int = Form(),
    natural_text: str = Form(),
    idempotency_key: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    classroom = require_teacher_class(db, user, classroom_id)
    school = db.get(School, classroom.school_id)
    try:
        parsed = request.app.state.llm_service.assignment(
            db, user.id, natural_text, school.timezone, datetime.now(UTC)
        )
        assignment = AssignmentService().create(
            db,
            user,
            classroom_id,
            parsed.title,
            parsed.instructions,
            parsed.due_at,
            school.timezone,
            idempotency_key,
        )
        db.commit()
        return RedirectResponse(
            f"/teacher/assignments/{assignment.id}?message=Assignment+created", status_code=303
        )
    except LLMInterpretationError as exc:
        db.commit()
        return RedirectResponse(f"/teacher?message={str(exc).replace(' ', '+')}", status_code=303)


@router.get("/teacher/assignments/{assignment_id}", response_class=HTMLResponse)
def teacher_assignment(
    request: Request,
    assignment_id: int,
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    assignment = require_teacher_assignment(db, user, assignment_id)
    rows = db.execute(
        select(StudentAssignmentState, User)
        .join(User, User.id == StudentAssignmentState.student_id)
        .where(StudentAssignmentState.assignment_id == assignment_id)
    ).all()
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.assignment_id == assignment_id)
            .order_by(Submission.created_at.desc())
        )
    )
    feedback = list(
        db.scalars(
            select(Feedback)
            .where(Feedback.assignment_id == assignment_id)
            .order_by(Feedback.created_at.desc())
        )
    )
    return render(
        request,
        "teacher/assignment.html",
        user=user,
        assignment=assignment,
        state_rows=rows,
        submissions=submissions,
        feedback=feedback,
        csrf_token=csrf(request),
        message=request.query_params.get("message"),
    )


@router.post("/teacher/assignments/{assignment_id}/deadline")
def update_deadline(
    request: Request,
    assignment_id: int,
    due_at: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    assignment = require_teacher_assignment(db, user, assignment_id)
    local = datetime.fromisoformat(due_at).replace(tzinfo=ZoneInfo(assignment.timezone))
    AssignmentService().update_deadline(db, user, assignment_id, local.astimezone(UTC))
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{assignment_id}?message=Deadline+updated", status_code=303
    )


@router.post("/teacher/assignments/{assignment_id}/instructions")
def update_instructions(
    request: Request,
    assignment_id: int,
    instructions: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    AssignmentService().clarify(db, user, assignment_id, instructions)
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{assignment_id}?message=Instructions+updated", status_code=303
    )


@router.post("/teacher/assignments/{assignment_id}/cancel")
def cancel_assignment(
    request: Request,
    assignment_id: int,
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    AssignmentService().cancel(db, user, assignment_id)
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{assignment_id}?message=Assignment+cancelled", status_code=303
    )


@router.post("/teacher/submissions/{submission_id}/feedback")
def create_feedback(
    request: Request,
    submission_id: int,
    message: str = Form(),
    outcome: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    feedback = FeedbackService().create(
        db, user, submission_id, message, complete=outcome == "complete"
    )
    student = db.get(User, feedback.student_id)
    request.app.state.telegram_client.send(
        db,
        student,
        None,
        f"Teacher feedback for assignment #{feedback.assignment_id}: {feedback.message}",
        kind="feedback",
    )
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{feedback.assignment_id}?message=Feedback+sent", status_code=303
    )


@router.post("/teacher/reminders/run")
def run_reminders(
    request: Request,
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    if not teacher_classes(db, user):
        raise HTTPException(403, "Teacher access required")
    reminders = request.app.state.reminder_service.run(db)
    db.commit()
    return RedirectResponse(
        f"/teacher?message=Processed+{len(reminders)}+reminders", status_code=303
    )


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
                ProgressEvent.assignment_id == assignment_id, ProgressEvent.student_id == user.id
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
        idempotency_key=str(uuid.uuid4()),
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
        destination = (settings.upload_dir.resolve() / f"{uuid.uuid4().hex}{safe_suffix}").resolve()
        if settings.upload_dir.resolve() not in destination.parents:
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
        text_content=text_content.strip() or None if content is None else None,
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


@router.get("/api/teacher/classes/{class_id}")
def api_teacher_class(class_id: int, user: User = Depends(web_user), db: Session = Depends(get_db)):
    item = require_teacher_class(db, user, class_id)
    return {"id": item.id, "name": item.name, "school_id": item.school_id}


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
