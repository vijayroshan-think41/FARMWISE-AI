from __future__ import annotations

from collections.abc import Iterable

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from pydantic import BaseModel, Field

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

try:
    from orchestrator.agent import root_agent
except ImportError:
    from Agents.orchestrator.agent import root_agent

app = FastAPI()


class ChatRequest(BaseModel):
    user_id: str
    message: str
    session_history: list[dict] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str


def _format_session_history(session_history: list[dict]) -> str:
    if not session_history:
        return ""

    lines = ["Previous conversation:"]
    for item in session_history:
        role = str(item.get("role", "unknown")).strip() or "unknown"
        message_text = str(item.get("message_text", "")).strip()
        if message_text:
            lines.append(f"{role}: {message_text}")
    return "\n".join(lines)


def _build_prompt(payload: ChatRequest) -> str:
    current_turn = f"user_id: {payload.user_id}\nmessage: {payload.message}"
    history = _format_session_history(payload.session_history)
    if history:
        return f"{history}\n\n{current_turn}"
    return current_turn


def _extract_text_from_parts(parts: Iterable[object]) -> str:
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts).strip()


@app.post("/agent/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    user_message = _build_prompt(request)
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=root_agent.name,
        user_id=request.user_id,
    )
    runner = Runner(
        agent=root_agent,
        app_name=root_agent.name,
        session_service=session_service,
    )
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    )

    reply = ""
    async for event in runner.run_async(
        user_id=request.user_id,
        session_id=session.id,
        new_message=content,
    ):
        if not event.is_final_response():
            continue

        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None)
        if parts:
            reply = _extract_text_from_parts(parts)

    return ChatResponse(reply=reply)
