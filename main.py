import streamlit as st
from auth import verify_user
import scheduler  # dispara materializaciones "lazy"
from crear_admin import ensure_admin_user
ensure_admin_user()

# Persistencia de sesión vía token (remember.py)
from remember import (
    ensure_tables,
    validate_token,
    issue_token,
    revoke_token,
    current_token_in_url,
    set_url_token,
    clear_url_token,
)

from pathlib import Path
import base64

# ---------------------------
# UI: logo para pantalla de login
# ---------------------------
def _hero_logo_login(width_px: int = 200, opacity: float = 0.95):
    logo_path = Path(__file__).with_name("assets").joinpath("topo_logo_blanco.png")
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        st.markdown(
            f"""
            <div style="display:flex;justify-content:center;margin:8px 0 18px 0;">
              <img src="data:image/png;base64,{b64}" alt="Topo" style="width:{width_px}px;opacity:{opacity};"/>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ==================================================
#  Autologin por token
# ==================================================
ensure_tables()
if "user" not in st.session_state:
    url_token = current_token_in_url()
    if url_token:
        user_from_token = validate_token(url_token)
        if user_from_token:
            st.session_state.user = user_from_token
            st.rerun()

# =============================
# CSS global: ocultar “cadenitas” (anchors de títulos)
# =============================
st.markdown("""
<style>
  :where(h1,h2,h3,h4,h5,h6) a[href^="#"] {
    display: none !important;
    visibility: hidden !important;
  }
</style>
""", unsafe_allow_html=True)

# =============================
# Encabezado minimal + Logout (condicional)
# =============================
col_title, col_btn = st.columns([0.9, 0.1])

with col_title:
    # portada limpia, sin títulos
    pass

with col_btn:
    if "user" in st.session_state:
        user = st.session_state.get("user") or {}
        # Botón de menú ⋮ con Popover (si tu versión de Streamlit lo soporta)
        if hasattr(st, "popover"):
            with st.popover("⋮"):
                if st.button("Cerrar sesión", key="logout_from_menu"):
                    tok = current_token_in_url()
                    if tok:
                        revoke_token(tok)
                        clear_url_token()
                    for k in list(st.session_state.keys()):
                        if k in ("user", "admin_page", "jugador_page", "flash"):
                            del st.session_state[k]
                    st.rerun()
                # Opcionales:
                if user.get("rol") == "jugador":
                    if st.button("Mi perfil", key="menu_perfil"):
                        st.session_state["jugador_page"] = "perfil"
                        st.rerun()
        else:
            # Fallback para versiones sin st.popover: un expander con el mismo contenido
            with st.expander("⋮", expanded=False):
                if st.button("Cerrar sesión", key="logout_from_menu_fallback"):
                    tok = current_token_in_url()
                    if tok:
                        revoke_token(tok)
                        clear_url_token()
                    for k in list(st.session_state.keys()):
                        if k in ("user", "admin_page", "jugador_page", "flash"):
                            del st.session_state[k]
                    st.rerun()
                if user.get("rol") == "jugador":
                    if st.button("Mi perfil", key="menu_perfil_fallback"):
                        st.session_state["jugador_page"] = "perfil"
                        st.rerun()


# --- LOGIN ---
if "user" not in st.session_state:
    _hero_logo_login(width_px=180)

    username = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    remember_me = st.checkbox("Mantener sesión en este dispositivo", value=True)

    if st.button("Ingresar"):
        user = verify_user(username, password)
        if user:
            try:
                if hasattr(user, "keys"):
                    user = {k: user[k] for k in user.keys()}
            except Exception:
                pass

            st.session_state.user = user

            if remember_me:
                user_id = user.get("id")
                if not user_id:
                    from db import get_connection
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT id FROM usuarios WHERE username = ?", (user["username"],))
                        row = cur.fetchone()
                    user_id = row[0] if row else None
                if user_id:
                    tok = issue_token(user_id)
                    set_url_token(tok)

            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos")

else:
    user = st.session_state.get("user") or {}
    rol = user.get("rol")
    if rol is None:
        rol = "admin" if str(user.get("is_admin")).lower() in ("1", "true", "t", "yes") else "jugador"
        user["rol"] = rol
        st.session_state.user = user

    # ==================================================
    # PANEL ADMIN
    # ==================================================
    if rol == "admin":
        st.header(f"Panel Administrador - {user['username']}")
        if "admin_page" not in st.session_state:
            st.session_state.admin_page = None

        if st.session_state.admin_page is None:
            st.subheader("Selecciona una opción:")
            if st.button("1️⃣ Gestión de jugadores"): st.session_state.admin_page = "jugadores"; st.rerun()
            if st.button("2️⃣ Gestión de canchas"):   st.session_state.admin_page = "canchas"; st.rerun()
            if st.button("3️⃣ Gestión de partidos"):  st.session_state.admin_page = "crear_partido"; st.rerun()
            if st.button("4️⃣ Generar equipos"):      st.session_state.admin_page = "generar_equipos"; st.rerun()
            if st.button("5️⃣ Registrar resultado"):  st.session_state.admin_page = "registrar_resultado"; st.rerun()
            if st.button("6️⃣ Historial"):            st.session_state.admin_page = "historial"; st.rerun()
            if st.button("7️⃣ Administrar usuarios"): st.session_state.admin_page = "usuarios"; st.rerun()
            if st.button("8️⃣ Temporadas (cambio y cierre) 🗓️", key="btn_admin_temporadas"):
                st.session_state.admin_page = "temporadas"; st.rerun()
            if st.button("9️⃣ Estadísticas globales 📊", key="btn_admin_global_stats"):
                st.session_state.admin_page = "estadisticas_globales"; st.rerun()

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
        import jugador_panel
        if "jugador_page" not in st.session_state:
            st.session_state.jugador_page = "menu"

        if st.session_state.jugador_page == "menu":
            jugador_panel.panel_menu_jugador(user)
        elif st.session_state.jugador_page == "partidos":
            jugador_panel.panel_partidos_disponibles(user)
        elif st.session_state.jugador_page == "stats":
            jugador_panel.panel_mis_estadisticas(user)
        elif st.session_state.jugador_page == "perfil":
            jugador_panel.panel_mi_perfil(user)
        else:
            st.session_state.jugador_page = "menu"; st.rerun()
