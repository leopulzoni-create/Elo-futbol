# auth.py
from typing import Optional, Dict, Any
from passlib.hash import pbkdf2_sha256, bcrypt
import os

try:
    import streamlit as st
except Exception:
    st = None  # permite usar fuera de Streamlit (tests)

# usamos el conector central del proyecto
from db import get_connection


def _pepper() -> str:
    """Pepper opcional para endurecer el hash (no se guarda en la DB)."""
    if st is not None:
        try:
            val = st.secrets.get("PEPPER")
            if val:
                return str(val)
        except Exception:
            pass
    return os.getenv("PEPPER", "") or ""


def _with_pepper(plain: str) -> str:
    return (plain or "") + _pepper()


def hash_password(plain: str) -> str:
    """Para crear/rotar contraseñas nuevas con PBKDF2 (recomendado)."""
    return pbkdf2_sha256.hash(_with_pepper(plain or ""))


def _verify_against_hash(plain: str, stored_hash: str) -> bool:
    """Acepta PBKDF2 o bcrypt; fallback a comparación directa (legacy)."""
    s = str(stored_hash or "")
    p = _with_pepper(plain or "")
    # PBKDF2
    if s.startswith("$pbkdf2-sha256$"):
        try:
            return pbkdf2_sha256.verify(p, s)
        except Exception:
            return False
    # bcrypt ($2a/$2b/$2y)
    if s.startswith("$2a$") or s.startswith("$2b$") or s.startswith("$2y$"):
        try:
            return bcrypt.verify(p, s)
        except Exception:
            return False
    # legacy (no recomendado)
    return p == s


def _row_to_dict(cur, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        return dict(row)  # sqlite3.Row
    except Exception:
        cols = [d[0] for d in cur.description] if cur and cur.description else []
        return {cols[i]: row[i] for i in range(len(cols))}


def verify_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve el usuario (dict con al menos id, username, rol, jugador_id)
    si las credenciales son válidas; si no, devuelve None.
    """
    if not username:
        return None

    with get_connection() as conn:
        cur = conn.cursor()

        # Traer columnas de usuarios para detectar password_hash vs password
        cur.execute("PRAGMA table_info(usuarios)")
        cols = [ (r["name"] if isinstance(r, dict) or hasattr(r, "keys") else r[1]) for r in cur.fetchall() ]
        has_pwd_hash = "password_hash" in cols
        has_pwd_plain = "password" in cols

        # Buscar por username
        cur.execute("SELECT * FROM usuarios WHERE username = ? LIMIT 1", (username,))
        row = cur.fetchone()
        user = _row_to_dict(cur, row)
        if not user:
            return None

        # Elegir fuente del hash/contraseña
        stored = None
        if has_pwd_hash:
            stored = user.get("password_hash")
        elif has_pwd_plain:
            stored = user.get("password")
        else:
            # no hay ninguna columna de password
            return None

        if not _verify_against_hash(password or "", stored or ""):
            return None

        # Filtrar/normalizar campos mínimos esperados por el resto de la app
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "rol": user.get("rol"),
            "jugador_id": user.get("jugador_id"),
            # podés agregar más campos si los usa tu UI
        }
