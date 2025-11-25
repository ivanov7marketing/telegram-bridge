import qrcode
from io import BytesIO
import base64
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
import logging
import asyncio

logger = logging.getLogger(__name__)


class QRAuthHandler:
    """Обработчик QR-авторизации для Telegram"""
    
    def __init__(self, client: Client):
        self.client = client
        self.qr_token = None
        self.qr_expires_at = None
        self._auth_task = None
    
    async def generate_qr_link(self) -> str:
        """
        Генерация ссылки для QR-кода (tg://login?token=...)
        """
        try:
            # Получаем токен для QR авторизации
            from pyrogram.raw import functions
            from pyrogram.raw.types import auth
            
            # Запрашиваем QR login token
            result = await self.client.invoke(
                functions.auth.ExportLoginToken(
                    api_id=self.client.api_id,
                    api_hash=self.client.api_hash,
                    except_ids=[]
                )
            )
            
            if isinstance(result, auth.LoginToken):
                # Токен в base64url формате
                token = base64.urlsafe_b64encode(result.token).decode('utf-8').rstrip('=')
                self.qr_token = token
                self.qr_expires_at = result.expires
                
                qr_link = f"tg://login?token={token}"
                return qr_link
            
            raise Exception("Failed to get login token")
            
        except Exception as e:
            logger.error(f"QR generation error: {e}")
            raise
    
    def generate_qr_image(self, link: str) -> str:
        """
        Генерация QR-кода в формате base64 PNG
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(link)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Конвертируем в base64
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    async def wait_for_auth(self, timeout: int = 60) -> bool:
        """
        Ожидание сканирования QR-кода
        """
        try:
            from pyrogram.raw import functions, types
            
            start_time = asyncio.get_event_loop().time()
            
            while asyncio.get_event_loop().time() - start_time < timeout:
                # Проверяем статус авторизации
                result = await self.client.invoke(
                    functions.auth.ExportLoginToken(
                        api_id=self.client.api_id,
                        api_hash=self.client.api_hash,
                        except_ids=[]
                    )
                )
                
                if isinstance(result, types.auth.LoginTokenSuccess):
                    # Авторизация успешна!
                    authorization = result.authorization
                    
                    if isinstance(authorization, types.auth.Authorization):
                        return True
                
                elif isinstance(result, types.auth.LoginTokenMigrateTo):
                    # Нужна миграция на другой DC
                    logger.info(f"Migrating to DC {result.dc_id}")
                    await self.client.connect()
                    
                await asyncio.sleep(2)  # Проверяем каждые 2 секунды
            
            return False
            
        except SessionPasswordNeeded:
            # Требуется 2FA
            logger.warning("2FA required")
            return False
        except Exception as e:
            logger.error(f"Auth wait error: {e}")
            return False