from db import get_connection
# jugador_panel.py
import streamlit as st
import sqlite3
import scheduler
from datetime import date, datetime
import pytz

# Deep-links
from urllib.parse import urlencode
from remember import set_url_page, current_token_in_url, current_page_in_url

DB_NAME = "elo_futbol.db"
CUPO_PARTIDO = 10
CUPO_ESPERA = 4
TZ_AR = pytz.timezone("America/Argentina/Buenos_Aires")


def get_connection():
    from db import get_connection as _gc
    return _gc()


def _now_ar_str():
    return datetime.now(TZ_AR).strftime("%Y-%m-%d %H:%M:%S")


# ---------- Flash ----------
def _ensure_flash_store():
    if "flash" not in st.session_state:
        st.session_state["flash"] = []


def _push_flash(msg, level="info"):
    _ensure_flash_store()
    st.session_state["flash"].append((level, msg))


def _render_flash():
    _ensure_flash_store()
    if st.session_state["flash"]:
        for level, msg in st.session_state["flash"]:
            {"success": st.success, "warning": st.warning,
             "error": st.error}.get(level, st.info)(msg)
        st.session_state["flash"].clear()


# ---------- Utils ----------
def _row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
        return dict(row)
    except Exception:
        return row


def _rows_to_dicts(rows):
    return [_row_to_dict(r) for r in rows] if rows else []


def time_label_from_int(hhmm_int):
    if hhmm_int is None:
        return "Sin hora"
    hh = int(hhmm_int) // 100
    mm = int(hhmm_int) % 100
    return f"{hh:02d}:{mm:02d}"


_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _weekday_es(yyyy_mm_dd: str) -> str:
    try:
        dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return _DIAS_ES[dt.weekday()]
    except Exception:
        return ""


def _format_fecha_ddmmyyyy(yyyy_mm_dd: str) -> str:
    try:
        dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return yyyy_mm_dd


def _cancha_label(cancha_id):
    if cancha_id is None:
        return "Sin asignar"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT nombre, direccion FROM canchas WHERE id = ?", (cancha_id,))
        row = cur.fetchone()
        if not row:
            return "Sin asignar"
        nombre = row["nombre"] or "Sin asignar"
        direccion = (row["direccion"] or "").strip()
        return f"{nombre} ({direccion})" if direccion else nombre


# ---------- Lista de espera ----------
def _waitlist_get(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT le.partido_id, le.jugador_id, le.created_at, j.nombre
            FROM lista_espera le
            JOIN jugadores j ON j.id = le.jugador_id
            WHERE le.partido_id = ?
            ORDER BY le.created_at ASC
        """, (partido_id,))
        return _rows_to_dicts(cur.fetchall())


def _waitlist_count(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM lista_espera WHERE partido_id=?", (partido_id,))
        r = _row_to_dict(cur.fetchone())
        return r["c"] if r else 0


def _waitlist_is_in(partido_id, jugador_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM lista_espera WHERE partido_id=? AND jugador_id=? LIMIT 1",
                    (partido_id, jugador_id))
        return cur.fetchone() is not None


def _waitlist_join(partido_id, jugador_id):
    if _waitlist_is_in(partido_id, jugador_id):
        return False, "Ya estabas en la lista de espera."
    if _waitlist_count(partido_id) >= CUPO_ESPERA:
        return False, "La lista de espera está completa."
    ts = datetime.now(TZ_AR).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO lista_espera (partido_id, jugador_id, created_at) VALUES (?,?,?)",
                    (partido_id, jugador_id, ts))
        conn.commit()
    return True, "Te anotaste en la lista de espera 🕒"


def _waitlist_leave(partido_id, jugador_id):
    if not _waitlist_is_in(partido_id, jugador_id):
        return False, "No estabas en la lista de espera."
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM lista_espera WHERE partido_id=? AND jugador_id=?",
                    (partido_id, jugador_id))
        conn.commit()
    return True, "Saliste de la lista de espera."


# ---------- Roster / equipos ----------
def _jugadores_en_partido(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                pj.jugador_id,
                pj.confirmado_por_jugador,
                pj.equipo,
                pj.ingreso_desde_espera,
                pj.camiseta,
                j.nombre
            FROM partido_jugadores pj
            JOIN jugadores j ON j.id = pj.jugador_id
            WHERE pj.partido_id = ?
            ORDER BY j.nombre ASC
        """, (partido_id,))
        return _rows_to_dicts(cur.fetchall())


