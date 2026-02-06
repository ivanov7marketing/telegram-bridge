import asyncpg
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Глобальный пул соединений
_pool: Optional[asyncpg.Pool] = None


async def init_db():
    """Инициализация подключения к БД"""
    global _pool
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.warning("⚠️ DATABASE_URL not set - session persistence disabled")
        return None
    
    try:
        # Парсим DATABASE_URL для asyncpg
        # Формат: postgresql://user:password@host:port/database
        _pool = await asyncpg.create_pool(
            database_url,
            min_size=1,
            max_size=10
        )
        
        # Создаем таблицу для хранения сессий
        async with _pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_sessions (
                    session_id VARCHAR(255) PRIMARY KEY,
                    session_string TEXT NOT NULL,
                    api_id INTEGER NOT NULL,
                    api_hash VARCHAR(255) NOT NULL,
                    phone VARCHAR(50),
                    webhook_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # На всякий случай добавляем колонку webhook_url, если БД уже существовала
            await conn.execute("""
                ALTER TABLE telegram_sessions
                ADD COLUMN IF NOT EXISTS webhook_url TEXT
            """)
        
        logger.info("✅ Database connection initialized")
        return _pool
        
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        return None


async def close_db():
    """Закрытие подключения к БД"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("🔌 Database connection closed")


async def save_session(
    session_id: str,
    session_string: str,
    api_id: int,
    api_hash: str,
    phone: Optional[str] = None,
    webhook_url: Optional[str] = None
):
    """Сохранение session string в БД"""
    if not _pool:
        return False
    
    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO telegram_sessions 
                (session_id, session_string, api_id, api_hash, phone, webhook_url, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
                ON CONFLICT (session_id) 
                DO UPDATE SET 
                    session_string = $2,
                    api_id = $3,
                    api_hash = $4,
                    phone = $5,
                    webhook_url = $6,
                    updated_at = CURRENT_TIMESTAMP
            """, session_id, session_string, api_id, api_hash, phone, webhook_url)
        
        logger.info(f"💾 Session {session_id} saved to database")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error saving session {session_id}: {e}")
        return False


async def load_session(session_id: str) -> Optional[dict]:
    """Загрузка session string из БД"""
    if not _pool:
        return None
    
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT session_string, api_id, api_hash, phone, webhook_url
                FROM telegram_sessions
                WHERE session_id = $1
            """, session_id)
            
            if row:
                logger.info(f"📂 Session {session_id} loaded from database")
                return {
                    "session_string": row["session_string"],
                    "api_id": row["api_id"],
                    "api_hash": row["api_hash"],
                    "phone": row["phone"],
                    "webhook_url": row["webhook_url"]
                }
            return None
            
    except Exception as e:
        logger.error(f"❌ Error loading session {session_id}: {e}")
        return None


async def load_all_sessions() -> list:
    """Загрузка всех сессий из БД"""
    if not _pool:
        return []
    
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT session_id, session_string, api_id, api_hash, phone, webhook_url
                FROM telegram_sessions
            """)
            
            sessions = []
            for row in rows:
                sessions.append({
                    "session_id": row["session_id"],
                    "session_string": row["session_string"],
                    "api_id": row["api_id"],
                    "api_hash": row["api_hash"],
                    "phone": row["phone"],
                    "webhook_url": row["webhook_url"]
                })
            
            logger.info(f"📂 Loaded {len(sessions)} sessions from database")
            return sessions
            
    except Exception as e:
        logger.error(f"❌ Error loading all sessions: {e}")
        return []


async def delete_session(session_id: str):
    """Удаление сессии из БД"""
    if not _pool:
        return False
    
    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM telegram_sessions
                WHERE session_id = $1
            """, session_id)
        
        logger.info(f"🗑️ Session {session_id} deleted from database")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error deleting session {session_id}: {e}")
        return False

