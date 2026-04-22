"""
Gemini API Client with key rotation, model fallback, and rate-limit handling.

Supports:
- Multiple API keys with round-robin rotation on 429 errors
- Model fallback chain: gemini-2.0-flash -> gemini-2.5-flash
- Structured JSON output via responseSchema
- Thread-safe key rotation
"""

import os
import time
import logging
import threading
import base64
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction prompt & schema (from PROMPT_INTEGRACAO.md — DO NOT MODIFY)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """Objetivo: Extrair informações estruturadas de guias médicas em PDF, garantindo consistência e formato fixo.

Instruções:
- Identificar o campo 'Número da Guia Prestador' (formato XX-XXXXXXXX, ex: R3-10078428). NUNCA retorne 'VAZIO' neste campo, sempre deve haver valor válido.
- Coletar TODAS as 'Datas de Atendimento' (DD/MM/AAAA ou DD-MM-AAAA).
- Para cada data preenchida, verificar a coluna '15-Assinatura' correspondente. Marcar 'Sim' se houver assinatura na linha, 'Não' caso contrário.
- Capturar o 'Nome do Beneficiário'. ATENÇÃO: NUNCA confunda com o nome do prestador ou clínica (ex: LARISSA MARTINS FERREIRA). O beneficiário é sempre o paciente.
- Capturar o 'Número da Guia Principal' (coluna 14), retornar 'VAZIO' se não houver.
- NUNCA derivar guia principal do número do prestador, apenas se explícito.
- REGRA DE DATAS (MUITO IMPORTANTE): Extraia APENAS as linhas onde a 'Data do Atendimento' está explicitamente preenchida com uma data válida. IGNORE COMPLETAMENTE linhas em branco. Se a tabela tem 22 linhas mas só 2 estão preenchidas, retorne APENAS 2 itens no array de atendimentos."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "numeroGuiaPrestador": {
            "type": "STRING",
            "description": "Número da Guia Prestador"
        },
        "nomeBeneficiario": {
            "type": "STRING",
            "description": "Nome completo do paciente/beneficiário"
        },
        "numeroGuiaPrincipal": {
            "type": "STRING",
            "description": "Número da Guia Principal ou 'VAZIO'"
        },
        "atendimentos": {
            "type": "ARRAY",
            "description": "Lista de datas de atendimento e status de assinatura",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "data": {
                        "type": "STRING",
                        "description": "Data no formato DD-MM-AAAA"
                    },
                    "assinatura": {
                        "type": "STRING",
                        "description": "'Sim' ou 'Não'"
                    }
                }
            }
        }
    },
    "required": [
        "numeroGuiaPrestador",
        "nomeBeneficiario",
        "numeroGuiaPrincipal",
        "atendimentos"
    ]
}

# Model fallback priority
MODELS_PRIORITY = ["gemini-2.0-flash", "gemini-2.5-flash"]

# Max retries per key before moving to the next
MAX_RETRIES_PER_KEY = 2

# Backoff base in seconds for 429 errors
BACKOFF_BASE_SECONDS = 2.0


class GeminiClientError(Exception):
    """Raised when all keys and models are exhausted."""
    pass


class GeminiClient:
    """
    Thread-safe Gemini API client with key rotation and model fallback.

    Usage:
        client = GeminiClient.from_env()
        result = client.extract_from_pdf(pdf_bytes)
    """

    def __init__(self, api_keys: list[str]):
        if not api_keys:
            raise ValueError("At least one Gemini API key is required.")
        self._keys = api_keys
        self._current_key_idx = 0
        self._lock = threading.Lock()
        logger.info(f"GeminiClient initialized with {len(api_keys)} API key(s)")

    # -- Factory -----------------------------------------------------------

    @classmethod
    def from_env(cls) -> "GeminiClient":
        """Create a GeminiClient from the GEMINI_API_KEYS env var (comma-separated)."""
        raw = os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            raise ValueError(
                "Environment variable GEMINI_API_KEYS is empty. "
                "Set it to a comma-separated list of Gemini API keys."
            )
        return cls(keys)

    # -- Key rotation ------------------------------------------------------

    def _next_key(self) -> str:
        """Thread-safe round-robin key selection."""
        with self._lock:
            key = self._keys[self._current_key_idx]
            self._current_key_idx = (self._current_key_idx + 1) % len(self._keys)
            return key

    @property
    def current_key_index(self) -> int:
        return self._current_key_idx

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    # -- Core extraction ---------------------------------------------------

    def extract_from_pdf(self, pdf_bytes: bytes) -> dict:
        """
        Send a PDF to Gemini and return the structured JSON extraction.

        Tries each model in MODELS_PRIORITY, rotating keys on 429 errors.
        Returns the parsed dict on success; raises GeminiClientError on total failure.
        """
        import json as json_lib

        # Lazy import to avoid startup failure if package not installed
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise GeminiClientError(
                "google-genai package not installed. Run: pip install google-genai"
            )

        last_error: Optional[Exception] = None
        max_key_rotations = len(self._keys) * MAX_RETRIES_PER_KEY

        for rotation in range(max_key_rotations):
            api_key = self._next_key()
            key_index = (self._current_key_idx - 1) % len(self._keys)

            for model_name in MODELS_PRIORITY:
                try:
                    logger.info(
                        f"Gemini attempt {rotation + 1}/{max_key_rotations}: "
                        f"model={model_name}, key_index={key_index}"
                    )

                    client = genai.Client(api_key=api_key)

                    response = client.models.generate_content(
                        model=model_name,
                        contents=[
                            types.Content(
                                parts=[
                                    types.Part.from_text(text=EXTRACTION_PROMPT),
                                    types.Part.from_bytes(
                                        data=pdf_bytes,
                                        mime_type="application/pdf"
                                    ),
                                ]
                            )
                        ],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=RESPONSE_SCHEMA,
                            temperature=0.1,
                        ),
                    )

                    # Parse the response text as JSON
                    raw_text = response.text
                    if not raw_text:
                        raise GeminiClientError(f"Empty response from {model_name}")

                    result = json_lib.loads(raw_text)
                    logger.info(
                        f"Extraction successful: model={model_name}, "
                        f"guia={result.get('numeroGuiaPrestador', '?')}"
                    )
                    return {
                        **result,
                        "_meta": {
                            "model": model_name,
                            "key_index": key_index,
                        }
                    }

                except Exception as e:
                    error_str = str(e).lower()
                    last_error = e

                    # Rate limit — backoff and try next model
                    if "429" in str(e) or "resource_exhausted" in error_str:
                        wait = BACKOFF_BASE_SECONDS * (rotation + 1)
                        logger.warning(
                            f"Rate limit hit (429) on key {key_index}, "
                            f"model {model_name}. Backing off {wait:.1f}s and trying fallback model..."
                        )
                        time.sleep(wait)
                        continue  # Try next model in MODELS_PRIORITY with SAME KEY

                    # Model not found / unavailable — try next model
                    if "not found" in error_str or "not available" in error_str:
                        logger.warning(
                            f"Model {model_name} unavailable, trying fallback..."
                        )
                        continue  # Try next model in MODELS_PRIORITY

                    # Other error — log and try next model
                    logger.error(
                        f"Gemini error (key={key_index}, model={model_name}): {e}"
                    )
                    continue
            
            # If we exhausted all models for this key, the loop continues to the next key rotation

        raise GeminiClientError(
            f"All {max_key_rotations * len(MODELS_PRIORITY)} Gemini attempts failed. Last error: {last_error}"
        )
