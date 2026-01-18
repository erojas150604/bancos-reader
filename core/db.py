# src/bancos_reader/core/db.py
from pathlib import Path
import sqlite3
import pandas as pd

def init_db(ruta_db: str) -> None:
    ruta_db = Path(ruta_db)
    conn = sqlite3.connect(ruta_db)
    conn.close()

def guardar_movimientos(df: pd.DataFrame, ruta_db: str, nombre_tabla: str) -> None:
    ruta_db = Path(ruta_db)
    with sqlite3.connect(ruta_db) as conn:
        df.to_sql(nombre_tabla, conn, if_exists="append", index=False)

def listar_tablas(ruta_db: Path) -> list[str]:
    if not ruta_db.exists():
        return []
    with sqlite3.connect(str(ruta_db)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        ).fetchall()
    return [r[0] for r in rows]
