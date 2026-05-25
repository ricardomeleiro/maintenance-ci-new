import asyncio
import logging
from typing import Optional
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
from config import settings

logger = logging.getLogger(__name__)

PRIORITY_LABELS_PT = {
    "low": "Baixa", "medium": "Média", "high": "Alta", "critical": "Crítica",
}
CATEGORY_LABELS_PT = {
    "electrical": "Elétrica", "hydraulic": "Hidráulica", "civil": "Civil",
    "hvac": "HVAC / Climatização", "security": "Segurança",
    "cleaning": "Limpeza", "other": "Outros",
}
STATUS_LABELS_PT = {
    "open": "Aberto", "under_review": "Em Análise", "approved": "Aprovado",
    "in_progress": "Em Execução", "completed": "Concluído",
    "rejected": "Rejeitado", "cancelled": "Cancelado",
}
STATUS_COLOR_HEX = {
    "approved": "28a745", "rejected": "dc3545", "completed": "198754",
    "in_progress": "0d6efd", "under_review": "0dcaf0",
}

# Statuses that trigger an e-mail to the requester
NOTIFY_REQUESTER_ON = {"approved", "rejected", "completed", "in_progress"}


# ── HTML helpers ─────────────────────────────────────────────────────────────

def _row(label: str, value: str) -> str:
    return (
        '<tr style="border-bottom:1px solid #e9ecef">'
        f'<td style="font-weight:bold;color:#495057;width:35%;padding:8px;white-space:nowrap">{label}</td>'
        f'<td style="color:#212529;padding:8px">{value}</td></tr>'
    )


def _base_html(title: str, color: str, rows: str, link_url: str, btn_text: str = "Ver chamado") -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center" style="padding:32px 16px">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
      <tr><td style="background:#{color};padding:24px 32px">
        <h2 style="margin:0;color:#fff;font-size:20px">{title}</h2>
      </td></tr>
      <tr><td style="padding:24px 32px">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e9ecef;border-radius:4px">
          {rows}
        </table>
      </td></tr>
      <tr><td style="padding:0 32px 24px 32px">
        <a href="{link_url}"
           style="display:inline-block;background:#{color};color:#fff;padding:10px 24px;
                  border-radius:4px;text-decoration:none;font-weight:bold">
          {btn_text}
        </a>
      </td></tr>
      <tr><td style="background:#f8f9fa;padding:12px 32px;font-size:12px;color:#6c757d">
        {settings.APP_NAME} — mensagem automática, não responda este e-mail.
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


# ── Email DB logging ──────────────────────────────────────────────────────────

def _log_email_sync(
    recipient: str,
    subject: str,
    status: str,
    error_message: Optional[str] = None,
    ticket_id: Optional[int] = None,
) -> None:
    from database import SessionLocal
    from models.email_log import EmailLog
    db = SessionLocal()
    try:
        db.add(EmailLog(
            recipient=recipient,
            subject=subject,
            status=status,
            error_message=error_message,
            ticket_id=ticket_id,
        ))
        db.commit()
    except Exception as exc:
        logger.error("Failed to write email log: %s", exc)
    finally:
        db.close()


# ── Transport ─────────────────────────────────────────────────────────────────

