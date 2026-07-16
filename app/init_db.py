"""Runs on container startup: creates tables and seeds the first admin."""
from database import engine, Base, SessionLocal
import models  # noqa: registers all models with Base


def _apply_migrations(db):
    """Aplica colunas/tabelas novas sem destruir dados existentes."""
    conn = db.connection()

    migrations = [
        # approval_level em users
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='users' AND column_name='approval_level'
          ) THEN
            ALTER TABLE users ADD COLUMN approval_level INTEGER;
          END IF;
        END $$;
        """,
        # current_approval_level em tickets
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='tickets' AND column_name='current_approval_level'
          ) THEN
            ALTER TABLE tickets ADD COLUMN current_approval_level INTEGER;
          END IF;
        END $$;
        """,
    ]

    for sql in migrations:
        conn.execute(db.connection().connection.cursor().__class__.__module__ and __import__(
            'sqlalchemy', fromlist=['text']).text(sql) if False else __import__(
            'sqlalchemy', fromlist=['text']).text(sql))

    db.commit()


def init():
    # Cria tabelas novas (approval_config, ticket_attachments, etc.)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        from sqlalchemy import text

        # ── Migração: colunas que podem não existir no banco antigo ──
        migrations = [
            """
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='approval_level'
              ) THEN
                ALTER TABLE users ADD COLUMN approval_level INTEGER;
              END IF;
            END $$;
            """,
            """
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='tickets' AND column_name='current_approval_level'
              ) THEN
                ALTER TABLE tickets ADD COLUMN current_approval_level INTEGER;
              END IF;
            END $$;
            """,
            """
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='must_change_password'
              ) THEN
                ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
              END IF;
            END $$;
            """,
            # Indice unico funcional case-insensitive em users.email.
            # Impede duplicados que so diferem por caixa (ex.: "Ricardo@..." e "ricardo@...").
            # Tolerante a duplicados pre-existentes: emite WARNING mas nao aborta o startup,
            # para que 'dedupe_users.py' possa ser rodado depois e o proximo restart crie o indice.
            """
            DO $$ BEGIN
              BEGIN
                CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON users (lower(email));
              EXCEPTION WHEN unique_violation THEN
                RAISE WARNING 'ux_users_email_lower NAO criado: existem emails duplicados por caixa. Rode dedupe_users.py --apply e reinicie.';
              END;
            END $$;
            """,
        ]

        for sql in migrations:
            db.execute(text(sql))
        db.commit()
        print("[init_db] Migrations applied.")

        # ── Seed: admin ──
        from models.user import User, Role
        from models.settings import ApprovalConfig
        from services.auth import hash_password
        from config import settings

        admin_email = (settings.ADMIN_EMAIL or "").lower().strip()
        if not db.query(User).filter(User.role == Role.ADMIN).first():
            admin = User(
                name=settings.ADMIN_NAME,
                email=admin_email,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                role=Role.ADMIN,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"[init_db] Admin created: {admin_email}")
        else:
            print("[init_db] Admin already exists, skipping seed.")

        # ── Seed: ApprovalConfig ──
        if not db.query(ApprovalConfig).first():
            db.add(ApprovalConfig(num_levels=2))
            db.commit()
            print("[init_db] ApprovalConfig created with 2 levels.")

    finally:
        db.close()


if __name__ == "__main__":
    init()
