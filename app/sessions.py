from typing import Dict, Optional
from .client import TelegramClient
from .models import SessionStatus, SessionInfo
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SessionManager:
    """Менеджер активных Telegram сессий"""
    
    def __init__(self):
        self.sessions: Dict[str, TelegramClient] = {}
        self.sessions_info: Dict[str, SessionInfo] = {}
    
    def create_session(
        self,
        session_id: str,
        api_id: int,
        api_hash: str,
        auth_method: str = "qr",
        phone: Optional[str] = None
    ) -> TelegramClient:
        """Создание новой сессии"""
        
        if session_id in self.sessions:
            raise ValueError(f"Session {session_id} already exists")
        
        client = TelegramClient(
            session_id=session_id,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone
        )
        
        self.sessions[session_id] = client
        self.sessions_info[session_id] = SessionInfo(
            session_id=session_id,
            status=SessionStatus.PENDING,
            auth_method=auth_method,
            created_at=datetime.utcnow()
        )
        
        return client
    
    def get_session(self, session_id: str) -> Optional[TelegramClient]:
        """Получение сессии"""
        return self.sessions.get(session_id)
    
    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Получение информации о сессии"""
        return self.sessions_info.get(session_id)
    
    def update_session_status(
        self,
        session_id: str,
        status: SessionStatus,
        user: Optional[Dict] = None
    ):
        """Обновление статуса сессии"""
        if session_id in self.sessions_info:
            info = self.sessions_info[session_id]
            old_status = info.status
            info.status = status
            if user:
                info.user = user
            if status == SessionStatus.CONNECTED:
                info.connected_at = datetime.utcnow()
            
            logger.info(f"📝 Session {session_id} status: {old_status} → {status}")
    
    async def remove_session(self, session_id: str):
        """Удаление сессии"""
        if session_id in self.sessions:
            client = self.sessions[session_id]
            await client.stop()
            del self.sessions[session_id]
        
        if session_id in self.sessions_info:
            del self.sessions_info[session_id]
    
    async def cleanup_all(self):
        """Закрытие всех сессий"""
        for client in self.sessions.values():
            await client.stop()
        
        self.sessions.clear()
        self.sessions_info.clear()


# Глобальный менеджер сессий
session_manager = SessionManager()