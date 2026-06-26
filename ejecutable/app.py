import os
import sys
import time
import datetime
import calendar
import threading
import json
import traceback
import customtkinter as ctk
from tkinter import messagebox
import pandas as pd
from dotenv import load_dotenv

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

IS_MP_HELPER = getattr(sys, "frozen", False) and any(
    marker in " ".join(sys.argv)
    for marker in ("multiprocessing.resource_tracker", "multiprocessing.spawn")
)


def _resolve_runtime_paths():
    if getattr(sys, "frozen", False):
        resource_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        runtime_dir = os.path.dirname(os.path.abspath(sys.executable))

        if sys.platform == "darwin":
            macos_dir = os.path.dirname(os.path.abspath(sys.executable))
            contents_dir = os.path.dirname(macos_dir)
            if os.path.basename(macos_dir) == "MacOS" and os.path.basename(contents_dir) == "Contents":
                app_bundle_dir = os.path.dirname(contents_dir)
                runtime_dir = os.path.dirname(app_bundle_dir)

        return resource_dir, runtime_dir

    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return project_dir, project_dir


resource_dir, base_dir = _resolve_runtime_paths()

for path in (resource_dir, base_dir):
    if path not in sys.path:
        sys.path.append(path)

try:
    os.chdir(base_dir)
except OSError:
    pass


def _write_boot_log(message):
    if IS_MP_HELPER:
        return
    try:
        boot_log_path = os.path.join(base_dir, "app_boot.log")
        with open(boot_log_path, "a", encoding="utf-8") as boot_log:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            boot_log.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _log_exception(prefix, exc_type, exc_value, exc_traceback):
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    _write_boot_log(f"{prefix}\n{details}")


def _global_excepthook(exc_type, exc_value, exc_traceback):
    _log_exception("Excepción no controlada al iniciar la app.", exc_type, exc_value, exc_traceback)
    try:
        messagebox.showerror(
            "Error al abrir la aplicación",
            (
                "La app falló durante el arranque.\n\n"
                f"Revisa el archivo:\n{os.path.join(base_dir, 'app_boot.log')}"
            ),
        )
    except Exception:
        pass


sys.excepthook = _global_excepthook
_write_boot_log(
    "Inicio de app "
    f"(frozen={getattr(sys, 'frozen', False)}, resource_dir={resource_dir}, runtime_dir={base_dir})"
)


def _show_error_dialog(title, message):
    try:
        messagebox.showerror(title, message)
    except Exception:
        pass

# Forzar a dotenv a buscar archivos en la carpeta del ejecutable/raíz del proyecto
dotenv_candidates = [os.path.join(base_dir, ".env")]
if resource_dir != base_dir:
    dotenv_candidates.append(os.path.join(resource_dir, ".env"))

for dotenv_path in dotenv_candidates:
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
        _write_boot_log(f"Archivo .env cargado desde: {dotenv_path}")
        break
else:
    load_dotenv()
    _write_boot_log("No se encontró .env explícito; se cargó dotenv por búsqueda estándar.")

# Interceptar BigQuery Ingest para reporte consolidado local
from bigquery import bq_ingestion

ingested_dfs = []
original_ingest_to_bigquery = bq_ingestion.ingest_to_bigquery

def custom_ingest_to_bigquery(df, project_id=None):
    if df is not None and len(df) > 0:
        ingested_dfs.append(df.copy())
    return original_ingest_to_bigquery(df, project_id)

bq_ingestion.ingest_to_bigquery = custom_ingest_to_bigquery

# Importar flujos de orquestación
from scrapers import pase_rpa, supramax_rpa, edenred_rpa, fleetup_rpa
from extractors import edenred_extractor


class CustomConsoleRedirector:
    def __init__(self, textbox_widget):
        self.textbox_widget = textbox_widget

    def write(self, string):
        self.textbox_widget.configure(state="normal")
        self.textbox_widget.insert("end", string)
        self.textbox_widget.see("end")
        self.textbox_widget.configure(state="disabled")

    def flush(self):
        pass