async def _send_email(
    to: str,
    subject: str,
    html_body: str,
    ticket_id: Optional[int] = None,
) -> None:
    loop = asyncio.get_running_loop()

    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning("SMTP not configured — skipping e-mail to %s", to)
        await loop.run_in_executor(
            None, _log_email_sync, to, subject, "skipped", "SMTP not configured", ticket_id
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info("E-mail sent to %s — %s", to, subject)
        await loop.run_in_executor(
            None, _log_email_sync, to, subject, "sent", None, ticket_id
        )
    except Exception as exc:
        logger.error("Failed to send e-mail to %s: %s", to, exc)
        await loop.run_in_executor(
            None, _log_email_sync, to, subject, "failed", str(exc), ticket_id
        )


async def _send_teams(title: str, message: str, color: str = "0078D4") -> None:
    if not settings.TEAMS_WEBHOOK_URL:
        return
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "sections": [{"activityTitle": f"**{title}**", "activityText": message}],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.TEAMS_WEBHOOK_URL, json=payload)
    except Exception as exc:
        logger.error("Teams notification failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

async def notify_ticket_created(ticket, creator_name: str, approver_emails: list[str]) -> None:
    """Notify all approvers and admins when a new ticket is opened."""
    if not approver_emails:
        return

    priority = PRIORITY_LABELS_PT.get(ticket.priority.value, ticket.priority.value)
    category = CATEGORY_LABELS_PT.get(ticket.category.value, ticket.category.value)
    link = f"{settings.BASE_URL}/tickets/{ticket.id}"
    subject = f"[{settings.APP_NAME}] Novo chamado #{ticket.id}: {ticket.title}"

    desc = ticket.description
    if len(desc) > 300:
        desc = desc[:300] + "..."

    rows = (
        _row("Chamado #", str(ticket.id))
        + _row("Solicitante", creator_name)
        + _row("Categoria", category)
        + _row("Prioridade", priority)
        + _row("Local", ticket.location or "Não informado")
        + _row("Descrição", desc)
    )
    color = "dc6c00" if ticket.priority.value in ("high", "critical") else "0d6efd"
    html = _base_html(f"Novo Chamado #{ticket.id}", color, rows, link)

    teams_text = (
        f"**{ticket.title}**\n"
        f"Solicitante: {creator_name} | {category} | Prioridade: {priority}"
        + (f" | {ticket.location}" if ticket.location else "")
    )
    tasks = [_send_email(e, subject, html, ticket.id) for e in approver_emails]
    tasks.append(_send_teams(f"Novo Chamado #{ticket.id}", teams_text, color))
    await asyncio.gather(*tasks, return_exceptions=True)


async def notify_requester_status_changed(
    ticket,
    new_status: str,
    requester_email: str,
    note: Optional[str] = None,
    scheduled_date=None,
) -> None:
    """
    Notify the requester when their ticket reaches a terminal/key status.
    Only fires for: approved, rejected, completed.
    """
    if new_status not in NOTIFY_REQUESTER_ON:
        return

    label = STATUS_LABELS_PT.get(new_status, new_status)
    color = STATUS_COLOR_HEX.get(new_status, "6c757d")
    link = f"{settings.BASE_URL}/tickets/{ticket.id}"
    subject = f"[{settings.APP_NAME}] Chamado #{ticket.id} — {label}"

    rows = (
        _row("Chamado #", str(ticket.id))
        + _row("Título", ticket.title)
        + _row("Status", label)
    )

    if new_status == "approved":
        sd = scheduled_date or ticket.scheduled_date
        if sd:
            rows += _row("Data agendada", sd.strftime("%d/%m/%Y"))
        if getattr(ticket, "assignee", None):
            rows += _row("Responsável", ticket.assignee.name)
        if note:
            rows += _row("Observação", note)

    elif new_status == "rejected":
        reason = getattr(ticket, "rejection_reason", None) or note or ""
        if reason:
            rows += _row("Motivo da rejeição", reason)

    elif new_status == "in_progress":
        if getattr(ticket, "assignee", None):
            rows += _row("Responsável pela execução", ticket.assignee.name)
        if note:
            rows += _row("Observação", note)

    elif new_status == "completed":
        if note:
            rows += _row("Observação de conclusão", note)

    html = _base_html(f"Chamado {label}", color, rows, link)

    await asyncio.gather(
        _send_email(requester_email, subject, html, ticket.id),
        _send_teams(f"Chamado #{ticket.id} — {label}", f"{ticket.title}\n{note or ''}", color),
        return_exceptions=True,
    )


# Backward-compat alias used by approvals.py
async def notify_status_changed(
    ticket, new_status: str, recipient_email: str, note: Optional[str] = None
) -> None:
    await notify_requester_status_changed(ticket, new_status, recipient_email, note)


async def notify_user_created(user_email: str, user_name: str, temp_password: str) -> None:
    """Welcome email sent when an admin creates a new user account."""
    subject = f"[{settings.APP_NAME}] Seu acesso foi criado"
    login_url = f"{settings.BASE_URL}/login"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#060b14;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#060b14;min-height:100vh">
<tr><td align="center" style="padding:40px 16px">

  <!-- Card -->
  <table width="520" cellpadding="0" cellspacing="0"
         style="background:#0d1b2e;border:1px solid #1e3a5f;border-radius:20px;overflow:hidden;
                box-shadow:0 24px 64px rgba(0,0,0,0.7)">

    <!-- Header -->
    <tr><td style="background:linear-gradient(135deg,#0f2a50 0%,#0d1b2e 100%);
                   padding:32px 40px;text-align:center;
                   border-bottom:1px solid #1e3a5f">
      <img src="https://assinatura.c-innovation.com.br/ci-logo.png"
           alt="C-Innovation" height="36"
           style="opacity:0.9;margin-bottom:14px;display:block;margin-left:auto;margin-right:auto">
      <div style="font-size:28px;font-weight:800;color:#f8fafc;letter-spacing:-1px;line-height:1">
        Predial<span style="color:#3b82f6;text-shadow:0 0 18px rgba(59,130,246,0.5)">360</span>
      </div>
      <div style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:6px;
                  letter-spacing:2.5px;text-transform:uppercase">
        Sistema de Manutenção Predial
      </div>
    </td></tr>

    <!-- Welcome banner -->
    <tr><td style="background:linear-gradient(135deg,rgba(37,99,235,0.18),rgba(29,78,216,0.08));
                   padding:24px 40px;border-bottom:1px solid #1e3a5f;text-align:center">
      <div style="font-size:22px;font-weight:700;color:#f1f5f9;margin-bottom:6px">
        👋 Bem-vindo(a), {user_name}!
      </div>
      <div style="font-size:14px;color:rgba(255,255,255,0.55);line-height:1.6">
        Sua conta foi criada no {settings.APP_NAME}.
      </div>
    </td></tr>

    <!-- Body -->
    <tr><td style="padding:32px 40px">

      <p style="margin:0 0 20px;font-size:14px;color:rgba(255,255,255,0.65);line-height:1.6">
        Suas credenciais de acesso estão abaixo. Ao fazer login pela primeira vez,
        você será solicitado(a) a <strong style="color:#93c5fd">criar uma nova senha</strong>.
      </p>

      <!-- Credentials box -->
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#060f1e;border:1px solid #1e3a5f;border-radius:12px;
                    overflow:hidden;margin-bottom:24px">
        <tr><td style="padding:14px 20px;border-bottom:1px solid #1a3050">
          <div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;
                      letter-spacing:1.5px;margin-bottom:4px">E-mail de acesso</div>
          <div style="font-size:15px;color:#93c5fd;font-family:monospace">{user_email}</div>
        </td></tr>
        <tr><td style="padding:14px 20px">
          <div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;
                      letter-spacing:1.5px;margin-bottom:4px">Senha temporária</div>
          <div style="font-size:16px;color:#f1f5f9;font-family:monospace;letter-spacing:2px;
                      font-weight:600;background:#0d2240;padding:8px 12px;border-radius:8px;
                      border:1px solid #2563eb;display:inline-block">{temp_password}</div>
        </td></tr>
      </table>

      <!-- Warning -->
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:rgba(234,179,8,0.06);border:1px solid rgba(234,179,8,0.2);
                    border-radius:10px;margin-bottom:28px">
        <tr><td style="padding:14px 18px;font-size:13px;color:#fde047;line-height:1.5">
          ⚠️ &nbsp;Esta é uma senha temporária. Por segurança, você será obrigado(a) a
          alterá-la no primeiro acesso. Não compartilhe estas credenciais.
        </td></tr>
      </table>

      <!-- CTA Button -->
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center">
          <a href="{login_url}"
             style="display:inline-block;background:linear-gradient(135deg,#2563eb,#1d4ed8);
                    color:#fff;text-decoration:none;font-weight:700;font-size:15px;
                    padding:14px 40px;border-radius:12px;letter-spacing:0.3px;
                    box-shadow:0 8px 24px rgba(37,99,235,0.4)">
            Acessar o sistema →
          </a>
        </td></tr>
      </table>

    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:20px 40px;text-align:center;border-top:1px solid #1e3a5f;
                   background:#060f1e">
      <div style="font-size:11px;color:rgba(255,255,255,0.2);letter-spacing:0.5px">
        © 2026 C-Innovation · Predial360 — mensagem automática, não responda este e-mail.
      </div>
    </td></tr>

  </table>
</td></tr>
</table>
</body></html>"""

    await _send_email(user_email, subject, html)


async def notify_password_reset(user_email: str, user_name: str, reset_link: str) -> None:
    """Send a password reset link to the user."""
    subject = f"[{settings.APP_NAME}] Redefinição de senha"
    rows = (
        _row("Nome", user_name)
        + _row("E-mail", user_email)
        + _row(
            "Instruções",
            "Clique no botão abaixo para criar uma nova senha. "
            "O link expira em <strong>1 hora</strong>. "
            "Se você não solicitou a redefinição, ignore este e-mail.",
        )
    )
    html = _base_html(
        "Redefinição de Senha", "2563eb", rows, reset_link,
        btn_text="Redefinir minha senha",
    )
    await _send_email(user_email, subject, html)


async def notify_password_changed(user_email: str, user_name: str, new_password: str) -> None:
    """Send the new password to the user after an admin reset."""
    subject = f"[{settings.APP_NAME}] Sua senha foi redefinida"
    pw_cell = (
        f'<code style="background:#f8f9fa;padding:3px 8px;border-radius:4px;'
        f'font-family:monospace;font-size:15px;letter-spacing:1px">{new_password}</code>'
    )
    rows = (
        _row("Nome", user_name)
        + _row("E-mail de acesso", user_email)
        + _row("Nova senha", pw_cell)
    )
    link = f"{settings.BASE_URL}/login"
    html = _base_html(
        "Redefinição de Senha", "6c757d", rows, link, btn_text="Acessar o sistema"
    )
    await _send_email(user_email, subject, html)
