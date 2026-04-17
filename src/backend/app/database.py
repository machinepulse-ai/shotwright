"""MongoDB connection manager.

All cache and session data goes through MongoDB for now.
The abstraction layer (get_db / get_cache_collection) keeps a clear seam
so we can swap in Redis for cache and PG for relational data later.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None


async def connect_db() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.mongo_uri)
    db = _client[settings.mongo_db]
    await db["sessions"].create_index([("updated_at", -1)])
    await db["containers"].create_index([("session_id", 1), ("created_at", -1)])
    await db["projects"].create_index([("session_id", 1), ("created_at", -1)])
    await db["messages"].create_index([("session_id", 1), ("created_at", 1)])
    await db["events"].create_index([("session_id", 1), ("created_at", 1)])


async def close_db() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_db() -> AsyncIOMotorDatabase:
    assert _client is not None, "Database not connected — call connect_db() first"
    return _client[settings.mongo_db]


# --- Cache abstraction (backed by Mongo for now) ---

def get_cache_collection(name: str = "cache"):
    """Return a Mongo collection used as a key-value cache.

    Future: replace with a Redis client returning the same async get/set
    interface once cache volume justifies a dedicated store.
    """
    return get_db()[name]


# --- Session store abstraction ---

def get_session_collection():
    """Return the sessions collection.

    Future: migrate to PostgreSQL if relational queries are needed.
    """
    return get_db()["sessions"]


def get_container_collection():
    return get_db()["containers"]


def get_admin_collection():
    return get_db()["admin"]


def get_project_collection():
    return get_db()["projects"]


def get_message_collection():
    return get_db()["messages"]


def get_event_collection():
    return get_db()["events"]
