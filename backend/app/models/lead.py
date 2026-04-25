from sqlalchemy import Column, String, DateTime, Text
from app.core.database import Base
from datetime import datetime
import uuid
from sqlalchemy.dialects.postgresql import UUID

class Lead(Base):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    phone = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)
