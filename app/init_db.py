"""Runs on container startup: creates tables and seeds the first admin."""
from database import engine, Base, SessionLocal
import models  # noqa: registers all models with Base


def init():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        from models.user import User, Role
        from models.settings import ApprovalConfig
        from services.auth import hash_password
        from config import settings

        # Seed admin
        if not db.query(User).filter(User.role == Role.ADMIN).first():
            admin = User(
                name=settings.ADMIN_NAME,
                email=settings.ADMIN_EMAIL,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                role=Role.ADMIN,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"[init_db] Admin created: {settings.ADMIN_EMAIL}")
        else:
            print("[init_db] Admin already exists, skipping seed.")

        # Seed ApprovalConfig (padrão: 2 níveis)
        if not db.query(ApprovalConfig).first():
            db.add(ApprovalConfig(num_levels=2))
            db.commit()
            print("[init_db] ApprovalConfig created with 2 levels.")

    finally:
        db.close()


if __name__ == "__main__":
    init()
