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
        workdir: str = "./sessions",
        session_string: Optional[str] = None
    ):
        self.session_id = session_id
        self.phone = phone
        self.api_id = api_id
        self.api_hash = api_hash
        
        # Если есть session_string, используем его для восстановления
        if session_string:
            # Правильный способ восстановления сессии из session_string в Pyrogram
            # Pyrogram автоматически распознает session_string и использует StringSession
            self.client = Client(
                name=session_id,
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,  # Pyrogram автоматически использует StringSession
                workdir=workdir
            )
        else:
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
                
                # ВАЖНО: Запускаем клиент для получения обновлений
                if not self.client.is_started:
                    await self.client.start()
                    logger.info(f"🚀 Started client for session {self.session_id} - ready to receive messages")
                
                # Сохраняем session string после успешной авторизации
                await self._save_session_to_db()
                
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
            
            # ВАЖНО: Запускаем клиент для получения обновлений
            if not self.client.is_started:
                await self.client.start()
                logger.info(f"🚀 Started client for session {self.session_id} - ready to receive messages")
            
            # Сохраняем session string после успешной авторизации
            await self._save_session_to_db()
            
        except SessionPasswordNeeded:
            if not password:
                raise ValueError("2FA password required")
            await self.client.check_password(password)
            self.is_connected = True
            await self._setup_message_handler()
            
            # ВАЖНО: Запускаем клиент для получения обновлений
            if not self.client.is_started:
                await self.client.start()
                logger.info(f"🚀 Started client for session {self.session_id} - ready to receive messages")
            
            # Сохраняем session string после успешной авторизации
            await self._save_session_to_db()
            
        except PhoneCodeInvalid:
            raise ValueError("Invalid verification code")
    
    async def _save_session_to_db(self):
        """Сохранение session string в БД"""
        try:
            from .database import save_session
            
            session_string = await self.export_session_string()
            await save_session(
                session_id=self.session_id,
                session_string=session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                phone=self.phone,
                webhook_url=self.webhook_url
            )
        except Exception as e:
            logger.error(f"Failed to save session to DB: {e}")
    
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
    
    async def import_contact(self, phone: str, first_name: str = "", last_name: str = "") -> Optional[Dict]:
        """
        Импорт контакта по номеру телефона в Telegram.
        
        Args:
            phone: Номер телефона в формате +79991234567 или 79991234567
            first_name: Имя контакта (опционально)
            last_name: Фамилия контакта (опционально)
            
        Returns:
            Dict с информацией о пользователе (user_id, username, first_name, phone) или None если не найден
        """
        try:
            # Нормализуем номер (убираем пробелы, дефисы, скобки)
            phone = phone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            
            # Если номер начинается с 8, заменяем на +7
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            elif not phone.startswith('+'):
                phone = '+' + phone
            
            logger.info(f"📥 Importing contact for {phone}")
            
            # Убираем + для использования в API
            phone_clean = phone.lstrip('+')
            
            # Используем raw API ImportContacts с правильным синтаксисом
            try:
                from pyrogram.raw.types import InputPhoneContact
                from pyrogram.raw import functions
                import random
                
                logger.info(f"📥 Importing contact {phone} to Telegram")
                
                # Создаём контакт для импорта
                contact = InputPhoneContact(
                    client_id=random.randint(0, 2**31 - 1),
                    phone=phone_clean,
                    first_name=first_name or "",
                    last_name=last_name or ""
                )
                
                # Импортируем контакт через raw API с именованным параметром contacts
                import_result = await self.client.invoke(
                    functions.contacts.ImportContacts(contacts=[contact])
                )
                
                logger.info(f"✅ Contact import result: {len(import_result.users) if import_result.users else 0} users found")
                
                # Получаем импортированного пользователя
                if import_result.users and len(import_result.users) > 0:
                    user = import_result.users[0]
                    user_id = user.id
                    
                    # Формируем информацию о пользователе
                    user_info = {
                        "user_id": user_id,
                        "id": user_id,  # Для совместимости
                        "chat_id": user_id,  # Для совместимости
                        "phone": phone,
                        "username": getattr(user, 'username', None),
                        "first_name": getattr(user, 'first_name', first_name) or first_name,
                        "last_name": getattr(user, 'last_name', last_name) or last_name
                    }
                    
                    logger.info(f"✅ Contact imported successfully: user_id={user_id}, username={user_info.get('username')}")
                    return user_info
                else:
                    logger.warning(f"⚠️ User not found after import for {phone}")
                    return None
                    
            except Exception as import_error:
                logger.error(f"❌ Contact import failed for {phone}: {import_error}")
                # Пробуем альтернативный способ через get_users
                try:
                    user = await self.client.get_users(phone_clean)
                    
                    if user:
                        user_id = user.id if hasattr(user, 'id') else None
                        if user_id:
                            user_info = {
                                "user_id": user_id,
                                "id": user_id,
                                "chat_id": user_id,
                                "phone": phone,
                                "username": getattr(user, 'username', None),
                                "first_name": getattr(user, 'first_name', first_name) or first_name,
                                "last_name": getattr(user, 'last_name', last_name) or last_name
                            }
                            
                            logger.info(f"✅ Found user via get_users: user_id={user_id}")
                            return user_info
                    else:
                        logger.warning(f"⚠️ User not found via get_users for {phone}")
                        return None
                except Exception as get_users_error:
                    logger.error(f"❌ get_users also failed: {get_users_error}")
                    return None
        
        except Exception as e:
            logger.error(f"❌ Failed to import contact for {phone}: {e}", exc_info=True)
            return None
    
    async def send_message_by_phone(self, phone: str, text: str):
        """
        Отправка сообщения по номеру телефона.
        Поддерживает отправку первого сообщения без предыдущей переписки.
        
        ВАЖНО: Перед отправкой импортирует контакт в Telegram, так как
        Telegram требует, чтобы контакт был добавлен перед отправкой первого сообщения.
        
        Args:
            phone: Номер телефона в формате +79991234567 или 79991234567
            text: Текст сообщения
            
        Returns:
            Message объект от Pyrogram
            
        Raises:
            ValueError: Если номер невалидный или пользователь не найден
        """
        try:
            # Нормализуем номер (убираем пробелы, дефисы, скобки)
            phone = phone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            
            # Если номер начинается с 8, заменяем на +7
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            elif not phone.startswith('+'):
                phone = '+' + phone
            
            logger.info(f"📱 Attempting to send message to {phone}")
            
            # Убираем + для использования в API
            phone_clean = phone.lstrip('+')
            
            # ВАЖНО: Сначала импортируем контакт в Telegram
            # Telegram требует, чтобы контакт был добавлен перед отправкой первого сообщения
            try:
                from pyrogram.raw.types import InputPhoneContact
                from pyrogram.raw import functions
                import random
                
                logger.info(f"📥 Importing contact {phone} before sending message")
                
                # Создаём контакт для импорта
                contact = InputPhoneContact(
                    client_id=random.randint(0, 2**31 - 1),
                    phone=phone_clean,
                    first_name="",  # Можно оставить пустым
                    last_name=""
                )
                
                # Импортируем контакт через raw API с именованным параметром contacts
                import_result = await self.client.invoke(
                    functions.contacts.ImportContacts(contacts=[contact])
                )
                
                logger.info(f"✅ Contact import result: {len(import_result.users) if import_result.users else 0} users found")
                
                # Получаем импортированного пользователя
                if import_result.users and len(import_result.users) > 0:
                    user = import_result.users[0]
                    user_id = user.id
                    logger.info(f"✅ Found user ID: {user_id} for phone {phone}")
                    
                    # Теперь отправляем сообщение по user_id
                    message = await self.client.send_message(user_id, text)
                    logger.info(f"✅ Message sent to {phone} (user_id={user_id}): message_id={message.id}")
                    return message
                else:
                    # Если пользователь не найден после импорта, пробуем альтернативные методы
                    logger.warning(f"⚠️ User not found after import for {phone}, trying alternative methods")
                    raise ValueError(f"User with phone {phone} not found after import")
                    
            except ValueError:
                # Пробрасываем ValueError дальше
                raise
            except Exception as import_error:
                logger.warning(f"⚠️ Contact import failed for {phone}: {import_error}, trying direct send")
                
                # Fallback 1: Пробуем отправить напрямую по номеру (может сработать если контакт уже есть)
                try:
                    message = await self.client.send_message(phone, text)
                    logger.info(f"✅ Message sent directly to {phone}: message_id={message.id}")
                    return message
                except Exception as direct_error:
                    logger.warning(f"⚠️ Direct send failed: {direct_error}, trying get_users")
                    
                    # Fallback 2: Пробуем через get_users
                    try:
                        users = await self.client.get_users(phone_clean)
                        
                        if users:
                            user = users[0] if isinstance(users, list) else users
                            logger.info(f"✅ Found user by get_users: {user.id}")
                            message = await self.client.send_message(user.id, text)
                            return message
                        else:
                            raise ValueError(f"User with phone {phone} not found")
                    except Exception as get_users_error:
                        logger.error(f"❌ All methods failed for {phone}")
                        logger.error(f"  - Import error: {import_error}")
                        logger.error(f"  - Direct send error: {direct_error}")
                        logger.error(f"  - Get users error: {get_users_error}")
                        raise ValueError(f"Cannot send message to {phone}: User not found or contact import failed. Error: {str(import_error)}")
        
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to send message to {phone}: {e}", exc_info=True)
            raise ValueError(f"Failed to send message to {phone}: {str(e)}")
    
    async def set_webhook(self, webhook_url: str):
        """Установка webhook для входящих сообщений"""
        self.webhook_url = webhook_url
        logger.info(f"🔔 Setting webhook for session {self.session_id}: {webhook_url}")
        
        # Если обработчик уже зарегистрирован, нужно перерегистрировать его
        # чтобы замыкание обновилось с новым webhook_url
        if self._message_handler_registered:
            # Сбрасываем флаг и перерегистрируем обработчик
            self._message_handler_registered = False
            await self._setup_message_handler()
        else:
            # Если обработчик ещё не зарегистрирован, просто регистрируем его
            await self._setup_message_handler()
    
    async def _setup_message_handler(self):
        """Настройка обработчика входящих сообщений"""
        if self._message_handler_registered:
            logger.debug(f"[webhook] Обработчик уже зарегистрирован для сессии {self.session_id}")
            return
        
        logger.info(f"📝 Registering message handler for session {self.session_id}, webhook_url={self.webhook_url}")
        
        @self.client.on_message(filters.incoming & ~filters.service)
        async def handle_incoming(client, message):
            # Пропускаем исходящие сообщения (от бота)
            if message.outgoing:
                logger.debug(f"[webhook] Пропускаем исходящее сообщение {message.id} для сессии {self.session_id}")
                return
            
            logger.info(f"📨 Received incoming message {message.id} for session {self.session_id}, webhook_url={self.webhook_url}")
            
            if self.webhook_url:
                await self._send_to_webhook(message)
            else:
                logger.warning(f"⚠️ Webhook URL не настроен для сессии {self.session_id}, сообщение {message.id} не будет отправлено")
        
        self._message_handler_registered = True
        logger.info(f"✅ Message handler registered for session {self.session_id}")
    
    async def _send_to_webhook(self, message):
        """Отправка сообщения на webhook"""
        import httpx
        
        if not self.webhook_url:
            logger.debug(f"[webhook] Webhook URL не настроен для сессии {self.session_id}")
            return
        
        try:
            # Формируем payload в формате, который ожидает основное приложение
            payload = {
                "session_id": self.session_id,
                "message": {
                    "id": str(message.id),
                    "chat_id": str(message.chat.id),
                    "from_user": {
                        "id": message.from_user.id if message.from_user else None,
                        "username": message.from_user.username if message.from_user else None,
                        "phone": getattr(message.from_user, 'phone', None)
                    } if message.from_user else None,
                    "text": message.text or message.caption or "",
                    "date": message.date.isoformat() if message.date else None
                }
            }
            
            logger.info(f"📨 Sending webhook for session {self.session_id} to {self.webhook_url}")
            
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                response = await http_client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                logger.info(f"📨 Webhook response for session {self.session_id}: {response.status_code}")
                
                if response.status_code != 200:
                    logger.warning(f"⚠️ Webhook returned non-200 status: {response.status_code}, body: {response.text[:200]}")
                    
        except httpx.TimeoutException:
            logger.error(f"❌ Timeout при вызове webhook для сессии {self.session_id}")
        except httpx.ConnectError:
            logger.error(f"❌ Ошибка подключения к webhook для сессии {self.session_id}: {self.webhook_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка при вызове webhook для сессии {self.session_id}: {e}", exc_info=True)
    
    async def export_session_string(self) -> str:
        """Экспорт session string для сохранения"""
        try:
            return await self.client.export_session_string()
        except Exception as e:
            logger.error(f"Error exporting session string: {e}")
            raise
    
    async def stop(self):
        """Остановка клиента"""
        if self.client.is_connected:
            await self.client.stop()
        self.is_connected = False