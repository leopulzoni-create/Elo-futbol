# db.py
import os
import sqlite3

def _get_secret(name: str):
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None

def get_connection():
    libsql_url   = _get_secret("LIBSQL_URL")   or _get_secret("TURSO_DATABASE_URL")
    libsql_token = _get_secret("LIBSQL_AUTH_TOKEN") or _get_secret("TURSO_AUTH_TOKEN")

    if libsql_url:
        import libsql  # pip install libsql
        return libsql.connect(
            "replica.db",              # réplica local (rápida) – efímera en la nube, no pasa nada
            sync_url=libsql_url,
            auth_token=libsql_token
        )

    # Fallback local: SQLite
    conn = sqlite3.connect("elo_futbol.db")
    conn.row_factory = sqlite3.Row
    return conn


