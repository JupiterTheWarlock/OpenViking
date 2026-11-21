"""Keep a review receipt alongside a resumable, local Claude Code session."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass
class ClaudeReview:
    directory: Path
    session_id: str
    archive_uri: str | None
    publish: Callable[..., Awaitable] | None

    @classmethod
    def for_session(cls, root, ctx, session_id, archive_uri=None, publish=None):
        scope = json.dumps([ctx.account_id, ctx.user.user_id, session_id]).encode()
        folder = hashlib.sha256(scope).hexdigest()[:24]
        return cls(root / folder, session_id, archive_uri, publish)

    @asynccontextmanager
    async def call(self, documents, images, model):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        call_id = str(uuid4())
        archive = self.archive_uri.rstrip("/") if self.archive_uri else None
        record = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "openviking_session_id": self.session_id,
            "claude_session_id": call_id,
            "review_folder": self.directory.name,
            "requested_model": model,
            "documents": list(documents),
            "images": list(images),
            "memory_diff_uri": f"{archive}/memory_diff.json" if archive else None,
        }
        try:
            yield record
        except BaseException as exc:
            record["status"] = "interrupted" if not isinstance(exc, Exception) else "failed"
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            content = json.dumps(record, ensure_ascii=False, indent=2)
            filename = f"claude_review_{call_id}.json"
            temporary = self.directory / f".{call_id}.tmp"
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.directory / filename)
            if archive and self.publish:
                try:
                    await self.publish(uri=f"{archive}/{filename}", content=content)
                except Exception:
                    logger.exception(
                        "Claude review archive copy failed; local receipt %s", filename
                    )
