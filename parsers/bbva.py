from pathlib import Path
from typing import List, Dict, Optional
import re

import pandas as pd

from .base import BaseParser
from bancos_reader.pdf_utils.reader import leer_tablas_pdf
import pdfplumber

PATRON_FECHA = re.compile(r"\d{2}/[A-Z]{3}")
PATRON_MONTO = re.compile(r"^[\d,]+\.\d{2}$")

def _limpiar_monto(valor) -> Optional[float]:
    if valor is None:
        return None
    txt = str(valor).strip()
    if txt == "" or txt == "-":
        return None

    txt = txt.replace("$", "").replace(" ", "")

    if "," in txt and "." in txt:
        txt = txt.replace(",", "")
    else:
        if "," in txt and "." not in txt:
            txt = txt.replace(".", "").replace(",", ".")

    # dejamos el signo como venga; no invertimos nada
    try:
        return float(txt.replace("-", "")) if txt.count("-") == 1 and txt.startswith("-") else float(txt)
    except ValueError:
        return None


def _mes_str_a_num(mes: str) -> str:
    mes = mes.upper()
    mapa = {
        "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
        "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
        "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12",
    }
    return mapa.get(mes, "01")


def _fecha_ddmes_a_iso(fecha_ddmes: str, anio: str) -> str:
    """
    Convierte '01/JUL' y '2025' en '2025-07-01'
    """
    m = re.match(r"^(\d{2})/([A-Z]{3})$", fecha_ddmes.upper())
    if not m:
        return fecha_ddmes  # fallback crudo
    dia = m.group(1)
    mes = _mes_str_a_num(m.group(2))
    return f"{anio}-{mes}-{dia}"


