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
        phone: Optional[str] = None,
        session_string: Optional[str] = None
    ) -> TelegramClient:
        """Создание новой сессии"""
        
        if session_id in self.sessions:
            raise ValueError(f"Session {session_id} already exists")
        
        client = TelegramClient(
            session_id=session_id,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            session_string=session_string
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
        
        # Удаляем из БД
        from .database import delete_session
        await delete_session(session_id)
    
    async def restore_sessions_from_db(self):
        """Восстановление всех сессий из БД при старте"""
        from .database import load_all_sessions
        
        sessions_data = await load_all_sessions()
        
        for session_data in sessions_data:
            try:
                session_id = session_data["session_id"]
                
                # Пропускаем если сессия уже существует
                if session_id in self.sessions:
                    continue
                
                # Восстанавливаем клиент из session string
                client = TelegramClient(
                    session_id=session_id,
                    api_id=session_data["api_id"],
                    api_hash=session_data["api_hash"],
                    phone=session_data["phone"],
                    session_string=session_data["session_string"]
                )
                
                # Пытаемся подключиться
                try:
                    await client.client.connect()
                    if client.client.is_connected:
                        client.is_connected = True
                        await client._setup_message_handler()
                        
                        # Получаем информацию о пользователе
                        user = await client.get_me()
                        
                        self.sessions[session_id] = client
                        self.sessions_info[session_id] = SessionInfo(
                            session_id=session_id,
                            status=SessionStatus.CONNECTED,
                            auth_method="phone",  # По умолчанию
                            user=user,
                            created_at=datetime.utcnow(),
                            connected_at=datetime.utcnow()
                        )
                        
                        logger.info(f"✅ Restored session {session_id} from database")
                    else:
                        logger.warning(f"⚠️ Session {session_id} restored but not connected")
                except Exception as e:
                    logger.error(f"❌ Failed to restore session {session_id}: {e}")
                    
            except Exception as e:
                logger.error(f"❌ Error restoring session {session_data.get('session_id', 'unknown')}: {e}")
    
    async def cleanup_all(self):
        """Закрытие всех сессий"""
        for client in self.sessions.values():
            await client.stop()
        
        self.sessions.clear()
        self.sessions_info.clear()


# Глобальный менеджер сессий
session_manager = SessionManager()