import datetime as dt
from dataclasses import dataclass


DEFAULT_RANGE_LABEL = "Mes pasado (Predeterminado)"
CURRENT_YEAR_LABEL = "Este año"
PREVIOUS_YEAR_LABEL = "Año pasado"
CUSTOM_RANGE_LABEL = "Rango personalizado"
RANGE_OPTIONS = [
    DEFAULT_RANGE_LABEL,
    CURRENT_YEAR_LABEL,
    PREVIOUS_YEAR_LABEL,
    CUSTOM_RANGE_LABEL,
]


@dataclass(frozen=True)
class DateRangeSelection:
    mode: str
    start_text: str
    end_text: str
    target_months: list[tuple[int, int]]
    edenred_months: list[str]


def _month_key(value: dt.date) -> tuple[int, int]:
    return value.year, value.month


def _edenred_key(value: dt.date) -> str:
    return value.strftime("%m/%Y")


def _previous_month_window(today: dt.date) -> tuple[dt.date, dt.date]:
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - dt.timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, last_day_prev_month


def build_date_selection(
    range_label: str,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> DateRangeSelection:
    today = today or dt.date.today()

    if range_label == DEFAULT_RANGE_LABEL:
        start, end = _previous_month_window(today)
        return DateRangeSelection(
            mode="mes_pasado",
            start_text=start.strftime("%d/%m/%Y"),
            end_text=end.strftime("%d/%m/%Y"),
            target_months=[_month_key(start)],
            edenred_months=[_edenred_key(start)],
        )

    if range_label == CURRENT_YEAR_LABEL:
        target_months = [(today.year, month) for month in range(1, today.month + 1)]
        edenred_months = [f"{month:02d}/{today.year}" for month in range(1, today.month + 1)]
        return DateRangeSelection(
            mode="rango",
            start_text=f"01/01/{today.year}",
            end_text=today.strftime("%d/%m/%Y"),
            target_months=target_months,
            edenred_months=edenred_months,
        )

    if range_label == PREVIOUS_YEAR_LABEL:
        year = today.year - 1
        target_months = [(year, month) for month in range(1, 13)]
        edenred_months = [f"{month:02d}/{year}" for month in range(1, 13)]
        return DateRangeSelection(
            mode="rango",
            start_text=f"01/01/{year}",
            end_text=f"31/12/{year}",
            target_months=target_months,
            edenred_months=edenred_months,
        )

    if range_label == CUSTOM_RANGE_LABEL:
        if start_date is None or end_date is None:
            raise ValueError("El rango personalizado requiere fecha de inicio y fin.")
        if start_date > end_date:
            raise ValueError("La fecha inicio no puede ser posterior a la fecha fin.")

        current = start_date
        target_months: list[tuple[int, int]] = []
        edenred_months: list[str] = []
        while current <= end_date:
            target_months.append(_month_key(current))
            edenred_months.append(_edenred_key(current))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return DateRangeSelection(
            mode="rango",
            start_text=start_date.strftime("%d/%m/%Y"),
            end_text=end_date.strftime("%d/%m/%Y"),
            target_months=sorted(set(target_months)),
            edenred_months=sorted(set(edenred_months)),
        )

    raise ValueError(f"Rango de fechas no soportado: {range_label}")


def build_period_label(selection: DateRangeSelection) -> str:
    return f"Periodo real a procesar: del {selection.start_text} al {selection.end_text}"
