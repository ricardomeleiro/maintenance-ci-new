from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)
    recipient = Column(String(200), nullable=False)
    subject = Column(String(500))
    status = Column(String(20), nullable=False)   # sent | failed | skipped
    error_message = Column(Text)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)
