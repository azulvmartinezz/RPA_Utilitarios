import argparse
import os
import re
import pandas as pd
from bs4 import BeautifulSoup


def _normalize_eco(val):
    s = str(val).strip().upper().replace(" ", "").replace(".", "")
    m = re.match(r"^(AU|CA)-?(\d{1,3})(?!\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


def _normalize_card(val):
    if pd.isna(val):
        return None
    text = str(val).strip().rstrip('.')
    if not text or text.lower() == "nan":
        return None

    try:
        num = float(text.replace(",", ""))
        if num.is_integer():
            return str(int(num))
    except Exception:
        pass

    return text


def _get_html_pase_map():
    html_map = {}
    
    # 1. Intentar leer desde el archivo JSON de tags mapeados por el scraper
    json_path = "HTML_PASE/tags_mapeados.json"
    if os.path.exists(json_path):
        try:
            import json
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    for k, v in data.items():
                        html_map[k] = v
        except Exception:
            pass

    # 2. Intentar leer desde el HTML manual anterior (como fallback)
    html_path = "HTML_PASE/pestaña_tags.html"
    if os.path.exists(html_path):
        try:
            with open(html_path, "r", encoding="utf-8") as fh:
                soup = BeautifulSoup(fh.read(), "html.parser")
            for row_div in soup.find_all("div"):
                link = row_div.find("a", href=re.compile(r"/uc/detalletag/"))
                if link:
                    tag_text = link.text.strip().replace("\n", "").replace(" ", "").rstrip(".")
                    for p in row_div.find_all("p"):
                        m = re.search(r"^(AU|CA)-\d{3}", p.text.strip())
                        if m:
                            eco = m.group(0)
                            if eco not in html_map:
                                html_map[eco] = tag_text
                            break
        except Exception:
            pass

    return html_map


def _get_supramax_tags_map():
    supra_map = {}
    json_path = "HTML_SUPRAMAX/tags_mapeados.json"
    if os.path.exists(json_path):
        try:
            import json
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    for k, v in data.items():
                        supra_map[k] = v
        except Exception:
            pass
    return supra_map


def _get_master_map():
    master_map = {}
    path = "tablamaestra.csv"
    if not os.path.exists(path):
        return master_map
    try:
        df_tm = pd.read_csv(path, sep="\t").fillna("")
        for _, row in df_tm.iterrows():
            eco = _normalize_eco(row.get("ECO", ""))
            if not eco or not re.match(r"^(AU|CA)-\d{3}$", eco):
                continue
            master_map[eco] = {
                "Supramax": str(row.get("Supramax ID", "")).strip().replace(" ", ""),
                "Ticket Card": str(row.get("Ticket Card ID", "")).strip().replace(" ", ""),
                "Pase": str(row.get("IAVE Pase ID", "")).strip().replace(" ", "").rstrip("."),
            }
    except Exception:
        pass
    return master_map


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


def _build_system_map(df, eco_col, card_col, output_col, date_col=None):
    if eco_col not in df.columns or card_col not in df.columns:
        return pd.DataFrame(columns=["ECO", output_col])

    cols = [eco_col, card_col]
    if date_col and date_col in df.columns:
        cols.append(date_col)

    work = df[cols].copy()
    work["ECO"] = work[eco_col].apply(_normalize_eco)
    work[output_col] = work[card_col].apply(_normalize_card)
    work = work.dropna(subset=["ECO", output_col])
    work = work[work["ECO"].str.match(r"^(AU|CA)-\d{3}$", na=False)]

    if date_col and date_col in work.columns:
        work["_temp_date"] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values("_temp_date", ascending=True)

    return (
        work.groupby("ECO", as_index=False)[output_col]
        .last()
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

    supra = _build_system_map(df_supramax, "PLACAS", "IDENTIFICADOR", "Supramax", "FECHA")
    eden = _build_system_map(df_edenred, "Vehículo", "Núm Tarjeta", "Ticket Card", "Fecha Transacción")

    pase_card_col = next((c for c in df_pase.columns if str(c).strip().lower() == "tarjeta idmx"), None)
    if not pase_card_col:
        pase_card_col = next((c for c in df_pase.columns if "tarjeta" in str(c).strip().lower()), None)
    pase_eco_col = next((c for c in df_pase.columns if str(c).strip().upper() == "ECO"), None)
    pase = _build_system_map(df_pase, pase_eco_col or "ECO", pase_card_col or "__missing__", "Pase", "Fecha")

    active_ecos = []
    if os.path.exists("Activas.txt"):
        with open("Activas.txt", "r") as fh:
            active_ecos = [line.strip() for line in fh if line.strip()]
        active_ecos = [_normalize_eco(e) for e in active_ecos]

    ecos_list = list(set(
        supra["ECO"].tolist() +
        eden["ECO"].tolist() +
        pase["ECO"].tolist() +
        active_ecos
    ))
    ecos = pd.DataFrame({"ECO": ecos_list}).drop_duplicates().sort_values("ECO").reset_index(drop=True)

    reporte = ecos.merge(supra, on="ECO", how="left")
    reporte = reporte.merge(eden, on="ECO", how="left")
    reporte = reporte.merge(pase, on="ECO", how="left")

    # Cargar mapas de fallback (HTML y Tabla Maestra)
    html_pase_map = _get_html_pase_map()
    supramax_tags_map = _get_supramax_tags_map()
    master_map = _get_master_map()

    # Rellenar tarjetas vacías usando los fallbacks
    for idx, row in reporte.iterrows():
        eco = row["ECO"]
        # Fallback para Pase
        if pd.isna(row["Pase"]) or str(row["Pase"]).strip() == "" or str(row["Pase"]).lower() == "nan":
            val = html_pase_map.get(eco)
            if not val and eco in master_map:
                val = master_map[eco].get("Pase")
            if val:
                reporte.at[idx, "Pase"] = val

        # Fallback para Supramax
        if pd.isna(row["Supramax"]) or str(row["Supramax"]).strip() == "" or str(row["Supramax"]).lower() == "nan":
            val = supramax_tags_map.get(eco)
            if not val and eco in master_map:
                val = master_map[eco].get("Supramax")
            if val:
                reporte.at[idx, "Supramax"] = val

        # Fallback para Ticket Card
        if pd.isna(row["Ticket Card"]) or str(row["Ticket Card"]).strip() == "" or str(row["Ticket Card"]).lower() == "nan":
            if eco in master_map:
                val = master_map[eco].get("Ticket Card")
                if val:
                    reporte.at[idx, "Ticket Card"] = val

    # Limpiar formatos de float (.0) causados por los NaN del merge
    for col in ["Supramax", "Ticket Card", "Pase"]:
        if col in reporte.columns:
            reporte[col] = (
                reporte[col]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
                .replace("nan", "")
                .str.strip()
            )

    if args.output.lower().endswith(".csv"):
        reporte.to_csv(args.output, index=False, encoding="utf-8-sig")
    else:
        reporte.to_excel(args.output, index=False)

    print(f"✅ Reporte generado: {args.output}")
    if pase_card_col is None:
        print("⚠️ Pase no incluyó una columna de tarjeta en el archivo de entrada; la columna 'Pase' quedó vacía.")


if __name__ == "__main__":
    main()
