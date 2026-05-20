import enum
from sqlalchemy import Column, Integer, String, Text, Boolean, Enum, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketCategory(str, enum.Enum):
    ELECTRICAL = "electrical"
    HYDRAULIC = "hydraulic"
    CIVIL = "civil"
    HVAC = "hvac"
    SECURITY = "security"
    CLEANING = "cleaning"
    OTHER = "other"


STATUS_LABELS = {
    TicketStatus.OPEN: "Aberto",
    TicketStatus.UNDER_REVIEW: "Em Análise",
    TicketStatus.APPROVED: "Aprovado",
    TicketStatus.IN_PROGRESS: "Em Execução",
    TicketStatus.COMPLETED: "Concluído",
    TicketStatus.REJECTED: "Rejeitado",
    TicketStatus.CANCELLED: "Cancelado",
}

STATUS_COLORS = {
    TicketStatus.OPEN: "secondary",
    TicketStatus.UNDER_REVIEW: "info",
    TicketStatus.APPROVED: "primary",
    TicketStatus.IN_PROGRESS: "warning",
    TicketStatus.COMPLETED: "success",
    TicketStatus.REJECTED: "danger",
    TicketStatus.CANCELLED: "dark",
}

PRIORITY_LABELS = {
    TicketPriority.LOW: "Baixa",
    TicketPriority.MEDIUM: "Média",
    TicketPriority.HIGH: "Alta",
    TicketPriority.CRITICAL: "Crítica",
}

PRIORITY_COLORS = {
    TicketPriority.LOW: "success",
    TicketPriority.MEDIUM: "warning",
    TicketPriority.HIGH: "danger",
    TicketPriority.CRITICAL: "dark",
}

CATEGORY_LABELS = {
    TicketCategory.ELECTRICAL: "Elétrica",
    TicketCategory.HYDRAULIC: "Hidráulica",
    TicketCategory.CIVIL: "Civil",
    TicketCategory.HVAC: "HVAC / Climatização",
    TicketCategory.SECURITY: "Segurança",
    TicketCategory.CLEANING: "Limpeza",
    TicketCategory.OTHER: "Outros",
}


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(Enum(TicketCategory), nullable=False)
    priority = Column(Enum(TicketPriority), default=TicketPriority.MEDIUM)
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN, index=True)
    location = Column(String(300))

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_to_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_reason = Column(Text)

    scheduled_date = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    creator = relationship("User", foreign_keys=[created_by_id], back_populates="tickets_created")
    assignee = relationship("User", foreign_keys=[assigned_to_id])
    approver_user = relationship("User", foreign_keys=[approved_by_id])
    comments = relationship(
        "TicketComment", back_populates="ticket", order_by="TicketComment.created_at"
    )
    status_history = relationship(
        "TicketStatusHistory", back_populates="ticket", order_by="TicketStatusHistory.created_at"
    )


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    is_internal = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    ticket = relationship("Ticket", back_populates="comments")
    author = relationship("User", back_populates="comments")


class TicketStatusHistory(Base):
    __tablename__ = "ticket_status_history"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    old_status = Column(Enum(TicketStatus))
    new_status = Column(Enum(TicketStatus), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(Text)
    created_at = Column(DateTime, default=func.now())

    ticket = relationship("Ticket", back_populates="status_history")
    changed_by = relationship("User")
