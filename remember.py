from db import get_connection
# remember.py
import sqlite3, secrets, hashlib
import streamlit as st
from datetime import datetime, timedelta
import extra_streamlit_components as stx

DB_NAME = "elo_futbol.db"


def _conn():
    from db import get_connection as _gc
    return _gc()


def ensure_tables():
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS login_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _in_30_days_iso():
    return (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_token(user_id: int) -> str:
    """Crea un token nuevo, lo guarda hasheado en la tabla y devuelve el token plano."""
    token = secrets.token_urlsafe(32)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO login_tokens (user_id, token_hash, expires_at) VALUES (?,?,?)",
            (user_id, _hash(token), _in_30_days_iso()),
        )
        conn.commit()
    return token


def revoke_token(token: str):
    """Elimina el token (por logout manual)."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM login_tokens WHERE token_hash = ?",
            (_hash(token),),
        )
        conn.commit()


def validate_token(token: str):
    """Devuelve el dict de usuario si el token es válido y no expiró, si no, None."""
    if not token:
        return None

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, expires_at FROM login_tokens WHERE token_hash = ?",
            (_hash(token),),
        )
        row = cur.fetchone()
        if not row:
            return None

        # Chequeo de expiración
        try:
            if datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ") < datetime.utcnow():
                cur.execute(
                    "DELETE FROM login_tokens WHERE token_hash = ?",
                    (_hash(token),),
                )
                conn.commit()
                return None
        except Exception:
            return None

        # Traigo el usuario
        cur.execute(
            "SELECT id, username, rol, jugador_id FROM usuarios WHERE id = ?",
            (row["user_id"],),
        )
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


# Navegación por URL (sección del panel jugador)
def current_page_in_url(default: str = "menu") -> str:
    return st.query_params.get("page", default) or default


def set_url_page(page: str):
    st.query_params["page"] = page


# ================================
#  Remember-me también en cookie
# ================================
COOKIE_NAME = "auth_token"


@st.fragment
def _get_cookie_manager():
    """
    Fragmento que renderiza el componente CookieManager y lo devuelve.
    Usar siempre este helper para get/set/delete cookies.
    """
    return stx.CookieManager()


def get_token_from_cookie() -> str:
    """Devuelve el token guardado en cookie (o "" si no hay nada)."""
    try:
        cookie_manager = _get_cookie_manager()
        cookies = cookie_manager.get_all() or {}
        return cookies.get(COOKIE_NAME, "") or ""
    except Exception:
        return ""


def set_token_cookie(token: str):
    """Guarda el token en una cookie del navegador."""
    try:
        cookie_manager = _get_cookie_manager()
        cookie_manager.set(COOKIE_NAME, token)  # expira en ~1 día por defecto
    except Exception:
        pass


def clear_token_cookie():
    """Borra la cookie de token."""
    try:
        cookie_manager = _get_cookie_manager()
        cookie_manager.delete(COOKIE_NAME)
    except Exception:
        pass
