from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# G-2b：email 校验升级为 pydantic EmailStr（email-validator 后端，
# 默认 check_deliverability=False 不做 DNS 查询）。自制正则会放行
# 连续点/点开头/域标签连字符首尾等畸形地址，已移除。


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
