from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

import database


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.from_user:
            await database.ensure_user(event.from_user.id, event.from_user.username)
            user = await database.get_user(event.from_user.id)
            if user and user["role"] == "trial" and user["trial_expires_at"]:
                if user["trial_expires_at"] < datetime.now(timezone.utc):
                    await database.set_role(event.from_user.id, "none")
                    user = await database.get_user(event.from_user.id)
            data["user"] = user
        return await handler(event, data)
