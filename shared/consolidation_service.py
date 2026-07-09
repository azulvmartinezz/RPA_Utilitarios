from dataclasses import dataclass

from .logging_redirect import redirect_std_streams


@dataclass(frozen=True)
class ConsolidationResult:
    success: bool
    message: str


def run_full_consolidation(
    logger=print,
    *,
    rebuild_sources: bool = True,
    rebuild_all_sources: bool = False,
    only_movements: bool = False,
) -> ConsolidationResult:
    with redirect_std_streams(logger):
        logger("\n" + "=" * 60)
        logger("📊 INICIANDO PROCESO DE CONSOLIDACIÓN DESDE INTERFAZ 🚀")
        logger("=" * 60)

        try:
            if rebuild_sources:
                from scripts_onedrive import unificar_respaldos_local
                unificar_respaldos_local.unificar_respaldos_desde_onedrive(
                    rebuild_all=rebuild_all_sources
                )
            else:
                logger("⏩ Omitiendo reconstrucción de respaldos locales. Se usará la base consolidada actual.")

            from scripts import consolidar_utilitarios

            if only_movements:
                consolidar_utilitarios.actualizar_movimientos_solo()
            else:
                consolidar_utilitarios.consolidar_todo()
            logger("\n✅ ¡Consolidación finalizada con éxito!")
            return ConsolidationResult(
                success=True,
                message="Reporte Dashboard Final consolidado con éxito en la carpeta local configurada.",
            )
        except Exception as exc:
            logger(f"❌ Error durante la consolidación: {exc}")
            return ConsolidationResult(
                success=False,
                message=f"Ocurrió un error al consolidar: {exc}",
            )
