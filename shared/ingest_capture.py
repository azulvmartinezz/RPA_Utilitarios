import threading

from bigquery import bq_ingestion


_lock = threading.Lock()
_captured_dfs = []
_original_ingest = None


def install_ingest_capture(forward_to_original: bool = False) -> None:
    global _original_ingest

    if _original_ingest is not None:
        return

    _original_ingest = bq_ingestion.ingest_to_bigquery

    def captured_ingest(df, project_id=None):
        if df is not None and len(df) > 0:
            with _lock:
                _captured_dfs.append(df.copy())
        if forward_to_original:
            return _original_ingest(df, project_id)
        print("📦 Ingesta remota omitida: datos capturados para consolidacion local.")
        return None

    bq_ingestion.ingest_to_bigquery = captured_ingest


def clear_captured_data() -> None:
    with _lock:
        _captured_dfs.clear()


def get_captured_data():
    with _lock:
        return list(_captured_dfs)