class BBVAParser(BaseParser):
    nombre_banco = "BBVA"

    def __init__(self, cuenta_por_defecto: str | None = None, moneda_por_defecto: str | None = None):
        self.cuenta_por_defecto = cuenta_por_defecto
        self.moneda_por_defecto = moneda_por_defecto or "MXN"
        
    # --- NUEVO: helper para convertir '01/JUL' + '2025' a '2025-07-01' ---
    def _parse_fecha(self, fecha_ddmes: str, anio: str | None) -> str:
        """
        Convierte una fecha tipo '01/JUL' al formato ISO 'YYYY-MM-DD'.
        Si no se puede parsear, regresa el texto original.
        """
        if not anio:
            # si no se detectó año en la portada, podemos usar un default
            anio = "2025"

        fecha_ddmes = fecha_ddmes.strip().upper()
        m = re.match(r"^(\d{2})/([A-Z]{3})$", fecha_ddmes)
        if not m:
            return fecha_ddmes

        dia = m.group(1)
        mes = _mes_str_a_num(m.group(2))
        return f"{anio}-{mes}-{dia}"


    # ------------------ MÉTODO 2: texto, el importante ------------------
    def parse_movimientos(self, ruta_pdf: str | Path) -> pd.DataFrame:
        """
        Parser por layout: usa pdfplumber.extract_words()...
        """
        ruta_pdf = Path(ruta_pdf)
        cuenta = self.cuenta_por_defecto or ruta_pdf.stem
        moneda = self.moneda_por_defecto
        movimientos = []

        with pdfplumber.open(str(ruta_pdf)) as pdf:
            # Texto de la portada (página 0)
            texto_portada = pdf.pages[0].extract_text() or ""
            anio = self._obtener_anio_desde_portada(texto_portada)


            for num_pagina, page in enumerate(pdf.pages, start=1):
                # Solo saltar si parece portada (sin encabezados de tabla)
                txt = (page.extract_text() or "").upper()
                if num_pagina == 1 and ("CARGOS" not in txt or "ABONOS" not in txt):
                    continue

                words = page.extract_words()
                col_centers = self._detectar_columnas_montos(words)
                if not col_centers:
                    continue

                filas = self._agrupar_filas(words)
                idx_ultimo_mov = None

                for fila in filas:
                    texto_fila = " ".join(w["text"] for w in fila).strip()
                    if not texto_fila:
                        continue
                    
                    texto_fila_upper = texto_fila.upper()

                    # Corta al llegar a la sección de totales (última parte de la hoja)
                    if "TOTAL DE MOVIMIENTOS" in texto_fila_upper or "TOTAL MOVIMIENTOS" in texto_fila_upper:
                        break

                    # 1) Detectar si esta fila es un movimiento (tiene dos fechas dd/MES)
                    textos = [w["text"] for w in fila]
                    idx_fechas = [i for i, t in enumerate(textos) if PATRON_FECHA.fullmatch(t)]
                    if len(idx_fechas) >= 2:
                        # Movimiento principal
                        i1, i2 = idx_fechas[0], idx_fechas[1]
                        fecha_op_raw = textos[i1]
                        fecha_liq_raw = textos[i2]

                        fecha_op = self._parse_fecha(fecha_op_raw, anio)
                        fecha_liq = self._parse_fecha(fecha_liq_raw, anio)

                        # Código justo después de la segunda fecha
                        codigo = textos[i2 + 1] if len(textos) > i2 + 1 else ""

                        # Descripción: palabras entre código y la primera cantidad,
                        # pero solo las que están a la izquierda de la primera columna de montos
                        x_cargos = col_centers["CARGOS"]
                        desc_words = []
                        for w in fila:
                            x_centro = (w["x0"] + w["x1"]) / 2
                            if x_centro >= x_cargos:
                                continue
                            if w["text"] in (fecha_op_raw, fecha_liq_raw, codigo):
                                continue
                            desc_words.append(w["text"])

                        descripcion = " ".join(desc_words).strip()

                        # 2) Montos por columna usando la X real
                        cargos = abonos = saldo_oper = saldo_liq = None

                        for w in fila:
                            txt = w["text"]
                            if PATRON_MONTO.fullmatch(txt):
                                val = _limpiar_monto(txt)   # ← función global, sin self
                                x_centro = (w["x0"] + w["x1"]) / 2
                                col = self._columna_por_x(x_centro, col_centers)

                                if col == "CARGOS":
                                    cargos = val
                                elif col == "ABONOS":
                                    abonos = val
                                elif col == "OPERACION":
                                    saldo_oper = val
                                elif col == "LIQUIDACION":
                                    saldo_liq = val

                        movimientos.append({
                            "fecha_operacion": fecha_op,
                            "fecha_liquidacion": fecha_liq,
                            "codigo": codigo,
                            "descripcion": descripcion,
                            "cargos": cargos,
                            "abonos": abonos,
                            "saldo_operacion": saldo_oper,
                            "saldo_liquidacion": saldo_liq,
                            "detalle": "",
                            "cuenta": cuenta,
                            "moneda": moneda,
                            "origen_pdf": ruta_pdf.name,
                            "pagina": num_pagina,
                        })
                        idx_ultimo_mov = len(movimientos) - 1

                    else:
                        # No hay 2 fechas → probablemente es una línea de detalle
                        if idx_ultimo_mov is not None:
                            extra = texto_fila
                            if extra and not extra.startswith("FECHA SALDO"):
                                anterior = movimientos[idx_ultimo_mov].get("detalle", "")
                                movimientos[idx_ultimo_mov]["detalle"] = (
                                    (anterior + " | " + extra) if anterior else extra
                                )

        if not movimientos:
            return pd.DataFrame()

        return pd.DataFrame(movimientos)


    def _obtener_anio_desde_portada(self, texto: str) -> Optional[str]:
        """
        Busca algo como 'DEL 01/07/2025 AL 31/07/2025' y devuelve '2025'
        """
        m = re.search(r"DEL\s+\d{2}/\d{2}/(\d{4})\s+AL\s+\d{2}/\d{2}/\d{4}", texto)
        if m:
            return m.group(1)
        return None


    @staticmethod
    def _detectar_columnas_montos(words, tol_fila=3.0):
        """
        Devuelve los centros X de las columnas: CARGOS, ABONOS, OPERACION, LIQUIDACION
        Elige el encabezado correcto (misma fila) y evita que se pisen centros por
        apariciones repetidas en otras tablas de la página (p.ej. 'Total de movimientos').
        """
        def norm(txt: str) -> str:
            t = (txt or "").upper().strip()
            return (t.replace("Ó", "O")
                    .replace("Á", "A")
                    .replace("Í", "I")
                    .replace("Ú", "U"))

        objetivos = {"CARGOS", "ABONOS", "OPERACION", "LIQUIDACION"}

        # 1) filtrar solo palabras relevantes
        hits = []
        for w in words:
            t = norm(w.get("text", ""))
            if t in objetivos:
                hits.append({**w, "_k": t})

        if not hits:
            return None

        # 2) agrupar por fila (top similar)
        hits.sort(key=lambda ww: (ww["top"], ww["x0"]))
        grupos = []
        for w in hits:
            colocado = False
            for g in grupos:
                if abs(w["top"] - g[0]["top"]) <= tol_fila:
                    g.append(w)
                    colocado = True
                    break
            if not colocado:
                grupos.append([w])

        # 3) candidatos: grupos que tengan las 4 columnas
        candidatos = []
        for g in grupos:
            keys = {w["_k"] for w in g}
            if objetivos <= keys:
                centers = {}
                for w in g:
                    centers[w["_k"]] = (w["x0"] + w["x1"]) / 2
                candidatos.append((g[0]["top"], centers))

        if not candidatos:
            return None

        # 4) elegir el encabezado más arriba (menor top)
        candidatos.sort(key=lambda x: x[0])
        return candidatos[0][1]
                


    @staticmethod
    def _agrupar_filas(words, tol=2.0):
        """
        Agrupa las palabras en filas usando la coordenada 'top'.
        """
        rows = []
        current = []
        current_top = None

        for w in sorted(words, key=lambda ww: (ww["top"], ww["x0"])):
            if current_top is None:
                current_top = w["top"]
            elif abs(w["top"] - current_top) > tol:
                rows.append(current)
                current = []
                current_top = w["top"]
            current.append(w)

        if current:
            rows.append(current)

        return rows

    @staticmethod
    def _columna_por_x(x, col_centers):
        """
        Devuelve el nombre de columna ('CARGOS', 'ABONOS', 'OPERACION', 'LIQUIDACION')
        más cercana al centro X dado.
        """
        return min(col_centers.items(), key=lambda kv: abs(x - kv[1]))[0]
    
    def crear_tablas_meses(self, df):
        
        df_meses = {}
        self.meses = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}
        if 'Fecha' in df.columns:
            for line in df['Fecha']:
                if line.month in self.meses.keys() and self.meses[line.month] not in df_meses.keys():
                    df_meses[self.meses[line.month]] = line