from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from .models import *
from .sessions import session_manager
import logging
from typing import Optional
import os

# Default API credentials из переменных окружения
DEFAULT_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
DEFAULT_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Telegram Bridge API",
    description="REST API для работы с Telegram через Pyrogram",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "Telegram Bridge",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/sessions/start")
async def start_session(request: SessionStartRequest):
    """
    Создание и запуск новой Telegram сессии
    """
    try:
        # Использовать переданные credentials или взять из env
        api_id = request.api_id or DEFAULT_API_ID
        api_hash = request.api_hash or DEFAULT_API_HASH
        
        if not api_id or not api_hash:
            raise HTTPException(
                status_code=400,
                detail="Telegram API credentials not configured. Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables."
            )
        
        # Создаем сессию с полученными credentials
        client = session_manager.create_session(
            session_id=request.session_id,
            api_id=api_id,
            api_hash=api_hash,
            auth_method=request.auth_method,
            phone=request.phone
        )
        
        if request.auth_method == "qr":
            # QR-авторизация
            qr_image = await client.start_qr_auth()
            
            session_manager.update_session_status(
                request.session_id,
                SessionStatus.AWAITING_QR
            )
            
            return {
                "session_id": request.session_id,
                "status": "awaiting_qr",
                "qr_code": qr_image,
                "auth_method": "qr"
            }
        
        else:
            # Phone-авторизация
            if not request.phone:
                raise HTTPException(400, "Phone number required for phone auth")
            
            result = await client.start_phone_auth()
            
            session_manager.update_session_status(
                request.session_id,
                SessionStatus.AWAITING_CODE
            )
            
            return {
                "session_id": request.session_id,
                "status": "awaiting_code",
                "auth_method": "phone",
                **result
            }
    
    except Exception as e:
        logger.error(f"Failed to start session: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.get("/sessions/{session_id}/qr")
async def get_qr_code(session_id: str):
    """
    Получение нового QR-кода (для обновления)
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    if not client.qr_handler:
        raise HTTPException(400, "QR auth not initialized")
    
    try:
        qr_link = await client.qr_handler.generate_qr_link()
        qr_image = client.qr_handler.generate_qr_image(qr_link)
        
        return {"qr_code": qr_image}
    
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        raise HTTPException(500, str(e))


@app.post("/sessions/{session_id}/verify")
async def verify_code(session_id: str, request: CodeVerifyRequest):
    """
    Проверка кода подтверждения (для phone auth)
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    try:
        await client.verify_code(request.code, request.password)
        
        user = await client.get_me()
        
        session_manager.update_session_status(
            session_id,
            SessionStatus.CONNECTED,
            user
        )
        
        return {
            "session_id": session_id,
            "status": "connected",
            "user": user
        }
    
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Code verification failed: {e}")
        raise HTTPException(500, str(e))


@app.get("/sessions/{session_id}/status")
async def get_status(session_id: str):
    """
    Получение статуса сессии
    """
    client = session_manager.get_session(session_id)
    info = session_manager.get_session_info(session_id)
    
    if not client or not info:
        return {"status": "not_found"}
    
    # Обновляем статус если клиент подключился
    if client.is_connected and info.status != SessionStatus.CONNECTED:
        user = await client.get_me()
        session_manager.update_session_status(
            session_id,
            SessionStatus.CONNECTED,
            user
        )
        info = session_manager.get_session_info(session_id)
    
    return {
        "session_id": session_id,
        "status": info.status.value,
        "auth_method": info.auth_method,
        "user": info.user,
        "connected": client.is_connected,
        "created_at": info.created_at.isoformat(),
        "connected_at": info.connected_at.isoformat() if info.connected_at else None
    }


@app.get("/sessions/{session_id}/dialogs")
async def get_dialogs(
    session_id: str,
    limit: int = Query(50, ge=1, le=100)
):
    """
    Получение списка диалогов
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    if not client.is_connected:
        raise HTTPException(400, "Session not connected")
    
    try:
        dialogs = await client.get_dialogs(limit)
        return {"dialogs": dialogs}
    
    except Exception as e:
        logger.error(f"Failed to get dialogs: {e}")
        raise HTTPException(500, str(e))


@app.get("/sessions/{session_id}/messages/{chat_id}")
async def get_messages(
    session_id: str,
    chat_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset_id: int = Query(0, ge=0)
):
    """
    Получение сообщений из чата
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    if not client.is_connected:
        raise HTTPException(400, "Session not connected")
    
    try:
        messages = await client.get_messages(chat_id, limit, offset_id)
        return {"messages": messages}
    
    except Exception as e:
        logger.error(f"Failed to get messages: {e}")
        raise HTTPException(500, str(e))


@app.post("/sessions/{session_id}/send")
async def send_message(session_id: str, request: SendMessageRequest):
    """
    Отправка сообщения
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    if not client.is_connected:
        raise HTTPException(400, "Session not connected")
    
    try:
        message = await client.send_message(request.chat_id, request.text)
        
        return {
            "success": True,
            "message_id": message.id,
            "date": message.date.isoformat()
        }
    
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        raise HTTPException(500, str(e))


@app.post("/sessions/{session_id}/webhook")
async def set_webhook(session_id: str, webhook_url: str):
    """
    Установка webhook для входящих сообщений
    """
    client = session_manager.get_session(session_id)
    if not client:
        raise HTTPException(404, "Session not found")
    
    client.set_webhook(webhook_url)
    
    return {"success": True, "webhook_url": webhook_url}


@app.delete("/sessions/{session_id}")
async def stop_session(session_id: str):
    """
    Остановка и удаление сессии
    """
    await session_manager.remove_session(session_id)
    return {"success": True}


@app.on_event("startup")
async def startup():
    logger.info("🚀 Telegram Bridge API started")


@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 Shutting down Telegram Bridge...")
    await session_manager.cleanup_all()

@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 Shutting down Telegram Bridge...")
    await session_manager.cleanup_all()


# Добавить в конец файла:
if __name__ == "__main__":
    import uvicorn
    import os
    
    port = int(os.getenv("PORT", 8001))
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