def _roster_count(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM partido_jugadores WHERE partido_id=?", (partido_id,))
        r = _row_to_dict(cur.fetchone())
        return r["c"] if r else 0


def _equipos_estan_generados(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
              SUM(CASE WHEN CAST(equipo AS INTEGER) IN (1,2) THEN 1 ELSE 0 END) AS con_eq,
              COUNT(*) AS total
            FROM partido_jugadores
            WHERE partido_id = ?
        """, (partido_id,))
        r = cur.fetchone()
        con_eq = int((r["con_eq"] if r and r["con_eq"] is not None else 0))
        total = int((r["total"] if r and r["total"] is not None else 0))
        return (total == CUPO_PARTIDO) and (con_eq == CUPO_PARTIDO)


def _reset_equipos(partido_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE partido_jugadores SET equipo = NULL WHERE partido_id = ?", (partido_id,))
        conn.commit()


# ---------- Renderer de equipos con camisetas ----------
def _render_equipos(partido_id, inscritos):
    def _eq_num(x):
        try:
            return int(x) if x is not None else None
        except Exception:
            return None

    def _team_color_info(jug_list):
        if not jug_list:
            return "⬜", "clara"
        osc = sum(1 for j in jug_list if (j.get("camiseta") or "").lower() == "oscura")
        cla = sum(1 for j in jug_list if (j.get("camiseta") or "").lower() == "clara")
        if osc > cla:
            return "⬛", "oscura"
        if cla > osc:
            return "⬜", "clara"
        for j in jug_list:
            c = (j.get("camiseta") or "").lower()
            if c == "oscura":
                return "⬛", "oscura"
            if c == "clara":
                return "⬜", "clara"
        return "⬜", "clara"

    eq1 = [j for j in inscritos if _eq_num(j.get("equipo")) == 1]
    eq2 = [j for j in inscritos if _eq_num(j.get("equipo")) == 2]

    icon1, lab1 = _team_color_info(eq1)
    icon2, lab2 = _team_color_info(eq2)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{icon1} Equipo 1  (Camiseta {lab1})**")
        for j in eq1:
            extra = " (WL)" if j.get("ingreso_desde_espera") else ""
            st.write(f"{icon1}  {j.get('nombre','?')}{extra}")
    with c2:
        st.markdown(f"**{icon2} Equipo 2  (Camiseta {lab2})**")
        for j in eq2:
            extra = " (WL)" if j.get("ingreso_desde_espera") else ""
            st.write(f"{icon2}  {j.get('nombre','?')}{extra}")

    st.caption("⚠️ Si alguien se baja, los equipos se desarman automáticamente y el admin deberá regenerarlos.")


def _promote_from_waitlist_if_possible(partido_id):
    if _roster_count(partido_id) >= CUPO_PARTIDO:
        return False
    wl = _waitlist_get(partido_id)
    if not wl:
        return False
    first = wl[0]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador, camiseta, ingreso_desde_espera)
            VALUES (?, ?, 1, 'clara', 1)
        """, (partido_id, first["jugador_id"]))
        cur.execute("DELETE FROM lista_espera WHERE partido_id=? AND jugador_id=?",
                    (partido_id, first["jugador_id"]))
        conn.commit()
    return True


# ---------- Partidos visibles (robusto: sin grupos = visible a todos) ----------
def _detect_col(conn, table: str, candidates: list[str]) -> str:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = []
        for r in cur.fetchall():
            try:
                cols.append(r["name"])
            except Exception:
                cols.append(r[1])
        for c in candidates:
            if c in cols:
                return c
    except Exception:
        pass
    return candidates[0]


def _partidos_visibles_para_jugador(jugador_id: int):
    today_iso = date.today().isoformat()
    now_ar = _now_ar_str()

    with get_connection() as conn:
        cur = conn.cursor()

        jg_col = _detect_col(conn, "jugador_grupos", ["grupo_id", "group_id", "grupo"])
        pg_col = _detect_col(conn, "partido_grupos", ["grupo_id", "group_id", "grupo"])

        grupos_jugador = []
        try:
            cur.execute(f"SELECT {jg_col} FROM jugador_grupos WHERE jugador_id = ?", (jugador_id,))
            for r in cur.fetchall():
                try:
                    grupos_jugador.append(r[jg_col])
                except Exception:
                    grupos_jugador.append(r[0])
        except Exception:
            pass

        if not grupos_jugador:
            try:
                cur.execute("PRAGMA table_info(jugadores)")
                has_gcol = False
                for r in cur.fetchall():
                    try:
                        nm = r["name"]
                    except Exception:
                        nm = r[1]
                    if nm == "grupo_id":
                        has_gcol = True
                        break
                if has_gcol:
                    cur.execute("SELECT grupo_id FROM jugadores WHERE id = ?", (jugador_id,))
                    rr = cur.fetchone()
                    if rr:
                        try:
                            g = rr["grupo_id"]
                        except Exception:
                            g = rr[0]
                        if g is not None:
                            grupos_jugador = [g]
            except Exception:
                pass

        if grupos_jugador:
            placeholders = ",".join("?" * len(grupos_jugador))
            group_clause = f"""
              AND (
                    NOT EXISTS (SELECT 1 FROM partido_grupos pg WHERE pg.partido_id = p.id)
                 OR EXISTS (SELECT 1 FROM partido_grupos pg
                            WHERE pg.partido_id = p.id AND pg.{pg_col} IN ({placeholders}))
              )
            """
            group_params = tuple(int(x) for x in grupos_jugador)
        else:
            group_clause = """
              AND NOT EXISTS (SELECT 1 FROM partido_grupos pg WHERE pg.partido_id = p.id)
            """
            group_params = ()

        sql = f"""
            SELECT
              p.id, p.fecha, p.cancha_id, p.hora, p.tipo, p.ganador, p.diferencia_gol, p.publicar_desde
            FROM partidos p
            LEFT JOIN canchas c ON c.id = p.cancha_id
            WHERE substr(p.fecha, 1, 10) >= ?
              AND (p.tipo IS NULL OR p.tipo = 'abierto')
              AND p.ganador IS NULL
              AND (p.diferencia_gol IS NULL OR TRIM(p.diferencia_gol) = '')
              AND (p.publicar_desde IS NULL OR p.publicar_desde <= ?)
              {group_clause}
            ORDER BY datetime(p.fecha), p.id
        """
        params = [today_iso, now_ar] + list(group_params)
        cur.execute(sql, params)
        rows = cur.fetchall()

        cols = [d[0] for d in cur.description] if cur.description else []
        out = []
        for r in rows:
            try:
                out.append(dict(r))
            except Exception:
                out.append({cols[i]: r[i] for i in range(len(cols))})
        return out


# ---------- Helper de deep-link: construir URL con auth + page ----------
def _page_url(page: str) -> str:
    params = {"page": page}
    try:
        tok = current_token_in_url()
        if tok:
            params["auth"] = tok
    except Exception:
        pass
    return f"?{urlencode(params)}"


def _inject_link_styles():
    # Inserta CSS una sola vez por sesión para estilizar los <a> como botones Streamlit
    if not st.session_state.get("_btnlink_css_injected"):
        st.markdown("""
        <style>
        .btnlink {
            display: inline-flex;
            align-items: center;
            gap: .5rem;
            white-space: nowrap;
            padding: .55rem 1rem;
            border: 1px solid rgba(49,51,63,.35);
            border-radius: .75rem;
            font-weight: 600;
            text-decoration: none !important;
            color: inherit !important;          /* evita azul de los <a> */
            background: rgba(49,51,63,.20);     /* suave en tema oscuro */
            line-height: 1.2;
        }
        .btnlink:hover {
            border-color: rgba(49,51,63,.55);
            background: rgba(49,51,63,.30);
        }
        </style>
        """, unsafe_allow_html=True)
        st.session_state["_btnlink_css_injected"] = True


def _link_button(label: str, page: str):
    """Botón-link: navegación con recarga (misma pestaña) y estilo limpio."""
    _inject_link_styles()
    url = _page_url(page)
    st.markdown(
        f'<a class="btnlink" href="{url}" target="_self">{label}</a>',
        unsafe_allow_html=True,
    )


# ---------- Vistas públicas del jugador (menú / partidos / stats / perfil) ----------
def panel_menu_jugador(user):
    try:
        scheduler.run_programaciones_vencidas()
    except Exception:
        pass

    if "jugador_page" not in st.session_state:
        st.session_state["jugador_page"] = "menu"

    _render_flash()

    username = user.get("username") or "jugador"
    jugador_id = user.get("jugador_id")

    nombre_vinculado = None
    if jugador_id:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT nombre FROM jugadores WHERE id=?", (jugador_id,))
            r = _row_to_dict(cur.fetchone())
            nombre_vinculado = r["nombre"] if r else None

    st.header(f"Bienvenido, {nombre_vinculado or username} 👋")

    c1, c2, c3 = st.columns(3)
    with c1:
        _link_button("Ver partidos disponibles ⚽", "partidos")
    with c2:
        _link_button("Ver mis estadísticas 📊", "stats")
    with c3:
        _link_button("Ver mi perfil 👤", "perfil")


def panel_partidos_disponibles(user):
    _render_flash()

    jugador_id = user.get("jugador_id")
    if not jugador_id:
        st.warning("Tu usuario no está vinculado a ningún jugador. Pedile al admin que te vincule.")
        _link_button("⬅️ Volver", "menu")
        return

    st.subheader("Partidos disponibles")

    partidos = _partidos_visibles_para_jugador(jugador_id)
    if not partidos:
        st.info("No hay partidos disponibles para tu grupo por el momento.")
        _link_button("⬅️ Volver", "menu")
        return

    for p in partidos:
        partido_id = p["id"]
        fecha = p["fecha"]
        hora_lbl = time_label_from_int(p["hora"])
        cancha_name = _cancha_label(p["cancha_id"])
        inscritos = _jugadores_en_partido(partido_id)
        count = len(inscritos)
        wl = _waitlist_get(partido_id)
        wl_count = len(wl)

        yo_en_roster = any(j["jugador_id"] == jugador_id for j in inscritos)
        yo_en_espera = _waitlist_is_in(partido_id, jugador_id)

        badges = []
        if count >= CUPO_PARTIDO:
            badges.append("🧍‍🧍 Partido completo")
        if yo_en_roster:
            badges.append("✅ Confirmado")
        elif yo_en_espera:
            badges.append("🕒 En lista de espera")

        badge_txt = (" – " + " • ".join(badges)) if badges else ""

        fecha_es = _format_fecha_ddmmyyyy(fecha)
        dia_es = _weekday_es(fecha)
        titulo = f"{fecha_es} ({dia_es}) • {hora_lbl} hs • {cancha_name}{badge_txt}"

        with st.expander(titulo, expanded=False):
            if _equipos_estan_generados(partido_id):
                _render_equipos(partido_id, inscritos)
            else:
                st.write("### Inscripciones")
                if inscritos:
                    cols = st.columns(2)
                    for i, j in enumerate(inscritos):
                        mark = "🟢" if j["confirmado_por_jugador"] else "🔵"
                        extra = " (WL)" if j.get("ingreso_desde_espera") else ""
                        with cols[i % 2]:
                            st.write(f"{mark} {j['nombre']}{extra}")
                else:
                    st.write("_Aún no hay inscriptos._")

            st.write("---")
            c1, c2 = st.columns(2)
            with c1:
                can_confirm = (not yo_en_roster) and (count < CUPO_PARTIDO)
                if st.button("Confirmar asistencia", key=f"confirm_{partido_id}", disabled=not can_confirm):
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT OR IGNORE INTO partido_jugadores (partido_id, jugador_id, confirmado_por_jugador, camiseta, ingreso_desde_espera)
                            VALUES (?, ?, 1, 'clara', 0)
                        """, (partido_id, jugador_id))
                        conn.commit()
                    _push_flash("Confirmaste tu asistencia 🟢", "success")
                    st.rerun()

                can_join_wl = (not yo_en_roster) and (not yo_en_espera) and (count >= CUPO_PARTIDO) and (wl_count < CUPO_ESPERA)
                if st.button("Anotarme en lista de espera", key=f"join_wl_{partido_id}", disabled=not can_join_wl):
                    ok, msg = _waitlist_join(partido_id, jugador_id)
                    _push_flash(msg, "success" if ok else "warning")
                    st.rerun()

            with c2:
                if yo_en_roster:
                    if st.button("Cancelar asistencia", key=f"cancel_{partido_id}"):
                        with get_connection() as conn:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM partido_jugadores WHERE partido_id=? AND jugador_id=?",
                                        (partido_id, jugador_id))
                            conn.commit()
                        _reset_equipos(partido_id)
                        promoted = _promote_from_waitlist_if_possible(partido_id)
                        if promoted:
                            _push_flash("Cancelaste tu asistencia. Se promovió al primero de la lista de espera.", "info")
                        else:
                            _push_flash("Cancelaste tu asistencia.", "info")
                        st.rerun()

                if yo_en_espera:
                    if st.button("Salir de lista de espera", key=f"leave_wl_{partido_id}"):
                        ok, msg = _waitlist_leave(partido_id, jugador_id)
                        _push_flash(msg, "success" if ok else "warning")
                        st.rerun()

            st.write("---")
            st.write(f"**Lista de espera** ({wl_count}/{CUPO_ESPERA})")
            if wl:
                for i, w in enumerate(wl, start=1):
                    me = " ← vos" if w["jugador_id"] == jugador_id else ""
                    st.write(f"{i}. {w['nombre']}{me}")
            else:
                st.write("_Vacía_")

    st.divider()
    _link_button("⬅️ Volver", "menu")


def panel_mis_estadisticas(user):
    _render_flash()
    try:
        import jugador_stats
        jugador_stats.panel_mis_estadisticas(user)
    except Exception as e:
        st.subheader("Mis estadísticas")
        st.error("No se pudo cargar el módulo de estadísticas (jugador_stats.py).")
        st.exception(e)

    st.divider()
    # ÚNICO “Volver” (mismo estilo y comportamiento que el resto)
    _link_button("⬅️ Volver", "menu")



def panel_mi_perfil(user):
    import streamlit as st
    from db import get_connection
    import hashlib

    st.subheader("👤 Mi perfil")

    def _row_to_dict(cur, row):
        if row is None:
            return None
        try:
            return dict(row)  # sqlite3.Row
        except Exception:
            cols = [d[0] for d in cur.description] if cur.description else []
            return {cols[i]: row[i] for i in range(len(cols))}

    try:
        uid = user.get("id") if isinstance(user, dict) else None
    except Exception:
        uid = None
    if not uid:
        st.error("No se encontró el ID de tu usuario en sesión.")
        return

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute("SELECT * FROM usuarios WHERE id = ? LIMIT 1", (uid,))
        u = _row_to_dict(cur, cur.fetchone())
        if not u:
            st.error("No se encontró tu usuario.")
            return

        jugador_id = u.get("jugador_id")
        jugador_nombre = None
        if jugador_id:
            cur.execute("SELECT nombre FROM jugadores WHERE id = ?", (jugador_id,))
            j = _row_to_dict(cur, cur.fetchone())
            jugador_nombre = (j or {}).get("nombre")

        st.markdown("#### Nombre de jugador")
        if not jugador_id:
            st.info("Tu usuario no está vinculado a ningún jugador. Pedile al admin que te vincule para poder editar tu nombre visible.")
        else:
            nuevo_nombre = st.text_input(
                "Nombre visible en las planillas",
                value=jugador_nombre or "",
                key="perfil_nombre_visible",
            )
            if st.button("Guardar nombre", key="perfil_btn_guardar_nombre"):
                nombre_ok = (nuevo_nombre or "").strip()
                if not nombre_ok:
                    st.warning("Ingresá un nombre válido.")
                else:
                    cur.execute("UPDATE jugadores SET nombre = ? WHERE id = ?", (nombre_ok, jugador_id))
                    conn.commit()
                    st.success("Nombre actualizado.")
                    st.rerun()

        st.markdown("---")

        st.markdown("#### Cambiar contraseña")

        cur.execute("PRAGMA table_info(usuarios)")
        pragma_rows = cur.fetchall()
        colnames = []
        for r in pragma_rows:
            try:
                colnames.append(r["name"])
            except Exception:
                colnames.append(r[1])

        target_col = None
        hash_mode = False
        if "password_hash" in colnames:
            target_col = "password_hash"
            hash_mode = True
        elif "password" in colnames:
            target_col = "password"
        elif "pwd" in colnames:
            target_col = "pwd"

        if not target_col:
            st.caption("La tabla de usuarios no tiene columna de contraseña. El admin puede habilitarla desde el panel de usuarios.")
        else:
            pwd1 = st.text_input("Nueva contraseña", type="password", key="perfil_pwd1")
            pwd2 = st.text_input("Repetir contraseña", type="password", key="perfil_pwd2")
            if st.button("Guardar contraseña", key="perfil_btn_guardar_pwd"):
                if not pwd1:
                    st.warning("Ingresá una contraseña.")
                elif pwd1 != pwd2:
                    st.error("Las contraseñas no coinciden.")
                elif len(pwd1) < 4:
                    st.warning("Usá al menos 4 caracteres.")
                else:
                    value = pwd1
                    if hash_mode:
                        try:
                            from usuarios import hash_password
                            value = hash_password(pwd1)
                        except Exception:
                            value = hashlib.sha256(pwd1.encode("utf-8")).hexdigest()

                    cur.execute(f"UPDATE usuarios SET {target_col} = ? WHERE id = ?", (value, uid))
                    conn.commit()
                    st.success("Contraseña actualizada.")
                    st.rerun()

    st.divider()
    _link_button("⬅️ Volver", "menu")
