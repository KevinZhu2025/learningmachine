import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_session
from .models import AuthSession, AuthThrottle, UserProfile

SESSION_COOKIE = "learningmachine_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
LOGIN_ATTEMPTS_PER_WINDOW = int(os.getenv("LOGIN_ATTEMPTS_PER_WINDOW", "10"))
REGISTER_ATTEMPTS_PER_WINDOW = int(os.getenv("REGISTER_ATTEMPTS_PER_WINDOW", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "900"))
password_hasher = PasswordHasher(type=Type.ID)
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class AuthSettings:
    app_env: str
    admin_user_id: str
    csrf_secret: bytes
    allowed_origins: frozenset[str]

    @property
    def secure_cookie(self) -> bool:
        return self.app_env != "development"


def load_auth_settings() -> AuthSettings:
    app_env = os.getenv("APP_ENV", "development")
    admin_user_id = os.getenv("ADMIN_USER_ID", "Admin" if app_env == "development" else "").strip()
    csrf_secret = os.getenv("SESSION_CSRF_SECRET")
    origins = os.getenv("ALLOWED_ORIGINS")

    if app_env != "development":
        if not admin_user_id:
            raise RuntimeError("生产环境必须配置 ADMIN_USER_ID。")
        if len(admin_user_id) > 100:
            raise RuntimeError("ADMIN_USER_ID 不得超过 100 个字符。")
        if not csrf_secret or len(csrf_secret.encode("utf-8")) < 32:
            raise RuntimeError("生产环境 SESSION_CSRF_SECRET 至少需要 32 字节。")
        if not origins:
            raise RuntimeError("生产环境必须配置 ALLOWED_ORIGINS。")
    else:
        csrf_secret = csrf_secret or "local-development-only-csrf-secret"
        origins = origins or "http://127.0.0.1:5173,http://localhost:5173"

    allowed_origins = frozenset(origin.strip().rstrip("/") for origin in origins.split(",") if origin.strip())
    if not allowed_origins:
        raise RuntimeError("ALLOWED_ORIGINS 至少需要配置一个允许的来源。")
    if SESSION_TTL_SECONDS <= 0 or RATE_LIMIT_WINDOW_SECONDS <= 0:
        raise RuntimeError("会话有效期和认证限流窗口必须为正数。")
    if LOGIN_ATTEMPTS_PER_WINDOW <= 0 or REGISTER_ATTEMPTS_PER_WINDOW <= 0:
        raise RuntimeError("认证限流次数必须为正数。")
    return AuthSettings(
        app_env=app_env,
        admin_user_id=admin_user_id,
        csrf_secret=csrf_secret.encode("utf-8"),
        allowed_origins=allowed_origins,
    )


settings = load_auth_settings()


class Credentials(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId", min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("用户名不能为空。")
        return normalized


class RegistrationCredentials(Credentials):
    password: str = Field(min_length=12, max_length=128)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    session_token_hash: str
    csrf_token: str
    is_admin: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf_token(session_token: str) -> str:
    return hmac.new(settings.csrf_secret, session_token.encode("ascii"), hashlib.sha256).hexdigest()


def _rate_limit_key(action: str, identity: str) -> str:
    return hmac.new(
        settings.csrf_secret,
        f"{action}:{identity}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_rate_limit(
    session: Session,
    *,
    action: str,
    identity: str,
    maximum: int,
    now: datetime,
) -> None:
    session.execute(
        delete(AuthThrottle).where(
            AuthThrottle.window_started_at
            < now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS * 2)
        )
    )
    key = _rate_limit_key(action, identity)
    cutoff = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)

    for _ in range(2):
        reset_window = AuthThrottle.window_started_at <= cutoff
        result = session.execute(
            update(AuthThrottle)
            .where(AuthThrottle.rate_limit_key == key)
            .values(
                attempt_count=case(
                    (reset_window, 1),
                    else_=AuthThrottle.attempt_count + 1,
                ),
                window_started_at=case(
                    (reset_window, now),
                    else_=AuthThrottle.window_started_at,
                ),
            )
        )
        if result.rowcount:
            count = session.scalar(
                select(AuthThrottle.attempt_count).where(AuthThrottle.rate_limit_key == key)
            )
            session.commit()
            if count is not None and count > maximum:
                raise HTTPException(
                    429,
                    "尝试次数过多，请稍后再试。",
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
                )
            return

        try:
            session.add(
                AuthThrottle(
                    rate_limit_key=key,
                    attempt_count=1,
                    window_started_at=now,
                )
            )
            session.commit()
            return
        except IntegrityError:
            session.rollback()

    raise HTTPException(503, "认证服务暂不可用，请稍后重试。")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in settings.allowed_origins:
        raise HTTPException(403, "请求来源不受信任。")


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> AuthenticatedUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "请先登录。")

    token_hash = _token_hash(token)
    auth_session = session.get(AuthSession, token_hash)
    now = _utcnow()
    if auth_session is None:
        raise HTTPException(401, "请先登录。")
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires_at <= now:
        session.delete(auth_session)
        session.commit()
        raise HTTPException(401, "登录已过期，请重新登录。")

    return AuthenticatedUser(
        user_id=auth_session.user_id,
        session_token_hash=token_hash,
        csrf_token=_csrf_token(token),
        is_admin=auth_session.user_id == settings.admin_user_id,
    )


