"""
Extraction Pipeline — Business rules for post-processing Gemini output.

Pipeline: Gemini JSON → Normalize Prefix → Blacklist Check → Data Validation → Build Filename

All functions are pure/stateless for easy unit testing.
"""

import re
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# A. Prefix Normalization (Guia Prestador)
# ---------------------------------------------------------------------------

PREFIX_MAP = {
    "0": "O",
    "1": "I",
    "5": "S",
    "8": "B",
}


def normalize_guia_prefix(guia: str) -> str:
    """
    Normalize numeric prefixes to letters.
    
    Examples:
        '02-10073265' → 'O2-10073265'
        '12-10073265' → 'I2-10073265'
        '51-10073087' → 'S1-10073087'
        '81-50001234' → 'B1-50001234'
        'R3-10078428' → 'R3-10078428' (already alpha, no change)
    """
    if not guia or len(guia) < 2:
        return guia

    first_char = guia[0]
    if first_char in PREFIX_MAP:
        return PREFIX_MAP[first_char] + guia[1:]
    return guia


# ---------------------------------------------------------------------------
# B. Blacklist Validation (Beneficiary Name)
# ---------------------------------------------------------------------------

BLACKLIST_EXACT = {
    "GUIA COMPROVANTE",
    "GUIA COMPROVANTE PRESENCIAL",
    "CLINICA",
    "CLINICA LARISSA MARTINS",
    "LARISSA MARTINS FERREIRA",
    "TERAPIAS PEDIATRICAS",
    "TERAPIAS PEDIÁTRICAS",
    "SESSAO DE",
    "SESSAO DE PSICOMOTRICIDADE",
    "SESSÃO DE PSICOMOTRICIDADE",
    "PSICOMOTRICIDADE",
    "DADOS DO CONTRATADO",
    "NOME DO PROFISSIONAL",
    "PROFISSIONAL",
    "LABORATORIO",
    "LABORATÓRIO",
    "CLÍNICA",
    "HOSPITAL",
    "PRONTUÁRIO",
    "PRESENÇA",
    "ATENDIMENTO",
    "PROCEDIMENTO",
    "EXAME",
    "TERAPIA",
}

# Uppercase version for fast lookup
_BLACKLIST_UPPER = {w.upper() for w in BLACKLIST_EXACT}


def validate_against_blacklist(nome: str) -> tuple[bool, str]:
    """
    Validate beneficiary name against blacklist.

    Returns:
        (is_valid, rejection_reason)
        is_valid=True means the name is acceptable.
    """
    if not nome or not nome.strip():
        return False, "Nome vazio"

    nome_upper = nome.strip().upper()

    # Check exact matches
    if nome_upper in _BLACKLIST_UPPER:
        return False, f"Nome na blacklist (exato): '{nome_upper}'"

    # Check if name CONTAINS any blacklisted phrase
    for bl_word in _BLACKLIST_UPPER:
        if bl_word in nome_upper:
            return False, f"Nome contém termo bloqueado: '{bl_word}'"

    # Pattern: contains numbers
    if re.search(r"\d", nome_upper):
        return False, "Nome contém números"

    # Pattern: fewer than 2 words
    words = nome_upper.split()
    if len(words) < 2:
        return False, "Nome com menos de 2 palavras"

    # Pattern: entire name is too short
    # Usually a valid name (First + Last) is at least 5 characters (e.g., "ED SA" = 5, "LI WU" = 5)
    if len(nome_upper.replace(" ", "")) < 4:
        return False, f"Nome muito curto: '{nome_upper}'"

    return True, ""


# ---------------------------------------------------------------------------
# C. Data Validation
# ---------------------------------------------------------------------------

def validate_date(date_str: str) -> tuple[bool, str]:
    """
    Validate a date string in DD/MM/AAAA or DD-MM-AAAA format.
    Returns (is_valid, normalized_date_str in DD-MM-AAAA).
    """
    if not date_str or not date_str.strip():
        return False, ""

    # Normalize separators
    clean = date_str.strip().replace("/", "-")

    try:
        parsed = datetime.strptime(clean, "%d-%m-%Y")
        # Range check: valid day/month
        if parsed.day < 1 or parsed.day > 31:
            return False, ""
        if parsed.month < 1 or parsed.month > 12:
            return False, ""
        return True, clean
    except ValueError:
        return False, ""


def validate_guia_number(guia: str) -> tuple[bool, str]:
    """
    Validate guia number: prefix + '-' + exactly 8 digits.
    Examples: 'O2-10073265' → True, 'ABC' → False
    """
    if not guia:
        return False, "Número da guia vazio"

    # Pattern: 1-2 chars (alpha) + '-' + 8 digits
    pattern = r"^[A-Z]{1,2}\d?-\d{8}$"
    if re.match(pattern, guia.upper()):
        return True, ""

    # Also accept the raw format before normalization: digit + digit + '-' + 8 digits
    pattern_raw = r"^\d{1,2}-\d{8}$"
    if re.match(pattern_raw, guia):
        return True, ""

    return False, f"Formato inválido: '{guia}' (esperado: XX-XXXXXXXX)"


