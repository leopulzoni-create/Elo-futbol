# jugador_panel.py
from db import get_connection
import streamlit as st
import sqlite3
import scheduler
from datetime import date, datetime
import pytz
from pathlib import Path
import base64
from remember import current_token_in_url, revoke_token, clear_url_token

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

def _logout():
    tok = current_token_in_url()
    if tok:
        revoke_token(tok)
        clear_url_token()
    for k in list(st.session_state.keys()):
        if k in ("user", "admin_page", "jugador_page", "flash"):
            del st.session_state[k]
    st.rerun()

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


# ---------- Equipos UI ----------
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


# ---------- Helpers detección columnas ----------
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


# ---------- Partidos visibles ----------
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
                has_gcol = any((r["name"] if isinstance(r, sqlite3.Row) else r[1]) == "grupo_id" for r in cur.fetchall())
                if has_gcol:
                    cur.execute("SELECT grupo_id FROM jugadores WHERE id = ?", (jugador_id,))
                    rr = cur.fetchone()
                    if rr:
                        g = rr["grupo_id"] if isinstance(rr, sqlite3.Row) else rr[0]
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
            group_clause = "AND NOT EXISTS (SELECT 1 FROM partido_grupos pg WHERE pg.partido_id = p.id)"
            group_params = ()

        sql = f"""
            SELECT p.id, p.fecha, p.cancha_id, p.hora, p.tipo, p.ganador, p.diferencia_gol, p.publicar_desde
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


# ---------- UI helpers (logo + menú apilado) ----------
def _hero_logo():
    """Logo PNG blanco centrado (robusto) usando HTML + flex."""
    logo_path = Path(__file__).with_name("assets").joinpath("topo_logo_blanco.png")
    if logo_path.exists():
        import base64
        b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        st.markdown(
            f"""
            <div style="display:flex;justify-content:center;margin:8px 0 18px 0;">
              <img src="data:image/png;base64,{b64}" alt="Topo" style="width:220px;opacity:0.95;"/>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _menu_links_column():
    """2 botones iguales, apilados y centrados (sin 'Ver mi perfil')."""
    st.markdown(
        """
        <style>
          .menu-col { max-width: 420px; margin: 0 auto; }
          .menu-col .stButton>button{
            width:100%;
            height:56px;
            border-radius:14px;
            font-weight:800;
            font-size:18px;
            margin:8px 0;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="menu-col">', unsafe_allow_html=True)

    if st.button("Ver partidos disponibles ⚽", key="btn_partidos_disponibles", use_container_width=True):
        st.session_state["jugador_page"] = "partidos"; st.rerun()

    if st.button("Ver mis estadísticas 📊", key="btn_mis_stats", use_container_width=True):
        st.session_state["jugador_page"] = "stats"; st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ---------- Vistas públicas del jugador ----------
def panel_menu_jugador(user):
    """Pantalla principal del jugador: logo, bienvenida, menú y próximos partidos."""
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

    # --- HERO + saludo centrado ---
    _hero_logo()
    st.markdown(
        f"<h1 style='text-align:center;margin:8px 0 18px 0;'>Bienvenido, {nombre_vinculado or username} 👋</h1>",
        unsafe_allow_html=True,
    )

    # --- Menú apilado (2 botones iguales) ---
    _menu_links_column()

    # --- Próximos partidos confirmados ---
    if not jugador_id:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.fecha, p.hora, p.cancha_id
            FROM partidos p
            JOIN partido_jugadores pj ON pj.partido_id = p.id
            WHERE pj.jugador_id = ?
              AND pj.confirmado_por_jugador = 1
              AND (p.ganador IS NULL OR TRIM(p.ganador) = '')
              AND date(p.fecha) >= date('now')
            ORDER BY p.fecha ASC
        """, (jugador_id,))
        proximos = _rows_to_dicts(cur.fetchall())

    if proximos:
        st.markdown("---")
        st.markdown("### 🗓️ Tus próximos partidos:")

        for p in proximos:
            fecha = _format_fecha_ddmmyyyy(p["fecha"])
            hora_lbl = time_label_from_int(p["hora"])
            cancha_name = _cancha_label(p["cancha_id"])
            titulo = f"{fecha} • {hora_lbl} hs • {cancha_name}"

            with st.expander(titulo, expanded=False):
                st.write("Estás confirmado ✅")
                if st.button("Cancelar asistencia", key=f"cancel_menu_{p['id']}"):
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "DELETE FROM partido_jugadores WHERE partido_id=? AND jugador_id=?",
                            (p["id"], jugador_id),
                        )
                        conn.commit()
                    _reset_equipos(p["id"])
                    promoted = _promote_from_waitlist_if_possible(p["id"])
                    if promoted:
                        _push_flash("Cancelaste tu asistencia. Se promovió al primero de la lista de espera.", "info")
                    else:
                        _push_flash("Cancelaste tu asistencia.", "info")
                    st.rerun()
    else:
        st.markdown("---")
        st.markdown("_No tenés partidos próximos confirmados._")



def panel_partidos_disponibles(user):
    _render_flash()

    jugador_id = user.get("jugador_id")
    if not jugador_id:
        st.warning("Tu usuario no está vinculado a ningún jugador. Pedile al admin que te vincule.")
        if st.button("⬅️ Volver", key="back_sin_vinculo"):
            st.session_state["jugador_page"] = "menu"; st.rerun()
        return

    st.subheader("Partidos disponibles")

    partidos = _partidos_visibles_para_jugador(jugador_id)
    if not partidos:
        st.info("No hay partidos disponibles para tu grupo por el momento.")
        if st.button("⬅️ Volver", key="back_sin_partidos"):
            st.session_state["jugador_page"] = "menu"; st.rerun()
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
    if st.button("⬅️ Volver", key="back_partidos"):
        st.session_state["jugador_page"] = "menu"; st.rerun()


def panel_mis_estadisticas(user):
    try:
        import jugador_stats
        return jugador_stats.panel_mis_estadisticas(user)
    except Exception as e:
        _render_flash()
        st.subheader("Mis estadísticas")
        st.error("No se pudo cargar el módulo de estadísticas (jugador_stats.py).")
        st.exception(e)
        st.divider()
        if st.button("⬅️ Volver", key="back_stats_missing_mod"):
            st.session_state["jugador_page"] = "menu"; st.rerun()
        return


def panel_mi_perfil(user):
    import streamlit as st
    from db import get_connection
    import hashlib

    st.subheader("👤 Mi perfil")

    def _row_to_dict(cur, row):
        if row is None:
            return None
        try:
            return dict(row)
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

    if st.button("⬅️ Volver", key="back_perfil"):
        st.session_state["jugador_page"] = "menu"; st.rerun()
