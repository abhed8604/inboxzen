from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from pathlib import Path

# Ensure data directory exists
data_dir = Path(__file__).parent.parent / "data"
data_dir.mkdir(exist_ok=True)

# Database URL (SQLite with aiosqlite for async support)
DATABASE_URL = "sqlite+aiosqlite:///./data/inboxzen.db"

# Create async engine
engine = create_async_engine(DATABASE_URL, echo=False)

# Create async session factory
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Base class for all models
class Base(DeclarativeBase):
    pass

# Dependency to get database session
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

# Initialize database tables
async def init_db():
    async with engine.begin() as conn:
        # Import models here to ensure they are registered with Base
        from app.models import Account, Email, Settings
        await conn.run_sync(Base.metadata.create_all)