def validate_extraction(data: dict) -> list[str]:
    """
    Validate the full extraction result from Gemini.
    Returns a list of error messages (empty = valid).
    """
    errors = []

    # Guia Prestador
    guia = data.get("numeroGuiaPrestador", "")
    if not guia or guia.upper() == "VAZIO":
        errors.append("Número da Guia Prestador ausente")
    else:
        valid, msg = validate_guia_number(guia)
        if not valid:
            errors.append(msg)

    # Beneficiary Name
    nome = data.get("nomeBeneficiario", "")
    valid, msg = validate_against_blacklist(nome)
    if not valid:
        errors.append(f"Nome do Beneficiário inválido: {msg}")

    # Atendimentos
    atendimentos = data.get("atendimentos", [])
    if not atendimentos:
        errors.append("Nenhum atendimento extraído")
    else:
        for i, atend in enumerate(atendimentos):
            dt_valid, _ = validate_date(atend.get("data", ""))
            if not dt_valid:
                errors.append(f"Atendimento [{i}]: data inválida '{atend.get('data')}'")

    return errors


# ---------------------------------------------------------------------------
# D. Filename Construction & Physical Rename
# ---------------------------------------------------------------------------

# Characters invalid on Windows/Linux filesystems
_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Remove or replace filesystem-invalid characters."""
    return _INVALID_FS_CHARS.sub("_", name).strip()


def build_filename(
    numero_guia_principal: str,
    numero_guia_prestador: str,
    data_atendimento: str,
    nome_beneficiario: str,
) -> str:
    """
    Build the new filename according to spec:
    Format: {guia} - {data_atendimento} - {nome_beneficiario}.pdf
    Priority: Use Guia Principal. If 'VAZIO', use Guia Prestador.
    Everything in UPPERCASE.
    """
    # Select guia with priority
    guia = numero_guia_principal
    if not guia or guia.upper() == "VAZIO":
        guia = numero_guia_prestador

    # Get first date from atendimentos
    data = data_atendimento.strip() if data_atendimento else "SEM-DATA"

    # Build and sanitize
    nome = nome_beneficiario.strip().upper() if nome_beneficiario else "DESCONHECIDO"
    guia = guia.strip().upper()
    data = data.upper()

    raw_name = f"{guia} - {data} - {nome}.pdf"
    return sanitize_filename(raw_name)


def rename_file_safe(
    src_path: str,
    dest_dir: str,
    new_name: str,
) -> str:
    """
    Rename a file to dest_dir/new_name.
    Handles conflicts by appending sequential counter: NOME-1.pdf, NOME-2.pdf.

    Returns the final absolute path of the renamed file.
    """
    os.makedirs(dest_dir, exist_ok=True)

    base, ext = os.path.splitext(new_name)
    dest_path = os.path.join(dest_dir, new_name)

    counter = 0
    while os.path.exists(dest_path):
        counter += 1
        dest_path = os.path.join(dest_dir, f"{base}-{counter}{ext}")

    try:
        os.rename(src_path, dest_path)
        logger.info(f"Renamed: {src_path} → {dest_path}")
    except OSError as e:
        # Fallback: copy + delete (cross-device rename)
        import shutil
        shutil.move(src_path, dest_path)
        logger.info(f"Moved (cross-device): {src_path} → {dest_path}")

    return dest_path


# ---------------------------------------------------------------------------
# E. Full Pipeline (orchestration per single file)
# ---------------------------------------------------------------------------

def process_single_extraction(gemini_result: dict) -> dict:
    """
    Run the full post-processing pipeline on a single Gemini extraction result.

    Returns a dict with:
        - success: bool
        - guia_normalizada: str
        - nome_final: str (proposed filename)
        - errors: list[str]
        - data: dict (processed data)
    """
    errors = []

    # Step 1: Normalize prefix
    guia_raw = gemini_result.get("numeroGuiaPrestador", "")
    guia_normalizada = normalize_guia_prefix(guia_raw)

    guia_principal = gemini_result.get("numeroGuiaPrincipal", "VAZIO")
    if guia_principal and guia_principal.upper() != "VAZIO":
        guia_principal = normalize_guia_prefix(guia_principal)

    # Step 2: Validate blacklist
    nome = gemini_result.get("nomeBeneficiario", "")
    bl_valid, bl_reason = validate_against_blacklist(nome)
    if not bl_valid:
        errors.append(f"Blacklist: {bl_reason}")

    # Step 3: Validate all data
    validation_errors = validate_extraction({
        **gemini_result,
        "numeroGuiaPrestador": guia_normalizada,
    })
    errors.extend(validation_errors)

    # Step 4: Build filename (use first atendimento date)
    atendimentos = gemini_result.get("atendimentos", [])
    first_date = ""
    if atendimentos:
        first_date = atendimentos[0].get("data", "")
        # Normalize date format
        _, normalized = validate_date(first_date)
        if normalized:
            first_date = normalized

    nome_final = build_filename(
        guia_principal,
        guia_normalizada,
        first_date,
        nome,
    )

    return {
        "success": len(errors) == 0,
        "guia_normalizada": guia_normalizada,
        "guia_principal": guia_principal,
        "nome_beneficiario": nome.upper() if nome else "",
        "nome_final": nome_final,
        "errors": errors,
        "atendimentos": atendimentos,
    }
