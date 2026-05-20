import enum
from sqlalchemy import Column, Integer, String, Boolean, Enum, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base


class Role(str, enum.Enum):
    USER = "user"
    APPROVER = "approver"
    ADMIN = "admin"


ROLE_LABELS = {
    Role.USER: "Usuário",
    Role.APPROVER: "Aprovador",
    Role.ADMIN: "Administrador",
}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    role = Column(Enum(Role), default=Role.USER, nullable=False)
    department = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    tickets_created = relationship(
        "Ticket", foreign_keys="Ticket.created_by_id", back_populates="creator"
    )
    comments = relationship("TicketComment", back_populates="author")
    audit_logs = relationship("AuditLog", back_populates="user")
