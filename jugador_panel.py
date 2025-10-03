from db import get_connection
# jugador_panel.py
import streamlit as st
import sqlite3
import scheduler
from datetime import date, datetime
import pytz
from urllib.parse import urlencode
from remember import current_token_in_url, set_url_page

DB_NAME = "elo_futbol.db"
CUPO_PARTIDO = 10
CUPO_ESPERA = 4
TZ_AR = pytz.timezone("America/Argentina/Buenos_Aires")

# ------------------------
# Helper para construir URLs con ?auth=...&page=...
# ------------------------
def _page_url(page: str) -> str:
    params = {}
    tok = current_token_in_url()
    if tok:
        params["auth"] = tok
    params["page"] = page
    return f"?{urlencode(params)}"


def get_connection():
    from db import get_connection as _gc
    return _gc()

def _now_ar_str() -> str:
    return datetime.now(TZ_AR).strftime("%d/%m/%Y %H:%M")

def _ensure_flash_store():
    if "flash" not in st.session_state:
        st.session_state["flash"] = []

def _push_flash(txt: str, level: str = "info"):
    _ensure_flash_store()
    st.session_state["flash"].append((level, txt))

def _render_flash():
    _ensure_flash_store()
    if not st.session_state["flash"]:
        return
    for level, txt in st.session_state["flash"]:
        if level == "success":
            st.success(txt)
        elif level == "warning":
            st.warning(txt)
        elif level == "error":
            st.error(txt)
        else:
            st.info(txt)
    st.session_state["flash"].clear()

def _row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in row.keys()}
    # ya dict / tuple
    try:
        return dict(row)
    except Exception:
        return row

def _rows_to_dicts(rows):
    return [_row_to_dict(r) for r in rows or []]

def time_label_from_int(hhmm: int) -> str:
    # 2030 -> "20:30"
    hh = hhmm // 100
    mm = hhmm % 100
    return f"{hh:02d}:{mm:02d}"

_DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

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

def _cancha_label(cancha_row):
    if not cancha_row:
        return "Cancha"
    return f"{cancha_row['nombre']} — {cancha_row['direccion'] or ''}".strip(" —")

# ---------- WAITLIST helpers ----------
def _waitlist_get(conn, partido_id: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT w.partido_id, w.jugador_id, j.nombre
        FROM waitlist w
        JOIN jugadores j ON j.id = w.jugador_id
        WHERE w.partido_id = ?
        ORDER BY w.created_at
    """, (partido_id,))
    return _rows_to_dicts(cur.fetchall())

def _waitlist_count(conn, partido_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM waitlist WHERE partido_id = ?", (partido_id,))
    r = _row_to_dict(cur.fetchone())
    return int(r["c"] or 0)

def _waitlist_is_in(conn, partido_id: int, jugador_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM waitlist WHERE partido_id=? AND jugador_id=? LIMIT 1", (partido_id, jugador_id))
    return cur.fetchone() is not None

def _waitlist_join(conn, partido_id: int, jugador_id: int):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO waitlist (partido_id, jugador_id, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (partido_id, jugador_id))
    conn.commit()

def _waitlist_leave(conn, partido_id: int, jugador_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM waitlist WHERE partido_id=? AND jugador_id=?", (partido_id, jugador_id))
    conn.commit()

# ---------- Roster & equipos ----------
def _jugadores_en_partido(conn, partido_id: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT ip.partido_id, ip.jugador_id, j.nombre, ip.equipo, ip.ingreso_desde_waitlist AS ingreso_desde_waitlist
        FROM inscritos_partido ip
        JOIN jugadores j ON j.id = ip.jugador_id
        WHERE ip.partido_id = ?
        ORDER BY ip.equipo, j.nombre
    """, (partido_id,))
    return _rows_to_dicts(cur.fetchall())

