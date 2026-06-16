import argparse
import os
import re
import pandas as pd


def _normalize_eco(val):
    s = str(val).strip().upper().replace(" ", "").replace(".", "")
    m = re.match(r"^(AU|CA)-?(\d{1,3})(?!\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


def _normalize_card(val):
    if pd.isna(val):
        return None
    text = str(val).strip()
    if not text or text.lower() == "nan":
        return None

    try:
        num = float(text.replace(",", ""))
        if num.is_integer():
            return str(int(num))
    except Exception:
        pass

    return text


def _collapse_cards(series):
    cards = []
    seen = set()
    for value in series:
        card = _normalize_card(value)
        if not card or card in seen:
            continue
        seen.add(card)
        cards.append(card)
    return " | ".join(cards) if cards else None


def _build_system_map(df, eco_col, card_col, output_col):
    if eco_col not in df.columns or card_col not in df.columns:
        return pd.DataFrame(columns=["ECO", output_col])

    work = df[[eco_col, card_col]].copy()
    work["ECO"] = work[eco_col].apply(_normalize_eco)
    work[output_col] = work[card_col].apply(_normalize_card)
    work = work.dropna(subset=["ECO", output_col])
    work = work[work["ECO"].str.match(r"^(AU|CA)-\d{3}$", na=False)]

    return (
        work.groupby("ECO", as_index=False)[output_col]
        .agg(_collapse_cards)
        .sort_values("ECO")
        .reset_index(drop=True)
    )


def _read_table(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if path.lower().endswith(".xlsx"):
        return pd.read_excel(path)
    return pd.read_csv(path, low_memory=False)


def main():
    parser = argparse.ArgumentParser(
        description="Genera un cruce de tarjetas por ECO para Supramax, Ticket Card y Pase."
    )
    parser.add_argument(
        "--supramax",
        default="CONSOLIDADO_CRUDO_SUPRAMAX.csv",
        help="Ruta al consolidado crudo de Supramax.",
    )
    parser.add_argument(
        "--edenred",
        default="CONSOLIDADO_CRUDO_EDENRED.csv",
        help="Ruta al consolidado crudo de Ticket Card / Edenred.",
    )
    parser.add_argument(
        "--pase",
        default="CONSOLIDADO_CRUDO_PASE.csv",
        help="Ruta al consolidado crudo de Pase.",
    )
    parser.add_argument(
        "--output",
        default="reporte_tarjetas_por_eco.xlsx",
        help="Archivo de salida (.xlsx o .csv).",
    )
    args = parser.parse_args()

    print("Generando reporte de tarjetas por ECO...")

    df_supramax = _read_table(args.supramax)
    df_edenred = _read_table(args.edenred)
    df_pase = _read_table(args.pase)

    supra = _build_system_map(df_supramax, "PLACAS", "IDENTIFICADOR", "Supramax")
    eden = _build_system_map(df_edenred, "Vehículo", "Núm Tarjeta", "Ticket Card")

    pase_card_col = next((c for c in df_pase.columns if str(c).strip().lower() == "tarjeta idmx"), None)
    if not pase_card_col:
        pase_card_col = next((c for c in df_pase.columns if "tarjeta" in str(c).strip().lower()), None)
    pase_eco_col = next((c for c in df_pase.columns if str(c).strip().upper() == "ECO"), None)
    pase = _build_system_map(df_pase, pase_eco_col or "ECO", pase_card_col or "__missing__", "Pase")

    ecos = (
        pd.concat([
            supra[["ECO"]],
            eden[["ECO"]],
            pase[["ECO"]],
        ], ignore_index=True)
        .drop_duplicates()
        .sort_values("ECO")
        .reset_index(drop=True)
    )

    reporte = ecos.merge(supra, on="ECO", how="left")
    reporte = reporte.merge(eden, on="ECO", how="left")
    reporte = reporte.merge(pase, on="ECO", how="left")

    if args.output.lower().endswith(".csv"):
        reporte.to_csv(args.output, index=False, encoding="utf-8-sig")
    else:
        reporte.to_excel(args.output, index=False)

    print(f"✅ Reporte generado: {args.output}")
    if pase_card_col is None:
        print("⚠️ Pase no incluyó una columna de tarjeta en el archivo de entrada; la columna 'Pase' quedó vacía.")


if __name__ == "__main__":
    main()
