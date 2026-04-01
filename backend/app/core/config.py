import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # 优先读取环境变量，如果没找到则使用后面的默认值作为兜底
    SECRET_KEY: str = os.getenv("SECRET_KEY", "default-fallback-secret")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24

    # 【新增】服务器配置
    HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("SERVER_PORT", 8010))


settings = Settings()
