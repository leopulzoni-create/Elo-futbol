# auth.py — verificación de usuario con contraseña (hash o texto plano)
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, Optional


def _hash_password_fallback(pwd: str) -> str:
    """
    Fallback si no existe usuarios.hash_password: SHA-256 simple.
    """
    return hashlib.sha256(pwd.encode("utf-8")).hexdigest()


def _row_to_dict(cur, row) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)  # sqlite3.Row
    except Exception:
        cols = [d[0] for d in cur.description] if cur.description else []
        return {cols[i]: row[i] for i in range(len(cols))}


def verify_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve un dict normalizado del usuario si (y sólo si) la contraseña es válida.
    En caso contrario, devuelve None.
    """
    if not username or not password:
        return None

    from db import get_connection

    with get_connection() as conn:
        cur = conn.cursor()

        # Traigo TODO el registro para no depender de columnas fijas
        cur.execute("SELECT * FROM usuarios WHERE username = ? LIMIT 1", (username,))
        row = _row_to_dict(cur, cur.fetchone())
        if not row:
            return None

        # ¿Qué campo de contraseña hay?
        pwd_field = None
        hash_mode = False
        for f in ("password_hash", "password", "pwd"):
            if f in row:
                pwd_field = f
                hash_mode = (f == "password_hash")
                break

        # Si no hay columna de contraseña o está vacía -> NO autenticar
        if not pwd_field:
            return None
        stored = row.get(pwd_field)
        if stored is None or str(stored).strip() == "":
            return None

        # Comparación según modo
        ok = False
        try:
            if hash_mode:
                # Intentar usar el hash del proyecto si existe; si no, SHA-256
                try:
                    from usuarios import hash_password as project_hash
                    candidate = project_hash(password)
                except Exception:
                    candidate = _hash_password_fallback(password)
                ok = hmac.compare_digest(str(stored), str(candidate))
            else:
                # Texto plano (sólo por retro-compatibilidad)
                ok = hmac.compare_digest(str(stored), str(password))
        except Exception:
            ok = False

        if not ok:
            return None

        # Normalización de rol/is_admin
        is_admin_raw = row.get("is_admin")
        rol = (row.get("rol") or "").strip().lower()
        if not rol:
            # si no hay rol pero is_admin está seteado, lo derivamos
            is_admin_bool = False
            if is_admin_raw is not None:
                is_admin_bool = str(is_admin_raw).strip().lower() in ("1", "true", "t", "yes")
            rol = "admin" if is_admin_bool else "jugador"

        # Devuelvo un dict minimal y consistente
        return {
            "id": row.get("id"),
            "username": row.get("username"),
            "rol": rol,
            "is_admin": 1 if rol == "admin" else 0,
            "jugador_id": row.get("jugador_id"),
        }
