# auth.py
def verify_user(username, password):
    from db import get_connection                         # ← NUEVO
    with get_connection() as conn:                        # ← CAMBIO CLAVE
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM usuarios WHERE username = ? LIMIT 1",
            (username,)
        )
        row = cur.fetchone()
        # Convertimos la fila a dict aunque el driver devuelva tupla
        if row:
            cols = [d[0] for d in cur.description]
            try:
                # Si es tipo sqlite3.Row, esto ya devuelve dict
                row = dict(row)
            except Exception:
                # Si es tupla (libsql), mapeamos con los nombres
                row = dict(zip(cols, row))

    if not row:
        return None

    # A partir de acá tu lógica existente (ej. validar password)
    # Ejemplo (si usás texto plano):
    # if row.get("password") != password: return None
    # return {"id": row["id"], "username": row["username"], "is_admin": row.get("is_admin", 0)}
