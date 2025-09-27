# auth.py
from typing import Optional, Dict, Any
from passlib.hash import pbkdf2_sha256, bcrypt
from db import get_connection
import sqlite3

# --- util ---
def _row_to_dict(cur, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        return dict(row)  # sqlite3.Row
    except Exception:
        cols = [d[0] for d in cur.description] if cur.description else []
        return {cols[i]: row[i] for i in range(len(cols))}

# --- API pública que usa el resto de la app ---

def hash_password(plain: str) -> str:
    """Genera hash pbkdf2_sha256 (estándar actual del proyecto)."""
    return pbkdf2_sha256.hash((plain or "").strip())

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = ? LIMIT 1", (username,))
        return _row_to_dict(cur, cur.fetchone())

def _extract_stored_password(u: Dict[str, Any]) -> Optional[str]:
    """
    Devuelve el valor de contraseña almacenada intentando en este orden:
    password_hash, password, pwd. Si ninguna existe o está vacía -> None.
    """
    if not u:
        return None
    for k in ("password_hash", "password", "pwd"):
        val = u.get(k)
        if val is not None and str(val).strip() != "":
            return str(val)
    return None

def verify_password(plain: str, stored: str) -> bool:
    """
    Verifica contra múltiples formatos:
      - $pbkdf2-sha256$...  -> passlib.pbkdf2_sha256
      - $2a$ / $2b$ / $2y$  -> passlib.bcrypt
      - sin prefijo         -> comparación en claro (legacy)
    """
    if stored is None:
        return False
    s = str(stored)
    p = (plain or "")
    try:
        if s.startswith("$pbkdf2-sha256$"):
            return pbkdf2_sha256.verify(p, s)
        if s.startswith("$2a$") or s.startswith("$2b$") or s.startswith("$2y$"):
            return bcrypt.verify(p, s)
        # Último recurso: texto plano legado
        return p == s
    except Exception:
        # si algo falla al verificar el hash, tratamos como inválido
        return False

def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve el usuario (dict) si las credenciales son válidas; si no, None.
    """
    u = get_user_by_username(username)
    if not u:
        return None
    stored = _extract_stored_password(u)
    if not stored:
        return None
    return u if verify_password(password, stored) else None
