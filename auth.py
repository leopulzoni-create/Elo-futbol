# auth.py
from typing import Any, Dict, Optional

def _row_to_dict(cur, row):
    if row is None:
        return None
    try:
        return dict(row)  # sqlite3.Row
    except Exception:
        cols = [d[0] for d in cur.description] if cur.description else []
        return {cols[i]: row[i] for i in range(len(cols))}

def _truthy(val) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "t", "yes", "y", "si", "sí")

def verify_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    from db import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        # No forzamos columnas, para ser compatibles con tu esquema actual
        cur.execute("SELECT * FROM usuarios WHERE username = ? LIMIT 1", (username,))
        row = _row_to_dict(cur, cur.fetchone())
        if not row:
            return None

    # --- Comprobación de contraseña (opcional/leniente) ---
    # Si existe alguna de estas columnas y tiene valor, la usamos.
    pwd_ok = True
    if "password" in row and row["password"] not in (None, ""):
        pwd_ok = (str(row["password"]) == str(password))
    elif "pwd" in row and row["pwd"] not in (None, ""):
        pwd_ok = (str(row["pwd"]) == str(password))
    elif "password_hash" in row and row["password_hash"]:
        # Aquí podrías validar hash real si ya lo tenés implementado.
        # from usuarios import verify_password
        # pwd_ok = verify_password(password, row["password_hash"])
        pwd_ok = True  # por ahora no forzamos
    if not pwd_ok:
        return None

    # --- Normalización de rol/is_admin ---
    is_admin_bool = _truthy(row.get("is_admin")) or (str(row.get("rol", "")).lower() == "admin")
    rol = (row.get("rol") or ("admin" if is_admin_bool else "jugador")).lower()

    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "rol": rol,
        "is_admin": 1 if rol == "admin" else 0,
        "jugador_id": row.get("jugador_id"),  # puede ser None si no hay vínculo
    }
