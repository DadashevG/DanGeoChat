from sqlalchemy import Column, Integer, Float, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
from app.database import Base

class Query(Base):
    __tablename__ = "queries"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    question = Column(Text, nullable=False)
    scenario_type = Column(String(50), default="baseline")
    answer = Column(Text, nullable=True)
    model_used = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    
    evaluations = relationship("EvaluationResult", back_populates="query")

class TestScenario(Base):
    __tablename__ = "test_scenarios"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    question = Column(Text, nullable=False)
    expected_answer = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    
    id = Column(Integer, primary_key=True, index=True)
    query_id = Column(Integer, ForeignKey("queries.id"), nullable=False)
    correctness = Column(Float, nullable=True)
    groundedness = Column(Float, nullable=True)
    relevance = Column(Float, nullable=True)
    spatial_awareness = Column(Float, nullable=True)
    hallucination_score = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    evaluator_notes = Column(Text, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now())
    
    query = relationship("Query", back_populates="evaluations")
