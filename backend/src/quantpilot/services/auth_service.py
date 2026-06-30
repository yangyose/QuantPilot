"""AuthService：用户注册与账户管理（V1.5-G G-2）。

- register：开放自助注册（校验密码强度 + username/email 唯一）→ 建 user(level=L1)
  + 自动建空账户（user_id 绑定）。并发竞态走 IntegrityError 重查 → 409。
- get_user_by_username / get_user_by_id：登录与依赖注入用。
- update_me：改 level（L1/L2/L3 自选）+ 可选改 email / 密码。

session 由调用方（get_db）托管 commit。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import hash_password, validate_password_strength
from quantpilot.models.account import Account
from quantpilot.models.user import User

_VALID_LEVELS = frozenset({"L1", "L2", "L3"})


class DuplicateUserError(Exception):
    """username 或 email 已被注册。"""


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_username(self, username: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def register(self, username: str, email: str, password: str) -> User:
        """注册新用户 + 自动建空账户。

        - 密码强度不达标 → ValueError（路由转 422）。
        - username/email 已存在 → DuplicateUserError（路由转 409），含并发竞态
          IntegrityError 重查兜底。
        """
        validate_password_strength(password)
        username = username.strip()
        email = email.strip().lower()

        # 应用层预检（友好 409；DB UNIQUE 是最终兜底）
        if await self.get_user_by_username(username) is not None:
            raise DuplicateUserError("用户名已被注册")
        if await self.get_user_by_email(email) is not None:
            raise DuplicateUserError("邮箱已被注册")

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            level="L1",
            is_active=True,
        )
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # 并发竞态：预检后另一请求已 INSERT → 撞 UNIQUE → 重查回 409
            await self._session.rollback()
            raise DuplicateUserError("用户名或邮箱已被注册") from exc

        # 自动建空账户（user_id 绑定；1 用户:1 账户）
        account = Account(
            user_id=user.id,
            name=f"{username} 的账户",
            account_type="REAL",
            cash=0.0,
            total_assets=0.0,
        )
        self._session.add(account)
        await self._session.flush()
        return user

    async def update_me(
        self,
        user: User,
        *,
        level: str | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> User:
        """改当前用户的 level / email / 密码。"""
        if level is not None:
            if level not in _VALID_LEVELS:
                raise ValueError("level 必须是 L1/L2/L3")
            user.level = level
        if email is not None:
            new_email = email.strip().lower()
            if new_email != user.email:
                existing = await self.get_user_by_email(new_email)
                if existing is not None:
                    raise DuplicateUserError("邮箱已被注册")
                user.email = new_email
        if password is not None:
            validate_password_strength(password)
            user.password_hash = hash_password(password)
        await self._session.flush()
        return user
