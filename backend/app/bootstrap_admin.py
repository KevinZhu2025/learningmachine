import getpass
import os
import sys

from argon2 import PasswordHasher, Type
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal, engine
from .models import Base, UserProfile


def main() -> int:
    admin_user_id = os.getenv("ADMIN_USER_ID", "").strip()
    if not admin_user_id or len(admin_user_id) > 100:
        print("请设置长度为 1–100 个字符的 ADMIN_USER_ID。", file=sys.stderr)
        return 2
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    password = getpass.getpass("管理员密码（12–128 个字符）: ")
    if not 12 <= len(password) <= 128:
        print("管理员密码长度必须为 12–128 个字符。", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        if session.get(UserProfile, admin_user_id) is not None:
            print("管理员账户已存在；未修改密码。", file=sys.stderr)
            return 1
        session.add(
            UserProfile(
                user_id=admin_user_id,
                password_hash=PasswordHasher(type=Type.ID).hash(password),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            print("管理员账户已存在；未修改密码。", file=sys.stderr)
            return 1
    print(f"管理员账户 {admin_user_id} 已创建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
