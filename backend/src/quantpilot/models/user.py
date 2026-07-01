from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    String,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base


class User(Base):
    """系统用户（V1.5-G 多用户）。

    - `username`/`email` DB 层 UNIQUE，防并发注册竞态（应用层 lower 校验 + IntegrityError 重查）。
    - `level` = L1/L2/L3 自选偏好（非权限/RBAC），控制界面复杂度与解释深度，默认 L1。
    - `is_active` = false 停用账号（保留数据，禁登录）。
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(72), nullable=False)
    level: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default="L1"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("level IN ('L1','L2','L3')", name="ck_user_level"),
    )
