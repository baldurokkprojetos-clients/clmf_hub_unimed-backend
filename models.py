from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Float, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(Text, nullable=False)
    api_key = Column(Text, unique=True, nullable=False)
    status = Column(Text, nullable=False) # Ativo, Inativo
    validade = Column(Date)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Carteirinha(Base):
    __tablename__ = "carteirinhas"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha = Column(Text, unique=True, nullable=False)
    paciente = Column(Text)
    id_paciente = Column(Integer, index=True)
    id_pagamento = Column(Integer, index=True)
    status = Column(Text, default="ativo")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    is_temporary = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    jobs = relationship("Job", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    guias = relationship("BaseGuia", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="carteirinha_rel", cascade="all, delete-orphan")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    status = Column(Text, nullable=False, default="pending", index=True) # success, pending, processing, error
    attempts = Column(Integer, default=0)
    priority = Column(Integer, default=0)
    locked_by = Column(Text) # Server URL
    timeout = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="jobs")
    logs = relationship("Log", back_populates="job_rel", cascade="all, delete-orphan")

class BaseGuia(Base):
    __tablename__ = "base_guias"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    guia = Column(Text)
    data_autorizacao = Column(Date)
    senha = Column(Text)
    validade = Column(Date)
    codigo_terapia = Column(Text)
    qtde_solicitada = Column(Integer)
    sessoes_autorizadas = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="guias")

class PeiTemp(Base):
    __tablename__ = "pei_temp"

    id = Column(Integer, primary_key=True, index=True)
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"), unique=True)
    pei_semanal = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class PatientPei(Base):
    __tablename__ = "patient_pei"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    codigo_terapia = Column(Text)
    
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"))
    
    pei_semanal = Column(Float)
    validade = Column(Date)
    status = Column(Text) # Validated, Pendente
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha")
    base_guia_rel = relationship("BaseGuia")


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="Set NULL"), nullable=True)
    level = Column(Text, default="INFO") # INFO, WARN, ERROR
    message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job_rel = relationship("Job", back_populates="logs")
    carteirinha_rel = relationship("Carteirinha", back_populates="logs")

# Update relationships in Job and Carteirinha (monkey-patching or manual update below)
# We need to add 'logs' relationship to Job and Carteirinha classes above.
# Ideally I should have edited the classes. I will use a second tool call or try to match nicely.
# Actually I can't easily monkeypatch via replace inside the file text easily if I don't touch the classes.
# I will rewrite the file segments for Job and Carteirinha to include 'logs = relationship(...)'


# Event Listeners for Automatic PEI Calculation
from sqlalchemy import event
from sqlalchemy.orm import Session
from services.pei_service import update_patient_pei




# Event Listeners removed - Replaced by Database Triggers (migrations/0006)
