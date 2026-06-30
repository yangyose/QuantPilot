import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

# 轻量 email 格式校验（避免引入 email-validator 依赖；严格验证待 G-2b 升 EmailStr）
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError("邮箱格式不正确")
    return value


EmailStr = Annotated[str, AfterValidator(_validate_email)]


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(min_length=8)


class UserMeResponse(BaseModel):
    username: str
    email: str
    level: str


class UpdateMeRequest(BaseModel):
    level: Literal["L1", "L2", "L3"] | None = None
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
