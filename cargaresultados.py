from db import get_connection
# cargaresultados.py
import streamlit as st
import sqlite3
from datetime import datetime
import equipos

DB_NAME = "elo_futbol.db"


def get_connection():
    from db import get_connection as _gc
    return _gc()

    return conn


# --- ELO: cálculo a nivel equipo (sin redondear aquí) ---
def calcular_elo(elo_a, elo_b, score_a, score_b, K):
    exp_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
    exp_b = 1 - exp_a
    new_a = elo_a + K * (score_a - exp_a)
    new_b = elo_b + K * (score_b - exp_b)
    return new_a, new_b  # floats


def _flash_show_and_clear():
    msg = st.session_state.pop("_flash_msg", None)
    typ = st.session_state.pop("_flash_type", "info") if msg else None
    if msg:
        if typ == "success":
            st.success(msg)
        elif typ == "warning":
            st.warning(msg)
        elif typ == "error":
            st.error(msg)
        else:
            st.info(msg)


def _get_partidos_listos():
    """
    Partidos listos para registrar:
    - tipo = 'abierto'
    - equipos confirmados (10 jugadores asignados a equipo 1/2)
    - camisetas asignadas y uniformes por equipo
    - SIN resultado (ganador y diferencia_gol NULL)
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT p.id,
                        p.fecha,
                        IFNULL(c.nombre,'Sin asignar') AS cancha,
                        p.ganador,
                        p.diferencia_gol
          FROM partidos p
          JOIN partido_jugadores pj ON pj.partido_id = p.id
     LEFT JOIN canchas c ON c.id = p.cancha_id
         WHERE p.tipo = 'abierto'
      ORDER BY p.fecha DESC, p.id DESC
    """
    )
    rows = cur.fetchall()
    conn.close()

    opciones = []
    for p in rows:
        conf, _, _, _, _ = equipos.equipos_ya_confirmados(p["id"])
        cam1 = equipos.obtener_camiseta_equipo(p["id"], 1)
        cam2 = equipos.obtener_camiseta_equipo(p["id"], 2)
        sin_resultado = (p["ganador"] is None) and (p["diferencia_gol"] is None)
        if conf and cam1 and cam2 and sin_resultado:
            etiqueta = "ID %s - %s - %s" % (p["id"], p["fecha"], p["cancha"])
            opciones.append((p["id"], etiqueta))
    return opciones


def _ultimo_partido_con_resultado():
    """Devuelve (id, es_oficial) del último partido con resultado (o None)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, es_oficial
          FROM partidos
         WHERE ganador IS NOT NULL OR diferencia_gol IS NOT NULL
      ORDER BY id DESC
         LIMIT 1
    """
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return row["id"], row["es_oficial"]
    return None


