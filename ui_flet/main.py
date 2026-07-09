"""UI Flet para RPA Utilitarios – reescritura limpia."""
import calendar
import datetime as dt
import os
import ssl
import sys
import tempfile
import threading
import ctypes

import certifi

CA_BUNDLE_PATH = certifi.where()
os.environ.setdefault("SSL_CERT_FILE", CA_BUNDLE_PATH)
os.environ.setdefault("REQUESTS_CA_BUNDLE", CA_BUNDLE_PATH)
os.environ.setdefault("CURL_CA_BUNDLE", CA_BUNDLE_PATH)


def _certifi_https_context(*args, **kwargs):
    kwargs.setdefault("cafile", CA_BUNDLE_PATH)
    return ssl.create_default_context(*args, **kwargs)


ssl._create_default_https_context = _certifi_https_context

try:
    import flet as ft
    import dotenv
    import os
except ImportError as exc:
    raise SystemExit(
        "Falta instalar Flet. Ejecuta `pip install flet` en tu entorno."
    ) from exc

from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from shared.auth_service import run_o365_auth
from shared.consolidation_service import run_full_consolidation
from shared.config_service import CONFIG_FIELDS, load_config_values, save_config_values
from shared.date_ranges import (
    CUSTOM_RANGE_LABEL,
    DEFAULT_RANGE_LABEL,
    RANGE_OPTIONS,
    build_date_selection,
    build_period_label,
)
from shared.ingest_capture import clear_captured_data, install_ingest_capture
from shared.pipeline_runner import PipelineOptions, run_selected_flows

install_ingest_capture(forward_to_original=False)

def _async_raise(tid, exctype):
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.py_object(exctype))
    if res == 0: raise ValueError("invalid thread id")
    elif res != 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
        raise SystemError("PyThreadState_SetAsyncExc failed")

class KillableThread(threading.Thread):
    def get_id(self):
        if hasattr(self, '_thread_id'):
            return self._thread_id
        for id, thread in threading._active.items():
            if thread is self:
                return id
    def raise_exc(self, exctype):
        _async_raise(self.get_id(), exctype)
    def terminate(self):
        self.raise_exc(SystemExit)

# ── Paleta ──────────────────────────────────────────────────────────────────
PALETTE = {
    "bg_dark": "#011E2D",
    "card_dark": "#023047",
    "surface": "#011E2D",
    "primary": "#FFB703",
    "primary_hover": "#FB8500",
    "secondary": "#8ECAE6",
    "secondary_strong": "#219EBC",
    "text_dark": "#011E2D",
    "text_light": "#FFFFFF",
    "muted_light": "#CFE7F3",
}

MONTH_NAMES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


# ── Utilidades de fecha ──────────────────────────────────────────────────────
def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value.strip(), "%d/%m/%Y").date()


def _format_date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def _month_caption(value: dt.date) -> str:
    return f"{MONTH_NAMES_ES[value.month - 1]} {value.year}"


def _previous_month(value: dt.date) -> dt.date:
    if value.month == 1:
        return value.replace(year=value.year - 1, month=12, day=1)
    return value.replace(month=value.month - 1, day=1)


def _next_month(value: dt.date) -> dt.date:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def _guess_initial_directory(current_value: str, expect_directory: bool) -> str:
    value = (current_value or "").strip()
    if not value:
        return os.path.expanduser("~")
    if expect_directory:
        return value if os.path.isdir(value) else (os.path.dirname(value) or os.path.expanduser("~"))
    if os.path.isdir(value):
        return value
    return os.path.dirname(value) or os.path.expanduser("~")


def _border_all(width, color):
    s = ft.BorderSide(width=width, color=color)
    return ft.Border(left=s, top=s, right=s, bottom=s)


def _radius_top(left, right):
    return ft.BorderRadius(top_left=left, top_right=right, bottom_left=0, bottom_right=0)


