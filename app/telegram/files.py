from __future__ import annotations

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.exceptions import TelegramDeliveryError
from app.telegram.client import TelegramClient


@dataclass(frozen=True)
class StoredTelegramFile:
    path: str
    original_filename: str
    mime_type: str
    content_hash: str
    content: bytes


class TelegramFileService:
    def __init__(self, settings: Settings, client: TelegramClient):
        self.settings = settings
        self.client = client

    def download(
        self, file_id: str, original_filename: str | None, mime_type: str | None
    ) -> StoredTelegramFile:
        content = self.client.download_file(file_id, self.settings.max_upload_bytes)
        if not content:
            raise TelegramDeliveryError("Telegram returned an empty file")
        original = Path(original_filename or "telegram-upload").name[:240]
        suffix = Path(original).suffix.lower()[:10]
        root = self.settings.upload_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = (root / f"{uuid.uuid4().hex}{suffix}").resolve()
        if root not in destination.parents:
            raise TelegramDeliveryError("Unsafe upload path")
        destination.write_bytes(content)
        detected = mime_type or mimetypes.guess_type(original)[0] or "application/octet-stream"
        return StoredTelegramFile(
            path=str(destination),
            original_filename=original,
            mime_type=detected,
            content_hash=hashlib.sha256(content).hexdigest(),
            content=content,
        )
