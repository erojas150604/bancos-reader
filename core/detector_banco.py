# src/bancos_reader/core/detector_banco.py

from __future__ import annotations

from pathlib import Path
import re
import pdfplumber

from bancos_reader.parsers.bbva import BBVAParser
from bancos_reader.parsers.bbva_tc import BBVATCParser
from bancos_reader.parsers.base import BaseParser


def _nombre_indica_tc(nombre_archivo: str) -> bool:
    """
    Detecta TC por nombre de archivo (rápido y confiable si tú lo nombras así).
    Acepta: " TC ", "TDC", "TARJETA", "CREDITO", etc.
    """
    s = (nombre_archivo or "").upper()

    # normaliza separadores a espacios
    s = re.sub(r"[_\-\.\(\)\[\]\{\}]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    patrones = [
        r"\bTC\b",
        r"\bTDC\b",
        r"\bTARJETA\b",
        r"\bCREDITO\b",
        r"\bCR[EÉ]DITO\b",
    ]
    return any(re.search(p, s) for p in patrones)


def _es_bbva_tc_por_contenido(ruta_pdf: str) -> bool:
    """
    Fallback: Detecta TC por contenido leyendo SOLO la primera página.
    Útil si el nombre no trae 'TC', pero el PDF sí es TC.
    """
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            texto = (pdf.pages[0].extract_text() or "").upper()

        claves_tc = [
            "TARJETA TITULAR",
            "MOVIMIENTOS EFECTUADOS",
            "IMPORTE CARGOS",
            "IMPORTE ABONOS",
            "T NEGOCIO",
            "T NEGOCIO / LCDIGITAL",
        ]
        return any(k in texto for k in claves_tc)
    except Exception:
        return False


def get_parser_for_file(ruta_pdf: str) -> BaseParser | None:
    nombre = Path(ruta_pdf).name.upper()

    if "BBVA" in nombre:
        # 1) TC por nombre
        if _nombre_indica_tc(nombre) or _es_bbva_tc_por_contenido(ruta_pdf):
            return BBVATCParser(cuenta_por_defecto="TC", moneda_por_defecto="MXN")

        # 2) Cuenta + moneda por nombre (genérico)
        m_moneda = re.search(r"\b(MXN|USD)\b", nombre)
        m_cuenta = re.search(r"\b(\d{4})\b", nombre)  # 4 dígitos tipo 5516, 2697, 9999

        if m_moneda and m_cuenta:
            moneda = m_moneda.group(1)
            cuenta = m_cuenta.group(1)
            return BBVAParser(cuenta_por_defecto=cuenta, moneda_por_defecto=moneda)

        # 3) Fallback final
        return BBVAParser()

    return None

