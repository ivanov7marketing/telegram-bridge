from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class SessionStatus(str, Enum):
    PENDING = "pending"
    AWAITING_CODE = "awaiting_code"
    AWAITING_QR = "awaiting_qr"
    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class SessionStartRequest(BaseModel):
    session_id: str = Field(..., description="Уникальный ID сессии")
    api_id: Optional[int] = Field(None, description="Telegram API ID")
    api_hash: Optional[str] = Field(None, description="Telegram API Hash")
    auth_method: str = Field(default="phone", description="Метод авторизации")
    phone: Optional[str] = Field(None, description="Номер телефона")


class CodeVerifyRequest(BaseModel):
    code: str = Field(..., description="Код подтверждения из Telegram")
    password: Optional[str] = Field(None, description="2FA пароль")


class SendMessageRequest(BaseModel):
    chat_id: str = Field(..., description="ID или username чата")
    text: str = Field(..., description="Текст сообщения")


class SessionInfo(BaseModel):
    session_id: str
    status: SessionStatus
    user: Optional[Dict[str, Any]] = None
    auth_method: str
    created_at: datetime
    connected_at: Optional[datetime] = None


class Dialog(BaseModel):
    id: int
    type: str
    title: Optional[str]
    username: Optional[str]
    unread_count: int
    last_message: Optional[Dict[str, Any]]


class Message(BaseModel):
    id: int
    from_user: Optional[Dict[str, Any]]
    text: Optional[str]
    date: datetime
    outgoing: bool