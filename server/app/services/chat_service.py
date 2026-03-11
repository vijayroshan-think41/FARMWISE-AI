from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ChatMessage, ChatSession, User, utcnow
from app.services.agent_client import send_chat_request


def _build_session_title(message: str) -> str:
    compact = " ".join(message.split())
    return compact[:60] if len(compact) <= 60 else f"{compact[:57]}..."


async def _get_user_with_region(db: AsyncSession, user_id: UUID) -> User:
    result = await db.execute(
        select(User).options(selectinload(User.region)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _get_session_for_user(db: AsyncSession, session_id: UUID, user_id: UUID) -> ChatSession:
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return session


async def list_user_sessions(db: AsyncSession, *, user_id: UUID) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(desc(ChatSession.updated_at), desc(ChatSession.created_at))
    )
    return list(result.scalars().all())


async def get_session_history(db: AsyncSession, *, session_id: UUID, user_id: UUID) -> ChatSession:
    return await _get_session_for_user(db, session_id, user_id)


async def process_chat_message(
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID | None,
    message: str,
) -> tuple[ChatSession, str]:
    await _get_user_with_region(db, user_id)
    if session_id is None:
        session = ChatSession(user_id=user_id, title=_build_session_title(message))
        db.add(session)
        await db.flush()
        session_history: list[dict[str, str]] = []
    else:
        session = await _get_session_for_user(db, session_id, user_id)
        session_history = [
            {"role": msg.role, "message_text": msg.message_text}
            for msg in sorted(session.messages, key=lambda item: item.created_at)
        ]

    payload = {
        "user_id": str(user_id),
        "message": message,
        "session_history": session_history,
    }
    assistant_reply = await send_chat_request(payload)

    db.add(
        ChatMessage(
            session_id=session.id,
            role="user",
            message_text=message,
            message_metadata={"source": "client"},
        )
    )
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            message_text=assistant_reply,
            message_metadata={"source": "agent_service"},
        )
    )
    session.updated_at = utcnow()
    await db.commit()
    await db.refresh(session)
    return session, assistant_reply