class CTkCalendar(ctk.CTkToplevel):
    def __init__(self, parent, callback, current_date=None):
        super().__init__(parent)
        self.title("Seleccionar Fecha")
        self.geometry("340x360")
        self.resizable(False, False)
        
        # Hacerla modal sobre la ventana principal
        self.grab_set() 
        self.focus()
        
        self.callback = callback
        self.selected_date = current_date or datetime.date.today()
        self.year = self.selected_date.year
        self.month = self.selected_date.month
        
        self.meses_nombres = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]
        
        self.crear_interfaz()
        
    def crear_interfaz(self):
        # Header: Mes y Año + Flechas de navegación
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=12, padx=15)
        
        btn_prev = ctk.CTkButton(header, text="◀", width=35, height=35, fg_color="#313244", hover_color="#45475a", font=("Segoe UI", 12), command=self.prev_month)
        btn_prev.pack(side="left")
        
        self.lbl_month_year = ctk.CTkLabel(header, text="", font=ctk.CTkFont(family="Century Gothic", size=14, weight="bold"), text_color="#cdd6f4")
        self.lbl_month_year.pack(side="left", expand=True)
        
        btn_next = ctk.CTkButton(header, text="▶", width=35, height=35, fg_color="#313244", hover_color="#45475a", font=("Segoe UI", 12), command=self.next_month)
        btn_next.pack(side="right")
        
        # Grid para los nombres de días de la semana
        dias_semana_frame = ctk.CTkFrame(self, fg_color="transparent")
        dias_semana_frame.pack(fill="x", padx=15)
        dias_letras = ["Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sa"]
        for d in dias_letras:
            lbl = ctk.CTkLabel(dias_semana_frame, text=d, font=ctk.CTkFont(family="Century Gothic", size=11, weight="bold"), text_color="#cba6f7", width=42)
            lbl.pack(side="left", expand=True)
            
        # Contenedor del grid de días
        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=15, pady=(5, 15))
        
        self.dibujar_dias()
        
    def prev_month(self):
        if self.month == 1:
            self.month = 12
            self.year -= 1
        else:
            self.month -= 1
        self.dibujar_dias()
        
    def next_month(self):
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1
        self.dibujar_dias()
        
    def dibujar_dias(self):
        # Limpiar grid anterior
        for widget in self.days_frame.winfo_children():
            widget.destroy()
            
        self.lbl_month_year.configure(text=f"{self.meses_nombres[self.month-1]} {self.year}")
        
        # Obtener calendario con primer día el Domingo (firstweekday=6)
        cal = calendar.TextCalendar(firstweekday=6)
        month_days = cal.monthdayscalendar(self.year, self.month)
        
        for r_idx, week in enumerate(month_days):
            for c_idx, day in enumerate(week):
                if day == 0:
                    lbl = ctk.CTkLabel(self.days_frame, text="", width=42, height=32)
                    lbl.grid(row=r_idx, column=c_idx, padx=1, pady=1)
                else:
                    es_seleccionado = (self.selected_date.day == day and 
                                       self.selected_date.month == self.month and 
                                       self.selected_date.year == self.year)
                                       
                    bg = "#cba6f7" if es_seleccionado else "#313244"
                    fg = "#11111b" if es_seleccionado else "white"
                    hover = "#f5c2e7" if es_seleccionado else "#45475a"
                    
                    btn = ctk.CTkButton(
                        self.days_frame, 
                        text=str(day), 
                        width=42, 
                        height=32, 
                        fg_color=bg, 
                        text_color=fg, 
                        hover_color=hover,
                        font=ctk.CTkFont(family="Century Gothic", size=11, weight="bold"),
                        command=lambda d=day: self.seleccionar_dia(d)
                    )
                    btn.grid(row=r_idx, column=c_idx, padx=1, pady=1)
                    
    def seleccionar_dia(self, day):
        fecha = datetime.date(self.year, self.month, day)
        self.callback(fecha)
        self.destroy()


