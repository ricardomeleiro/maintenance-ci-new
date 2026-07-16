"""One-off: deduplica usuarios com email variando so por caso.

Estrategia:
- Para cada grupo de usuarios com lower(email) igual, mantem o mais antigo (menor id).
- Realoca todas as FKs (tickets, comments, historico, anexos, audit, approval_config)
  do(s) duplicado(s) para o mantido.
- Apaga tokens de password_reset do(s) duplicado(s) (expiram sozinhos).
- Apaga o(s) registro(s) duplicado(s).
- Normaliza o email do mantido para lower-case.

Uso:
    docker compose exec app python dedupe_users.py           # dry-run (mostra o que faria)
    docker compose exec app python dedupe_users.py --apply   # aplica as mudancas
"""
import sys
from sqlalchemy import func
from database import SessionLocal
from models.user import User
from models.ticket import Ticket, TicketComment, TicketStatusHistory
from models.attachment import TicketAttachment
from models.audit import AuditLog
from models.settings import ApprovalConfig
from models.password_reset import PasswordResetToken


def find_duplicate_groups(db):
    """Retorna dict {lower_email: [User, ...]} apenas para grupos com >1 usuario."""
    rows = (
        db.query(func.lower(User.email).label("le"), func.count(User.id))
        .group_by(func.lower(User.email))
        .having(func.count(User.id) > 1)
        .all()
    )
    groups = {}
    for lower_email, _ in rows:
        users = (
            db.query(User)
            .filter(func.lower(User.email) == lower_email)
            .order_by(User.id.asc())
            .all()
        )
        groups[lower_email] = users
    return groups


def reassign_fks(db, from_id: int, to_id: int) -> dict:
    """Move todas as FKs de from_id para to_id. Retorna contagens por tabela."""
    counts = {}
    counts["tickets.created_by_id"] = (
        db.query(Ticket).filter(Ticket.created_by_id == from_id)
        .update({Ticket.created_by_id: to_id}, synchronize_session=False)
    )
    counts["tickets.assigned_to_id"] = (
        db.query(Ticket).filter(Ticket.assigned_to_id == from_id)
        .update({Ticket.assigned_to_id: to_id}, synchronize_session=False)
    )
    counts["tickets.approved_by_id"] = (
        db.query(Ticket).filter(Ticket.approved_by_id == from_id)
        .update({Ticket.approved_by_id: to_id}, synchronize_session=False)
    )
    counts["ticket_comments.author_id"] = (
        db.query(TicketComment).filter(TicketComment.author_id == from_id)
        .update({TicketComment.author_id: to_id}, synchronize_session=False)
    )
    counts["ticket_status_history.changed_by_id"] = (
        db.query(TicketStatusHistory).filter(TicketStatusHistory.changed_by_id == from_id)
        .update({TicketStatusHistory.changed_by_id: to_id}, synchronize_session=False)
    )
    counts["ticket_attachments.uploaded_by_id"] = (
        db.query(TicketAttachment).filter(TicketAttachment.uploaded_by_id == from_id)
        .update({TicketAttachment.uploaded_by_id: to_id}, synchronize_session=False)
    )
    counts["audit_logs.user_id"] = (
        db.query(AuditLog).filter(AuditLog.user_id == from_id)
        .update({AuditLog.user_id: to_id}, synchronize_session=False)
    )
    counts["approval_config.updated_by_id"] = (
        db.query(ApprovalConfig).filter(ApprovalConfig.updated_by_id == from_id)
        .update({ApprovalConfig.updated_by_id: to_id}, synchronize_session=False)
    )
    counts["password_reset_tokens (deletados)"] = (
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == from_id)
        .delete(synchronize_session=False)
    )
    return counts


def normalize_all_emails(db) -> int:
    """Poe todos os emails em minusculo. Retorna quantos foram alterados."""
    changed = 0
    for u in db.query(User).all():
        if u.email and u.email != u.email.lower().strip():
            u.email = u.email.lower().strip()
            changed += 1
    return changed


def main():
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        groups = find_duplicate_groups(db)
        if not groups:
            print("Nenhum grupo de emails duplicados encontrado.")
        else:
            print(f"Grupos duplicados encontrados: {len(groups)}")
            for lower_email, users in groups.items():
                keeper = users[0]
                dupes = users[1:]
                print()
                print(f"  Email: {lower_email}")
                print(f"    MANTER: id={keeper.id} email={keeper.email!r} created_at={keeper.created_at}")
                for d in dupes:
                    print(f"    APAGAR: id={d.id} email={d.email!r} created_at={d.created_at}")
                    counts = reassign_fks(db, d.id, keeper.id)
                    for tbl, n in counts.items():
                        if n:
                            print(f"      -> {tbl}: {n} linhas realocadas")
                    if apply:
                        db.delete(d)

        norm_changed = normalize_all_emails(db)
        print()
        print(f"Emails normalizados para lower-case: {norm_changed}")

        if apply:
            db.commit()
            print()
            print("APLICADO. Commit feito.")
        else:
            db.rollback()
            print()
            print("DRY-RUN. Nada foi commitado. Rode com --apply para aplicar.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
