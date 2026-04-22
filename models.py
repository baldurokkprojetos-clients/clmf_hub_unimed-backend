from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Float, Boolean, JSON
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
    permitir_protocolo = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Convenio(Base):
    __tablename__ = "convenios"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    status = Column(Text, default="ativo")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # carteirinhas = relationship("Carteirinha", back_populates="convenio_rel")

class Carteirinha(Base):
    __tablename__ = "carteirinhas"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha = Column(Text, unique=True, nullable=False, index=True)
    paciente = Column(Text, index=True)
    id_paciente = Column(Integer, index=True)
    id_pagamento = Column(Integer, index=True)
    status = Column(Text, default="ativo", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)
    
    is_temporary = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

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
    valida_prestador = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="jobs")
    logs = relationship("Log", back_populates="job_rel", cascade="all, delete-orphan")

class BaseGuia(Base):
    __tablename__ = "base_guias"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"), index=True)
    guia = Column(Text, index=True)
    data_autorizacao = Column(Date, index=True)
    senha = Column(Text)
    validade = Column(Date, index=True)
    codigo_procedimento = Column(Text, index=True)
    qtde_solicitada = Column(Integer)
    sessoes_autorizadas = Column(Integer)
    valida_prestador = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

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
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"), index=True)
    codigo_procedimento = Column(Text, index=True)
    
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"), index=True)
    
    pei_semanal = Column(Float)
    validade = Column(Date, index=True)
    status = Column(Text, index=True) # Validated, Pendente
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

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
    job_rel = relationship("Job", back_populates="logs")
    carteirinha_rel = relationship("Carteirinha", back_populates="logs")

class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(Text, unique=True, nullable=False)
    status = Column(Text, default="offline") # idle, processing, offline, error
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now())
    current_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    command = Column(Text, nullable=True) # restart, stop, etc.
    meta = Column(Text, nullable=True) # JSON string for CPU, RAM, Version
    first_error_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    current_job = relationship("Job")

class Procedimento(Base):
    __tablename__ = "procedimentos"

    id = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, index=True)
    nome = Column(Text, nullable=False)
    codigo_procedimento = Column(Text, index=True)
    autorizacao = Column(Text)
    status = Column(Text, default="ativo")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

# Update relationships in Job and Carteirinha (monkey-patching or manual update below)
# We need to add 'logs' relationship to Job and Carteirinha classes above.
# Ideally I should have edited the classes. I will use a second tool call or try to match nicely.
# Actually I can't easily monkeypatch via replace inside the file text easily if I don't touch the classes.
# I will rewrite the file segments for Job and Carteirinha to include 'logs = relationship(...)'


class ProtocoloLote(Base):
    """Batch (lote) of PDF files for extraction processing."""
    __tablename__ = "protocolo_lotes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = Column(Text, nullable=False, default="pending", index=True)  # pending, processing, completed, error
    total_arquivos = Column(Integer, default=0)
    total_processado = Column(Integer, default=0)
    total_erro = Column(Integer, default=0)
    total_sucesso = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    arquivos = relationship("ProtocoloArquivo", back_populates="lote_rel", cascade="all, delete-orphan")


class ProtocoloArquivo(Base):
    """Individual PDF file within a processing batch."""
    __tablename__ = "protocolo_arquivos"

    id = Column(Integer, primary_key=True, index=True)
    lote_id = Column(Integer, ForeignKey("protocolo_lotes.id", ondelete="CASCADE"), nullable=False, index=True)
    nome_original = Column(Text, nullable=False)
    nome_final = Column(Text)
    status = Column(Text, nullable=False, default="pendente", index=True)  # pendente, processando, sucesso, erro, revisao
    tamanho_bytes = Column(Integer, default=0)

    # Extracted data from Gemini
    numero_guia_prestador = Column(Text)
    nome_beneficiario = Column(Text)
    numero_guia_principal = Column(Text)
    atendimentos = Column(JSON, nullable=True)  # [{data, assinatura}, ...]

    # Post-processing data
    guia_normalizada = Column(Text)
    erro_mensagem = Column(Text)
    gemini_model_used = Column(Text)
    gemini_api_key_index = Column(Integer)

    # Physical file paths
    caminho_original = Column(Text)
    caminho_final = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lote_rel = relationship("ProtocoloLote", back_populates="arquivos")


# Event Listeners for Automatic PEI Calculation
from sqlalchemy import event
from sqlalchemy.orm import Session
from services.pei_service import update_patient_pei




# Event Listeners removed - Replaced by Database Triggers (migrations/0006)
