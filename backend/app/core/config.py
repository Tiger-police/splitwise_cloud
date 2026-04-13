import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ENV_FILE = PROJECT_ROOT / "backend" / ".env"

load_dotenv(BACKEND_ENV_FILE)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # 优先读取环境变量，如果没找到则使用后面的默认值作为兜底
    SECRET_KEY: str = os.getenv("SECRET_KEY", "default-fallback-secret")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    OPENWEBUI_JWT_SECRET: str = os.getenv("OPENWEBUI_JWT_SECRET", "")
    OPENWEBUI_JWT_ALGORITHM: str = os.getenv("OPENWEBUI_JWT_ALGORITHM", "HS256")
    OPENWEBUI_SKIP_SIGNATURE_VERIFY: bool = env_bool("OPENWEBUI_SKIP_SIGNATURE_VERIFY", False)
    OPENWEBUI_USER_ID_CLAIM: str = "id"
    OPENWEBUI_USERNAME_CLAIMS: tuple[str, ...] = tuple(
        claim.strip()
        for claim in os.getenv("OPENWEBUI_USERNAME_CLAIMS", "sub,username,email,name,preferred_username").split(",")
        if claim.strip()
    )
    OPENWEBUI_ROLE_CLAIMS: tuple[str, ...] = tuple(
        claim.strip()
        for claim in os.getenv("OPENWEBUI_ROLE_CLAIMS", "role,groups").split(",")
        if claim.strip()
    )
    OPENWEBUI_EXPECTED_ISSUER: str = os.getenv("OPENWEBUI_EXPECTED_ISSUER", "")
    OPENWEBUI_EXPECTED_AUDIENCE: str = os.getenv("OPENWEBUI_EXPECTED_AUDIENCE", "")

    # 服务器配置
    HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("SERVER_PORT", 8010))
    PUBLIC_BASE_URL: str = os.getenv("SERVER_PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}")

    # 外部服务配置
    PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://10.144.144.2:9090")
    ALGORITHM_API_URL: str = os.getenv("ALGORITHM_API_URL", "http://10.144.144.2:5000/api/calculate")
    NETWORK_PING_COUNT: int = int(os.getenv("NETWORK_PING_COUNT", 4))
    NETWORK_PING_TIMEOUT_SECONDS: float = float(os.getenv("NETWORK_PING_TIMEOUT_SECONDS", 1.0))
    NETWORK_ENABLE_IPERF3: bool = env_bool("NETWORK_ENABLE_IPERF3", False)
    NETWORK_IPERF3_DURATION_SECONDS: int = int(os.getenv("NETWORK_IPERF3_DURATION_SECONDS", 3))
    NETWORK_DEFAULT_EDGE_RTT_MS: float = float(os.getenv("NETWORK_DEFAULT_EDGE_RTT_MS", 4.84))
    NETWORK_DEFAULT_CLOUD_RTT_MS: float = float(os.getenv("NETWORK_DEFAULT_CLOUD_RTT_MS", 2.72))
    NETWORK_DEFAULT_BANDWIDTH_MBPS: float = float(os.getenv("NETWORK_DEFAULT_BANDWIDTH_MBPS", 1000.0))
    NETWORK_DEFAULT_PACKET_LOSS: float = float(os.getenv("NETWORK_DEFAULT_PACKET_LOSS", 0.0))
    PROMETHEUS_QUERY_TIMEOUT: float = float(os.getenv("PROMETHEUS_QUERY_TIMEOUT", 3.0))
    PROMETHEUS_CACHE_SECONDS: float = float(os.getenv("PROMETHEUS_CACHE_SECONDS", 15.0))
    NETWORK_PROBE_CACHE_SECONDS: float = float(os.getenv("NETWORK_PROBE_CACHE_SECONDS", 30.0))
    NETWORK_MAX_CONCURRENT_PROBES: int = int(os.getenv("NETWORK_MAX_CONCURRENT_PROBES", 5))
    FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"


settings = Settings()
