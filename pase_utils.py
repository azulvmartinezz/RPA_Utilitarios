import csv
from typing import Iterable

import pandas as pd


_PASE_ENCODINGS = ("latin1", "utf-8-sig", "utf-8")
_SNIFFER_DELIMITERS = ",;\t|"


def _dedupe_headers(headers: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    deduped = []
    for raw_header in headers:
        header = str(raw_header)
        count = seen.get(header, 0)
        seen[header] = count + 1
        deduped.append(header if count == 0 else f"{header}__dup{count + 1}")
    return deduped


def _detect_csv_dialect(file_path: str, encoding: str) -> csv.Dialect:
    with open(file_path, "r", encoding=encoding, newline="") as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SNIFFER_DELIMITERS)
    except csv.Error:
        return csv.excel


def _read_pase_csv_with_encoding(file_path: str, encoding: str) -> pd.DataFrame:
    dialect = _detect_csv_dialect(file_path, encoding)

    with open(file_path, "r", encoding=encoding, newline="") as handle:
        reader = csv.reader(
            handle,
            delimiter=dialect.delimiter,
            quotechar=dialect.quotechar,
            skipinitialspace=dialect.skipinitialspace,
        )
        try:
            raw_headers = next(reader)
        except StopIteration:
            return pd.DataFrame()

        max_cols = len(raw_headers)
        for row in reader:
            if len(row) > max_cols:
                max_cols = len(row)

    headers = _dedupe_headers(raw_headers)
    extra_cols = max_cols - len(headers)
    if extra_cols > 0:
        headers.extend(f"__extra_col_{idx}" for idx in range(1, extra_cols + 1))

    df = pd.read_csv(
        file_path,
        encoding=encoding,
        sep=dialect.delimiter,
        quotechar=dialect.quotechar,
        header=0,
        names=headers,
        engine="python",
        dtype=str,
        keep_default_na=False,
    )

    df.columns = df.columns.str.strip()
    empty_extra_cols = [
        col
        for col in df.columns
        if col.startswith("__extra_col_") and df[col].astype(str).str.strip().eq("").all()
    ]
    if empty_extra_cols:
        df = df.drop(columns=empty_extra_cols)
    return df


def read_pase_csv_lossless(file_path: str) -> pd.DataFrame:
    last_error = None
    for encoding in _PASE_ENCODINGS:
        try:
            return _read_pase_csv_with_encoding(file_path, encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError(f"No se pudo leer el archivo CSV de Pase: {file_path}")


def parse_pase_fecha(series: pd.Series) -> pd.Series:
    texto = series.astype(str).str.strip()
    serie = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    mask_ymd = texto.str.match(r"^\d{4}/\d{2}/\d{2}$", na=False)
    if mask_ymd.any():
        serie.loc[mask_ymd] = pd.to_datetime(
            texto.loc[mask_ymd],
            format="%Y/%m/%d",
            errors="coerce",
        )

    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(
            texto.loc[restantes],
            dayfirst=True,
            errors="coerce",
        )

    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(texto.loc[restantes], errors="coerce")

    return serie
