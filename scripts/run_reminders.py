from app.config import get_settings
from app.database import SessionLocal, init_db
from app.dependencies import build_llm_provider
from app.services.reminder import ReminderService
from app.telegram.client import TelegramClient


def main() -> None:
    settings = get_settings()
    init_db()
    service = ReminderService(build_llm_provider(settings), TelegramClient(settings))
    with SessionLocal.begin() as db:
        reminders = service.run(db)
        for item in reminders:
            print(
                f"{item.reminder_type}: assignment={item.assignment_id} student={item.student_id} status={item.status}"
            )
    print(f"Processed {len(reminders)} reminder(s).")


if __name__ == "__main__":
    main()
