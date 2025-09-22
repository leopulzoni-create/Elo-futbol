# remember.py
import sqlite3, secrets, hashlib
import streamlit as st
from datetime import datetime, timedelta

DB_NAME = "elo_futbol.db"

def _conn():
    c = sqlite3.connect(DB_NAME)
    c.row_factory = sqlite3.Row
    return c

def ensure_tables():
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS login_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """)
        conn.commit()

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _in_30_days_iso():
    return (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

def issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO login_tokens (user_id, token_hash, expires_at) VALUES (?,?,?)",
            (user_id, _hash(token), _in_30_days_iso())
        )
        conn.commit()
    return token

def revoke_token(token: str):
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM login_tokens WHERE token_hash = ?", (_hash(token),))
        conn.commit()

def validate_token(token: str):
    if not token:
        return None
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, expires_at FROM login_tokens WHERE token_hash = ?", (_hash(token),))
        row = cur.fetchone()
        if not row:
            return None
        try:
            if datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ") < datetime.utcnow():
                cur.execute("DELETE FROM login_tokens WHERE token_hash = ?", (_hash(token),))
                conn.commit()
                return None
        except Exception:
            return None
        cur.execute("SELECT id, username, rol, jugador_id FROM usuarios WHERE id = ?", (row["user_id"],))
        u = cur.fetchone()
        if not u:
            return None
        return {k: u[k] for k in u.keys()}

# -------- URL helpers (con st.query_params) --------
def current_token_in_url() -> str:
    return st.query_params.get("auth", "")

def set_url_token(token: str):
    st.query_params["auth"] = token

def clear_url_token():
    if "auth" in st.query_params:
        del st.query_params["auth"]
