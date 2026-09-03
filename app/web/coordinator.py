from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SchoolMembership, User
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
