from pyrogram import Client, filters
from pyrogram.errors import PhoneCodeInvalid, SessionPasswordNeeded, FloodWait
from typing import Optional, List, Dict, Callable
import logging
import asyncio
from datetime import datetime
from .qr_auth import QRAuthHandler

logger = logging.getLogger(__name__)


class TelegramClient:
    """Обертка над Pyrogram клиентом"""
    
    def __init__(
        self,
        session_id: str,
        api_id: int,
        api_hash: str,
        phone: Optional[str] = None,
        workdir: str = "./sessions"
    ):
        self.session_id = session_id
        self.phone = phone
        self.api_id = api_id
        self.api_hash = api_hash
        
        self.client = Client(
            name=session_id,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone,
            workdir=workdir
        )
        
        self.is_connected = False
        self._phone_code_hash = None
        self.webhook_url: Optional[str] = None
        self.qr_handler: Optional[QRAuthHandler] = None
        self._message_handler_registered = False
    
    async def start_qr_auth(self) -> str:
        """
        Запуск авторизации через QR-код
        """
        try:
            # Подключаемся БЕЗ авторизации
            if not self.client.is_connected:
                await self.client.connect()
            
            # Создаем обработчик QR
            self.qr_handler = QRAuthHandler(self.client)
            
            # Генерируем QR-код
            qr_link = await self.qr_handler.generate_qr_link()
            qr_image = self.qr_handler.generate_qr_image(qr_link)
            
            # Запускаем ожидание сканирования в фоне
            asyncio.create_task(self._wait_qr_scan())
            
            return qr_image
            
        except Exception as e:
            logger.error(f"QR auth start error: {e}")
            raise
    
    async def _wait_qr_scan(self):
        """Ожидание сканирования QR-кода в фоне"""
        if not self.qr_handler:
            logger.error("QR handler not initialized")
            return
        
        try:
            logger.info(f"🔍 Starting QR scan monitoring for session {self.session_id}")
            success = await self.qr_handler.wait_for_auth(timeout=120)
            
            if success:
                self.is_connected = True
                await self._setup_message_handler()
                
                # Обновляем статус сессии
                from .sessions import session_manager
                from .models import SessionStatus
                user = await self.get_me()
                session_manager.update_session_status(
                    self.session_id,
                    SessionStatus.CONNECTED,
                    user
                )
                
                logger.info(f"✅ Session {self.session_id} connected via QR")
            else:
                logger.warning(f"⏱️ QR auth timeout for session {self.session_id}")
                
        except Exception as e:
            logger.error(f"❌ QR scan wait error: {e}", exc_info=True)
    
    async def start_phone_auth(self):
        """
        Запуск авторизации по номеру телефона
        """
        if not self.phone:
            raise ValueError("Phone number required")
        
        await self.client.connect()
        sent_code = await self.client.send_code(self.phone)
        self._phone_code_hash = sent_code.phone_code_hash
        
        return {
            "phone_code_hash": self._phone_code_hash,
            "next_type": sent_code.next_type,
            "timeout": sent_code.timeout
        }
    
    async def verify_code(self, code: str, password: Optional[str] = None):
        """Проверка кода подтверждения"""
        try:
            await self.client.sign_in(self.phone, self._phone_code_hash, code)
            self.is_connected = True
            await self._setup_message_handler()
            
        except SessionPasswordNeeded:
            if not password:
                raise ValueError("2FA password required")
            await self.client.check_password(password)
            self.is_connected = True
            await self._setup_message_handler()
            
        except PhoneCodeInvalid:
            raise ValueError("Invalid verification code")
    
    async def get_me(self) -> Dict:
        """Получение информации о текущем пользователе"""
        me = await self.client.get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone_number,
            "is_premium": me.is_premium
        }
    
    async def get_dialogs(self, limit: int = 50) -> List[Dict]:
        """Получение списка диалогов"""
        dialogs = []
        async for dialog in self.client.get_dialogs(limit=limit):
            dialogs.append({
                "id": dialog.chat.id,
                "type": dialog.chat.type.value,
                "title": dialog.chat.title or dialog.chat.first_name or "Unknown",
                "username": dialog.chat.username,
                "unread_count": dialog.unread_messages_count,
                "last_message": {
                    "text": dialog.top_message.text if dialog.top_message else None,
                    "date": dialog.top_message.date.isoformat() if dialog.top_message else None
                } if dialog.top_message else None
            })
        return dialogs
    
    async def get_messages(
        self,
        chat_id: str,
        limit: int = 50,
        offset_id: int = 0
    ) -> List[Dict]:
        """Получение сообщений из чата"""
        messages = []
        
        try:
            async for message in self.client.get_chat_history(
                chat_id,
                limit=limit,
                offset_id=offset_id
            ):
                messages.append({
                    "id": message.id,
                    "from_user": {
                        "id": message.from_user.id if message.from_user else None,
                        "username": message.from_user.username if message.from_user else None,
                        "first_name": message.from_user.first_name if message.from_user else None
                    } if message.from_user else None,
                    "text": message.text or message.caption,
                    "date": message.date.isoformat(),
                    "outgoing": message.outgoing
                })
        except FloodWait as e:
            logger.warning(f"FloodWait: waiting {e.value} seconds")
            await asyncio.sleep(e.value)
            return await self.get_messages(chat_id, limit, offset_id)
            
        return messages
    
    async def send_message(self, chat_id: str, text: str):
        """Отправка сообщения"""
        return await self.client.send_message(chat_id, text)
    
    def set_webhook(self, webhook_url: str):
        """Установка webhook для входящих сообщений"""
        self.webhook_url = webhook_url
    
    async def _setup_message_handler(self):
        """Настройка обработчика входящих сообщений"""
        if self._message_handler_registered:
            return
        
        @self.client.on_message(filters.incoming & ~filters.service)
        async def handle_incoming(client, message):
            if self.webhook_url:
                await self._send_to_webhook(message)
        
        self._message_handler_registered = True
    
    async def _send_to_webhook(self, message):
        """Отправка сообщения на webhook"""
        import httpx
        
        try:
            async with httpx.AsyncClient() as http_client:
                await http_client.post(
                    self.webhook_url,
                    json={
                        "session_id": self.session_id,
                        "message": {
                            "id": message.id,
                            "chat_id": message.chat.id,
                            "from_user": {
                                "id": message.from_user.id if message.from_user else None,
                                "username": message.from_user.username if message.from_user else None,
                                "first_name": message.from_user.first_name if message.from_user else None
                            } if message.from_user else None,
                            "text": message.text or message.caption,
                            "date": message.date.isoformat()
                        }
                    },
                    timeout=10.0
                )
        except Exception as e:
            logger.error(f"Webhook error: {e}")
    
    async def stop(self):
        """Остановка клиента"""
        if self.client.is_connected:
            await self.client.stop()
        self.is_connected = False