class CTkSettings(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Configuración de Rutas")
        self.geometry("750x450")
        self.resizable(False, False)

        self.transient(parent)
        self.grab_set()
        self.focus()
        self.lift()
        
        # Paleta de colores
        self.bg_color = "#1e1e2e"
        self.card_color = "#252538"
        self.btn_save_color = "#a6e3a1"
        self.btn_save_hover = "#94e2d5"
        self.btn_cancel_color = "#f38ba8"
        self.btn_cancel_hover = "#f5e0dc"
        
        self.configure(fg_color=self.bg_color)
        
        self.lbl_title = ctk.CTkLabel(
            self, 
            text="⚙️ CONFIGURACIÓN DE RUTAS LOCALES (ONEDRIVE)", 
            font=ctk.CTkFont(family="Century Gothic", size=15, weight="bold"), 
            text_color="#f5e0dc"
        )
        self.lbl_title.pack(pady=(15, 10))
        
        self.container = ctk.CTkFrame(self, fg_color=self.card_color, corner_radius=10, border_width=1, border_color="#313244")
        self.container.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.crear_campos()
        
    def crear_campos(self):
        from tkinter import filedialog
        
        def create_path_row(parent, label_text, env_key):
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            frame.pack(fill="x", padx=15, pady=8)
            
            lbl = ctk.CTkLabel(frame, text=label_text, font=ctk.CTkFont(family="Century Gothic", size=11, weight="bold"), text_color="#cba6f7", width=220, anchor="w")
            lbl.pack(side="left")
            
            val = os.getenv(env_key, "")
            entry = ctk.CTkEntry(frame, font=ctk.CTkFont(family="Consolas", size=11), text_color="#a6e3a1", fg_color="#11111b", height=30)
            entry.insert(0, val)
            entry.pack(side="left", expand=True, fill="x", padx=10)
            
            def browse():
                try:
                    if env_key == "ONEDRIVE_RESPALDOS_DIR":
                        path = filedialog.askdirectory(parent=self, title=f"Seleccionar {label_text}")
                    elif env_key == "EXCEL_OUTPUT_PATH":
                        path = filedialog.asksaveasfilename(
                            parent=self,
                            title=f"Seleccionar {label_text}",
                            filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
                            defaultextension=".xlsx",
                        )
                    else:
                        path = filedialog.askopenfilename(
                            parent=self,
                            title=f"Seleccionar {label_text}",
                            filetypes=[("Excel Files", ("*.xlsx", "*.xlsm")), ("All Files", "*.*")],
                        )

                    if path:
                        path = os.path.normpath(path).replace("\\", "/")
                        entry.delete(0, "end")
                        entry.insert(0, path)
                except Exception:
                    _log_exception(
                        f"Error al abrir selector de archivo para {env_key}.",
                        *sys.exc_info(),
                    )
                    _show_error_dialog(
                        "Error al seleccionar ruta",
                        (
                            "No se pudo abrir el selector de archivos.\n\n"
                            f"Revisa el archivo:\n{os.path.join(base_dir, 'app_boot.log')}"
                        ),
                    )
            
            btn = ctk.CTkButton(frame, text="Examinar", width=80, height=30, fg_color="#313244", hover_color="#45475a", font=("Segoe UI", 11), command=browse)
            btn.pack(side="right")
            
            return entry
            
        self.ent_respaldos = create_path_row(self.container, "Carpeta Respaldos OneDrive:", "ONEDRIVE_RESPALDOS_DIR")
        self.ent_maestro = create_path_row(self.container, "Excel Tabla Maestra:", "EXCEL_MAESTRO_PATH")
        self.ent_mantenimiento = create_path_row(self.container, "Excel Mantenimientos:", "EXCEL_MANTENIMIENTO_PATH")
        self.ent_output = create_path_row(self.container, "Excel Reporte Salida (Dashboard):", "EXCEL_OUTPUT_PATH")
        
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(10, 15))
        
        btn_cancel = ctk.CTkButton(btn_frame, text="Cancelar", fg_color=self.btn_cancel_color, hover_color=self.btn_cancel_hover, text_color="#11111b", font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), width=120, height=35, command=self.destroy)
        btn_cancel.pack(side="right", padx=(10, 0))
        
        btn_save = ctk.CTkButton(btn_frame, text="Guardar Cambios", fg_color=self.btn_save_color, hover_color=self.btn_save_hover, text_color="#11111b", font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), width=150, height=35, command=self.guardar)
        btn_save.pack(side="right")
        
    def guardar(self):
        respaldos = self.ent_respaldos.get().strip()
        maestro = self.ent_maestro.get().strip()
        mantenimiento = self.ent_mantenimiento.get().strip()
        output = self.ent_output.get().strip()
        
        updates = {
            "ONEDRIVE_RESPALDOS_DIR": respaldos,
            "EXCEL_MAESTRO_PATH": maestro,
            "EXCEL_MANTENIMIENTO_PATH": mantenimiento,
            "EXCEL_OUTPUT_PATH": output
        }
        
        try:
            env_path = os.path.join(base_dir, '.env')
            lines = []
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            
            new_lines = []
            updated_keys = set()
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in line:
                    key, val = stripped.split('=', 1)
                    key = key.strip()
                    if key in updates:
                        new_lines.append(f"{key}={updates[key]}\n")
                        updated_keys.add(key)
                        continue
                new_lines.append(line)
                
            for key, val in updates.items():
                if key not in updated_keys:
                    new_lines.append(f"{key}={val}\n")
                    
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
            for key, val in updates.items():
                os.environ[key] = val
                
            load_dotenv(env_path, override=True)
            
            messagebox.showinfo("Configuración", "¡Configuración guardada exitosamente!")
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar la configuración: {e}")


