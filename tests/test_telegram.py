from sqlalchemy import func, select

from app.models import ProcessedTelegramUpdate, ProgressEvent


def test_same_telegram_update_runs_once(app, db, data):
    update = {
        "update_id": 44,
        "message": {
            "from": {"id": 201},
            "chat": {"id": 201},
            "text": f"/ack {data['assignment'].id}",
        },
    }
    first = app.state.telegram_service.process(db, update)
    db.commit()
    second = app.state.telegram_service.process(db, update)
    db.commit()
    assert first["ok"] is True and second["duplicate"] is True
    assert db.scalar(select(func.count()).select_from(ProcessedTelegramUpdate)) == 1
    assert db.scalar(select(func.count()).select_from(ProgressEvent)) == 1


def test_repeated_submission_update_creates_one_submission(app, db, data):
    from app.models import Submission

    update = {
        "update_id": 45,
        "message": {
            "from": {"id": 201},
            "chat": {"id": 201},
            "text": f"/submit {data['assignment'].id} my work",
        },
    }
    app.state.telegram_service.process(db, update)
    db.commit()
    app.state.telegram_service.process(db, update)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Submission)) == 1
