from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.security import validate_csrf
from app.database import get_db
from app.exceptions import LLMInterpretationError
from app.models import (
    ActivityEvent,
    Assignment,
    ClassMembership,
    Feedback,
    Invite,
    NotificationDelivery,
    Reminder,
    School,
    SchoolMembership,
    StudentAssignmentState,
    Submission,
    User,
)
from app.services.authorization import (
    require_teacher_assignment,
    require_teacher_class,
)
from app.services.classroom import ClassroomService
from app.services.risk import RiskService
from app.services.submission import FeedbackService
from app.web.helpers import csrf, operation_key, render, teacher_classes, web_user

router = APIRouter()


@router.get("/teacher", response_class=HTMLResponse)
def teacher_dashboard(
    request: Request, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    classes = teacher_classes(db, user)
    if not classes:
        raise HTTPException(403, "Teacher or coordinator access required")
    class_ids = [item.id for item in classes]
    coordinator_school_ids = list(
        db.scalars(
            select(SchoolMembership.school_id).where(
                SchoolMembership.user_id == user.id,
                SchoolMembership.role == "coordinator",
            )
        )
    )
    schools = list(
        db.scalars(
            select(School)
            .join(SchoolMembership, SchoolMembership.school_id == School.id)
            .where(
                SchoolMembership.user_id == user.id,
                SchoolMembership.role.in_(["teacher", "coordinator"]),
            )
            .distinct()
        )
    )
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.classroom_id.in_(class_ids))
            .order_by(Assignment.created_at.desc())
        )
    )
    risks = RiskService().for_actor(db, user)
    risk_facts = [
        f"{item.student_name} needs attention on {item.assignment_title}: {', '.join(item.reasons)}."
        for item in risks
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
            select(NotificationDelivery)
            .where(
                or_(
                    NotificationDelivery.classroom_id.in_(class_ids),
                    NotificationDelivery.school_id.in_(coordinator_school_ids),
                )
            )
            .order_by(NotificationDelivery.created_at.desc())
            .limit(10)
        )
    )
    return render(
        request,
        "teacher/dashboard.html",
        user=user,
        classes=classes,
        schools=schools,
        assignments=assignments,
        risks=risks,
        risk_summary=risk_summary,
        activity=activity,
        deliveries=deliveries,
        csrf_token=csrf(request),
        idempotency_key=operation_key("assignment"),
        message=request.query_params.get("message"),
    )


@router.get("/teacher/classes/{class_id}", response_class=HTMLResponse)
def teacher_class(
    request: Request,
    class_id: int,
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
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
    invites = list(
        db.scalars(
            select(Invite)
            .where(Invite.classroom_id == class_id)
            .order_by(Invite.created_at.desc())
        )
    )
    return render(
        request,
        "teacher/class.html",
        user=user,
        classroom=classroom,
        students=students,
        assignments=assignments,
        invites=invites,
        csrf_token=csrf(request),
        message=request.query_params.get("message"),
    )


@router.post("/teacher/classes")
def create_classroom(
    request: Request,
    school_id: int = Form(),
    name: str = Form(),
    grade: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    classroom = ClassroomService().create_classroom(db, user, school_id, name, grade)
    db.commit()
    return RedirectResponse(
        f"/teacher/classes/{classroom.id}?message=Class+created", status_code=303
    )


@router.post("/teacher/classes/{class_id}/students")
def add_student(
    request: Request,
    class_id: int,
    name: str = Form(),
    email: str = Form(),
    temporary_password: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    ClassroomService().add_student(db, user, class_id, name, email, temporary_password)
    db.commit()
    return RedirectResponse(
        f"/teacher/classes/{class_id}?message=Student+added", status_code=303
    )


@router.post("/teacher/classes/{class_id}/invites")
def create_invite(
    request: Request,
    class_id: int,
    expires_in_days: int = Form(default=7),
    max_uses: int = Form(default=20),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    ClassroomService().create_invite(db, user, class_id, expires_in_days, max_uses)
    db.commit()
    return RedirectResponse(
        f"/teacher/classes/{class_id}?message=Invite+created", status_code=303
    )


@router.post("/teacher/invites/{invite_id}/disable")
def disable_invite(
    request: Request,
    invite_id: int,
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    invite = ClassroomService().disable_invite(db, user, invite_id)
    db.commit()
    return RedirectResponse(
        f"/teacher/classes/{invite.classroom_id}?message=Invite+disabled", status_code=303
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
        assignment = request.app.state.assignment_service.create(
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
    reminders = list(
        db.scalars(
            select(Reminder)
            .where(Reminder.assignment_id == assignment_id)
            .order_by(Reminder.scheduled_for)
        )
    )
    deliveries = list(
        db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.assignment_id == assignment_id)
            .order_by(NotificationDelivery.created_at.desc())
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
        reminders=reminders,
        deliveries=deliveries,
        csrf_token=csrf(request),
        deadline_key=operation_key("deadline"),
        instructions_key=operation_key("instructions"),
        cancel_key=operation_key("cancel"),
        feedback_keys={item.id: operation_key("feedback") for item in submissions},
        message=request.query_params.get("message"),
    )


@router.post("/teacher/assignments/{assignment_id}/deadline")
def update_deadline(
    request: Request,
    assignment_id: int,
    due_at: str = Form(),
    idempotency_key: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    assignment = require_teacher_assignment(db, user, assignment_id)
    local = datetime.fromisoformat(due_at).replace(tzinfo=ZoneInfo(assignment.timezone))
    request.app.state.assignment_service.update_deadline(
        db, user, assignment_id, local.astimezone(UTC), idempotency_key
    )
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{assignment_id}?message=Deadline+updated", status_code=303
    )


@router.post("/teacher/assignments/{assignment_id}/instructions")
def update_instructions(
    request: Request,
    assignment_id: int,
    instructions: str = Form(),
    idempotency_key: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    request.app.state.assignment_service.clarify(
        db, user, assignment_id, instructions, idempotency_key
    )
    db.commit()
    return RedirectResponse(
        f"/teacher/assignments/{assignment_id}?message=Instructions+updated", status_code=303
    )


@router.post("/teacher/assignments/{assignment_id}/cancel")
def cancel_assignment(
    request: Request,
    assignment_id: int,
    idempotency_key: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    request.app.state.assignment_service.cancel(db, user, assignment_id, idempotency_key)
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
    idempotency_key: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    feedback = FeedbackService(request.app.state.notification_service).create(
        db,
        user,
        submission_id,
        message,
        complete=outcome == "complete",
        idempotency_key=idempotency_key,
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
    classroom_ids = {item.id for item in teacher_classes(db, user)}
    if not classroom_ids:
        raise HTTPException(403, "Teacher access required")
    reminders = request.app.state.reminder_service.run(db, classroom_ids=classroom_ids)
    db.commit()
    return RedirectResponse(
        f"/teacher?message=Processed+{len(reminders)}+reminders", status_code=303
    )


@router.get("/api/teacher/classes/{class_id}")
def api_teacher_class(
    class_id: int, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    item = require_teacher_class(db, user, class_id)
    return {"id": item.id, "name": item.name, "school_id": item.school_id}
