from tests.conftest import authenticate


def test_student_cannot_read_another_students_submission(client, app, db, data):
    from app.services.submission import SubmissionService

    submission = SubmissionService().submit(
        db, data["student2"], data["assignment"].id, "student2-only", text_content="private"
    )
    db.commit()
    authenticate(client, app, data["student1"].id)
    response = client.get(f"/api/student/submissions/{submission.id}")
    assert response.status_code == 403


def test_teacher_cannot_access_unrelated_class(client, app, data):
    authenticate(client, app, data["teacher1"].id)
    response = client.get(f"/api/teacher/classes/{data['class2'].id}")
    assert response.status_code == 403


def test_student_cannot_access_untargeted_assignment(client, app, db, data):
    from app.models import Assignment

    outsider = Assignment(
        school_id=data["school1"].id,
        classroom_id=data["class1"].id,
        created_by_user_id=data["teacher1"].id,
        title="Private",
        instructions="Private",
        status="assigned",
        due_at=data["assignment"].due_at,
        timezone="Asia/Kolkata",
    )
    db.add(outsider)
    db.commit()
    authenticate(client, app, data["student1"].id)
    assert client.get(f"/api/student/assignments/{outsider.id}").status_code == 403
