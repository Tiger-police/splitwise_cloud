from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import bcrypt
import jwt
from app.core.config import settings

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_internal_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def decode_openwebui_access_token(token: str) -> dict[str, Any]:
    if settings.OPENWEBUI_SKIP_SIGNATURE_VERIFY:
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
            algorithms=[settings.OPENWEBUI_JWT_ALGORITHM],
        )
        exp = payload.get("exp")
        if exp is not None and datetime.fromtimestamp(exp, tz=timezone.utc) <= datetime.now(timezone.utc):
            raise jwt.ExpiredSignatureError("OpenWebUI token 已过期")
        return payload

    if not settings.OPENWEBUI_JWT_SECRET:
        raise RuntimeError("OpenWebUI token exchange 尚未配置 OPENWEBUI_JWT_SECRET")

    decode_kwargs: dict[str, Any] = {
        "key": settings.OPENWEBUI_JWT_SECRET,
        "algorithms": [settings.OPENWEBUI_JWT_ALGORITHM],
    }

    if settings.OPENWEBUI_EXPECTED_ISSUER:
        decode_kwargs["issuer"] = settings.OPENWEBUI_EXPECTED_ISSUER

    if settings.OPENWEBUI_EXPECTED_AUDIENCE:
        decode_kwargs["audience"] = settings.OPENWEBUI_EXPECTED_AUDIENCE
    else:
        decode_kwargs["options"] = {"verify_aud": False}

    return jwt.decode(token, **decode_kwargs)


def extract_claim(payload: dict[str, Any], claim_names: Iterable[str]) -> Any:
    for claim_name in claim_names:
        value = payload.get(claim_name)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            return value
    return None