def _roster_count(conn, partido_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM inscritos_partido WHERE partido_id = ?", (partido_id,))
    r = _row_to_dict(cur.fetchone())
    return int(r["c"] or 0)

def _equipos_estan_generados(conn, partido_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM equipos WHERE partido_id = ?", (partido_id,))
    r = _row_to_dict(cur.fetchone())
    return int(r["c"] or 0) > 0

def _reset_equipos(conn, partido_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM equipos WHERE partido_id=?", (partido_id,))
    conn.commit()

def _render_equipos(conn, partido_id: int):
    inscritos = _jugadores_en_partido(conn, partido_id)

    def _eq_num(v):
        try:
            return int(v or 0)
        except Exception:
            return 0

    def _team_color_info(lista):
        # Retorna (emoji, etiqueta)
        colores = [("oscura", "⬛"), ("clara", "⬜")]
        # Buscamos si algún jugador cargó preferencia de color
        cur = conn.cursor()
        cur.execute("""
            SELECT color_camiseta AS c
            FROM jugadores
            WHERE id IN (SELECT jugador_id FROM inscritos_partido WHERE partido_id=?)
        """, (partido_id,))
        colprefs = [(_row_to_dict(r) or {}).get("c") for r in cur.fetchall()]
        for c, icon in [("oscura", "⬛"), ("clara", "⬜")]:
            if c in colprefs:
                return icon, c
        # fallback por contador
        osc = sum(1 for j in lista if (j.get("equipo") == 1))
        cla = sum(1 for j in lista if (j.get("equipo") == 2))
        if osc >= cla:
            return "⬛", "oscura"
        if cla > osc:
            return "⬜", "clara"
        # si empate, prioriza "clara" para visitante
        return "⬜", "clara"

    eq1 = [j for j in inscritos if _eq_num(j.get("equipo")) == 1]
    eq2 = [j for j in inscritos if _eq_num(j.get("equipo")) == 2]

    icon1, lab1 = _team_color_info(eq1)
    icon2, lab2 = _team_color_info(eq2)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{icon1} Equipo 1  (Camiseta {lab1})**")
        for j in eq1:
            extra = " (WL)" if j.get("ingreso_desde_waitlist") else ""
            st.write(f"- {j['nombre']}{extra}")
    with c2:
        st.markdown(f"**{icon2} Equipo 2  (Camiseta {lab2})**")
        for j in eq2:
            extra = " (WL)" if j.get("ingreso_desde_waitlist") else ""
            st.write(f"- {j['nombre']}{extra}")

def _promote_from_waitlist_if_possible(conn, partido_id: int):
    # Si hay cupo libre, sube al primero de la lista de espera
    cupo = CUPO_PARTIDO
    count = _roster_count(conn, partido_id)
    if count >= cupo:
        return
    wl = _waitlist_get(conn, partido_id)
    if not wl:
        return
    first = wl[0]
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO inscritos_partido (partido_id, jugador_id, equipo, ingreso_desde_waitlist) VALUES (?, ?, 0, 1)", (partido_id, first["jugador_id"]))
    cur.execute("DELETE FROM waitlist WHERE partido_id=? AND jugador_id=?", (partido_id, first["jugador_id"]))
    conn.commit()

# ---------- filtros visibles ----------
def _detect_col(cur, table: str, col: str) -> bool:
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        return col in cols
    except Exception:
        return False

def _partidos_visibles_para_jugador(conn, jugador_id: int):
    cur = conn.cursor()
    # Sólo partidos publicados y (sin grupo o grupo del jugador)
    cur.execute("""
        SELECT p.id, p.fecha, p.hora_inicio, p.cancha_id, p.publicado, p.grupo_id
        FROM partidos p
        WHERE p.publicado = 1
        ORDER BY p.fecha, p.hora_inicio
    """)
    filas = _rows_to_dicts(cur.fetchall())

    # Filtro por grupo si existe esa columna en jugadores (seguridad)
    grupos_col = _detect_col(cur, "jugadores", "grupo_id")
    if grupos_col and jugador_id:
        cur.execute("SELECT grupo_id FROM jugadores WHERE id=?", (jugador_id,))
        r = _row_to_dict(cur.fetchone())
        gid = int(r["grupo_id"] or 0) if r else 0
        filas = [f for f in filas if (int(f.get("grupo_id") or 0) in (0, gid))]

    return filas

# =========================
# Panel: menú del jugador
# =========================
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
        st.link_button("Ver partidos disponibles ⚽", _page_url("partidos"), key="lnk_partidos")
    with c2:
        st.link_button("Ver mis estadísticas 📊", _page_url("stats"), key="lnk_stats")
    with c3:
        st.link_button("Ver mi perfil 👤", _page_url("perfil"), key="lnk_perfil")

    with st.expander("Notas / novedades"):
        st.write("- Podés entrar directo con links: `?auth=<TOKEN>&page=partidos|stats|perfil`")

# =========================
# Panel: Partidos disponibles
# =========================
def panel_partidos_disponibles(user):
    _render_flash()
    jugador_id = user.get("jugador_id")

    # Validaciones de vínculo
    if not jugador_id:
        st.subheader("No estás vinculado a un jugador.")
        st.info("Pedile al admin que te vincule.")
        if st.button("⬅️ Volver", key="back_sin_vinculo"):
            set_url_page("menu")
            st.session_state["jugador_page"] = "menu"
            st.rerun()
        return

    st.subheader("Partidos disponibles")

    with get_connection() as conn:
        visibles = _partidos_visibles_para_jugador(conn, jugador_id)
        if not visibles:
            st.info("No hay partidos publicados para tu grupo por el momento.")
            if st.button("⬅️ Volver", key="back_sin_partidos"):
                set_url_page("menu")
                st.session_state["jugador_page"] = "menu"
                st.rerun()
            return

        for p in visibles:
            st.divider()
            partido_id = int(p["id"])
            fecha = p["fecha"]
            hora = int(p["hora_inicio"] or 0)

            # cancha
            cur = conn.cursor()
            cur.execute("SELECT id, nombre, direccion FROM canchas WHERE id=?", (p["cancha_id"],))
            cancha = _row_to_dict(cur.fetchone())
            cancha_txt = _cancha_label(cancha)

            # roster
            inscritos = _jugadores_en_partido(conn, partido_id)
            total = len(inscritos)
            cupo = CUPO_PARTIDO
            espera = CUPO_ESPERA

            # ya está inscripto?
            yo = next((j for j in inscritos if j["jugador_id"] == jugador_id), None)
            ya_inscripto = yo is not None

            # waitlist
            wl_count = _waitlist_count(conn, partido_id)
            en_waitlist = _waitlist_is_in(conn, partido_id, jugador_id)

            # Cabecera tarjeta
            st.markdown(f"### { _weekday_es(fecha) } { _format_fecha_ddmmyyyy(fecha) } — { time_label_from_int(hora) }")
            st.caption(cancha_txt)

            # cupo info
            st.write(f"**Inscritos**: {total}/{cupo}  |  **Espera**: {wl_count}/{espera}")

            # Equipos si existen
            if _equipos_estan_generados(conn, partido_id):
                _render_equipos(conn, partido_id)

            # Acciones
            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                if not ya_inscripto and total < cupo:
                    if st.button("Anotarme ✅", key=f"btn_in_{partido_id}"):
                        cur = conn.cursor()
                        cur.execute("INSERT OR IGNORE INTO inscritos_partido (partido_id, jugador_id, equipo) VALUES (?, ?, 0)", (partido_id, jugador_id))
                        conn.commit()
                        _push_flash("Inscripción registrada.", "success")
                        st.rerun()
                elif ya_inscripto:
                    if st.button("Bajarme ❌", key=f"btn_out_{partido_id}"):
                        cur = conn.cursor()
                        cur.execute("DELETE FROM inscritos_partido WHERE partido_id=? AND jugador_id=?", (partido_id, jugador_id))
                        conn.commit()
                        # puede promover a waitlist
                        _promote_from_waitlist_if_possible(conn, partido_id)
                        _push_flash("Inscripción cancelada.", "warning")
                        st.rerun()
                else:
                    st.button("Cupo completo", key=f"btn_full_{partido_id}", disabled=True)

            with ac2:
                if not ya_inscripto and total >= cupo:
                    # join waitlist
                    if not en_waitlist and wl_count < espera:
                        if st.button("Unirme a lista de espera ⏳", key=f"btn_wl_in_{partido_id}"):
                            _waitlist_join(conn, partido_id, jugador_id)
                            _push_flash("Te uniste a la lista de espera.", "info")
                            st.rerun()
                    elif en_waitlist:
                        if st.button("Salir de lista de espera ↩️", key=f"btn_wl_out_{partido_id}"):
                            _waitlist_leave(conn, partido_id, jugador_id)
                            _push_flash("Saliste de la lista de espera.", "warning")
                            st.rerun()
                    else:
                        st.button("Lista de espera completa", key=f"btn_wl_full_{partido_id}", disabled=True)

            with ac3:
                # marcador de “soy yo”
                if yo:
                    st.success("Estás inscripto ✅")
                elif en_waitlist:
                    st.info("Estás en espera ⏳")

            # Mostrar waitlist
            with st.expander("Lista de espera"):
                wl = _waitlist_get(conn, partido_id)
                if wl:
                    for i, w in enumerate(wl, start=1):
                        me = " ← vos" if w["jugador_id"] == jugador_id else ""
                        st.write(f"{i}. {w['nombre']}{me}")
                else:
                    st.write("_Vacía_")

    st.divider()
    if st.button("⬅️ Volver", key="back_partidos"):
        set_url_page("menu")
        st.session_state["jugador_page"] = "menu"
        st.rerun()

# =========================
# Panel: Estadísticas (reenvía a módulo dedicado si existe)
# =========================
def panel_mis_estadisticas(user):
    try:
        import jugador_stats
        return jugador_stats.panel_mis_estadisticas(user)
    except Exception as e:
        _render_flash()
        st.subheader("Mis estadísticas")
        st.info("El módulo `jugador_stats.py` no está disponible o falló. Mostramos un placeholder.")
        st.write("• ELO actual, goles, asistencias, rachas, últimas 10 actuaciones…")
        st.write("• Agregá `jugador_stats.py` para la vista completa.")
        st.divider()
        if st.button("⬅️ Volver", key="back_stats_missing_mod"):
            set_url_page("menu")
            st.session_state["jugador_page"] = "menu"
            st.rerun()
        return

# =========================
# Panel: Perfil del jugador
# =========================
def panel_mi_perfil(user):
    _render_flash()
    st.subheader("Mi perfil")
    jugador_id = user.get("jugador_id")

    if not jugador_id:
        st.warning("No hay jugador vinculado a este usuario.")
        if st.button("⬅️ Volver", key="back_perfil"):
            set_url_page("menu")
            st.session_state["jugador_page"] = "menu"
            st.rerun()
        return

    # Datos básicos
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, camiseta_fetiche AS camiseta, color_camiseta AS color_camiseta
            FROM jugadores
            WHERE id=?
        """, (jugador_id,))
        j = _row_to_dict(cur.fetchone())

        if not j:
            st.error("Jugador no encontrado.")
            if st.button("⬅️ Volver", key="back_perfil"):
                set_url_page("menu")
                st.session_state["jugador_page"] = "menu"
                st.rerun()
            return

        st.write(f"**Nombre:** {j['nombre']}")
        st.write(f"**Camiseta preferida:** {j.get('camiseta') or '-'}")
        st.write(f"**Color preferido:** {j.get('color_camiseta') or '-'}")

        # Editor simple de preferencias
        st.divider()
        st.markdown("### Preferencias")

        nueva_camiseta = st.text_input("Camiseta fetiche", value=j.get("camiseta") or "", key="inp_camiseta_fetiche")
        nueva_color = st.selectbox("Color preferido", ["", "oscura", "clara"], index=["","oscura","clara"].index(j.get("color_camiseta") or ""), key="sel_color_camiseta")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Guardar cambios 💾", key="btn_save_perfil"):
                cur.execute("""
                    UPDATE jugadores
                    SET camiseta_fetiche=?, color_camiseta=?
                    WHERE id=?
                """, (nueva_camiseta or None, nueva_color or None, jugador_id))
                conn.commit()
                _push_flash("Perfil actualizado.", "success")
                st.rerun()

        with c2:
            if st.button("Descartar cambios ↩️", key="btn_cancel_perfil"):
                _push_flash("Cambios descartados.", "warning")
                st.rerun()

    st.divider()
    if st.button("⬅️ Volver", key="back_perfil"):
        set_url_page("menu")
        st.session_state["jugador_page"] = "menu"
        st.rerun()
