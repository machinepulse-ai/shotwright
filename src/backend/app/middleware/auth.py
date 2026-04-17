"""Auth middleware — admin JWT tokens backed by MongoDB."""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 h


def verify_password(plain: str, expected: str) -> bool:
    # For simple admin password, do direct comparison.
    # If hashed passwords are stored later, switch to _pwd_ctx.verify().
    return plain == expected


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Dependency — raises 401 if not a valid admin token."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[ALGORITHM])
        sub: str | None = payload.get("sub")
        if sub != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not admin")
        return sub
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
