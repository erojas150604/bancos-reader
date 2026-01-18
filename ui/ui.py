# src/bancos_reader/ui/ui.py

import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import re
import threading
import queue

from bancos_reader.core.detector_banco import get_parser_for_file
from bancos_reader.core.db import guardar_movimientos, listar_tablas
from bancos_reader.transformers.plantilla import crear_df_plantilla, formatear_df_para_excel
from bancos_reader.transformers.nombres import (
    extraer_banco_y_moneda_desde_nombre,
    extraer_tipo_moneda_cuenta_desde_nombre,
    construir_nombre_excel_por_df,
    mes_nombre_es,
    excel_safe_sheet_name,
    _normalizar_cuenta as normalizar_cuenta,  # ideal: renómbrala sin "_" en tu módulo
)

from bancos_reader.version import __app_name__, __version__
from bancos_reader.ui.ui_utils import aplicar_icono


# ==========================
# Debug / Logging simple
# ==========================
DEBUG = True

def log(msg: str):
    if DEBUG:
        print(f"[UI] {msg}")


def safe_sql_table_name(name: str) -> str:
    s = str(name)
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "TABLA"


class BankReaderApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # (Opcional) silenciar mensajes feos de pdfminer:
        # import logging
        # logging.getLogger("pdfminer").setLevel(logging.ERROR)

        aplicar_icono(self)

        self.title(f"{__app_name__} - Tecnología Empresarial v{__version__}")
        self.geometry("800x500")
        self.minsize(700, 400)

        # raíz del proyecto: .../bancos_reader
        root = Path(__file__).resolve().parents[3]
        self.db_path = root / "src" / "bancos_reader" / "base_de_datos.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        log(f"App iniciada: {__app_name__} v{__version__}")
        log(f"Ruta proyecto: {root}")
        log(f"DB temporal: {self.db_path}")

        if self.db_path.exists():
            try:
                self.db_path.unlink()
                log("DB temporal anterior eliminada al iniciar.")
            except OSError as e:
                log(f"[WARN] No se pudo borrar DB temporal al iniciar: {type(e).__name__}: {e}")

        self.selected_files: list[str] = []

        self.bank_options = ["BBVA"]
        self.bank_vars: dict[str, tk.BooleanVar] = {}

        self.context_menu = None

        self._build_widgets()

        self._ui_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self.after(100, self._poll_ui_queue)

        self._save_reply_q: queue.Queue = queue.Queue()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------------
    # helpers log + status
    # -------------------------------------------------------
    def _log_status(self, msg: str):
        log(msg)
        self._ui_queue.put(("STATUS", msg))

    # -------------------------------------------------------
    # UI
    # -------------------------------------------------------
    def _build_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill="x")

        ttk.Button(top_frame, text="Cargar archivos PDF...", command=self.on_cargar_archivos).pack(side="left")
        ttk.Button(top_frame, text="Resetear interfaz", command=self.on_reset).pack(side="left", padx=(10, 0))

        self.label_carpeta = ttk.Label(top_frame, text="No se han cargado archivos", anchor="w")
        self.label_carpeta.pack(side="left", padx=10, fill="x", expand=True)

        self.progress = ttk.Progressbar(top_frame, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=(10, 0))
        self.progress.stop()
        self.progress.pack_forget()

        center_frame = ttk.LabelFrame(main_frame, text="Archivos seleccionados")
        center_frame.pack(fill="both", expand=True, pady=10)

        self.listbox_archivos = tk.Listbox(center_frame, height=12)
        scrollbar = ttk.Scrollbar(center_frame, orient="vertical", command=self.listbox_archivos.yview)
        self.listbox_archivos.config(yscrollcommand=scrollbar.set)
        self.listbox_archivos.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Quitar de la lista", command=self._remove_selected_file)
        self.listbox_archivos.bind("<Button-3>", self._show_context_menu)

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=(10, 0))

        bancos_frame = ttk.LabelFrame(bottom_frame, text="Bancos disponibles:")
        bancos_frame.pack(side="left", fill="x", expand=True)

        for nombre in self.bank_options:
            var = tk.BooleanVar(value=True)
            self.bank_vars[nombre] = var
            ttk.Checkbutton(bancos_frame, text=nombre, variable=var).pack(side="left", padx=5)

        ttk.Button(bottom_frame, text="Exportar a Excel", command=self.on_exportar_excel).pack(side="right", padx=5)

    def _start_busy(self, msg: str | None = None):
        if msg:
            self.label_carpeta.config(text=msg)
        self.progress.pack(side="right", padx=(10, 0))
        self.progress.start(12)
        self.update_idletasks()

    def _stop_busy(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.update_idletasks()

    # -------------------------------------------------------
    # Acciones
    # -------------------------------------------------------
    def on_cargar_archivos(self):
        self._start_busy("Cargando archivos...")
        try:
            rutas = filedialog.askopenfilenames(
                title="Selecciona estados de cuenta (PDF)",
                filetypes=[("Archivos PDF", "*.pdf")],
            )
            if not rutas:
                return

            nuevos = [r for r in rutas if r not in self.selected_files]
            if not nuevos:
                return

            self.selected_files.extend(nuevos)
            self._refrescar_listbox()
            self._actualizar_label_carpeta()

            log(f"Cargados {len(nuevos)} PDF(s). Total ahora: {len(self.selected_files)}")
        finally:
            self._stop_busy()

    def on_exportar_excel(self):
        if not self.selected_files:
            messagebox.showwarning("Sin archivos", "Primero selecciona uno o más archivos PDF.")
            return

        bancos_activos = [n.upper() for n, var in self.bank_vars.items() if var.get()]
        if not bancos_activos:
            messagebox.showwarning("Sin bancos seleccionados", "Selecciona al menos un banco para procesar.")
            return

        self._start_busy("Procesando archivos...")
        self._worker_thread = threading.Thread(
            target=self._exportar_excel_worker,
            args=(bancos_activos,),
            daemon=True
        )
        self._worker_thread.start()

    # -------------------------------------------------------
    # Worker
    # -------------------------------------------------------
    def _exportar_excel_worker(self, bancos_activos: list[str]):
        generados, cancelados, fallidos = [], [], []
        resumen_excels = []

        try:
            log(f"[WORKER] Iniciando exportación | PDFs={len(self.selected_files)} | bancos_activos={bancos_activos}")

            if self.db_path.exists():
                try:
                    self.db_path.unlink()
                    log("[WORKER] DB temporal borrada antes de procesar.")
                except OSError:
                    pass

            self._log_status("Leyendo PDFs (parser) ...")
            df_total, fallidos = self._procesar_archivos(bancos_activos)

            if df_total is None or df_total.empty:
                log("[WORKER] Sin movimientos totales (df_total vacío).")
                self._ui_queue.put(("DONE", {"generados": [], "cancelados": [], "fallidos": fallidos, "df_total_len": 0}))
                return

            log(f"[WORKER] Parseo terminado | movimientos_total={len(df_total)} | fallidos={len(fallidos)}")

            for col in ["banco", "moneda", "tipo_producto", "cuenta", "origen_pdf"]:
                if col not in df_total.columns:
                    df_total[col] = ""

            df_total["cuenta"] = df_total["cuenta"].astype(str).str.strip()

            cuentas = sorted([c for c in df_total["cuenta"].dropna().unique() if str(c).strip()])
            if not cuentas:
                cuentas = ["SIN_CUENTA"]
                df_total["cuenta"] = "SIN_CUENTA"

            total_cuentas = len(cuentas)
            log(f"[WORKER] Cuentas detectadas: {total_cuentas}")

            for i, cuenta in enumerate(cuentas, start=1):
                df_c = df_total[df_total["cuenta"] == cuenta].copy()

                bancos = sorted({str(x).strip().upper() for x in df_c.get("banco", pd.Series(dtype="object")).dropna() if str(x).strip()})
                tipos  = sorted({str(x).strip().upper() for x in df_c.get("tipo_producto", pd.Series(dtype="object")).dropna() if str(x).strip()})
                mons   = sorted({str(x).strip().upper() for x in df_c.get("moneda", pd.Series(dtype="object")).dropna() if str(x).strip()})

                banco_txt = bancos[0] if len(bancos) == 1 else ("MULTI" if len(bancos) > 1 else "BANCO")
                tipo_txt  = "TC" if "TC" in tipos else ""
                mon_txt   = mons[0] if len(mons) == 1 else ("+".join(mons) if len(mons) > 1 else "")

                movs = len(df_c)
                cuenta_txt = str(cuenta)

                resumen_excels.append({
                    "banco": banco_txt,
                    "tipo": tipo_txt,
                    "moneda": mon_txt,
                    "cuenta": cuenta_txt,
                    "movs": movs
                })

                if df_c.empty:
                    log(f"[WORKER] Cuenta {cuenta} sin movimientos (skip).")
                    continue

                self._log_status(f"Cuenta {i}/{total_cuentas}: {cuenta_txt} | movs={movs}")

                cuenta_sql = safe_sql_table_name(cuenta)

                df_plantilla_raw = crear_df_plantilla(df_c)
                df_plantilla_fmt = formatear_df_para_excel(df_plantilla_raw)

                # Guarda plantilla formateada en DB (debug/backup)
                guardar_movimientos(df_plantilla_fmt, str(self.db_path), f"plantilla_fmt_{cuenta_sql}")

                # Guardar por periodos (DB)
                fechas_raw = pd.to_datetime(df_plantilla_raw["Fecha"], errors="coerce")
                if fechas_raw.notna().any():
                    tmp = df_plantilla_raw.copy()
                    tmp["_ANIO"] = fechas_raw.dt.year
                    tmp["_MES"] = fechas_raw.dt.month

                    periodos = (
                        tmp[["_ANIO", "_MES"]]
                        .dropna()
                        .drop_duplicates()
                        .sort_values(by=["_ANIO", "_MES"])
                        .itertuples(index=False, name=None)
                    )

                    for anio, mes in periodos:
                        df_periodo = tmp[(tmp["_ANIO"] == anio) & (tmp["_MES"] == mes)].drop(columns=["_ANIO", "_MES"], errors="ignore")
                        guardar_movimientos(df_periodo, str(self.db_path), f"plantilla_{cuenta_sql}_{int(mes):02d}_{int(anio)}")

                _ = listar_tablas(self.db_path)  # debug (si quieres, loguea aquí)

                try:
                    nombre_sugerido = construir_nombre_excel_por_df(df_c)
                except Exception:
                    nombre_sugerido = f"Estado de Cuenta - {cuenta}.xlsx"

                self._ui_queue.put(("ASK_SAVE", {"cuenta": cuenta, "initialfile": nombre_sugerido}))
                ruta_excel = self._save_reply_q.get()

                if not ruta_excel:
                    log(f"[WORKER] Cancelado guardar Excel para cuenta {cuenta_txt}")
                    cancelados.append(cuenta)
                    continue

                ruta_excel = Path(ruta_excel)
                ruta_excel.parent.mkdir(parents=True, exist_ok=True)

                if ruta_excel.exists():
                    try:
                        ruta_excel.unlink()
                    except PermissionError:
                        self._ui_queue.put(("MSG_ERROR", {
                            "title": "Archivo en uso",
                            "text": f"No se puede sobrescribir:\n\n{ruta_excel.name}\n\nCierra Excel o guarda con otro nombre."
                        }))
                        cancelados.append(cuenta)
                        continue

                fechas = pd.to_datetime(df_plantilla_raw["Fecha"], errors="coerce")

                if fechas.notna().sum() == 0:
                    df_excel_unica = formatear_df_para_excel(df_plantilla_raw)
                    with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
                        df_excel_unica.to_excel(writer, sheet_name="MOVIMIENTOS", index=False)
                        writer.sheets["MOVIMIENTOS"].freeze_panes = "A2"
                else:
                    df_plantilla_raw = df_plantilla_raw.copy()
                    df_plantilla_raw["_ANIO"] = fechas.dt.year
                    df_plantilla_raw["_MES"] = fechas.dt.month

                    periodos = (
                        df_plantilla_raw[["_ANIO", "_MES"]]
                        .dropna()
                        .drop_duplicates()
                        .sort_values(by=["_ANIO", "_MES"])
                        .itertuples(index=False, name=None)
                    )

                    with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
                        used = set()
                        hojas = 0
                        for anio, mes in periodos:
                            df_periodo_raw = df_plantilla_raw[
                                (df_plantilla_raw["_ANIO"] == anio) & (df_plantilla_raw["_MES"] == mes)
                            ].drop(columns=["_ANIO", "_MES"], errors="ignore")

                            df_periodo_excel = formatear_df_para_excel(df_periodo_raw)

                            base = f"{mes_nombre_es(int(mes))}_{int(anio)}"
                            sheet = excel_safe_sheet_name(base, used)

                            df_periodo_excel.to_excel(writer, sheet_name=sheet, index=False)
                            writer.sheets[sheet].freeze_panes = "A2"
                            hojas += 1

                        if hojas == 0:
                            df_excel_unica = formatear_df_para_excel(df_plantilla_raw.drop(columns=["_ANIO", "_MES"], errors="ignore"))
                            df_excel_unica.to_excel(writer, sheet_name="MOVIMIENTOS", index=False)
                            writer.sheets["MOVIMIENTOS"].freeze_panes = "A2"

                generados.append(str(ruta_excel))
                log(f"[WORKER] Excel generado OK -> {ruta_excel}")

            self._ui_queue.put(("DONE", {
                "generados": generados,
                "cancelados": cancelados,
                "fallidos": fallidos,
                "df_total_len": len(df_total),
                "total_pdfs": len(self.selected_files),
                "resumen_excels": resumen_excels,
            }))

        except Exception as e:
            log(f"[WORKER][ERROR] {type(e).__name__}: {e}")
            self._ui_queue.put(("MSG_ERROR", {"title": "Error", "text": f"{type(e).__name__}: {e}"}))
            self._ui_queue.put(("DONE", {"generados": generados, "cancelados": cancelados, "fallidos": fallidos, "df_total_len": 0}))

    # -------------------------------------------------------
    # Parser pipeline
    # -------------------------------------------------------
    def _procesar_archivos(self, bancos_activos: list[str]) -> tuple[pd.DataFrame, list[dict]]:
        dfs: list[pd.DataFrame] = []
        fallidos: list[dict] = []
        bancos_activos_upper = {b.upper().strip() for b in bancos_activos}

        total = len(self.selected_files)

        for i, ruta in enumerate(self.selected_files, start=1):
            ruta = str(ruta)
            nombre_pdf = Path(ruta).name

            self._log_status(f"Leyendo PDF {i}/{total}: {nombre_pdf}")
            log(f"[PROCESO] ({i}/{total}) {nombre_pdf}")

            banco_nombre, moneda_nombre = extraer_banco_y_moneda_desde_nombre(ruta)
            tipo_nombre, moneda_nombre2, cuenta_nombre = extraer_tipo_moneda_cuenta_desde_nombre(ruta)
            if not moneda_nombre and moneda_nombre2:
                moneda_nombre = moneda_nombre2

            try:
                parser = get_parser_for_file(ruta)
                if parser is None:
                    fallidos.append({
                        "archivo": ruta, "banco": banco_nombre or "", "moneda": moneda_nombre or "",
                        "cuenta": cuenta_nombre or "", "tipo": tipo_nombre or "",
                        "error": "No se encontró parser compatible."
                    })
                    log(f"[SKIP] Sin parser: {nombre_pdf}")
                    continue

                banco_parser = str(getattr(parser, "nombre_banco", "")).upper().strip()
                banco = banco_parser or (banco_nombre or "")
                if not banco:
                    fallidos.append({
                        "archivo": ruta, "banco": "", "moneda": moneda_nombre or "",
                        "cuenta": cuenta_nombre or "", "tipo": tipo_nombre or "",
                        "error": "No se pudo determinar banco."
                    })
                    log(f"[FAIL] No se pudo determinar banco: {nombre_pdf}")
                    continue

                if banco.upper() not in bancos_activos_upper:
                    log(f"[SKIP] Banco no seleccionado ({banco}): {nombre_pdf}")
                    continue

                tipo_parser = str(getattr(parser, "tipo_producto", "") or "").upper().strip()
                tipo_final = "TC" if (tipo_nombre or "").upper().strip() == "TC" else (tipo_parser or (tipo_nombre or ""))

                log(f"[PARSE] Banco={banco} | Tipo={tipo_final or '-'} | Archivo={nombre_pdf}")
                df = parser.parse_movimientos(ruta)

                if df is None or df.empty:
                    fallidos.append({
                        "archivo": ruta, "banco": banco, "moneda": moneda_nombre or "",
                        "cuenta": cuenta_nombre or "", "tipo": tipo_nombre or "",
                        "error": "df vacío"
                    })
                    log(f"[WARN] df vacío: {nombre_pdf}")
                    continue

                df["banco"] = banco
                if "origen_pdf" not in df.columns:
                    df["origen_pdf"] = nombre_pdf

                df["tipo_producto"] = tipo_final

                cuenta_df_val = ""
                if "cuenta" in df.columns:
                    s = df["cuenta"].astype(str).str.strip()
                    cuenta_df_val = (s[s.ne("")].iloc[0] if (s.ne("").any()) else "")

                df["cuenta"] = normalizar_cuenta(cuenta_df_val, cuenta_nombre, ruta)

                if "moneda" not in df.columns or df["moneda"].astype(str).str.strip().eq("").all():
                    df["moneda"] = moneda_nombre or ""

                guardar_movimientos(df, str(self.db_path), f"mov_{banco.lower()}")
                log(f"[OK] Movimientos leídos: {len(df)} | cuenta={df['cuenta'].iloc[0] if 'cuenta' in df.columns else ''}")

                dfs.append(df)

            except Exception as e:
                fallidos.append({
                    "archivo": ruta, "banco": banco_nombre or "", "moneda": moneda_nombre or "",
                    "cuenta": cuenta_nombre or "", "tipo": tipo_nombre or "",
                    "error": f"{type(e).__name__}: {e}"
                })
                log(f"[ERROR] {nombre_pdf} -> {type(e).__name__}: {e}")

        # ✅ evita FutureWarning + concat de vacíos
        dfs = [d for d in dfs if d is not None and not d.empty]
        if not dfs:
            return pd.DataFrame(), fallidos

        return pd.concat(dfs, ignore_index=True), fallidos

    # -------------------------------------------------------
    # UI helpers
    # -------------------------------------------------------
    def _poll_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()

                if kind == "STATUS":
                    self.label_carpeta.config(text=str(payload))
                    self.update_idletasks()

                elif kind == "ASK_SAVE":
                    cuenta = payload["cuenta"]
                    initialfile = payload["initialfile"]

                    ruta_excel = filedialog.asksaveasfilename(
                        title=f"Guardar Excel - Cuenta {cuenta}",
                        defaultextension=".xlsx",
                        filetypes=[("Archivo de Excel", "*.xlsx")],
                        initialfile=initialfile
                    )
                    self._save_reply_q.put(ruta_excel or "")

                elif kind == "MSG_ERROR":
                    messagebox.showerror(payload.get("title", "Error"), payload.get("text", "Ocurrió un error."))

                elif kind == "DONE":
                    self._stop_busy()
                    self._actualizar_label_carpeta()

                    generados = payload.get("generados", [])
                    cancelados = payload.get("cancelados", [])
                    fallidos = payload.get("fallidos", [])
                    df_total_len = payload.get("df_total_len", 0)
                    total_pdfs = payload.get("total_pdfs", len(self.selected_files))
                    resumen_excels = payload.get("resumen_excels", [])

                    if df_total_len == 0:
                        if fallidos:
                            preview = "\n".join(f"- {Path(f['archivo']).name}: {f.get('error','')}" for f in fallidos[:10])
                            if len(fallidos) > 10:
                                preview += f"\n... y {len(fallidos) - 10} más."
                            messagebox.showwarning("Sin datos", "No se encontraron movimientos válidos.\n\nArchivos con error:\n\n" + preview)
                        else:
                            messagebox.showwarning("Sin datos", "No se encontraron movimientos en los archivos seleccionados.")
                        return

                    if not generados:
                        messagebox.showwarning("Sin datos", "No se generaron Excels (se cancelaron o quedaron vacíos).")
                        return

                    preview = "\n".join(generados[:10])
                    if len(generados) > 10:
                        preview += f"\n... y {len(generados) - 10} más."

                    extra = ""
                    if cancelados:
                        extra = "\n\nCuentas canceladas:\n- " + "\n- ".join(cancelados)

                    # ---- Texto resumen por Excel ----
                    resumen_txt = ""
                    if resumen_excels:
                        lineas = []
                        for r in resumen_excels:
                            partes = []
                            if r.get("banco"): partes.append(r["banco"])
                            if r.get("tipo"): partes.append(r["tipo"])          # TC si aplica
                            if r.get("moneda"): partes.append(r["moneda"])
                            if r.get("cuenta"): partes.append(f"Cuenta {r['cuenta']}")
                            etiqueta = " | ".join(partes) if partes else "EXCEL"
                            lineas.append(f"- {etiqueta}: {r.get('movs', 0)} movimientos")
                        resumen_txt = "\n\nMovimientos por Excel:\n" + "\n".join(lineas[:25])
                        if len(lineas) > 25:
                            resumen_txt += f"\n... y {len(lineas) - 25} más."

                    messagebox.showinfo(
                        "Exportación completada",
                        (
                            f"Archivos PDF procesados: {total_pdfs}\n"
                            f"Movimientos totales: {df_total_len}\n"
                            f"Excels generados: {len(generados)}\n\n"
                            f"Archivos:\n{preview}"
                            f"{extra}"
                            f"{resumen_txt}"
                        )
                    )

                    if fallidos:
                        preview_err = "\n".join(f"- {Path(f['archivo']).name}: {f.get('error','')}" for f in fallidos[:8])
                        if len(fallidos) > 8:
                            preview_err += f"\n... y {len(fallidos) - 8} más."
                        messagebox.showwarning("Lectura incompleta", "Se generaron los Excels, pero algunos PDFs fallaron:\n\n" + preview_err)

        except queue.Empty:
            pass

        self.after(100, self._poll_ui_queue)

    def _refrescar_listbox(self):
        self.listbox_archivos.delete(0, tk.END)
        for ruta in self.selected_files:
            self.listbox_archivos.insert(tk.END, ruta)

    def _actualizar_label_carpeta(self):
        if not self.selected_files:
            self.label_carpeta.config(text="No se han cargado archivos")
            return
        base_dirs = {str(Path(r).parent) for r in self.selected_files}
        self.label_carpeta.config(
            text=f"Carpeta: {base_dirs.pop()}" if len(base_dirs) == 1 else "Carpetas múltiples seleccionadas"
        )

    def _show_context_menu(self, event):
        index = self.listbox_archivos.nearest(event.y)
        if index >= 0:
            self.listbox_archivos.selection_clear(0, tk.END)
            self.listbox_archivos.selection_set(index)
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    def _remove_selected_file(self):
        sel = self.listbox_archivos.curselection()
        if not sel:
            return
        index = sel[0]
        try:
            del self.selected_files[index]
        except IndexError:
            return
        self._refrescar_listbox()
        self._actualizar_label_carpeta()

    def on_reset(self):
        self.selected_files = []
        self._refrescar_listbox()
        self.label_carpeta.config(text="No se han cargado archivos")
        for var in self.bank_vars.values():
            var.set(True)
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except OSError:
                pass
        messagebox.showinfo("Interfaz reiniciada", "Se han limpiado los archivos seleccionados y borrado la base temporal.")

    def on_close(self):
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except OSError:
                pass
        self.destroy()


def main():
    app = BankReaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
