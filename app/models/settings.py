from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base


class ApprovalConfig(Base):
    __tablename__ = "approval_config"

    id = Column(Integer, primary_key=True)
    num_levels = Column(Integer, default=2, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    updated_by = relationship("User", foreign_keys=[updated_by_id])
