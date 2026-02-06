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
        from .database import delete_session

        # Сначала пытаемся аккуратно остановить клиента, но ошибки "уже остановлен"
        # или любые другие не должны блокировать удаление записи о сессии.
        client = self.sessions.get(session_id)
        if client:
            try:
                await client.stop()
            except Exception as e:
                # Это нормальная ситуация, если клиент уже остановлен или завершён.
                logger.warning(f"⚠️ Error while stopping session {session_id}: {e}")
            finally:
                # В любом случае убираем из памяти
                self.sessions.pop(session_id, None)
        
        # Удаляем метаданные о сессии из памяти
        if session_id in self.sessions_info:
            self.sessions_info.pop(session_id, None)
        
        # Удаляем запись о сессии из БД (даже если клиент уже был остановлен)
        try:
            await delete_session(session_id)
        except Exception as e:
            # Ошибки БД логируем, но не даём им "ронять" API-эндпоинт
            logger.error(f"❌ Error deleting session {session_id} from DB: {e}")
    
    async def restore_sessions_from_db(self):
        """Восстановление всех сессий из БД при старте"""
        from .database import load_all_sessions, delete_session
        
        sessions_data = await load_all_sessions()
        
        for session_data in sessions_data:
            try:
                session_id = session_data["session_id"]
                
                # Пропускаем если сессия уже существует
                if session_id in self.sessions:
                    continue
                
                # ВАЖНО: Пропускаем старые сессии со случайным форматом ID
                # Новый формат: tg_{account_id}_main
                # Старый формат: tg_{account_id}_{user_id}_{random_hex}
                if not session_id.endswith("_main") and "_" in session_id:
                    # Это старая сессия со случайным ID - удаляем её из БД
                    logger.warning(f"⚠️ Найдена старая сессия со случайным ID: {session_id}, удаляем из БД")
                    try:
                        await delete_session(session_id)
                        logger.info(f"✅ Удалена старая сессия {session_id} из БД")
                    except Exception as delete_error:
                        logger.error(f"❌ Ошибка при удалении старой сессии {session_id}: {delete_error}")
                    continue
                
                # Проверяем наличие session_string (обязательно для восстановления)
                if not session_data.get("session_string"):
                    logger.warning(f"⚠️ Сессия {session_id} не имеет session_string, пропускаем")
                    continue
                
                # Восстанавливаем клиент из session string
                try:
                    client = TelegramClient(
                        session_id=session_id,
                        api_id=session_data["api_id"],
                        api_hash=session_data["api_hash"],
                        phone=session_data["phone"],
                        session_string=session_data["session_string"]
                    )
                except Exception as client_error:
                    logger.error(f"❌ Ошибка создания клиента для сессии {session_id}: {client_error}")
                    # Если не удалось создать клиент, удаляем сессию из БД
                    try:
                        await delete_session(session_id)
                        logger.info(f"✅ Удалена проблемная сессия {session_id} из БД")
                    except Exception as delete_error:
                        logger.error(f"❌ Ошибка при удалении проблемной сессии {session_id}: {delete_error}")
                    continue
                
                # Пытаемся подключиться
                try:
                    await client.client.connect()
                    if client.client.is_connected:
                        client.is_connected = True
                        
                        # ВАЖНО: Восстанавливаем webhook_url ДО регистрации обработчика
                        # чтобы обработчик мог использовать webhook_url в замыкании
                        webhook_url = session_data.get("webhook_url")
                        if webhook_url:
                            client.webhook_url = webhook_url
                            logger.info(f"✅ Restored webhook URL for session {session_id}: {webhook_url}")
                        
                        # Регистрируем обработчик ПОСЛЕ установки webhook_url
                        await client._setup_message_handler()
                        
                        # ВАЖНО: Запускаем клиент для получения обновлений
                        # Без start() клиент подключен, но не получает сообщения
                        try:
                            if not client.client.is_started:
                                await client.client.start()
                                logger.info(f"🚀 Started client for session {session_id} - ready to receive messages")
                            else:
                                logger.info(f"✅ Client for session {session_id} already started")
                        except Exception as start_error:
                            logger.error(f"❌ Failed to start client for session {session_id}: {start_error}")
                            # Продолжаем работу, но клиент может не получать обновления
                        
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
                        # Удаляем сессию без подключения из БД
                        try:
                            await delete_session(session_id)
                            logger.info(f"✅ Удалена неподключенная сессия {session_id} из БД")
                        except Exception as delete_error:
                            logger.error(f"❌ Ошибка при удалении неподключенной сессии {session_id}: {delete_error}")
                except Exception as e:
                    logger.error(f"❌ Failed to restore session {session_id}: {e}")
                    # Удаляем сессию, которую не удалось восстановить
                    try:
                        await delete_session(session_id)
                        logger.info(f"✅ Удалена невосстановимая сессия {session_id} из БД")
                    except Exception as delete_error:
                        logger.error(f"❌ Ошибка при удалении невосстановимой сессии {session_id}: {delete_error}")
                    
            except Exception as e:
                logger.error(f"❌ Error restoring session {session_data.get('session_id', 'unknown')}: {e}")
    
    async def cleanup_all(self):
        """Закрытие всех сессий"""
        for session_id, client in list(self.sessions.items()):
            try:
                await client.stop()
            except Exception as e:
                logger.warning(f"⚠️ Error while stopping session {session_id} during cleanup: {e}")
        
        self.sessions.clear()
        self.sessions_info.clear()


# Глобальный менеджер сессий
session_manager = SessionManager()