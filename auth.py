# auth.py
def verify_user(username, password):
    from db import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM usuarios WHERE username = ? LIMIT 1",
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

    # Normalización de claves:
    is_admin = row.get("is_admin")
    # Acepta 1/"1"/True
    is_admin_bool = str(is_admin).lower() in ("1", "true", "t", "yes") if is_admin is not None else False
    rol = (row.get("rol") or ("admin" if is_admin_bool else "jugador")).lower()

    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": 1 if rol == "admin" else 0,
        "rol": rol
    }