class RPAAppCTk(ctk.CTk):
    def __init__(self):
        _write_boot_log("RPAAppCTk.__init__() iniciando.")
        super().__init__()
        _write_boot_log("Ventana CTk creada.")

        # Configurar ventana principal
        self.title("RPA Utilitarios - Consola Corporativa")
        self.geometry("950x740")
        self.resizable(True, True)

        # Estado de ejecución
        self.ejecutando = False
        
        # Inicializar fechas internas seleccionadas
        self.selected_start_date = datetime.date.today()
        self.selected_end_date = datetime.date.today()

        _write_boot_log("Llamando crear_interfaz().")
        self.crear_interfaz()
        _write_boot_log("crear_interfaz() terminó.")
        self.report_callback_exception = self._report_callback_exception
        
        # Disparar actualización inicial del texto informativo de fechas
        self.actualizar_info_fechas()
        _write_boot_log("actualizar_info_fechas() terminó.")

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback):
        _log_exception("Excepción en callback de Tk.", exc_type, exc_value, exc_traceback)
        messagebox.showerror(
            "Error en la interfaz",
            (
                "Ocurrió un error dentro de la interfaz.\n\n"
                f"Revisa el archivo:\n{os.path.join(base_dir, 'app_boot.log')}"
            ),
        )

    def crear_interfaz(self):
        _write_boot_log("crear_interfaz(): configurando layout principal.")
        # Configurar grid layout principal (2 filas: Header y Contenido)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 1. Header con fondo oscuro y textos elegantes
        self.header_frame = ctk.CTkFrame(self, height=90, corner_radius=0, fg_color="#181825")
        self.header_frame.grid(row=0, column=0, sticky="nsew")
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self.header_frame, text="RPA UTILITARIOS", font=ctk.CTkFont(family="Century Gothic", size=22, weight="bold"), text_color="#f5e0dc")
        self.lbl_title.grid(row=0, column=0, sticky="w", padx=25, pady=(15, 2))

        self.lbl_subtitle = ctk.CTkLabel(self.header_frame, text="Automatización de Portales Financieros • Flota Petroil", font=ctk.CTkFont(family="Century Gothic", size=12, slant="italic"), text_color="#bac2de")
        self.lbl_subtitle.grid(row=1, column=0, sticky="w", padx=25, pady=(0, 15))

        # 2. Contenedor de Contenido Principal
        self.main_container = ctk.CTkFrame(self, corner_radius=15, fg_color="#1e1e2e")
        self.main_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=20)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(2, weight=1) # El textbox de logs (row 2) se estira

        # Panel 1: Configuración de Flujos (Checkboxes modernos)
        self.flujos_frame = ctk.CTkFrame(self.main_container, corner_radius=10, fg_color="#252538", border_width=1, border_color="#313244")
        self.flujos_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        
        self.lbl_flujos_title = ctk.CTkLabel(self.flujos_frame, text="Configuración de Flujos", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), text_color="#cba6f7")
        self.lbl_flujos_title.pack(anchor="w", padx=15, pady=(10, 5))

        self.checks_container = ctk.CTkFrame(self.flujos_frame, fg_color="transparent")
        self.checks_container.pack(fill="x", padx=15, pady=(0, 10))

        self.var_pase = ctk.BooleanVar(value=True)
        self.chk_pase = ctk.CTkCheckBox(self.checks_container, text="Portal Pase", variable=self.var_pase, font=ctk.CTkFont(family="Century Gothic", size=12))
        self.chk_pase.pack(side="left", padx=(0, 20), pady=5)

        self.var_supramax = ctk.BooleanVar(value=True)
        self.chk_supramax = ctk.CTkCheckBox(self.checks_container, text="Portal Supramax", variable=self.var_supramax, font=ctk.CTkFont(family="Century Gothic", size=12))
        self.chk_supramax.pack(side="left", padx=20, pady=5)

        self.var_edenred = ctk.BooleanVar(value=True)
        self.chk_edenred = ctk.CTkCheckBox(self.checks_container, text="Portal Edenred", variable=self.var_edenred, font=ctk.CTkFont(family="Century Gothic", size=12))
        self.chk_edenred.pack(side="left", padx=20, pady=5)

        self.var_fleetup = ctk.BooleanVar(value=True)
        self.chk_fleetup = ctk.CTkCheckBox(self.checks_container, text="Portal Fleetup", variable=self.var_fleetup, font=ctk.CTkFont(family="Century Gothic", size=12))
        self.chk_fleetup.pack(side="left", padx=20, pady=5)

        # Panel 2: Configuración de Fechas
        self.fechas_frame = ctk.CTkFrame(self.main_container, corner_radius=10, fg_color="#252538", border_width=1, border_color="#313244")
        self.fechas_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 15))
        
        self.lbl_fechas_title = ctk.CTkLabel(self.fechas_frame, text="Rango de Fechas a Procesar", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), text_color="#cba6f7")
        self.lbl_fechas_title.pack(anchor="w", padx=15, pady=(10, 2))

        self.date_selection_container = ctk.CTkFrame(self.fechas_frame, fg_color="transparent")
        self.date_selection_container.pack(fill="x", padx=15, pady=5)

        self.combo_fechas = ctk.CTkComboBox(self.date_selection_container, values=[
            "Mes pasado (Predeterminado)", 
            "Este año", 
            "Año pasado", 
            "Rango personalizado"
        ], width=230, font=ctk.CTkFont(family="Century Gothic", size=12), command=self.on_date_range_change)
        self.combo_fechas.pack(side="left", padx=(0, 15))

        # Sub-contenedor para fechas personalizadas - Muestra botones que abren nuestro CTkCalendar
        self.custom_dates_frame = ctk.CTkFrame(self.date_selection_container, fg_color="transparent")
        
        self.lbl_start = ctk.CTkLabel(self.custom_dates_frame, text="Inicio:", font=ctk.CTkFont(family="Century Gothic", size=11), text_color="#cba6f7")
        self.lbl_start.pack(side="left", padx=(5, 2))
        
        self.btn_start_date = ctk.CTkButton(
            self.custom_dates_frame, 
            text=self.selected_start_date.strftime("%d/%m/%Y"), 
            font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), 
            fg_color="#313244", 
            hover_color="#45475a", 
            width=130, 
            command=self.abrir_calendario_inicio
        )
        self.btn_start_date.pack(side="left", padx=5)

        self.lbl_end = ctk.CTkLabel(self.custom_dates_frame, text="Fin:", font=ctk.CTkFont(family="Century Gothic", size=11), text_color="#cba6f7")
        self.lbl_end.pack(side="left", padx=(10, 2))

        self.btn_end_date = ctk.CTkButton(
            self.custom_dates_frame, 
            text=self.selected_end_date.strftime("%d/%m/%Y"), 
            font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), 
            fg_color="#313244", 
            hover_color="#45475a", 
            width=130, 
            command=self.abrir_calendario_fin
        )
        self.btn_end_date.pack(side="left", padx=5)

        # Etiqueta informativa dinámica para mostrar qué fechas exactas se procesarán
        self.lbl_fechas_info = ctk.CTkLabel(self.fechas_frame, text="📅 Rango calculado: Carga de datos...", font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), text_color="#a6e3a1")
        self.lbl_fechas_info.pack(anchor="w", padx=15, pady=(2, 10))

        # Panel 3: Consola de salida de Logs
        self.console_frame = ctk.CTkFrame(self.main_container, corner_radius=10, fg_color="#11111b", border_width=1, border_color="#313244")
        self.console_frame.grid(row=2, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.console_frame.grid_columnconfigure(0, weight=1)
        self.console_frame.grid_rowconfigure(0, weight=1)

        self.console_text = ctk.CTkTextbox(self.console_frame, font=ctk.CTkFont(family="Consolas", size=12), text_color="#a6e3a1", fg_color="#11111b", border_spacing=10)
        self.console_text.grid(row=0, column=0, sticky="nsew")
        self.console_text.configure(state="disabled")

        # Redirigir stdout y stderr a la interfaz
        sys.stdout = CustomConsoleRedirector(self.console_text)
        sys.stderr = CustomConsoleRedirector(self.console_text)

        # Panel 4: Barra de Control
        self.control_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.control_frame.grid(row=3, column=0, sticky="ew", padx=15, pady=(0, 15))

        self.btn_ejecutar = ctk.CTkButton(self.control_frame, text="🚀 Iniciar Flujos Seleccionados", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), fg_color="#a6e3a1", hover_color="#94e2d5", text_color="#11111b", height=42, corner_radius=8, command=self.start_pipeline_thread)
        self.btn_ejecutar.pack(side="left", padx=(0, 15))

        self.btn_auth_o365 = ctk.CTkButton(self.control_frame, text="🔐 Conectar Office 365", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), fg_color="#cba6f7", hover_color="#f5c2e7", text_color="#11111b", height=42, corner_radius=8, command=self.start_auth_thread)
        self.btn_auth_o365.pack(side="left", padx=15)

        self.btn_consolidar = ctk.CTkButton(self.control_frame, text="📊 Generar Reporte Dashboard", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), fg_color="#f9e2af", hover_color="#f5e0dc", text_color="#11111b", height=42, corner_radius=8, command=self.start_consolidation_thread)
        self.btn_consolidar.pack(side="left", padx=15)

        self.btn_config = ctk.CTkButton(self.control_frame, text="⚙️ Configurar Rutas", font=ctk.CTkFont(family="Century Gothic", size=13, weight="bold"), fg_color="#45475a", hover_color="#585b70", text_color="white", height=42, corner_radius=8, command=self.abrir_configuracion)
        self.btn_config.pack(side="left", padx=15)

        self.lbl_status = ctk.CTkLabel(self.control_frame, text="Listo para iniciar.", font=ctk.CTkFont(family="Century Gothic", size=12, slant="italic"), text_color="#bac2de")
        self.lbl_status.pack(side="right", padx=10)

        # Mensajes de inicio en consola
        print("💡 Bienvenidos a la Consola Corporativa RPA Utilitarios.")
        print(f"Buscando configuración en: {base_dir}")
        if resource_dir != base_dir:
            print(f"Recursos empaquetados cargados desde: {resource_dir}")
        print("========================================================================\n")
        _write_boot_log("crear_interfaz(): widgets principales renderizados.")

    def abrir_configuracion(self):
        try:
            CTkSettings(self)
        except Exception:
            _log_exception("Error al abrir la ventana de configuración.", *sys.exc_info())
            _show_error_dialog(
                "Error de configuración",
                (
                    "No se pudo abrir la ventana de configuración.\n\n"
                    f"Revisa el archivo:\n{os.path.join(base_dir, 'app_boot.log')}"
                ),
            )

    def on_date_range_change(self, choice):
        if choice == "Rango personalizado":
            self.custom_dates_frame.pack(side="left")
        else:
            self.custom_dates_frame.pack_forget()
        self.actualizar_info_fechas()

    def abrir_calendario_inicio(self):
        # Abrir el calendario modal pasándole la fecha actual
        CTkCalendar(self, self.callback_start_date, self.selected_start_date)

    def callback_start_date(self, fecha):
        self.selected_start_date = fecha
        self.btn_start_date.configure(text=fecha.strftime("%d/%m/%Y"))
        self.actualizar_info_fechas()

    def abrir_calendario_fin(self):
        CTkCalendar(self, self.callback_end_date, self.selected_end_date)

    def callback_end_date(self, fecha):
        self.selected_end_date = fecha
        self.btn_end_date.configure(text=fecha.strftime("%d/%m/%Y"))
        self.actualizar_info_fechas()

    def actualizar_info_fechas(self, *args):
        try:
            modo, fini, ffin, _, _ = self.calcular_fechas()
            if modo == "mes_pasado":
                # Calcular mes anterior
                today = datetime.date.today()
                first_day_this_month = today.replace(day=1)
                last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
                first_day_prev_month = last_day_prev_month.replace(day=1)
                fini = first_day_prev_month.strftime("%d/%m/%Y")
                ffin = last_day_prev_month.strftime("%d/%m/%Y")
                
            self.lbl_fechas_info.configure(
                text=f"📅 Periodo real a procesar: del {fini} al {ffin}",
                text_color="#a6e3a1"
            )
        except ValueError as e:
            self.lbl_fechas_info.configure(
                text=f"⚠️ {str(e)}",
                text_color="#f38ba8"
            )

    def toggle_controles(self, habilitar):
        estado = "normal" if habilitar else "disabled"
        self.chk_pase.configure(state=estado)
        self.chk_supramax.configure(state=estado)
        self.chk_edenred.configure(state=estado)
        self.chk_fleetup.configure(state=estado)
        self.combo_fechas.configure(state=estado)
        self.btn_start_date.configure(state=estado)
        self.btn_end_date.configure(state=estado)
        
        if habilitar:
            self.btn_ejecutar.configure(state="normal", fg_color="#a6e3a1", text="🚀 Iniciar Flujos Seleccionados")
            self.btn_auth_o365.configure(state="normal", fg_color="#cba6f7")
            self.btn_consolidar.configure(state="normal", fg_color="#f9e2af")
            self.btn_config.configure(state="normal", fg_color="#45475a")
        else:
            self.btn_ejecutar.configure(state="disabled", fg_color="#585b70", text="⏳ Ejecutando...")
            self.btn_auth_o365.configure(state="disabled", fg_color="#585b70")
            self.btn_consolidar.configure(state="disabled", fg_color="#585b70")
            self.btn_config.configure(state="disabled", fg_color="#585b70")

    def calcular_fechas(self):
        rango = self.combo_fechas.get()
        today = datetime.date.today()
        
        if rango == "Mes pasado (Predeterminado)":
            return "mes_pasado", None, None, None, None
            
        elif rango == "Este año":
            fini = f"01/01/{today.year}"
            ffin = today.strftime("%d/%m/%Y")
            
            meses_objetivo = []
            meses_edenred = []
            for m in range(1, today.month + 1):
                meses_objetivo.append((today.year, m))
                meses_edenred.append(f"{m:02d}/{today.year}")
            return "rango", fini, ffin, meses_objetivo, meses_edenred
            
        elif rango == "Año pasado":
            year = today.year - 1
            fini = f"01/01/{year}"
            ffin = f"31/12/{year}"
            
            meses_objetivo = []
            meses_edenred = []
            for m in range(1, 13):
                meses_objetivo.append((year, m))
                meses_edenred.append(f"{m:02d}/{year}")
            return "rango", fini, ffin, meses_objetivo, meses_edenred
            
        elif rango == "Rango personalizado":
            d_start = self.selected_start_date
            d_end = self.selected_end_date
                
            if d_start > d_end:
                raise ValueError("La fecha inicio no puede ser posterior a la fecha fin.")
                
            fini = d_start.strftime("%d/%m/%Y")
            ffin = d_end.strftime("%d/%m/%Y")
                
            meses_objetivo = []
            meses_edenred = []
            curr = d_start
            while curr <= d_end:
                meses_objetivo.append((curr.year, curr.month))
                meses_edenred.append(curr.strftime("%m/%Y"))
                # Incrementar un mes de forma robusta
                if curr.month == 12:
                    curr = curr.replace(year=curr.year + 1, month=1)
                else:
                    curr = curr.replace(month=curr.month + 1)
                    
            meses_objetivo = sorted(list(set(meses_objetivo)))
            meses_edenred = sorted(list(set(meses_edenred)))
            
            return "rango", fini, ffin, meses_objetivo, meses_edenred

    def start_pipeline_thread(self):
        if not self.var_pase.get() and not self.var_supramax.get() and not self.var_edenred.get() and not self.var_fleetup.get():
            messagebox.showwarning("Selección vacía", "Por favor, selecciona al menos un sistema a ejecutar.")
            return
            
        try:
            self.modo_fecha, self.fini, self.ffin, self.meses_objetivo, self.meses_edenred = self.calcular_fechas()
        except ValueError as e:
            messagebox.showerror("Fechas Inválidas", "La fecha de inicio no puede ser posterior a la fecha final.")
            return
            
        self.ejecutando = True
        self.toggle_controles(False)
        self.lbl_status.configure(text="Procesando...", text_color="#f9e2af")
        
        ingested_dfs.clear()
        
        thread = threading.Thread(target=self.run_pipeline, daemon=True)
        thread.start()

    def run_pipeline(self):
        start_time = time.time()
        print("\n" + "="*60)
        print("🚀 INICIANDO EJECUCIÓN DEL FLUJO RPA SELECCIONADO 🚀")
        if self.modo_fecha == "rango":
            print(f"📅 Rango de proceso: {self.fini} ➔ {self.ffin}")
            print(f"📂 Meses objetivos identificados: {self.meses_edenred}")
        else:
            # Calcular para el log
            today = datetime.date.today()
            first_day_this_month = today.replace(day=1)
            last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
            first_day_prev_month = last_day_prev_month.replace(day=1)
            print(f"📅 Periodo de proceso: del {first_day_prev_month.strftime('%d/%m/%Y')} al {last_day_prev_month.strftime('%d/%m/%Y')} (Mes Pasado)")
        print("="*60)

        # 1. Ejecución del Portal Pase
        if self.var_pase.get():
            print("\n🎫 [PASE] Iniciando descarga e ingesta directa...")
            try:
                if self.modo_fecha == "rango":
                    pase_rpa.main(backfill_mode=True, meses_objetivo=self.meses_objetivo)
                else:
                    pase_rpa.main(backfill_mode=False)
            except Exception as e:
                print(f"❌ Error en flujo Pase: {e}")

        # 2. Ejecución de Supramax
        if self.var_supramax.get():
            print("\n📈 [SUPRAMAX] Ingestando rango de consumos...")
            try:
                if self.modo_fecha == "rango":
                    supramax_rpa.main(fini_override=self.fini, ffin_override=self.ffin)
                else:
                    supramax_rpa.main()
            except Exception as e:
                print(f"❌ Error en flujo Supramax: {e}")

        # 3. Ejecución de Fleetup
        if self.var_fleetup.get():
            print("\n🚛 [FLEETUP] Iniciando flujo (Descarga + Ingesta)...")
            try:
                # FleetUp no maneja rango de fechas personalizado por el momento
                fleetup_rpa.main()
            except Exception as e:
                print(f"❌ Error en flujo FleetUp: {e}")

        # 4. Ejecución de Edenred
        if self.var_edenred.get():
            print("\n💎 [EDENRED] Iniciando flujo (Solicitud + Extracción)...")
            try:
                if self.modo_fecha == "rango":
                    n_edenred = edenred_rpa.main(meses_override=self.meses_edenred)
                else:
                    n_edenred = edenred_rpa.main()
                edenred_extractor.main(n_expected=n_edenred)
            except Exception as e:
                print(f"❌ Error en flujo Edenred: {e}")

        # 4. Generar reporte consolidado local
        self.generar_reporte_consolidado()

        total_minutos = (time.time() - start_time) / 60
        print("\n" + "="*60)
        print(f"✅ PROCESO GLOBAL FINALIZADO EN {total_minutos:.2f} MINUTOS")
        print("="*60 + "\n")

        self.after(0, self.finalizar_ejecucion)

    def generar_reporte_consolidado(self):
        if not ingested_dfs:
            print("\n⚠️ No se procesó información nueva. No se generará reporte consolidado.")
            return

        print("\n📊 Generando Reporte Consolidado Local...")
        try:
            df_consolidado = pd.concat(ingested_dfs, ignore_index=True)
            
            reportes_dir = os.path.join(base_dir, "Reportes_Ejecutable")
            os.makedirs(reportes_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"Reporte_Consolidado_RPA_{timestamp}.xlsx"
            file_path = os.path.join(reportes_dir, file_name)
            
            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                df_consolidado.to_excel(writer, sheet_name='Detalle Consolidado', index=False)
                worksheet = writer.sheets['Detalle Consolidado']
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = max(max_len + 3, 12)
            
            print(f"✅ ¡Reporte consolidado guardado en:\n   -> {file_path}")
            self.after(0, lambda: messagebox.showinfo("Reporte Generado", f"Se generó el reporte consolidado exitosamente en:\n\n{file_path}"))
        except Exception as e:
            print(f"❌ Error al generar el reporte consolidado: {e}")

    def finalizar_ejecucion(self):
        self.ejecutando = False
        self.toggle_controles(True)
        self.lbl_status.configure(text="Ejecución terminada con éxito.", text_color="#a6e3a1")

    def start_auth_thread(self):
        self.ejecutando = True
        self.toggle_controles(False)
        self.lbl_status.configure(text="Autenticando...", text_color="#f5c2e7")
        thread = threading.Thread(target=self.run_auth, daemon=True)
        thread.start()

    def run_auth(self):
        print("\n🔐 Iniciando flujo de autenticación O365...")
        
        client_id = os.getenv('GRAPH_CLIENT_ID')
        tenant_id = os.getenv('GRAPH_TENANT_ID')
        
        if not client_id or not tenant_id:
            print("❌ Error: Faltan variables en el archivo .env.")
            self.after(0, lambda: messagebox.showerror("Faltan Credenciales", "No se encontraron GRAPH_CLIENT_ID o GRAPH_TENANT_ID en el archivo .env."))
            self.after(0, self.finalizar_ejecucion)
            return

        from O365 import Account, FileSystemTokenBackend

        def my_consent_gui(consent_url):
            print("\n" + "="*60)
            print("1. Abre este link en tu navegador de internet:")
            print(consent_url)
            print("========================================================================\n")
            
            url_win = ctk.CTkToplevel(self)
            url_win.title("Consentimiento de Microsoft")
            url_win.geometry("620x350")
            url_win.configure(fg_color="#1e1e2e")
            url_win.grab_set() 
            
            lbl_w_title = ctk.CTkLabel(url_win, text="🔓 Autenticación Microsoft Graph", font=ctk.CTkFont(family="Century Gothic", size=14, weight="bold"), text_color="#f5e0dc")
            lbl_w_title.pack(pady=15)
            
            txt_instrucciones = (
                "1. Copia y abre este enlace en tu navegador para iniciar sesión:\n"
                f"{consent_url}\n\n"
                "2. Tras aceptar permisos, la página quedará en blanco.\n"
                "3. Copia toda la URL del navegador y pégala aquí abajo:"
            )
            
            lbl_inst = ctk.CTkLabel(url_win, text=txt_instrucciones, font=ctk.CTkFont(family="Century Gothic", size=11), justify="left", text_color="#cdd6f4", wraplength=580)
            lbl_inst.pack(pady=5, padx=20)
            
            entry_url = ctk.CTkEntry(url_win, width=540, font=ctk.CTkFont(family="Consolas", size=10), fg_color="#11111b", border_color="#313244", text_color="#a6e3a1")
            entry_url.pack(pady=15)
            entry_url.focus_set()
            
            res = {"url": ""}
            
            def continuar():
                res["url"] = entry_url.get().strip()
                url_win.destroy()
                
            def cancelar():
                url_win.destroy()
                
            btn_frame = ctk.CTkFrame(url_win, fg_color="transparent")
            btn_frame.pack(pady=10)
            
            btn_ok = ctk.CTkButton(btn_frame, text="Aceptar", font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), fg_color="#a6e3a1", text_color="#11111b", hover_color="#94e2d5", width=120, command=continuar)
            btn_ok.pack(side="left", padx=15)
            
            btn_cancel = ctk.CTkButton(btn_frame, text="Cancelar", font=ctk.CTkFont(family="Century Gothic", size=12, weight="bold"), fg_color="#f38ba8", text_color="#11111b", hover_color="#e78284", width=120, command=cancelar)
            btn_cancel.pack(side="left", padx=15)
            
            self.wait_window(url_win)
            return res["url"]

        try:
            credentials = (client_id, "")
            token_backend = FileSystemTokenBackend(token_path=base_dir, token_filename='o365_token.txt')
            account = Account(credentials, auth_flow='authorization', tenant_id=tenant_id, token_backend=token_backend)
            
            if account.authenticate(scopes=['basic', 'message_all'], handle_consent=my_consent_gui):
                token_path = os.path.join(base_dir, 'o365_token.txt')
                print(f"\n✅ ¡Autenticación exitosa! Token guardado en: {token_path}")
                self.after(0, lambda: messagebox.showinfo("Éxito", "¡Token generado y autenticación exitosa!"))
            else:
                print("\n❌ La autenticación falló.")
                self.after(0, lambda: messagebox.showerror("Fallo", "La autenticación falló. Revisa las credenciales e intenta de nuevo."))
        except Exception as e:
            print(f"❌ Error en autenticación: {e}")
            self.after(0, lambda: messagebox.showerror("Error", f"Ocurrió un error: {e}"))
            
        self.after(0, self.finalizar_ejecucion)

    def start_consolidation_thread(self):
        self.ejecutando = True
        self.toggle_controles(False)
        self.lbl_status.configure(text="Consolidando...", text_color="#f9e2af")
        thread = threading.Thread(target=self.run_consolidation, daemon=True)
        thread.start()

    def run_consolidation(self):
        print("\n" + "="*60)
        print("📊 INICIANDO PROCESO DE CONSOLIDACIÓN DESDE INTERFAZ 🚀")
        print("="*60)
        
        try:
            # 1. Unificar los respaldos crudos locales en OneDrive
            from scripts_onedrive import unificar_respaldos_local
            unificar_respaldos_local.unificar_respaldos_desde_onedrive()
            
            # 2. Realizar el cruce de datos y generar reporte final
            from scripts import consolidar_utilitarios
            consolidar_utilitarios.consolidar_todo()
            print("\n✅ ¡Consolidación finalizada con éxito!")
            self.after(0, lambda: messagebox.showinfo("Proceso Terminado", "Reporte Dashboard Final consolidado con éxito en la carpeta de OneDrive configurada."))
        except Exception as e:
            print(f"❌ Error durante la consolidación: {e}")
            self.after(0, lambda: messagebox.showerror("Error", f"Ocurrió un error al consolidar: {e}"))
            
        self.after(0, self.finalizar_ejecucion)


if __name__ == "__main__":
    try:
        import multiprocessing

        multiprocessing.freeze_support()
        app = RPAAppCTk()
        app.mainloop()
    except Exception:
        _log_exception("Excepción fatal en el arranque principal.", *sys.exc_info())
        raise
