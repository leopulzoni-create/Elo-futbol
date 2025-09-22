# db.py (reemplazo de get_connection + helpers)

import os, sqlite3

def _get_secret(name: str):
    v = os.getenv(name)
    if v: return v
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None

# --- NUEVO: filas dict-like que también aceptan índice numérico ---
class RowLike(dict):
    __slots__ = ("_values",)
    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._values = tuple(values)
    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

def _to_rowlike(cur, row):
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    # sqlite3.Row o dict ya mapeable
    try:
        d = dict(row)
        # asegurar orden de columnas para índice numérico
        vals = [d.get(c) for c in cols]
        return RowLike(cols, vals)
    except Exception:
        # tupla: mapear por descripción
        return RowLike(cols, row)

def _to_rowlikes(cur, rows):
    cols = [d[0] for d in cur.description]
    out = []
    for r in rows:
        try:
            d = dict(r)
            vals = [d.get(c) for c in cols]
            out.append(RowLike(cols, vals))
        except Exception:
            out.append(RowLike(cols, r))
    return out

class DictCursor:
    def __init__(self, inner):
        self._cur = inner
    # delegación
    def __getattr__(self, name):
        return getattr(self._cur, name)
    # envoltorio de fetch*
    def fetchone(self):
        return _to_rowlike(self._cur, self._cur.fetchone())
    def fetchall(self):
        return _to_rowlikes(self._cur, self._cur.fetchall())

class ConnProxy:
    def __init__(self, inner):
        self._conn = inner
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def cursor(self, *a, **k):
        return DictCursor(self._conn.cursor(*a, **k))
    # compatibilidad con with get_connection() as conn:
    def __enter__(self):
        self._conn.__enter__() if hasattr(self._conn, "__enter__") else None
        return self
    def __exit__(self, *exc):
        return self._conn.__exit__(*exc) if hasattr(self._conn, "__exit__") else self._conn.close()

def get_connection():
    libsql_url   = _get_secret("LIBSQL_URL") or _get_secret("TURSO_DATABASE_URL")
    libsql_token = _get_secret("LIBSQL_AUTH_TOKEN") or _get_secret("TURSO_AUTH_TOKEN")

    if libsql_url:
        libsql_url = str(libsql_url).strip().strip("'\"")
        if not libsql_url.startswith("libsql://"):
            raise RuntimeError(f"LIBSQL_URL inválida: {libsql_url!r}. Debe empezar con 'libsql://'.")
        import libsql
        base = libsql.connect("replica.db", sync_url=libsql_url, auth_token=libsql_token)
        return ConnProxy(base)

    # Fallback local (SQLite)
    for candidate in ("elo_futbol.db", "elo-futbol.db"):
        if os.path.exists(candidate):
            conn = sqlite3.connect(candidate)
            conn.row_factory = sqlite3.Row  # por si corrés local
            return ConnProxy(conn)

    raise RuntimeError("No hay LIBSQL_URL y no se encontró base local (elo_futbol.db / elo-futbol.db).")
