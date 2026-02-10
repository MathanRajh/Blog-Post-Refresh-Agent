from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class Site(Base):
    __tablename__ = "sites"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True)
    domain = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship: One Site -> Many Sections
    sections = relationship("Section", back_populates="site", cascade="all, delete-orphan")

class Section(Base):
    __tablename__ = "sections"
    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"))
    
    heading = Column(String)      # For Structure Audit
    content = Column(Text)        # For Semantic Context
    level = Column(String)        # "h2" or "h3"
    order_index = Column(Integer) # Critical for reconstruction
    is_merged = Column(Boolean, default=False)
    
    site = relationship("Site", back_populates="sections")
    # Relationship: One Section -> Many Links
    links = relationship("Link", back_populates="section", cascade="all, delete-orphan")

class Link(Base):
    __tablename__ = "links"
    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("sections.id"))
    
    url = Column(String)
    text = Column(String)         # Anchor text
    target_title = Column(String, nullable=True) # Fetched by Python
    status = Column(String, default="pending")   # alive, dead, valid, invalid
    reason = Column(String, nullable=True)
    
    section = relationship("Section", back_populates="links")