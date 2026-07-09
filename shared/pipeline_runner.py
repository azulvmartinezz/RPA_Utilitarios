import datetime as dt
import os
import time
from dataclasses import dataclass

import pandas as pd

from extractors import edenred_extractor
from scrapers import edenred_rpa, fleetup_rpa, pase_rpa, supramax_rpa

from .date_ranges import DateRangeSelection
from .ingest_capture import get_captured_data
from .logging_redirect import redirect_std_streams


@dataclass(frozen=True)
class PipelineOptions:
    date_selection: DateRangeSelection
    report_root: str
    run_pase: bool
    run_supramax: bool
    run_edenred: bool
    run_fleetup: bool
    headless: bool = False


@dataclass(frozen=True)
class PipelineResult:
    duration_minutes: float
    report_path: str | None


def run_selected_flows(options: PipelineOptions, logger=print) -> PipelineResult:
    with redirect_std_streams(logger):
        start_time = time.time()
        logger("\n" + "=" * 60)
        logger("🚀 INICIANDO EJECUCIÓN DEL FLUJO RPA SELECCIONADO 🚀")
        if options.date_selection.mode == "rango":
            logger(
                f"📅 Rango de proceso: {options.date_selection.start_text} ➔ {options.date_selection.end_text}"
            )
            logger(
                f"📂 Meses objetivos identificados: {options.date_selection.edenred_months}"
            )
        else:
            logger(
                "📅 Periodo de proceso: "
                f"del {options.date_selection.start_text} al {options.date_selection.end_text} "
                "(Mes Pasado)"
            )
        logger("=" * 60)

        if options.run_pase:
            logger("\n🎫 [PASE] Iniciando descarga y procesamiento local...")
            try:
                if options.date_selection.target_months:
                    pase_rpa.main(
                        backfill_mode=True,
                        meses_objetivo=options.date_selection.target_months,
                        headless=options.headless
                    )
                else:
                    pase_rpa.main(
                        backfill_mode=False,
                        meses_objetivo=options.date_selection.target_months,
                        headless=options.headless,
                    )
            except Exception as exc:
                logger(f"❌ Error en flujo Pase: {exc}")

        if options.run_supramax:
            logger("\n📈 [SUPRAMAX] Procesando rango de consumos...")
            try:
                if options.date_selection.mode == "rango":
                    supramax_rpa.main(
                        fini_override=options.date_selection.start_text,
                        ffin_override=options.date_selection.end_text,
                        headless=options.headless
                    )
                else:
                    supramax_rpa.main(headless=options.headless)
            except Exception as exc:
                logger(f"❌ Error en flujo Supramax: {exc}")

        if options.run_fleetup:
            logger("\n🚛 [FLEETUP] Iniciando flujo local (Descarga + Procesamiento)...")
            try:
                fleetup_rpa.main(headless=options.headless)
            except Exception as exc:
                logger(f"❌ Error en flujo FleetUp: {exc}")

        if options.run_edenred:
            logger("\n💎 [EDENRED] Iniciando flujo (Solicitud + Extracción)...")
            try:
                if options.date_selection.mode == "rango":
                    expected_files = edenred_rpa.main(
                        meses_override=options.date_selection.edenred_months,
                        headless=options.headless
                    )
                else:
                    expected_files = edenred_rpa.main(headless=options.headless)
                edenred_extractor.main(n_expected=expected_files)
            except Exception as exc:
                logger(f"❌ Error en flujo Edenred: {exc}")

        report_path = generate_consolidated_report(options.report_root, logger=logger)

        duration_minutes = (time.time() - start_time) / 60
        logger("\n" + "=" * 60)
        logger(f"✅ PROCESO GLOBAL FINALIZADO EN {duration_minutes:.2f} MINUTOS")
        logger("=" * 60 + "\n")
        return PipelineResult(duration_minutes=duration_minutes, report_path=report_path)


def generate_consolidated_report(report_root: str, logger=print) -> str | None:
    captured_dfs = get_captured_data()
    if not captured_dfs:
        logger("\n⚠️ No se procesó información nueva. No se generará reporte consolidado.")
        return None

    logger("\n📊 Generando Reporte Consolidado Local...")
    try:
        df_consolidado = pd.concat(captured_dfs, ignore_index=True)

        reportes_dir = os.path.join(report_root, "Reportes_Ejecutable")
        os.makedirs(reportes_dir, exist_ok=True)

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"Reporte_Consolidado_RPA_{timestamp}.xlsx"
        file_path = os.path.join(reportes_dir, file_name)

        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df_consolidado.to_excel(writer, sheet_name="Detalle Consolidado", index=False)
            worksheet = writer.sheets["Detalle Consolidado"]
            for column in worksheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in column)
                column_letter = column[0].column_letter
                worksheet.column_dimensions[column_letter].width = max(max_len + 3, 12)

        logger(f"✅ ¡Reporte consolidado guardado en:\n   -> {file_path}")
        return file_path
    except Exception as exc:
        logger(f"❌ Error al generar el reporte consolidado: {exc}")
        return None