def require_csrf_user(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedUser:
    _require_allowed_origin(request)
    if csrf_header is None or not hmac.compare_digest(csrf_header, user.csrf_token):
        raise HTTPException(403, "CSRF 校验失败，请刷新页面后重试。")
    return user


def current_user_id(user: AuthenticatedUser = Depends(get_current_user)) -> str:
    return user.user_id


def current_user_for_write(user: AuthenticatedUser = Depends(require_csrf_user)) -> str:
    return user.user_id


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["authentication"])

    @router.post("/register", status_code=201)
    def register(
        payload: RegistrationCredentials,
        request: Request,
        session: Session = Depends(get_session),
    ):
        _require_allowed_origin(request)
        _check_rate_limit(
            session,
            action="register:ip",
            identity=_client_ip(request),
            maximum=REGISTER_ATTEMPTS_PER_WINDOW,
            now=_utcnow(),
        )
        if settings.app_env != "development" and payload.user_id == settings.admin_user_id:
            raise HTTPException(403, "管理员账户须通过受控的部署流程创建。")
        if session.get(UserProfile, payload.user_id) is not None:
            raise HTTPException(409, "用户名已被使用，请更换用户名。")
        user = UserProfile(
            user_id=payload.user_id,
            password_hash=password_hasher.hash(payload.password),
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "用户名已被使用，请更换用户名。") from exc
        return {"userId": user.user_id}

    @router.post("/login")
    def login(
        payload: Credentials,
        request: Request,
        response: Response,
        session: Session = Depends(get_session),
    ):
        _require_allowed_origin(request)
        now = _utcnow()
        ip = _client_ip(request)
        _check_rate_limit(
            session,
            action="login:ip",
            identity=ip,
            maximum=LOGIN_ATTEMPTS_PER_WINDOW * 3,
            now=now,
        )
        _check_rate_limit(
            session,
            action="login:user",
            identity=payload.user_id.casefold(),
            maximum=LOGIN_ATTEMPTS_PER_WINDOW,
            now=now,
        )

        user = session.get(UserProfile, payload.user_id)
        if user is None:
            try:
                password_hasher.verify(dummy_password_hash, payload.password)
            except VerifyMismatchError:
                pass
            raise HTTPException(401, "用户名或密码错误。")
        try:
            password_hasher.verify(user.password_hash, payload.password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise HTTPException(401, "用户名或密码错误。")

        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)
        session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
        session.add(
            AuthSession(
                session_token_hash=token_hash,
                user_id=user.user_id,
                created_at=now,
                expires_at=expires_at,
            )
        )
        session.commit()
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="lax",
            path="/api",
        )
        return {
            "userId": user.user_id,
            "csrfToken": _csrf_token(token),
            "isAdmin": user.user_id == settings.admin_user_id,
        }

    @router.post("/logout")
    def logout(
        response: Response,
        user: AuthenticatedUser = Depends(require_csrf_user),
        session: Session = Depends(get_session),
    ):
        auth_session = session.get(AuthSession, user.session_token_hash)
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
        response.delete_cookie(
            key=SESSION_COOKIE,
            path="/api",
            httponly=True,
            secure=settings.secure_cookie,
            samesite="lax",
        )
        return {"loggedOut": True}

    @router.get("/me")
    def me(user: AuthenticatedUser = Depends(get_current_user)):
        return {
            "userId": user.user_id,
            "csrfToken": user.csrf_token,
            "isAdmin": user.is_admin,
        }

    return router


auth_router = create_auth_router()
