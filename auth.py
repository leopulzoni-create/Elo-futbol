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
        if row:
            cols = [d[0] for d in cur.description]
            try:
                row = dict(row)          # sqlite3.Row
            except Exception:
                row = dict(zip(cols, row))  # libsql (tupla)
    if not row:
        return None

    # TODO: valida contraseña como la tengas implementada
    return {"id": row["id"], "username": row["username"], "is_admin": row.get("is_admin", 0)}
