from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import validate_csrf
from app.database import get_db
from app.exceptions import ValidationError
from app.models import SchoolMembership, User
from app.services.classroom import ClassroomService
from app.web.helpers import web_user
from app.web.teacher import teacher_dashboard

router = APIRouter()


@router.get("/coordinator", response_class=HTMLResponse)
def coordinator_dashboard(
    request: Request, user: User = Depends(web_user), db: Session = Depends(get_db)
):
    allowed = db.scalar(
        select(SchoolMembership.id).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.role == "coordinator",
        )
    )
    if not allowed:
        raise HTTPException(403, "Coordinator access required")
    return teacher_dashboard(request, user, db)


@router.post("/coordinator/teachers")
def add_teacher(
    request: Request,
    school_id: int = Form(),
    classroom_ids: list[int] = Form(),
    name: str = Form(),
    email: str = Form(),
    temporary_password: str = Form(),
    csrf_token: str = Form(),
    user: User = Depends(web_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    try:
        teacher = ClassroomService().add_teacher(
            db,
            user,
            school_id,
            classroom_ids,
            name,
            email,
            temporary_password,
        )
        db.commit()
    except ValidationError as exc:
        db.rollback()
        return RedirectResponse(
            f"/coordinator?message={quote_plus(str(exc))}", status_code=303
        )
    return RedirectResponse(
        f"/coordinator?message={quote_plus(f'Teacher {teacher.name} created')}",
        status_code=303,
    )
