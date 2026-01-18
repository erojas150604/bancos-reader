# src/bancos_reader/parsers/base.py

from abc import ABC, abstractmethod
from pathlib import Path
import pandas as pd


class BaseParser(ABC):
    """
    Clase base para todos los parsers de bancos.
    """

    nombre_banco: str = "BASE"

    @abstractmethod
    def parse_movimientos(self, ruta_pdf: str) -> pd.DataFrame:
        """
        Lee un PDF de estado de cuenta y devuelve un DataFrame con los movimientos.
        Las columnas mínimas recomendadas:
        fecha, descripcion, referencia, cargo, abono, saldo, cuenta, moneda, origen_pdf, pagina
        """
        raise NotImplementedError()
