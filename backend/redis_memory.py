from langchain.memory import RedisChatMessageHistory

def get_redis_history(session_id: str):
    history = RedisChatMessageHistory(
        session_id=session_id,
        url="redis://localhost:6379"
    )
    return history