def _deshacer_partido(partido_id: int):
    """
    Deshace resultado:
    - si fue oficial: restaura ELOs desde historial_elo y borra historial del partido
    - limpia ganador/diferencia_gol
    - deja es_oficial = 0 (por NOT NULL)
    - reabre el partido: tipo = 'abierto'
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT es_oficial FROM partidos WHERE id = ?", (partido_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise RuntimeError("Partido inexistente.")
    es_oficial = row["es_oficial"]

    # Si fue oficial, revertimos ELO y borramos historial de ese partido
    if es_oficial == 1:
        cur.execute(
            "SELECT jugador_id, elo_antes FROM historial_elo WHERE partido_id = ?",
            (partido_id,),
        )
        for r in cur.fetchall():
            cur.execute(
                "UPDATE jugadores SET elo_actual = ? WHERE id = ?",
                (r["elo_antes"], r["jugador_id"]),
            )
        cur.execute(
            "DELETE FROM historial_elo WHERE partido_id = ?",
            (partido_id,),
        )

    # En todos los casos limpiamos el resultado y reabrimos el partido
    cur.execute(
        """
        UPDATE partidos
           SET ganador = NULL,
               diferencia_gol = NULL,
               es_oficial = 0,
               resultado_cargado_por = NULL,
               tipo = 'abierto'
         WHERE id = ?
        """,
        (partido_id,),
    )
    conn.commit()
    conn.close()


# --- helper: cuenta partidos oficiales jugados de un jugador ---
def _partidos_oficiales_jugador(conn, jugador_id: int) -> int:
    cur = conn.cursor()
    # historial_elo sólo se llena en oficiales
    cur.execute(
        "SELECT COUNT(*) AS c FROM historial_elo WHERE jugador_id = ?",
        (jugador_id,),
    )
    row = cur.fetchone()
    return int(row["c"] if row and row["c"] is not None else 0)


def panel_resultados():
    st.subheader("📊 Registrar resultado")

    # NO mostramos flash acá arriba → se mueve debajo del botón principal

    # ==== Selector de partido listo ====
    opciones = _get_partidos_listos()

    if opciones:
        partido_sel = st.selectbox(
            "Selecciona un partido",
            [o[1] for o in opciones],
            key="sb_partido_listo",
        )
        partido_id = next(pid for pid, label in opciones if label == partido_sel)

        # Vista de equipos + colores a nivel de equipo
        st.markdown("### Equipos confirmados")
        cam1 = equipos.obtener_camiseta_equipo(partido_id, 1) or "—"
        cam2 = equipos.obtener_camiseta_equipo(partido_id, 2) or "—"
        equipos.render_vista_jugadores(partido_id)

        st.divider()
        st.markdown("### Parámetros del resultado")

        resultado = st.radio(
            "Resultado",
            [
                "Gana Equipo 1 (%s)" % cam1.capitalize(),
                "Gana Equipo 2 (%s)" % cam2.capitalize(),
                "Empate",
            ],
            key="rb_resultado",
        )
        dif_goles = st.number_input(
            "Diferencia de goles", min_value=0, step=1, key="ni_dif_goles"
        )
        oficial = st.radio(
            "Tipo de partido", ["Oficial", "Amistoso"], key="rb_oficial"
        )

        # Avisos de coherencia (soft)
        if "Empate" in resultado and dif_goles != 0:
            st.warning(
                "Para empate, la diferencia de goles debe ser 0. "
                "Se ajustará automáticamente al registrar."
            )
        elif "Empate" not in resultado and dif_goles == 0:
            st.warning(
                "Si hay ganador, la diferencia de goles debería ser > 0."
            )

        # BOTÓN principal
        clicked = st.button("✅ Registrar resultado", key="btn_registrar_resultado")

        if clicked:
            try:
                conn = get_connection()
                cur = conn.cursor()

                # Determinar ganador y ajustar diferencia para empate
                is_empate = "Empate" in resultado
                if "Equipo 1" in resultado:
                    ganador = 1
                elif "Equipo 2" in resultado:
                    ganador = 2
                else:
                    ganador = None
                    dif_goles = 0

                es_oficial_flag = 1 if oficial == "Oficial" else 0

                # Registrar quién cargó el resultado
                admin_username = "desconocido"
                try:
                    if "user" in st.session_state:
                        user = st.session_state["user"]
                        if isinstance(user, dict):
                            admin_username = user.get("username") or admin_username
                        elif hasattr(user, "get"):
                            admin_username = user.get("username", admin_username)
                except:
                    pass

                # Actualizar partidos
                cur.execute(
                    """
                    UPDATE partidos
                       SET ganador = ?,
                           diferencia_gol = ?,
                           es_oficial = ?,
                           resultado_cargado_por = ?
                     WHERE id = ?
                    """,
                    (ganador, dif_goles, es_oficial_flag, admin_username, partido_id),
                )
                conn.commit()

                # Cerrar partido
                cur.execute("UPDATE partidos SET tipo = 'cerrado' WHERE id = ?", (partido_id,))
                conn.commit()

                # SOLO si es oficial → calcular ELO + historial_elo
                if oficial == "Oficial":
                    jugadores = equipos.obtener_jugadores_partido_full(partido_id)
                    team1 = [j for j in jugadores if j["equipo"] == 1]
                    team2 = [j for j in jugadores if j["equipo"] == 2]
                    n1 = max(1, len(team1))
                    n2 = max(1, len(team2))

                    # promedios
                    elo1 = sum(j["elo"] for j in team1) / n1
                    elo2 = sum(j["elo"] for j in team2) / n2

                    # scores
                    if is_empate:
                        score1, score2 = 0.5, 0.5
                    elif ganador == 1:
                        score1, score2 = 1.0, 0.0
                    else:
                        score1, score2 = 0.0, 1.0

                    # factor multiplicador por diferencia de gol
                    K_base = st.session_state.get("K_val", 80)
                    factor = 1.0
                    if dif_goles >= 6:
                        factor = 1.8
                    elif dif_goles >= 3:
                        factor = 1.3
                    K_team = float(K_base) * factor

                    # cálculo ELO de equipos
                    new1, new2 = calcular_elo(elo1, elo2, score1, score2, K_team)
                    diff1, diff2 = new1 - elo1, new2 - elo2

                    # reparto por jugador
                    delta1 = diff1 / n1
                    delta2 = diff2 / n2

                    # actualizar ELO jugador x jugador
                    for j in team1 + team2:
                        elo_pre = j["elo"]
                        delta = delta1 if j in team1 else delta2

                        prev_oficiales = _partidos_oficiales_jugador(conn, j["jugador_id"])
                        if prev_oficiales < 5:
                            delta *= 1.25

                        elo_post = round(elo_pre + delta)

                        cur.execute(
                            "UPDATE jugadores SET elo_actual = ? WHERE id = ?",
                            (elo_post, j["jugador_id"]),
                        )
                        cur.execute(
                            """
                            INSERT INTO historial_elo (jugador_id, partido_id, elo_antes, elo_despues, fecha)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                j["jugador_id"],
                                partido_id,
                                elo_pre,
                                elo_post,
                                datetime.now().isoformat(),
                            ),
                        )
                    conn.commit()

                conn.close()

                st.session_state["_last_registered_id"] = partido_id
                st.session_state["_flash_msg"] = (
                    f"Resultado del partido {partido_id} registrado y partido cerrado."
                )
                st.session_state["_flash_type"] = "success"
                st.rerun()

            except Exception as e:
                st.session_state["_flash_msg"] = f"Error al registrar resultado: {e}"
                st.session_state["_flash_type"] = "error"
                st.rerun()

        # 👇 AHORA se muestra el flash DEBAJO del botón, donde corresponde
        _flash_show_and_clear()

    else:
        st.info(
            "No hay partidos listos para registrar "
            "(se requieren equipos confirmados, camisetas y sin resultado cargado)."
        )
        _flash_show_and_clear()

    # ==== Deshacer último resultado ====
    st.divider()
    st.markdown("### ⏪ Deshacer último resultado")

    ultimo_id = st.session_state.get("_last_registered_id")
    if ultimo_id is not None:
        st.caption(f"Último resultado cargado en esta sesión: partido ID {ultimo_id}")
        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button(
                f"Deshacer resultado de ID {ultimo_id}",
                key="btn_deshacer_ultimo_sesion",
            ):
                try:
                    _deshacer_partido(ultimo_id)
                    st.session_state["_flash_msg"] = (
                        f"Se deshizo el resultado del partido {ultimo_id} (reabierto)."
                    )
                    st.session_state["_flash_type"] = "warning"
                    st.session_state.pop("_last_registered_id", None)
                    st.rerun()
                except Exception as e:
                    st.session_state["_flash_msg"] = (
                        f"Error al deshacer (último de sesión): {e}"
                    )
                    st.session_state["_flash_type"] = "error"
                    st.rerun()
        with col_b:
            if st.button("Olvidar este ‘último’ (no deshacer)", key="btn_olvidar_ultimo_sesion"):
                st.session_state.pop("_last_registered_id", None)
                st.rerun()

    if ultimo_id is None:
        ult = _ultimo_partido_con_resultado()
        if ult:
            ult_id, ult_of = ult
            st.caption(f"Último partido con resultado en base de datos: ID {ult_id}")
            if st.button(f"Deshacer resultado de ID {ult_id}", key="btn_deshacer_ultimo_db"):
                try:
                    _deshacer_partido(ult_id)
                    st.session_state["_flash_msg"] = (
                        f"Se deshizo el resultado del partido {ult_id} (reabierto)."
                    )
                    st.session_state["_flash_type"] = "warning"
                    st.rerun()
                except Exception as e:
                    st.session_state["_flash_msg"] = (
                        f"Error al deshacer (último en DB): {e}"
                    )
                    st.session_state["_flash_type"] = "error"
                    st.rerun()
        else:
            st.caption("No hay resultados cargados para deshacer.")

    # ==== Control K ====
    st.divider()
    K_val = st.number_input(
        "Valor K (ELO)",
        min_value=10,
        max_value=200,
        value=80,
        step=10,
        key="K_val_input",
    )
    st.session_state.K_val = K_val

    # ==== Volver ====
    st.divider()
    if st.button("⬅️ Volver al menú principal", key="btn_volver_menu_resultados"):
        st.session_state.admin_page = None
        st.rerun()

