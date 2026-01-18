# src/bancos_reader/parsers/bbva_tc.py

from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Optional
import re

import pandas as pd
import pdfplumber

from .base import BaseParser


# Fechas TC: 08/01/25 o 08/01/2025
PATRON_FECHA_TC = re.compile(r"^\d{2}/\d{2}/\d{2,4}$")
# Monto TC: 1,071.00 o -12,432.34
PATRON_MONTO_TC = re.compile(r"^-?[\d,]+\.\d{2}$")


def _parse_fecha_tc(s: str) -> Optional[str]:
    """
    Convierte '08/01/25' a '2025-01-08' (ISO). Day-first.
    """
    s = (s or "").strip()
    if not s:
        return None
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return None
    return dt.strftime("%Y-%m-%d")


def _limpiar_monto(txt: str) -> Optional[float]:
    if txt is None:
        return None
    s = str(txt).strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # 1,234.56 -> 1234.56
    if "," in s and "." in s:
        s = s.replace(",", "")
    # 1.234,56 -> 1234.56 (por si acaso)
    elif "," in s and "." not in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _norm_line(line: str) -> str:
    # Normaliza espacios y quita caracteres raros sueltos
    line = (line or "").replace("\u00a0", " ")
    return " ".join(line.strip().split())


class BBVATCParser(BaseParser):
    # IMPORTANTE: debe ser "BBVA" para que pase tu filtro del checkbox
    nombre_banco = "BBVA"
    tipo_producto = "TC"

    def __init__(self, cuenta_por_defecto: str | None = None, moneda_por_defecto: str | None = None):
        self.cuenta_por_defecto = cuenta_por_defecto
        self.moneda_por_defecto = moneda_por_defecto or "MXN"

    def parse_movimientos(self, ruta_pdf: str | Path) -> pd.DataFrame:
        ruta_pdf = Path(ruta_pdf)
        cuenta = self.cuenta_por_defecto or ruta_pdf.stem
        moneda = self.moneda_por_defecto

        movimientos: List[Dict] = []

        # Corte real (aparece al final de la página donde ya no hay movimientos)
        stop_markers = [
            "TABLA / GRAFICO DE ESTADO DE CUENTA",
            "TABLA / GRÁFICO DE ESTADO DE CUENTA",
            "TABLA/GRAFICO DE ESTADO DE CUENTA",
            "TABLA/GRÁFICO DE ESTADO DE CUENTA",
        ]

        # Regex 1: línea con RFC (ej: "... AME 1404027R0 ******7111 $ 399.00")
        rx_con_rfc = re.compile(
            r"^(?P<f1>\d{2}/\d{2}/\d{2,4})\s+"
            r"(?P<f2>\d{2}/\d{2}/\d{2,4})\s+"
            r"(?P<concepto>.+?)\s+"
            r"(?P<rfc1>[A-ZÑ&]{3})\s+(?P<rfc2>[0-9A-Z]{8,12})\s+"
            r"(?P<ref>[\*Xx\d]{4,})\s+"
            r"\$\s*(?P<monto>-?[\d,]+\.\d{2})\s*$"
        )

        # Regex 2: línea SIN RFC (ej pago: "... PAGO TDC ******0110 $ -12,432.34")
        rx_sin_rfc = re.compile(
            r"^(?P<f1>\d{2}/\d{2}/\d{2,4})\s+"
            r"(?P<f2>\d{2}/\d{2}/\d{2,4})\s+"
            r"(?P<concepto>.+?)\s+"
            r"(?P<ref>[\*Xx\d]{4,})\s+"
            r"\$\s*(?P<monto>-?[\d,]+\.\d{2})\s*$"
        )

        # Líneas que NO son movimientos aunque parezcan texto
        skip_starts = (
            "ESTADO DE CUENTA",
            "PAGINA",
            "LINEA BBVA",
            "AV. PASEO",
            "BBVA MEXICO",
            "ESTIMADO TARJETAHABIENTE",
            "IVA",
            "\"SI ESTAS ADHERIDO",
            "SI ESTAS ADHERIDO",
        )

        stop_found = False

        with pdfplumber.open(str(ruta_pdf)) as pdf:
            for num_pagina, page in enumerate(pdf.pages, start=1):
                if stop_found:
                    break

                text = page.extract_text() or ""
                lines = [_norm_line(l) for l in (text.splitlines() if text else [])]
                if not lines:
                    continue

                for line in lines:
                    if not line:
                        continue

                    up = line.upper()

                    # ✅ CORTE POR LÍNEA (NO por página)
                    if any(m in up for m in stop_markers):
                        stop_found = True
                        break

                    if up.startswith(skip_starts):
                        continue

                    # ignora encabezados de tabla
                    if "FECHA" in up and "AUTORIZACION" in up and "APLICACION" in up:
                        continue
                    if "IMPORTE" in up and ("CARGOS" in up or "ABONOS" in up):
                        continue

                    m = rx_con_rfc.match(line)
                    rfc = ""
                    ref = ""

                    if m:
                        f1 = m.group("f1")
                        f2 = m.group("f2")
                        concepto = m.group("concepto").strip()
                        rfc = f"{m.group('rfc1').strip()} {m.group('rfc2').strip()}".strip()
                        ref = m.group("ref").strip()
                        monto_txt = m.group("monto").strip()
                    else:
                        m2 = rx_sin_rfc.match(line)
                        if not m2:
                            continue
                        f1 = m2.group("f1")
                        f2 = m2.group("f2")
                        concepto = m2.group("concepto").strip()
                        ref = m2.group("ref").strip()
                        monto_txt = m2.group("monto").strip()

                    fecha_op = _parse_fecha_tc(f1) or f1
                    fecha_liq = _parse_fecha_tc(f2) or f2

                    monto = _limpiar_monto(monto_txt)
                    if monto is None:
                        continue

                    cargos = None
                    abonos = None
                    if monto < 0:
                        abonos = abs(monto)
                    else:
                        cargos = monto

                    detalle_parts = []
                    if rfc:
                        detalle_parts.append(f"RFC:{rfc}")
                    if ref:
                        detalle_parts.append(f"REF:{ref}")
                    detalle = " ".join(detalle_parts).strip()

                    movimientos.append({
                        "fecha_operacion": fecha_op,
                        "fecha_liquidacion": fecha_liq,
                        "codigo": "",
                        "descripcion": concepto,
                        "cargos": cargos,
                        "abonos": abonos,
                        "saldo_operacion": None,
                        "saldo_liquidacion": None,
                        "detalle": detalle,
                        "cuenta": cuenta,
                        "moneda": moneda,
                        "origen_pdf": ruta_pdf.name,
                        "pagina": num_pagina,
                    })

        if not movimientos:
            return pd.DataFrame()

        df = pd.DataFrame(movimientos)

        df["fecha_liquidacion"] = pd.to_datetime(df["fecha_liquidacion"], errors="coerce")
        df = df.sort_values(by=["fecha_liquidacion", "pagina"], na_position="last").reset_index(drop=True)

        return df
