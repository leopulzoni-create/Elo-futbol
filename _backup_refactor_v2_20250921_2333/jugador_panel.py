from db import get_connection
# jugador_panel.py
import streamlit as st
import sqlite3
import scheduler
from datetime import date, datetime
import pytz

DB_NAME = "elo_futbol.db"
CUPO_PARTIDO = 10
CUPO_ESPERA = 4
TZ_AR = pytz.timezone("America/Argentina/Buenos_Aires")


def get_connection():
    conn = get_connection()

    return conn


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
            SELECT pj.jugador_id, pj.confirmado_por_jugador, pj.equipo, pj.ingreso_desde_espera, pj.camiseta, j.nombre
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


# ---------- Partidos visibles (con publicar_desde) ----------
def _partidos_visibles_para_jugador(jugador_id):
    hoy = date.today().strftime("%Y-%m-%d")
    ahora_ar = _now_ar_str()

    with get_connection() as conn:
        cur = conn.cursor()

        # grupos del jugador (M2M)
        cur.execute("SELECT grupo_id FROM jugador_grupos WHERE jugador_id = ?", (jugador_id,))
        grupos = [r["grupo_id"] for r in cur.fetchall()]

        # fallback legacy (jugadores.grupo_id)
        if not grupos:
            cur.execute("SELECT grupo_id FROM jugadores WHERE id = ?", (jugador_id,))
            row = cur.fetchone()
            if row and row["grupo_id"]:
                grupos = [row["grupo_id"]]

        if not grupos:
            return []

        placeholders = ",".join(["?"] * len(grupos))
        sql = f"""
            SELECT DISTINCT p.id, p.fecha, p.cancha_id, p.hora, p.tipo, p.ganador, p.diferencia_gol, p.publicar_desde
            FROM partidos p
            WHERE p.fecha >= ?
              AND p.tipo = 'abierto'
              AND p.ganador IS NULL
              AND p.diferencia_gol IS NULL
              AND (p.publicar_desde IS NULL OR p.publicar_desde <= ?)
              AND EXISTS (
                    SELECT 1
                      FROM partido_grupos pg
                     WHERE pg.partido_id = p.id
                       AND pg.grupo_id IN ({placeholders})
              )
            ORDER BY p.fecha ASC, p.hora ASC, p.id ASC
        """
        cur.execute(sql, [hoy, ahora_ar] + grupos)
        return _rows_to_dicts(cur.fetchall())


# ---------- Vistas públicas del jugador (menú / partidos / stats / perfil) ----------
def panel_menu_jugador(user):
    # Disparo LAZY: materializar programaciones vencidas al entrar
    try:
        scheduler.run_programaciones_vencidas()
    except Exception:
        # Silencioso: si falla, no bloquea la vista del jugador
        pass

    if "jugador_page" not in st.session_state:
        st.session_state["jugador_page"] = "menu"

    _render_flash()

    username = user.get("username") or "jugador"
    jugador_id = user.get("jugador_id")

    # nombre público
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
        if st.button("Ver partidos disponibles ⚽", key="btn_partidos_disponibles"):
            st.session_state["jugador_page"] = "partidos"; st.rerun()
    with c2:
        if st.button("Ver mis estadísticas 📊", key="btn_mis_stats"):
            st.session_state["jugador_page"] = "stats"; st.rerun()
    with c3:
        if st.button("Ver mi perfil 👤", key="btn_mi_perfil"):
            st.session_state["jugador_page"] = "perfil"; st.rerun()


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
            # Equipos generados => mostrar equipos; si no, lista simple
            if _equipos_estan_generados(partido_id):
                # listado simple por equipo sin ELO (ya lo tenías así)
                eq1 = [j for j in inscritos if str(j.get("equipo")) == "1"]
                eq2 = [j for j in inscritos if str(j.get("equipo")) == "2"]
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Equipo 1**")
                    for j in eq1:
                        extra = " (WL)" if j.get("ingreso_desde_espera") else ""
                        st.write(f"- {j['nombre']}{extra}")
                with c2:
                    st.markdown("**Equipo 2**")
                    for j in eq2:
                        extra = " (WL)" if j.get("ingreso_desde_espera") else ""
                        st.write(f"- {j['nombre']}{extra}")
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
                        # desarmar equipos
                        _reset_equipos(partido_id)
                        # promover
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
    # igual al tuyo anterior (omito por extensión) – mantén tu versión.
    st.subheader("Mi perfil 👤")
    st.info("Conserva tu implementación existente aquí (usuario, contraseña, nombre de jugador, etc.).")
    if st.button("⬅️ Volver", key="back_perfil"):
        st.session_state["jugador_page"] = "menu"; st.rerun()