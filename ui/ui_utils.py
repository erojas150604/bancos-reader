from pathlib import Path
import sys
import tkinter as tk

def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)  # PyInstaller

    # desarrollo: raíz del repo = .../bancos_reader
    # ui_utils.py está en .../bancos_reader/src/bancos_reader/ui_utils.py
    root = Path(__file__).resolve().parents[3]  # sube: bancos_reader/ui_utils.py -> bancos_reader/
    return str(root / relative_path)


def aplicar_icono(win: tk.Misc, icon_name: str = "Logo CUBE.ico") -> None:
    """
    Aplica el icono a Tk() o Toplevel() de forma robusta.
    """
    try:
        ico_path = resource_path(icon_name)

        # Windows: iconbitmap es lo más confiable para .ico
        win.iconbitmap(ico_path)

        # Bonus: asegura que también quede para nuevas ventanas si es root
        # (no siempre necesario, pero ayuda)
        if isinstance(win, tk.Tk):
            win.wm_iconbitmap(ico_path)
    except Exception as e:
        # No truena la app por icono
        print(f"[ICONO] No se pudo aplicar icono: {e}")
