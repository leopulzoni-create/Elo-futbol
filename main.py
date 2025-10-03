import streamlit as st
from auth import verify_user
import scheduler  # dispara materializaciones "lazy"
from crear_admin import ensure_admin_user
ensure_admin_user()
import streamlit.components.v1 as components

# Persistencia de sesión vía token en URL (usa remember.py con st.query_params)
from remember import (
    _install_popstate_reload()
    ensure_tables,
    validate_token,
    issue_token,
    revoke_token,
    current_token_in_url,
    set_url_token,
    clear_url_token,
    current_page_in_url,   # ← NUEVO deep-link
    set_url_page,          # ← NUEVO deep-link
)

# ==================================================
#  Autologin por token (persistencia de sesión)
# ==================================================
ensure_tables()
if "user" not in st.session_state:
    url_token = current_token_in_url()
    if url_token:
        user_from_token = validate_token(url_token)
        if user_from_token:
            st.session_state.user = user_from_token  # dict normalizado desde remember.py
            st.rerun()

# =============================
# Encabezado: Título + Logout
# =============================
col_title, col_btn = st.columns([0.9, 0.1])
with col_title:
    st.title("Topo Partidos ⚽")
with col_btn:
    if "user" in st.session_state:
        st.markdown("<div style='text-align:right;'>", unsafe_allow_html=True)
        if st.button("🚪", key="btn_logout", help="Cerrar sesión"):
            tok = current_token_in_url()
            if tok:
                revoke_token(tok)
                clear_url_token()
            # Limpiar estados de sesión utilizados
            for k in list(st.session_state.keys()):
                if k in ("user", "admin_page", "jugador_page", "flash"):
                    del st.session_state[k]
            # al salir dejo la URL limpia (sin page ni auth)
            for qp in ("page", "auth"):
                if qp in st.query_params:
                    del st.query_params[qp]
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# --- LOGIN ---
if "user" not in st.session_state:
    username = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    remember_me = st.checkbox("Mantener sesión en este dispositivo", value=True)

    if st.button("Ingresar"):
        user = verify_user(username, password)
        if user:
            # Normalizar Row -> dict
            try:
                if hasattr(user, "keys"):
                    user = {k: user[k] for k in user.keys()}
            except Exception:
                pass

            st.session_state.user = user

            # Si se tilda "Mantener sesión", emitir token y guardarlo en el URL (?auth=...)
            if remember_me:
                user_id = user.get("id")
                if not user_id:
                    # Buscar id por username si verify_user no lo retornó
                    from db import get_connection
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT id FROM usuarios WHERE username = ?", (user["username"],))
                        row = cur.fetchone()
                    user_id = row[0] if row else None
                if user_id:
                    tok = issue_token(user_id)
                    set_url_token(tok)

            # Al entrar, la página por defecto es el menú
            set_url_page("menu")
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos")

else:
    user = st.session_state.get("user") or {}
    rol = user.get("rol")
    if rol is None:
        # deduce rol por is_admin si faltara
        rol = "admin" if str(user.get("is_admin")).lower() in ("1", "true", "yes") else "jugador"

    # ==================================================
    # PANEL ADMIN
    # ==================================================
    if rol == "admin":
        st.warning("Estás logueado como administrador.", icon="⚠️")

        st.header(f"Panel Administrador - {user['username']}")

        # Guardamos qué página está activa
        if "admin_page" not in st.session_state:
            st.session_state.admin_page = None

        # --- MENÚ PRINCIPAL ---
        if st.session_state.admin_page is None:
            st.subheader("Selecciona una opción:")
            if st.button("1️⃣ Gestión de jugadores"):
                st.session_state.admin_page = "jugadores"; st.rerun()
            if st.button("2️⃣ Gestión de canchas"):
                st.session_state.admin_page = "canchas"; st.rerun()
            if st.button("3️⃣ Gestión de partidos"):
                st.session_state.admin_page = "crear_partido"; st.rerun()
            if st.button("4️⃣ Generar equipos"):
                st.session_state.admin_page = "generar_equipos"; st.rerun()
            if st.button("5️⃣ Registrar resultado"):
                st.session_state.admin_page = "registrar_resultado"; st.rerun()
            if st.button("6️⃣ Historial"):
                st.session_state.admin_page = "historial"; st.rerun()
            if st.button("7️⃣ Administrar usuarios"):
                st.session_state.admin_page = "usuarios"; st.rerun()
            if st.button("8️⃣ Temporadas (cambio y cierre) 🗓️", key="btn_admin_temporadas"):
                st.session_state.admin_page = "temporadas"; st.rerun()
            if st.button("9️⃣ Estadísticas globales 📊", key="btn_admin_global_stats"):
                st.session_state.admin_page = "estadisticas_globales"; st.rerun()

        # --- CARGA DE MÓDULOS SEGÚN BOTÓN ---
        elif st.session_state.admin_page == "jugadores":
            import jugadores; jugadores.panel_gestion()
        elif st.session_state.admin_page == "canchas":
            import canchas; canchas.panel_canchas()
        elif st.session_state.admin_page == "crear_partido":
            import partidos; partidos.panel_creacion()
        elif st.session_state.admin_page == "generar_equipos":
            import equipos; equipos.panel_generacion()
        elif st.session_state.admin_page == "registrar_resultado":
            import cargaresultados; cargaresultados.panel_resultados()
        elif st.session_state.admin_page == "historial":
            import historial; historial.panel_historial()
        elif st.session_state.admin_page == "usuarios":
            import usuarios; usuarios.panel_gestion()
        elif st.session_state.admin_page == "temporadas":
            import admin_temporadas; admin_temporadas.panel_temporadas()
        elif st.session_state.admin_page == "estadisticas_globales":
            import admin_stats; admin_stats.panel_estadisticas_globales()

    # ==================================================
    # PANEL JUGADOR
    # ==================================================
    elif rol == "jugador":
        import jugador_panel  # módulo del panel jugador
        st.header(f"Panel Jugador - {user['username']}")

        # Router con DEEP-LINK: si la URL trae ?page=xyz, sincronizamos el estado inicial
        url_page = current_page_in_url(default="menu")  # ← NUEVO deep-link
        if "jugador_page" not in st.session_state:
            st.session_state.jugador_page = url_page
        else:
            # si alguien pegó otro link con ?page=..., actualizar el estado
            if url_page != st.session_state.jugador_page:
                st.session_state.jugador_page = url_page

        # cargar vista
        if st.session_state.jugador_page == "menu":
            jugador_panel.panel_menu_jugador(user)
        elif st.session_state.jugador_page == "partidos":
            jugador_panel.panel_partidos_disponibles(user)
        elif st.session_state.jugador_page == "stats":
            jugador_panel.panel_mis_estadisticas(user)
        elif st.session_state.jugador_page == "perfil":
            jugador_panel.panel_mi_perfil(user)
        else:
            st.session_state.jugador_page = "menu"
            set_url_page("menu")  # ← NUEVO deep-link
            st.rerun()

def _install_popstate_reload():
    # JS mínimo: si el usuario toca Atrás/Adelante, recarga la app para que lea los nuevos query params
    components.html("""
    <script>
    (function(){
      if (window.__stPopstateInstalled) return;
      window.__stPopstateInstalled = true;
      window.addEventListener('popstate', function(){
        try { window.parent.location.reload(); } catch(e) { location.reload(); }
      });
    })();
    </script>
    """, height=0)
