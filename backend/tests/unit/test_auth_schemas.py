"""schemas/auth.py email 校验单元测试（V1.5-G G-2b：升 pydantic EmailStr）。

自制正则放行的畸形地址（连续点、@ 后无点前缀畸形等）应被 email-validator 拒绝。
EmailStr 默认 check_deliverability=False，不做 DNS 查询，@example.com 可通过。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantpilot.schemas.auth import RegisterRequest, UpdateMeRequest

_VALID = {"username": "alice", "password": "Str0ngPass"}


class TestRegisterEmailValidation:
    @pytest.mark.parametrize(
        "email",
        [
            "alice@example.com",
            "alice.smith+tag@sub.example.org",
            "ALICE@EXAMPLE.COM",
        ],
    )
    def test_valid_emails_accepted(self, email: str):
        req = RegisterRequest(**_VALID, email=email)
        assert "@" in req.email

    @pytest.mark.parametrize(
        "email",
        [
            "not-an-email",
            "no-at-sign.com",
            "two@@example.com",
            "a@b",                      # 无域名点
            "alice..double@example.com",  # 本地部分连续点（自制正则放行）
            ".leading-dot@example.com",   # 本地部分点开头（自制正则放行）
            "alice@-bad-.com",            # 域标签连字符首尾（自制正则放行）
        ],
    )
    def test_invalid_emails_rejected(self, email: str):
        with pytest.raises(ValidationError):
            RegisterRequest(**_VALID, email=email)

    def test_surrounding_whitespace_normalized(self):
        """EmailStr 剥离首尾空白 + 域名小写化（email-validator 标准规范化，
        优于拒绝；本地部分大小写保留，注册时 AuthService 再统一全小写）。"""
        req = RegisterRequest(**_VALID, email="  Alice@Example.COM ")
        assert req.email == "Alice@example.com"


class TestUpdateMeEmailValidation:
    def test_valid_email_accepted(self):
        req = UpdateMeRequest(email="new@example.com")
        assert req.email == "new@example.com"

    def test_none_email_allowed(self):
        req = UpdateMeRequest(level="L2")
        assert req.email is None

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            UpdateMeRequest(email="alice..double@example.com")
