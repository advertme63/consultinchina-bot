from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

import database


class AccessMiddleware(BaseMiddleware):
    """Бот открыт всем (ТЗ 2): только регистрирует пользователя и передаёт его запись в хендлеры."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.from_user:
            await database.ensure_user(event.from_user.id, event.from_user.username, event.from_user.first_name)
            data["user"] = await database.get_user(event.from_user.id)
        return await handler(event, data)
