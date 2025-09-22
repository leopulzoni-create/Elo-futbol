# auth.py
def _to_bool(val):
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1","true","t","yes","y","si","sí")

def verify_user(username, password):
    from db import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password, rol, is_admin FROM usuarios WHERE username = ? LIMIT 1",
            (username,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        try:
            row = dict(row)                  # sqlite3.Row
        except Exception:
            row = dict(zip(cols, row))       # libsql (tupla)

    # TODO: validar contraseña según tu lógica actual
    # if row.get("password") != password:
    #     return None

    # Normalización: que is_admin mande
    is_admin_bool = _to_bool(row.get("is_admin")) or (str(row.get("rol") or "").strip().lower() == "admin")
    rol = "admin" if is_admin_bool else "jugador"

    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": 1 if is_admin_bool else 0,
        "rol": rol,
    }