# ── Función principal ────────────────────────────────────────────────────────
def main(page: ft.Page) -> None:
    clipboard_service = ft.Clipboard()

    page.title = "Utilitarios - Automatizaciones"
    page.window_width = 1280
    page.window_height = 840
    page.window_min_width = 1080
    page.window_min_height = 700
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    # page.bgcolor = ft.Colors.SURFACE_CONTAINER_LOWEST
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.scroll = None
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary="#0090FF",
            primary_container="#005B9A",
            secondary="#0090FF",
            surface="#FFFFFF",
            surface_container_lowest="#F1F5F9",
            surface_container_highest="#F1F5F9",
            on_surface="#0B2131",
            on_surface_variant="#64748B",
            on_primary="#FFFFFF",
            outline="#E2E8F0",
            outline_variant="#E2E8F0",
            tertiary="#FB8500",
            shadow="#000000"
        ),
        font_family="Aptos",
    )
    page.dark_theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary="#0090FF",
            primary_container="#33A6FF",
            secondary="#0090FF",
            surface="#0B2131",
            surface_container_highest="#00121B",
            on_surface="#F0F5F9",
            on_surface_variant="#8ECAE6",
            on_primary="#06141F",
            outline="#1A496B",
            outline_variant="#0A3148",
            tertiary="#FB8500",
            shadow="#000000"
        ),
        font_family="Aptos",
    )

    # ── Estado de la app ─────────────────────────────────────────────────────

    def _toggle_theme(e):
        page.theme_mode = ft.ThemeMode.LIGHT if page.theme_mode == ft.ThemeMode.DARK else ft.ThemeMode.DARK
        e.control.icon = ft.Icons.LIGHT_MODE if page.theme_mode == ft.ThemeMode.DARK else ft.Icons.DARK_MODE
        
        # update colors for things that might need explicit refresh
        page.update()

    state = {"busy": False, "stopping": False, "worker_thread": None}
    config_values = load_config_values()

    # ── FilePicker ────────────────────────────────────────────────────────────
    # En Flet 0.85+ FilePicker es un Service: se auto-registra al instanciarse.
    # NO se debe añadir a page.overlay ni a page.controls.
    file_picker = ft.FilePicker()

    # ── Controles de fechas ──────────────────────────────────────────────────
    range_dropdown = ft.Dropdown(
        label="Rango de fechas",
        value=DEFAULT_RANGE_LABEL,
        expand=True,
        options=[ft.dropdown.Option(o) for o in RANGE_OPTIONS],
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        color=ft.Colors.ON_SURFACE,
        border_color=ft.Colors.OUTLINE,
        focused_border_color=ft.Colors.PRIMARY,
        label_style=ft.TextStyle(color=ft.Colors.ON_SURFACE_VARIANT),
    )

    start_input = ft.TextField(
        label="Inicio",
        value=_format_date(dt.date.today()),
        width=160,
        hint_text="dd/mm/aaaa",
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        color=ft.Colors.ON_SURFACE,
        border_color=ft.Colors.OUTLINE,
        focused_border_color=ft.Colors.PRIMARY,
        label_style=ft.TextStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        hint_style=ft.TextStyle(color="ft.Colors.ON_SURFACE_VARIANT"),
    )

    end_input = ft.TextField(
        label="Fin",
        value=_format_date(dt.date.today()),
        width=160,
        hint_text="dd/mm/aaaa",
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        color=ft.Colors.ON_SURFACE,
        border_color=ft.Colors.OUTLINE,
        focused_border_color=ft.Colors.PRIMARY,
        label_style=ft.TextStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        hint_style=ft.TextStyle(color="ft.Colors.ON_SURFACE_VARIANT"),
    )

    # Botones de calendario junto a los inputs de fecha
    start_cal_btn = ft.IconButton(
        icon=ft.Icons.CALENDAR_MONTH,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip="Seleccionar fecha de inicio",
    )
    end_cal_btn = ft.IconButton(
        icon=ft.Icons.CALENDAR_MONTH,
        icon_color=ft.Colors.ON_SURFACE_VARIANT,
        tooltip="Seleccionar fecha de fin",
    )

    custom_row = ft.Row(
        controls=[
            start_input, start_cal_btn,
            ft.Text(" – ", color=ft.Colors.ON_SURFACE_VARIANT),
            end_input, end_cal_btn,
        ],
        visible=False,
        spacing=6,
        wrap=True,
    )

    info_text = ft.Text(size=14, color=ft.Colors.ON_SURFACE_VARIANT, selectable=True)
    months_text = ft.Text(size=13, color=ft.Colors.ON_SURFACE)
    error_text = ft.Text(size=13, color=ft.Colors.PRIMARY_CONTAINER)
    status_text = ft.Text("Listo para iniciar.", size=13, color=ft.Colors.ON_SURFACE_VARIANT)

    hero_status_pill_text = ft.Text("En espera", size=12, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE)
    panel_status_pill_text = ft.Text("En espera", size=11, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE_VARIANT)

    panel_status_pill = ft.Container(
        padding=ft.Padding(left=10, right=14, top=4, bottom=4),
        border_radius=9999,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        content=ft.Row(
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.ON_SURFACE_VARIANT),
                panel_status_pill_text
            ]
        )
    )

    chk_pase = ft.Checkbox(value=True, active_color=ft.Colors.TERTIARY, check_color=ft.Colors.SURFACE_CONTAINER_LOWEST)
    chk_supramax = ft.Checkbox(value=True, active_color=ft.Colors.TERTIARY, check_color=ft.Colors.SURFACE_CONTAINER_LOWEST)
    chk_edenred = ft.Checkbox(value=True, active_color=ft.Colors.TERTIARY, check_color=ft.Colors.SURFACE_CONTAINER_LOWEST)
    chk_fleetup = ft.Checkbox(value=True, active_color=ft.Colors.TERTIARY, check_color=ft.Colors.SURFACE_CONTAINER_LOWEST)
    
    chk_headless = ft.Switch(label="Modo silencioso (Background)", value=False, active_color=ft.Colors.PRIMARY)

    log_listview = ft.ListView(expand=True, spacing=4, auto_scroll=True, visible=False)
    console_empty_state = ft.Container(
        alignment=ft.Alignment(x=0, y=0),
        bgcolor=ft.Colors.SURFACE,
        border_radius=12,
        padding=24,
        shadow=ft.BoxShadow(spread_radius=0, blur_radius=4, color="#0D000000", offset=ft.Offset(0, 1)),
        expand=True,
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.TERMINAL, size=48, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Consola en espera", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                ft.Text("Selecciona los flujos en el panel izquierdo e inicia la automatización\npara ver el registro en tiempo real.", text_align=ft.TextAlign.CENTER, color=ft.Colors.ON_SURFACE_VARIANT)
            ]
        )
    )
    
    log_output_container = ft.Container(
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        border=_border_all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=12,
        expand=True,
        padding=12,
        content=ft.Stack(
            expand=True,
            controls=[console_empty_state, log_listview]
        )
    )

    run_button = ft.ElevatedButton(
        "Iniciar Flujos",
        icon=ft.Icons.PLAY_ARROW,
        bgcolor=ft.Colors.TERTIARY,
        color=ft.Colors.SURFACE_CONTAINER_LOWEST,
        height=46,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
    )
    def _release_worker(expected_thread=None) -> bool:
        was_stopping = state.get("stopping", False)
        current = state.get("worker_thread")
        if expected_thread is None or current is expected_thread:
            state["worker_thread"] = None
        state["stopping"] = False
        return was_stopping

    def confirm_stop(e):
        def close_dlg(e):
            page.pop_dialog()
            safe_update()
            
        def do_stop(e):
            page.pop_dialog()
            worker = state.get("worker_thread")
            if not worker:
                set_busy(False, "Detenido.", ft.Colors.ERROR)
                return

            state["stopping"] = True
            append_log("\n🛑 Solicitud de detención enviada por el usuario.")
            set_busy(True, "Deteniendo proceso...", ft.Colors.ERROR)
            try:
                worker.terminate()
            except Exception as exc:
                append_log(f"⚠️ No se pudo interrumpir el hilo actual: {exc}")
                _release_worker(worker)
                set_busy(False, "Error al detener.", ft.Colors.ERROR)
            safe_update()

        stop_dialog = ft.AlertDialog(
            title=ft.Text("⚠️ Detener Proceso"),
            content=ft.Text("¿Estás seguro de que deseas detener el proceso actual? Esto cancelará la ejecución de los web scrapers de inmediato."),
            actions=[
                ft.TextButton("Cancelar", on_click=close_dlg),
                ft.ElevatedButton("Sí, Detener", color=ft.Colors.ON_ERROR, bgcolor=ft.Colors.ERROR, on_click=do_stop),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(stop_dialog)
        safe_update()

    stop_button = ft.ElevatedButton(
        "Detener",
        icon=ft.Icons.STOP,
        bgcolor=ft.Colors.ERROR,
        color=ft.Colors.ON_ERROR,
        on_click=confirm_stop,
        visible=False,
        height=46,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
    )
    auth_button = ft.OutlinedButton(
        "Conectar Office 365",
        icon=ft.Icons.ADMIN_PANEL_SETTINGS,
        height=46,
        style=ft.ButtonStyle(
            color=ft.Colors.ON_SURFACE,
            bgcolor="#0D000000",
            side=ft.BorderSide(width=1.5, color=ft.Colors.OUTLINE),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    def open_settings(e):
        import dotenv
        import os
        env_path = ".env"
        # Cargar valores frescos
        dotenv.load_dotenv(env_path, override=True)
        
        f_edenred_user = ft.TextField(label="Edenred Usuario", value=os.getenv("EDENRED_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        f_edenred_pass = ft.TextField(label="Edenred Contraseña", value=os.getenv("EDENRED_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        
        f_pase_user = ft.TextField(label="Pase Usuario", value=os.getenv("PASE_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        f_pase_pass = ft.TextField(label="Pase Contraseña", value=os.getenv("PASE_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        
        f_fleetup_user = ft.TextField(label="FleetUp Usuario", value=os.getenv("FLEETUP_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        f_fleetup_pass = ft.TextField(label="FleetUp Contraseña", value=os.getenv("FLEETUP_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
        
        f_twocaptcha = ft.TextField(label="2Captcha API Key", value=os.getenv("TWOCAPTCHA_API_KEY", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)

        def save_settings(e):
            dotenv.set_key(env_path, "EDENRED_USER", f_edenred_user.value)
            dotenv.set_key(env_path, "EDENRED_PASSWORD", f_edenred_pass.value)
            dotenv.set_key(env_path, "PASE_USER", f_pase_user.value)
            dotenv.set_key(env_path, "PASE_PASSWORD", f_pase_pass.value)
            dotenv.set_key(env_path, "FLEETUP_USER", f_fleetup_user.value)
            dotenv.set_key(env_path, "FLEETUP_PASSWORD", f_fleetup_pass.value)
            dotenv.set_key(env_path, "TWOCAPTCHA_API_KEY", f_twocaptcha.value)
            dotenv.load_dotenv(env_path, override=True)
            page.dialog.open = False
            show_snack("✅ Credenciales guardadas correctamente en .env", bgcolor="#00E676")
            safe_update()

        def close_settings(e):
            page.dialog.open = False
            safe_update()

        settings_dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.PRIMARY), ft.Text("Configuración de Credenciales")]),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                    controls=[
                        ft.Text("Estas credenciales se guardarán localmente en tu computadora (.env).", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Divider(),
                        ft.Text("Edenred (Ticket Car)", weight=ft.FontWeight.W_600),
                        ft.Row([f_edenred_user, f_edenred_pass]),
                        ft.Divider(),
                        ft.Text("Portal Pase (Peajes)", weight=ft.FontWeight.W_600),
                        ft.Row([f_pase_user, f_pase_pass]),
                        ft.Divider(),
                        ft.Text("FleetUp (GPS)", weight=ft.FontWeight.W_600),
                        ft.Row([f_fleetup_user, f_fleetup_pass]),
                        ft.Divider(),
                        ft.Text("APIs Externas", weight=ft.FontWeight.W_600),
                        ft.Row([f_twocaptcha]),
                    ]
                )
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=close_settings),
                ft.ElevatedButton("Guardar Cambios", on_click=save_settings, bgcolor=ft.Colors.PRIMARY, color=ft.Colors.ON_PRIMARY)
            ]
        )
        page.dialog = settings_dialog
        settings_dialog.open = True
        safe_update()

    settings_button = ft.Container(
        border_radius=10,
        bgcolor=ft.Colors.TRANSPARENT,
        padding=ft.Padding(left=12, right=12, top=10, bottom=10),
        on_click=open_settings,
        ink=True,
        content=ft.Row(
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.ON_SURFACE_VARIANT, size=18),
                ft.Text("Configuración", color=ft.Colors.ON_SURFACE_VARIANT, size=14),
            ],
        ),
    )
    consolidate_button = ft.OutlinedButton(
        "Agregar Nuevos Reportes",
        icon=ft.Icons.INSERT_CHART_OUTLINED,
        height=46,
        style=ft.ButtonStyle(
            color=ft.Colors.PRIMARY,
            bgcolor="#1A0090FF",
            side=ft.BorderSide(width=1.5, color=ft.Colors.PRIMARY),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    full_rebuild_button = ft.OutlinedButton(
        "Reconstruir Base Completa",
        icon=ft.Icons.REFRESH,
        height=46,
        style=ft.ButtonStyle(
            color=ft.Colors.ON_SURFACE,
            bgcolor="#0D000000",
            side=ft.BorderSide(width=1.5, color=ft.Colors.OUTLINE),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    movements_button = ft.OutlinedButton(
        "Actualizar Movimientos",
        icon=ft.Icons.DIRECTIONS_CAR_OUTLINED,
        height=46,
        style=ft.ButtonStyle(
            color=ft.Colors.ON_SURFACE,
            bgcolor="#0D000000",
            side=ft.BorderSide(width=1.5, color=ft.Colors.OUTLINE),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    def _collect_log_text() -> str:
        return "\n".join(
            ctrl.value for ctrl in log_listview.controls if hasattr(ctrl, "value") and ctrl.value
        )

    def _export_logs_snapshot(text: str) -> str:
        export_dir = os.path.join(os.getcwd(), "logs_ui")
        try:
            os.makedirs(export_dir, exist_ok=True)
        except OSError:
            export_dir = os.path.join(tempfile.gettempdir(), "RPA_Utilitarios_logs_ui")
            os.makedirs(export_dir, exist_ok=True)

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = os.path.join(export_dir, f"logs_ui_{timestamp}.txt")
        with open(export_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return export_path

    async def _copy_logs_async(text: str) -> None:
        try:
            await clipboard_service.set(text)
            show_snack("Logs copiados al portapapeles", bgcolor=ft.Colors.PRIMARY)
        except Exception:
            export_path = _export_logs_snapshot(text)
            show_snack(
                f"No se pudo usar el portapapeles. Logs guardados en: {export_path}",
                bgcolor=ft.Colors.AMBER,
            )

    def copy_logs(e):
        text = _collect_log_text()
        if not text.strip():
            show_snack("No hay logs para copiar", bgcolor=ft.Colors.AMBER)
            return
        page.run_task(_copy_logs_async, text)

    copy_logs_button = ft.TextButton(
        "Copiar logs",
        icon=ft.Icons.COPY,
        style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
        on_click=copy_logs
    )
    
    clear_logs_button = ft.TextButton(
        "Limpiar logs",
        icon=ft.Icons.CLEANING_SERVICES,
        style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT),
    )

    # ── Funciones auxiliares ─────────────────────────────────────────────────
    def safe_update() -> None:
        try:
            page.update()
        except Exception as exc:
            print(f"[FLET UPDATE ERROR] {exc}")

    def clear_logs(e=None) -> None:
        log_listview.controls.clear()
        log_listview.visible = False
        console_empty_state.visible = True
        safe_update()

    def append_log(message: str) -> None:
        console_empty_state.visible = False
        log_listview.visible = True
        log_listview.controls.append(
            ft.Text(message.strip("\n"), size=12, color=ft.Colors.ON_SURFACE, font_family="monospace", selectable=True)
        )
        # Keep only the last 1000 lines to avoid UI lag
        if len(log_listview.controls) > 1000:
            log_listview.controls = log_listview.controls[-1000:]
        safe_update()

    def show_snack(message: str, bgcolor: str = "#334155") -> None:
        page.snack_bar = ft.SnackBar(ft.Text(message), bgcolor=bgcolor)
        page.snack_bar.open = True
        safe_update()

    def set_busy(is_busy: bool, status: str, color: str) -> None:
        state["busy"] = is_busy
        status_text.value = status
        status_text.color = color if color != ft.Colors.OUTLINE else "#8ECAE6"
        pill_text = "Deteniendo..." if state.get("stopping") else ("Ejecutando..." if is_busy else "En espera")
        pill_bg = ft.Colors.ERROR if state.get("stopping") else ("#0A2540" if is_busy else ft.Colors.SURFACE_CONTAINER_HIGHEST)
        pill_fg = ft.Colors.ON_ERROR if state.get("stopping") else ("#8ECAE6" if is_busy else ft.Colors.ON_SURFACE_VARIANT)
        for pill, text in (
            (panel_status_pill, panel_status_pill_text),
        ):
            text.value = pill_text
            pill.bgcolor = pill_bg
            text.color = pill_fg

        run_button.visible = not is_busy
        stop_button.visible = is_busy
        for ctrl in [
            range_dropdown, start_input, end_input, start_cal_btn, end_cal_btn,
            chk_pase, chk_supramax, chk_edenred, chk_fleetup,
            auth_button, settings_button, consolidate_button, full_rebuild_button,
            movements_button, clear_logs_button,
            chk_headless,
        ]:
            ctrl.disabled = is_busy
        safe_update()

    def current_selection():
        start_date = None
        end_date = None
        if custom_row.visible:
            try:
                start_date = _parse_date(start_input.value)
            except Exception:
                pass
            try:
                end_date = _parse_date(end_input.value)
            except Exception:
                pass
        return build_date_selection(range_dropdown.value, start_date, end_date)

    # ── Calendar Dialog ──────────────────────────────────────────────────────
    def open_calendar_dialog(target: str) -> None:
        try:
            selected_date = _parse_date(
                start_input.value if target == "start" else end_input.value
            )
        except Exception:
            selected_date = dt.date.today()

        # Estado mutable del diálogo
        visible_date_box = [selected_date.replace(day=1)]
        selected_day_box = [selected_date]
        view_mode_box = ["days"]  # "days" o "months"

        header_btn = ft.TextButton(
            content=ft.Text(
                _month_caption(visible_date_box[0]),
                size=18,
                weight=ft.FontWeight.BOLD,
                color=ft.Colors.PRIMARY,
            ),
            on_click=lambda _: _toggle_view_mode(),
        )
        grid_col = ft.Column(spacing=8)

        def _close(_=None):
            page.pop_dialog()

        def _toggle_view_mode():
            if view_mode_box[0] == "days":
                view_mode_box[0] = "months"
            else:
                view_mode_box[0] = "days"
            _render()

        def _pick_month(y: int, m: int):
            visible_date_box[0] = dt.date(y, m, 1)
            view_mode_box[0] = "days"
            _render()

        def _pick_day(day_value: dt.date):
            if target == "start":
                start_input.value = _format_date(day_value)
            else:
                end_input.value = _format_date(day_value)
            _close()
            refresh_selection()

        def _render():
            vm = visible_date_box[0]
            
            if view_mode_box[0] == "days":
                header_btn.content.value = _month_caption(vm)
                # Días
                header = ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Container(
                            width=46,
                            alignment=ft.Alignment(x=0, y=0),
                            content=ft.Text(d, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.BOLD, size=12),
                        )
                        for d in ("Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sa")
                    ],
                )
                weeks = []
                for week in calendar.Calendar(firstweekday=6).monthdatescalendar(vm.year, vm.month):
                    row_controls = []
                    for day_val in week:
                        if day_val.month != vm.month:
                            row_controls.append(ft.Container(width=46, height=40))
                            continue
                        is_sel = day_val == selected_day_box[0]
                        dv = day_val  # capture for closure
                        row_controls.append(
                            ft.Container(
                                width=46,
                                height=40,
                                border_radius=8,
                                bgcolor=ft.Colors.PRIMARY if is_sel else "ft.Colors.OUTLINE_VARIANT",
                                alignment=ft.Alignment(x=0, y=0),
                                content=ft.Text(
                                    str(day_val.day),
                                    color=ft.Colors.ON_PRIMARY if is_sel else ft.Colors.ON_SURFACE,
                                    weight=ft.FontWeight.BOLD if is_sel else ft.FontWeight.NORMAL,
                                    size=14,
                                ),
                                on_click=lambda _, d=dv: _pick_day(d),
                                ink=True,
                            )
                        )
                    weeks.append(ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=row_controls))
                grid_col.controls = [header, *weeks]
            else:
                # Meses
                header_btn.content.value = str(vm.year)
                months_grid = ft.Row(
                    wrap=True, 
                    spacing=10, 
                    run_spacing=10, 
                    alignment=ft.MainAxisAlignment.CENTER,
                    width=340
                )
                for i, month_name in enumerate(MONTH_NAMES_ES):
                    month_num = i + 1
                    is_sel = (month_num == selected_day_box[0].month and vm.year == selected_day_box[0].year)
                    
                    months_grid.controls.append(
                        ft.Container(
                            width=100,
                            height=40,
                            border_radius=8,
                            bgcolor=ft.Colors.PRIMARY if is_sel else "ft.Colors.OUTLINE_VARIANT",
                            alignment=ft.Alignment(x=0, y=0),
                            content=ft.Text(
                                month_name,
                                color=ft.Colors.ON_PRIMARY if is_sel else ft.Colors.ON_SURFACE,
                                weight=ft.FontWeight.BOLD if is_sel else ft.FontWeight.NORMAL,
                                size=14,
                            ),
                            on_click=lambda _, m=month_num: _pick_month(vm.year, m),
                            ink=True,
                        )
                    )
                grid_col.controls = [months_grid]
            
            safe_update()

        def _go_prev(_):
            if view_mode_box[0] == "days":
                visible_date_box[0] = _previous_month(visible_date_box[0])
            else:
                visible_date_box[0] = visible_date_box[0].replace(year=visible_date_box[0].year - 1)
            _render()

        def _go_next(_):
            if view_mode_box[0] == "days":
                visible_date_box[0] = _next_month(visible_date_box[0])
            else:
                visible_date_box[0] = visible_date_box[0].replace(year=visible_date_box[0].year + 1)
            _render()

        _render()

        label_str = "Fecha de inicio" if target == "start" else "Fecha de fin"
        cal_dialog = ft.AlertDialog(
            modal=True,
            content=ft.Container(
                width=430,
                bgcolor=ft.Colors.SURFACE,
                border=_border_all(2, ft.Colors.OUTLINE),
                border_radius=20,
                padding=24,
                content=ft.Column(
                    tight=True,
                    spacing=14,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(
                                    f"Seleccionar {label_str}",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=ft.Colors.ON_SURFACE,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                                    on_click=_close,
                                ),
                            ],
                        ),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.IconButton(
                                    icon=ft.Icons.CHEVRON_LEFT,
                                    icon_color=ft.Colors.ON_SURFACE,
                                    on_click=_go_prev,
                                ),
                                header_btn,
                                ft.IconButton(
                                    icon=ft.Icons.CHEVRON_RIGHT,
                                    icon_color=ft.Colors.ON_SURFACE,
                                    on_click=_go_next,
                                ),
                            ],
                        ),
                        grid_col,
                    ],
                ),
            ),
        )

        page.show_dialog(cal_dialog)

    # ── Refresh de selección de rango ────────────────────────────────────────
    def refresh_selection(_=None) -> None:
        is_custom = range_dropdown.value == CUSTOM_RANGE_LABEL
        custom_row.visible = is_custom
        error_text.value = ""
        try:
            selection = current_selection()
            if not is_custom:
                start_input.value = selection.start_text
                end_input.value = selection.end_text
            info_text.value = build_period_label(selection)
            if selection.target_months:
                nombres_meses = [f"{MONTH_NAMES_ES[m - 1]} {y}" for y, m in selection.target_months]
                months_text.value = f"Meses: {', '.join(nombres_meses)}"
            else:
                months_text.value = "Meses: cálculo automático para el mes pasado."
        except Exception as exc:
            info_text.value = "Periodo: pendiente de definir"
            months_text.value = ""
            error_text.value = str(exc)
        safe_update()

    def handle_range_change(e) -> None:
        # on_select en Flet 0.85.3: el valor seleccionado viene en e.data
        if hasattr(e, 'data') and e.data:
            range_dropdown.value = e.data
        elif hasattr(e, 'control') and hasattr(e.control, 'value'):
            range_dropdown.value = e.control.value
        print(f"[RANGE] Cambiado a: {range_dropdown.value}")
        refresh_selection()

    # ── Configurar rutas ─────────────────────────────────────────────────────
    def open_settings_dialog(_=None) -> None:
        fields = {}
        for key, label in CONFIG_FIELDS:
            fields[key] = ft.TextField(
                value=config_values.get(key, ""),
                expand=4,
                bgcolor="#011E2D",
                color=ft.Colors.ON_SURFACE,
                border_color="#33219EBC",
                focused_border_color=ft.Colors.PRIMARY,
                label_style=ft.TextStyle(color=ft.Colors.ON_SURFACE_VARIANT),
                hint_text=label,
                hint_style=ft.TextStyle(color="ft.Colors.ON_SURFACE_VARIANT"),
                height=36,
                text_size=12,
                text_style=ft.TextStyle(font_family="monospace"),
                content_padding=ft.Padding(left=12, right=12, top=0, bottom=0),
                text_align=ft.TextAlign.RIGHT,
            )

        def _save(_):
            updates = {k: (v.value or "").strip() for k, v in fields.items()}
            try:
                save_config_values(PROJECT_ROOT, updates)
                config_values.update(updates)
                page.pop_dialog()
                show_snack("✅ Configuración guardada.", ft.Colors.OUTLINE)
                append_log("Configuración de rutas actualizada desde UI Flet.")
            except Exception as exc:
                show_snack(f"❌ No se pudo guardar: {exc}", ft.Colors.PRIMARY_CONTAINER)

        def _cancel(_):
            page.pop_dialog()

        async def _browse(field_key: str):
            expect_dir = (field_key == "ONEDRIVE_RESPALDOS_DIR")
            initial = _guess_initial_directory(fields[field_key].value or "", expect_dir)

            try:
                if expect_dir:
                    path = await file_picker.get_directory_path(dialog_title="Selecciona la carpeta", initial_directory=initial)
                    if path:
                        fields[field_key].value = path
                        safe_update()
                else:
                    files = await file_picker.pick_files(dialog_title="Selecciona el archivo", allow_multiple=False, initial_directory=initial)
                    if files and len(files) > 0:
                        fields[field_key].value = files[0].path
                        safe_update()
            except Exception as ex:
                print(f"FilePicker error: {ex}")

        def make_browse_handler(k):
            async def _handler(e):
                await _browse(k)
            return _handler

        rows = []
        for key, label in CONFIG_FIELDS:
            rows.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            content=ft.Text(f"{label}:", color=ft.Colors.ON_SURFACE_VARIANT, size=13, weight=ft.FontWeight.W_700, text_align=ft.TextAlign.RIGHT),
                            expand=3,
                            padding=ft.Padding(left=0, right=8, top=0, bottom=0),
                        ),
                        ft.Container(
                            content=fields[key],
                            expand=6,
                        ),
                        ft.Container(
                            content=ft.ElevatedButton(
                                "Examinar",
                                bgcolor="#5A9FB4",  # A soft cyan for examine button
                                color="#FFFFFF",
                                height=36,
                                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                                on_click=make_browse_handler(key),
                            ),
                            expand=2,
                            padding=ft.Padding(left=10, right=0, top=0, bottom=0)
                        ),
                    ],
                )
            )

        settings_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.ON_SURFACE_VARIANT, size=24),
                    ft.Text("CONFIGURACIÓN DE RUTAS LOCALES (ONEDRIVE)", color=ft.Colors.ON_SURFACE, weight=ft.FontWeight.W_900, size=18)
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            title_padding=ft.Padding(left=24, right=24, top=32, bottom=16),
            content=ft.Container(
                width=850,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border_radius=16,
                padding=ft.Padding(left=24, right=24, top=8, bottom=8),
                content=ft.Container(
                    bgcolor="#012A3A",
                    border_radius=16,
                    padding=ft.Padding(left=24, right=24, top=24, bottom=24),
                    content=ft.Column(
                        controls=rows,
                        spacing=16,
                        scroll=ft.ScrollMode.AUTO,
                        height=360,
                    ),
                ),
            ),
            actions=[
                ft.Row(
                    controls=[
                        ft.ElevatedButton(
                            "Guardar Cambios",
                            bgcolor=ft.Colors.TERTIARY,
                            color="#000000",
                            height=44,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=_save,
                        ),
                        ft.ElevatedButton(
                            "Cancelar",
                            bgcolor="#8ECAE6",
                            color="#000000",
                            height=44,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                            on_click=_cancel,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=12,
                )
            ],
            actions_padding=ft.Padding(left=24, right=24, top=8, bottom=24),
        )
        page.show_dialog(settings_dialog)

    # ── Autenticación Office 365 ─────────────────────────────────────────────
    def show_auth_dialog(consent_url: str) -> str:
        result = {"url": ""}
        waiter = threading.Event()
        url_field = ft.TextField(
            label="Pega aquí la URL final del navegador",
            min_lines=3, max_lines=5, multiline=True, autofocus=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST, color=ft.Colors.ON_SURFACE,
            border_color=ft.Colors.OUTLINE,
        )

        def _accept(_):
            result["url"] = (url_field.value or "").strip()
            page.pop_dialog()
            waiter.set()

        def _cancel(_):
            page.pop_dialog()
            waiter.set()

        auth_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Autenticación Office 365", color=ft.Colors.ON_SURFACE, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                width=680,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border_radius=14,
                padding=12,
                content=ft.Column(
                    spacing=16,
                    controls=[
                        ft.Text("1. Abre este enlace en tu navegador:", color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(consent_url, color=ft.Colors.PRIMARY, selectable=True, size=12),
                        ft.Text("2. Completa el inicio de sesión y pega la URL resultante aquí abajo:", color=ft.Colors.ON_SURFACE_VARIANT),
                        url_field,
                    ],
                ),
            ),
            actions=[
                ft.TextButton("Cancelar", style=ft.ButtonStyle(color=ft.Colors.ON_SURFACE_VARIANT), on_click=_cancel),
                ft.ElevatedButton(
                    "Aceptar",
                    bgcolor=ft.Colors.PRIMARY, color=ft.Colors.ON_PRIMARY,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                    on_click=_accept,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(auth_dialog)
        waiter.wait()
        return result["url"]

    # ── Acciones de botones ──────────────────────────────────────────────────
    def run_pipeline_action(_) -> None:
        if state["busy"]:
            return
        if not any([chk_pase.value, chk_supramax.value, chk_edenred.value, chk_fleetup.value]):
            show_snack("Selecciona al menos un sistema a ejecutar.", "#b42318")
            return
        try:
            selection = current_selection()
        except Exception as exc:
            show_snack(f"Revisa el rango de fechas: {exc}", "#b42318")
            return

        options = PipelineOptions(
            run_pase=chk_pase.value,
            run_supramax=chk_supramax.value,
            run_edenred=chk_edenred.value,
            run_fleetup=chk_fleetup.value,
            date_selection=selection,
            report_root=config_values.get("RUTAS_BASE", "") or "/tmp",
            headless=chk_headless.value,
        )

        def worker():
            current_worker = threading.current_thread()
            set_busy(True, "Ejecutando flujos RPA…", ft.Colors.PRIMARY)
            try:
                clear_captured_data()
                clear_logs()
                run_selected_flows(options, logger=append_log)
                if not state.get("stopping"):
                    set_busy(False, "Flujos completados.", ft.Colors.OUTLINE)
                    show_snack("✅ Flujos finalizados correctamente.", ft.Colors.OUTLINE)
            except SystemExit:
                pass
            except Exception as exc:
                append_log(f"❌ Error crítico: {exc}")
                set_busy(False, "Error en ejecución.", ft.Colors.PRIMARY_CONTAINER)
                show_snack(f"Error: {exc}", ft.Colors.PRIMARY_CONTAINER)
            finally:
                was_stopping = _release_worker(current_worker)
                if was_stopping:
                    append_log("🛑 PROCESO DETENIDO.")
                    set_busy(False, "Detenido.", ft.Colors.ERROR)
                    show_snack("Proceso detenido por el usuario.", ft.Colors.ERROR)

        t = KillableThread(target=worker, daemon=True)
        state['worker_thread'] = t
        t.start()

    def run_auth_action(_) -> None:
        if state["busy"]:
            return

        def worker():
            current_worker = threading.current_thread()
            set_busy(True, "Conectando con Office 365…", ft.Colors.PRIMARY)
            try:
                run_o365_auth(on_auth_needed=show_auth_dialog, on_log=append_log)
                if not state.get("stopping"):
                    set_busy(False, "Autenticación completada.", ft.Colors.OUTLINE)
                    show_snack("✅ Autenticación exitosa.", ft.Colors.OUTLINE)
            except SystemExit:
                pass
            except Exception as exc:
                append_log(f"❌ Error de autenticación: {exc}")
                set_busy(False, "Error de autenticación.", ft.Colors.PRIMARY_CONTAINER)
            finally:
                was_stopping = _release_worker(current_worker)
                if was_stopping:
                    append_log("🛑 AUTENTICACIÓN DETENIDA.")
                    set_busy(False, "Detenido.", ft.Colors.ERROR)
                    show_snack("Autenticación detenida por el usuario.", ft.Colors.ERROR)

        t = KillableThread(target=worker, daemon=True)
        state['worker_thread'] = t
        t.start()

    def run_consolidation_action(_) -> None:
        if state["busy"]:
            return

        def worker():
            current_worker = threading.current_thread()
            set_busy(True, "Agregando reportes nuevos…", ft.Colors.PRIMARY)
            try:
                append_log("\n" + "=" * 60)
                append_log("📊 INICIANDO PROCESO DE CONSOLIDACIÓN DESDE INTERFAZ 🚀")
                append_log("=" * 60)
                append_log("🧩 Modo incremental: se agregarán solo respaldos nuevos o modificados.")
                run_full_consolidation(
                    logger=append_log,
                    rebuild_sources=True,
                    rebuild_all_sources=False,
                )
                if not state.get("stopping"):
                    set_busy(False, "Consolidación completada.", ft.Colors.OUTLINE)
                    show_snack("✅ Nuevos reportes agregados y dashboard actualizado.", ft.Colors.OUTLINE)
            except SystemExit:
                pass
            except Exception as exc:
                append_log(f"❌ Error en consolidación: {exc}")
                set_busy(False, "Error en consolidación.", ft.Colors.PRIMARY_CONTAINER)
            finally:
                was_stopping = _release_worker(current_worker)
                if was_stopping:
                    append_log("🛑 CONSOLIDACIÓN DETENIDA.")
                    set_busy(False, "Detenido.", ft.Colors.ERROR)
                    show_snack("Consolidación detenida por el usuario.", ft.Colors.ERROR)

        t = KillableThread(target=worker, daemon=True)
        state['worker_thread'] = t
        t.start()

    def run_full_rebuild_action(_) -> None:
        if state["busy"]:
            return

        def worker():
            current_worker = threading.current_thread()
            set_busy(True, "Reconstruyendo base completa…", ft.Colors.PRIMARY)
            try:
                append_log("\n" + "=" * 60)
                append_log("🧱 INICIANDO RECONSTRUCCIÓN TOTAL DE RESPALDOS")
                append_log("=" * 60)
                append_log("♻️ Se reprocesarán todos los respaldos encontrados antes de regenerar el dashboard.")
                run_full_consolidation(
                    logger=append_log,
                    rebuild_sources=True,
                    rebuild_all_sources=True,
                )
                if not state.get("stopping"):
                    set_busy(False, "Reconstrucción completada.", ft.Colors.OUTLINE)
                    show_snack("✅ Base completa reconstruida.", ft.Colors.OUTLINE)
            except SystemExit:
                pass
            except Exception as exc:
                append_log(f"❌ Error en reconstrucción total: {exc}")
                set_busy(False, "Error en reconstrucción.", ft.Colors.PRIMARY_CONTAINER)
            finally:
                was_stopping = _release_worker(current_worker)
                if was_stopping:
                    append_log("🛑 RECONSTRUCCIÓN TOTAL DETENIDA.")
                    set_busy(False, "Detenido.", ft.Colors.ERROR)
                    show_snack("Reconstrucción total detenida por el usuario.", ft.Colors.ERROR)

        t = KillableThread(target=worker, daemon=True)
        state['worker_thread'] = t
        t.start()

    def run_movements_action(_) -> None:
        if state["busy"]:
            return

        def worker():
            current_worker = threading.current_thread()
            set_busy(True, "Actualizando movimientos…", ft.Colors.PRIMARY)
            try:
                append_log("\n" + "=" * 60)
                append_log("🚗 ACTUALIZANDO SOLO MOVIMIENTOS EN REPORTE FINAL")
                append_log("=" * 60)
                run_full_consolidation(
                    logger=append_log,
                    rebuild_sources=False,
                    only_movements=True,
                )
                if not state.get("stopping"):
                    set_busy(False, "Movimientos actualizados.", ft.Colors.OUTLINE)
                    show_snack("✅ Movimientos actualizados.", ft.Colors.OUTLINE)
            except SystemExit:
                pass
            except Exception as exc:
                append_log(f"❌ Error al actualizar movimientos: {exc}")
                set_busy(False, "Error en movimientos.", ft.Colors.PRIMARY_CONTAINER)
            finally:
                was_stopping = _release_worker(current_worker)
                if was_stopping:
                    append_log("🛑 ACTUALIZACIÓN DE MOVIMIENTOS DETENIDA.")
                    set_busy(False, "Detenido.", ft.Colors.ERROR)
                    show_snack("Actualización de movimientos detenida por el usuario.", ft.Colors.ERROR)

        t = KillableThread(target=worker, daemon=True)
        state['worker_thread'] = t
        t.start()


    # ── Conectar eventos ─────────────────────────────────────────────────────
    # En Flet 0.85.3, Dropdown usa on_select (no on_change)
    range_dropdown.on_select = handle_range_change
    start_input.on_change = refresh_selection
    end_input.on_change = refresh_selection
    start_cal_btn.on_click = lambda _: open_calendar_dialog("start")
    end_cal_btn.on_click = lambda _: open_calendar_dialog("end")
    run_button.on_click = run_pipeline_action
    auth_button.on_click = run_auth_action
    settings_button.on_click = open_settings_dialog
    consolidate_button.on_click = run_consolidation_action
    full_rebuild_button.on_click = run_full_rebuild_action
    movements_button.on_click = run_movements_action
    clear_logs_button.on_click = clear_logs

    # ── Layout de la interfaz ────────────────────────────────────────────────
    def _flow_row(checkbox, label, icon):
        return ft.Container(
            bgcolor=ft.Colors.SURFACE,
            border_radius=14,
            padding=ft.Padding(left=16, right=16, top=10, bottom=10),
            shadow=ft.BoxShadow(spread_radius=0, blur_radius=4, color="#0D000000", offset=ft.Offset(0, 1)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Container(
                                content=ft.Icon(icon, color=ft.Colors.PRIMARY, size=20),
                                bgcolor="#1A0090FF",
                                border_radius=8,
                                padding=6,
                            ),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(label, color=ft.Colors.ON_SURFACE, weight=ft.FontWeight.W_600),
                                    ft.Text("Disponible", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                                ],
                            ),
                        ],
                    ),
                    checkbox,
                ],
            ),
        )

    inputs_panel = ft.Container(
        bgcolor=ft.Colors.SURFACE,
        border=_border_all(1, ft.Colors.OUTLINE_VARIANT),
        shadow=ft.BoxShadow(spread_radius=0, blur_radius=4, color="#0D000000", offset=ft.Offset(0, 1)),
        border_radius=18,
        padding=18,
        content=ft.Column(
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Text("RANGO DE FECHAS", size=11, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                range_dropdown,
                custom_row,
            ],
        ),
    )

    period_panel = ft.Container(
        bgcolor=ft.Colors.SURFACE,
        border=_border_all(1, ft.Colors.OUTLINE_VARIANT),
        shadow=ft.BoxShadow(spread_radius=0, blur_radius=4, color="#0D000000", offset=ft.Offset(0, 1)),
        border_radius=16,
        padding=ft.Padding(left=18, right=18, top=14, bottom=14),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text("PERIODO CALCULADO", size=11, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                info_text,
                months_text,
                error_text,
                ft.Divider(height=10, color="transparent"),
                status_text,
            ],
        ),
    )

    config_card = ft.Container(
        expand=4,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=_border_all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=20,
        padding=22,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=14,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Column(
                            spacing=4,
                            controls=[
                                ft.Text("Panel de control", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                                ft.Text("Selecciona flujos, periodo y acciones disponibles.", color=ft.Colors.ON_SURFACE_VARIANT),
                            ],
                        ),
                        ft.Icon(ft.Icons.TUNE, color=ft.Colors.PRIMARY, size=24),
                    ],
                ),
                ft.Text("FLUJOS HABILITADOS", size=11, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                ft.ResponsiveRow(
                    controls=[
                        ft.Container(_flow_row(chk_pase, "Portal Pase", ft.Icons.CONFIRMATION_NUMBER_OUTLINED), col={"sm": 12, "md": 6}),
                        ft.Container(_flow_row(chk_supramax, "Portal Supramax", ft.Icons.LOCAL_GAS_STATION_OUTLINED), col={"sm": 12, "md": 6}),
                        ft.Container(_flow_row(chk_edenred, "Portal Edenred", ft.Icons.CREDIT_CARD_OUTLINED), col={"sm": 12, "md": 6}),
                        ft.Container(_flow_row(chk_fleetup, "Portal Fleetup", ft.Icons.LOCAL_SHIPPING_OUTLINED), col={"sm": 12, "md": 6}),
                    ],
                ),
                inputs_panel,
                period_panel,
                ft.Text("ACCIONES", size=11, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                ft.Row(
                    spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True,
                    controls=[run_button, stop_button, chk_headless, panel_status_pill]
                ),
                ft.Row(
                    spacing=10, wrap=True,
                    controls=[consolidate_button, full_rebuild_button, movements_button, auth_button],
                ),
            ],
        ),
    )

    logs_card = ft.Container(
        expand=5,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=_border_all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=20,
        padding=0,
        content=ft.Column(
            expand=True,
            controls=[
                ft.Container(
                    padding=22,
                    border_radius=_radius_top(20, 20),
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=4,
                                controls=[
                                    ft.Text("Consola operativa", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                                    ft.Text("Salida en tiempo real de los procesos RPA.", color=ft.Colors.ON_SURFACE_VARIANT),
                                ],
                            ),
                            ft.Row(spacing=4, controls=[copy_logs_button, clear_logs_button]),
                        ],
                    ),
                ),
                ft.Container(
                    expand=True,
                    padding=ft.Padding(left=18, right=18, top=0, bottom=18),
                    content=log_output_container,
                ),
            ],
        ),
    )


    # --- MULTI-VIEW ARCHITECTURE ---
    
    dashboard_view = ft.Row(
        expand=True,
        spacing=20,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[config_card, logs_card],
    )
    
    # Credentials state for Supramax
    import json
    import os
    import dotenv
    env_path = ".env"
    dotenv.load_dotenv(env_path, override=True)
    
    supramax_creds_raw = os.getenv("SUPRAMAX_CREDENTIALS", "[]")
    try:
        supramax_creds = json.loads(supramax_creds_raw)
    except:
        supramax_creds = []
        
    supramax_dt = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Empresa")),
            ft.DataColumn(ft.Text("Usuario")),
            ft.DataColumn(ft.Text("Contraseña")),
            ft.DataColumn(ft.Text("Acción")),
        ],
        rows=[]
    )
    
    def open_dialog(dlg):
        try:
            page.show_dialog(dlg)
        except AttributeError:
            page.dialog = dlg
            dlg.open = True
            safe_update()

    def close_dialog(dlg):
        try:
            page.pop_dialog()
        except AttributeError:
            dlg.open = False
            safe_update()

    def render_supramax_table():
        supramax_dt.rows.clear()
        for cred in supramax_creds:
            def make_delete_fn(cred_ref):
                def on_delete_click(e):
                    dlg = ft.AlertDialog(
                        title=ft.Text("Confirmar eliminación"),
                        content=ft.Text(f"¿Seguro que deseas borrar las credenciales de {cred_ref.get('Empresa', '')}?"),
                        actions=[
                            ft.TextButton("Cancelar", on_click=lambda e: close_dialog(dlg)),
                            ft.ElevatedButton("Borrar", bgcolor=ft.Colors.ERROR, color=ft.Colors.ON_ERROR, on_click=lambda e: confirm_delete(dlg, cred_ref))
                        ],
                    )
                    open_dialog(dlg)
                return on_delete_click
                
            def make_edit_fn(cred_ref):
                return lambda e: edit_supramax_row(cred_ref)
                
            supramax_dt.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(cred.get("Empresa", ""))),
                        ft.DataCell(ft.Text(cred.get("Usuario", ""))),
                        ft.DataCell(ft.Text("********")),
                        ft.DataCell(
                            ft.Row([
                                ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.PRIMARY, on_click=make_edit_fn(cred)),
                                ft.IconButton(icon=ft.Icons.DELETE, icon_color=ft.Colors.ERROR, on_click=make_delete_fn(cred))
                            ])
                        )
                    ]
                )
            )
        safe_update()

    def confirm_delete(dlg, cred_ref):
        close_dialog(dlg)
        if cred_ref in supramax_creds:
            supramax_creds.remove(cred_ref)
            render_supramax_table()

    def edit_supramax_row(cred_ref):
        f_emp = ft.TextField(label="Empresa", value=cred_ref.get("Empresa", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        f_usr = ft.TextField(label="Usuario", value=cred_ref.get("Usuario", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        f_pwd = ft.TextField(label="Contraseña", password=True, can_reveal_password=True, value=cred_ref.get("Contraseña", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        
        dlg = ft.AlertDialog(title=ft.Text("Editar Empresa Supramax"))
        
        def close_dlg(e):
            close_dialog(dlg)
            
        def save_edit(e):
            if f_emp.value and f_usr.value and f_pwd.value:
                cred_ref["Empresa"] = f_emp.value
                cred_ref["Usuario"] = f_usr.value
                cred_ref["Contraseña"] = f_pwd.value
                render_supramax_table()
                close_dialog(dlg)
                
        dlg.content = ft.Column([f_emp, f_usr, f_pwd], tight=True)
        dlg.actions = [ft.TextButton("Cancelar", on_click=close_dlg), ft.ElevatedButton("Guardar", on_click=save_edit, bgcolor=ft.Colors.PRIMARY, color=ft.Colors.ON_PRIMARY)]
        open_dialog(dlg)

    def add_supramax_row(e):
        f_emp = ft.TextField(label="Empresa", bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        f_usr = ft.TextField(label="Usuario", bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        f_pwd = ft.TextField(label="Contraseña", password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True)
        
        dlg = ft.AlertDialog(title=ft.Text("Añadir Empresa Supramax"))
        
        def close_dlg(e):
            close_dialog(dlg)
            
        def save_new(e):
            if f_emp.value and f_usr.value and f_pwd.value:
                supramax_creds.append({
                    "Empresa": f_emp.value,
                    "Usuario": f_usr.value,
                    "Contraseña": f_pwd.value
                })
                render_supramax_table()
                close_dialog(dlg)
                
        dlg.content = ft.Column([f_emp, f_usr, f_pwd], tight=True)
        dlg.actions = [ft.TextButton("Cancelar", on_click=close_dlg), ft.ElevatedButton("Añadir", on_click=save_new, bgcolor=ft.Colors.PRIMARY, color=ft.Colors.ON_PRIMARY)]
        open_dialog(dlg)

    render_supramax_table()
    
    f_edenred_user = ft.TextField(label="Usuario", value=os.getenv("EDENRED_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_edenred_pass = ft.TextField(label="Contraseña", value=os.getenv("EDENRED_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_pase_user = ft.TextField(label="Usuario", value=os.getenv("PASE_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_pase_pass = ft.TextField(label="Contraseña", value=os.getenv("PASE_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_fleetup_user = ft.TextField(label="Usuario", value=os.getenv("FLEETUP_USER", ""), bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_fleetup_pass = ft.TextField(label="Contraseña", value=os.getenv("FLEETUP_PASSWORD", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)
    f_twocaptcha = ft.TextField(label="API Key", value=os.getenv("TWOCAPTCHA_API_KEY", ""), password=True, can_reveal_password=True, bgcolor=ft.Colors.SURFACE, border_color=ft.Colors.TRANSPARENT, border_radius=14, filled=True, expand=True)

    def save_all_settings(e):
        dotenv.set_key(env_path, "EDENRED_USER", f_edenred_user.value)
        dotenv.set_key(env_path, "EDENRED_PASSWORD", f_edenred_pass.value)
        dotenv.set_key(env_path, "PASE_USER", f_pase_user.value)
        dotenv.set_key(env_path, "PASE_PASSWORD", f_pase_pass.value)
        dotenv.set_key(env_path, "FLEETUP_USER", f_fleetup_user.value)
        dotenv.set_key(env_path, "FLEETUP_PASSWORD", f_fleetup_pass.value)
        dotenv.set_key(env_path, "TWOCAPTCHA_API_KEY", f_twocaptcha.value)
        dotenv.set_key(env_path, "SUPRAMAX_CREDENTIALS", json.dumps(supramax_creds))
        dotenv.load_dotenv(env_path, override=True)
        show_snack("✅ Configuración guardada en .env", bgcolor="#00E676")

    btn_save = ft.ElevatedButton("Guardar Configuración", icon=ft.Icons.SAVE, on_click=save_all_settings, bgcolor=ft.Colors.PRIMARY, color=ft.Colors.ON_PRIMARY, height=40)
    btn_routes = ft.OutlinedButton(
        "Rutas locales",
        icon=ft.Icons.FOLDER_OPEN,
        on_click=open_settings_dialog,
        height=40,
        style=ft.ButtonStyle(
            color=ft.Colors.ON_SURFACE,
            bgcolor="#0D000000",
            side=ft.BorderSide(width=1.5, color=ft.Colors.OUTLINE),
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )

    def make_credential_card(title, subtitle, icon, controls_row):
        return ft.Container(
            border_radius=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border=None,
            padding=20,
            content=ft.Column(
                spacing=16,
                controls=[
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(
                                bgcolor="#1A0090FF", # Badge dedicado
                                border_radius=8,
                                padding=8,
                                content=ft.Icon(icon, color=ft.Colors.PRIMARY, size=24), # Delineado cian claro
                            ),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                                    ft.Text(subtitle, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                ]
                            )
                        ]
                    ),
                    controls_row
                ]
            )
        )

    settings_view = ft.Container(
        expand=True,
        bgcolor=ft.Colors.TRANSPARENT,
        padding=0,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=20,
            controls=[
                ft.Row([
                    ft.Row([
                        ft.Icon(ft.Icons.SETTINGS, size=28, color=ft.Colors.PRIMARY),
                        ft.Text("Configuración de Entorno", size=24, weight=ft.FontWeight.BOLD)
                    ]),
                    ft.Row([btn_routes, btn_save], spacing=10)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text("Las credenciales se guardan de forma local en tu computadora (.env).", color=ft.Colors.ON_SURFACE_VARIANT),
                
                make_credential_card("Edenred (Ticket Car)", "Credenciales de acceso al portal", ft.Icons.LOCAL_GAS_STATION, ft.Row([f_edenred_user, f_edenred_pass])),
                make_credential_card("Portal Pase (Peajes)", "Credenciales de acceso al portal", ft.Icons.CONFIRMATION_NUMBER_OUTLINED, ft.Row([f_pase_user, f_pase_pass])),
                make_credential_card("FleetUp (GPS)", "Credenciales de acceso al portal", ft.Icons.LOCATION_ON, ft.Row([f_fleetup_user, f_fleetup_pass])),
                make_credential_card("2Captcha", "API Key para resolución de captchas", ft.Icons.SECURITY, ft.Row([f_twocaptcha])),
                
                ft.Container(
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                    border=None,
                    border_radius=16,
                    padding=20,
                    content=ft.Column([
                        ft.Row([
                            ft.Row([
                                ft.Container(
                                    bgcolor="#1A0090FF",
                                    border_radius=8,
                                    padding=8,
                                    content=ft.Icon(ft.Icons.BUSINESS, color=ft.Colors.PRIMARY, size=24),
                                ),
                                ft.Column([
                                    ft.Text("Supramax (Multi-Empresa)", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                                    ft.Text("Gestión de credenciales", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                ], spacing=2)
                            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.ElevatedButton("Añadir", icon=ft.Icons.ADD, on_click=add_supramax_row)
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Container(
                            border=None,
                            border_radius=10,
                            bgcolor=ft.Colors.SURFACE,
                            padding=10,
                            content=supramax_dt
                        ),
                    ])
                ),
                
                ft.Container(height=40)
            ]
        )
    )

    main_content_container = ft.Container(
        expand=True,
        content=dashboard_view
    )

    def switch_view(view_name):
        if view_name == "dashboard":
            main_content_container.content = dashboard_view
            nav_dashboard.bgcolor = ft.Colors.OUTLINE + "30"
            nav_settings.bgcolor = ft.Colors.TRANSPARENT
        else:
            main_content_container.content = settings_view
            nav_dashboard.bgcolor = ft.Colors.TRANSPARENT
            nav_settings.bgcolor = ft.Colors.OUTLINE + "30"
        safe_update()

    nav_dashboard = ft.Container(
        border_radius=10,
        bgcolor=ft.Colors.OUTLINE + "30",
        padding=ft.Padding(left=12, right=12, top=10, bottom=10),
        ink=True,
        on_click=lambda _: switch_view("dashboard"),
        content=ft.Row(
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.DASHBOARD, color=ft.Colors.ON_SURFACE_VARIANT, size=18),
                ft.Text("Dashboard", color=ft.Colors.ON_SURFACE, size=14),
            ],
        ),
    )

    nav_settings = ft.Container(
        border_radius=10,
        bgcolor=ft.Colors.TRANSPARENT,
        padding=ft.Padding(left=12, right=12, top=10, bottom=10),
        on_click=lambda _: switch_view("settings"),
        ink=True,
        content=ft.Row(
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.ON_SURFACE_VARIANT, size=18),
                ft.Text("Configuración", color=ft.Colors.ON_SURFACE_VARIANT, size=14),
            ],
        ),
    )

    sidebar = ft.Container(
        width=220,
        bgcolor=ft.Colors.SURFACE,
        padding=ft.Padding(left=18, right=18, top=30, bottom=30),
        content=ft.Column(
            spacing=24,
            controls=[
                ft.Column(
                    spacing=6,
                    controls=[
                        ft.Text("RPA", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.PRIMARY),
                        ft.Text("Utilitarios", size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Divider(color="ft.Colors.OUTLINE_VARIANT"),
                    ],
                ),
                ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text("NAVEGACIÓN", size=10, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                        nav_dashboard,
                        nav_settings,
                    ],
                ),
                ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text("VERSIÓN", size=10, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_700),
                        ft.Text("UI Flet – Beta", size=12, color=ft.Colors.OUTLINE_VARIANT),
                        ft.Text("Backend compartido con app.py", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                ),

            ],
        ),
    )

    top_bar = ft.Container(
        bgcolor=ft.Colors.SURFACE,
        border=ft.Border(bottom=ft.BorderSide(1, "ft.Colors.OUTLINE_VARIANT")),
        padding=ft.Padding(left=28, right=28, top=18, bottom=18),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text("RPA Utilitarios", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE),
                ft.IconButton(
                    icon=ft.Icons.DARK_MODE,
                    icon_color=ft.Colors.ON_SURFACE,
                    tooltip="Cambiar tema",
                    on_click=lambda e: _toggle_theme(e)
                ),

                        ft.Text("Automatización de portales financieros · Flota Petroil", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                ),
                ft.Row(
                    spacing=16,
                    controls=[
                        ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.OUTLINE, size=10),
                        ft.Text("Sistema activo", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                ),
            ],
        ),
    )

    main_content = ft.Container(
        expand=True,
        content=ft.Row(
            expand=True,
            spacing=20,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[config_card, logs_card],
        ),
    )

    page.add(
        ft.Row(
            expand=True,
            spacing=0,
            controls=[
                sidebar,
                ft.Container(
                    expand=True,
                    bgcolor=PALETTE["surface"],
                    content=ft.Column(
                        expand=True,
                        spacing=0,
                        controls=[
                            top_bar,
                            ft.Container(
                                expand=True,
                                padding=24,
                                content=main_content_container,
                            ),
                        ],
                    ),
                ),
            ],
        )
    )

    # Inicializar el periodo calculado
    refresh_selection()


if __name__ == "__main__":
    ft.run(main)
