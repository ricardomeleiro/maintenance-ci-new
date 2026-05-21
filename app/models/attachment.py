import os
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(300), nullable=False)       # nome original
    stored_filename = Column(String(300), nullable=False) # nome no disco
    file_size = Column(Integer)
    mime_type = Column(String(100))
    created_at = Column(DateTime, default=func.now())

    ticket = relationship("Ticket", back_populates="attachments")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])

    @property
    def url(self):
        return f"/tickets/{self.ticket_id}/attachments/{self.stored_filename}"

    @property
    def size_human(self):
        if not self.file_size:
            return "—"
        for unit in ["B", "KB", "MB", "GB"]:
            if self.file_size < 1024:
                return f"{self.file_size:.0f} {unit}"
            self.file_size /= 1024
        return f"{self.file_size:.1f} GB"
