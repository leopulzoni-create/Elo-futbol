"""Editor de equipos: interacciones locales y guardado explícito."""
import hashlib
import logging
from html import escape

import streamlit as st
import equipos as core
import equipos_store as store
from equipos_logic import (fingerprint, grupos_actuales, validar_reglas,
                           validar_equipos, opciones_validas, unidades_equipo)


def panel_generacion():
    st.subheader('⚽ Generar equipos')
    if st.button('⬅️ Volver al menú principal', key='equipos_volver'):
        st.session_state.admin_page = None
        st.rerun()
    if not st.session_state.get('_equipo_separados_schema'):
        store.ensure_schema()
        st.session_state['_equipo_separados_schema'] = True
    partidos = core.obtener_partidos_abiertos()
    if not partidos:
        st.info('No hay partidos abiertos.')
        return
    por_id = {p['id']: p for p in partidos}

    def etiqueta(pid):
        p = por_id[pid]
        fecha = core.parsear_fecha(p['fecha'])
        texto = fecha.strftime('%d/%m/%y') if fecha else str(p['fecha'])
        return f"N° {p['np']} · {texto} {core.formatear_hora(p['hora'])} · {p['cancha_nombre']}"

    pid = st.selectbox('Partido', list(por_id), format_func=etiqueta, key='equipos_partido_v2')
    if st.button('🔄 Actualizar datos', key=f'equipos_refresh_{pid}'):
        st.rerun()
    jugadores, separados, activos = store.cargar(pid)
    st.caption('Las selecciones se editan sin consultar la base. Actualizá para ver cambios de otras personas.')
    if st.session_state.pop('_equipos_aviso_v2', None):
        st.success('Cambios guardados.')
    _editor(pid, jugadores, separados, activos)


@st.fragment
def _editor(pid, jugadores, separados, activos):
    firma = fingerprint(jugadores, separados)
    key = f'equipos_editor_{pid}'
    estado = st.session_state.get(key)
    if not estado or estado['firma'] != firma:
        estado = {'firma': firma, 'opciones': [], 'actual': None, 'revision': 0}
        st.session_state[key] = estado
    por_id = {j['jugador_id']: j for j in jugadores}
    ids = list(por_id)
    nombres = {j['id']: j['nombre'] for j in activos}
    nombres.update({jid: j['nombre'] for jid, j in por_id.items()})
    # Las claves incluyen partido y versión de nómina/reglas.
    version = hashlib.sha256(repr(firma).encode()).hexdigest()[:12]
    pref = f'eq_{pid}_{version}'
    label = lambda jid: f'{nombres[jid]} · #{jid}'
    grupos = grupos_actuales(jugadores)

    def guardar(accion, datos):
        try:
            user = st.session_state.get('user') or {}
            store.guardar(pid, jugadores, separados, accion, datos, user.get('username', 'desconocido'))
        except ValueError as exc:
            st.error(str(exc))
            return False
        except Exception:
            logging.getLogger(__name__).exception('No se pudo guardar la acción %s del partido %s', accion, pid)
            st.error('No se pudieron guardar los cambios. Actualizá los datos y volvé a intentar.')
            return False
        st.session_state['_equipos_aviso_v2'] = True
        st.rerun(scope='app')

    confirmado = len(jugadores) == 10 and all(j['equipo'] in (1, 2) for j in jugadores)
    if confirmado:
        st.success('Equipos confirmados')
        equipo = [jid for lado in (1, 2) for jid in ids if por_id[jid]['equipo'] == lado]
        _mostrar_equipos(equipo, por_id, camisetas=True, mostrar_elo=False)
        if st.button('↔️ Intercambiar camisetas', key=pref + '_camisetas'):
            guardar('camisetas', None)
        with st.expander('Rachas de camisetas'):
            if st.button('Consultar rachas', key=pref + '_rachas'):
                partido = next((p for p in core.obtener_partidos_abiertos() if p['id'] == pid), None)
                fecha = core.parsear_fecha(partido['fecha']) if partido else None
                estado['rachas'] = core.calcular_rachas_camiseta(pid, fecha, jugadores=jugadores)
            if 'rachas' in estado:
                for aviso in estado['rachas']:
                    st.write(f"{aviso['nombre']}: {aviso['veces']} partidos con camiseta {aviso['camiseta']}")
                if not estado['rachas']:
                    st.caption('No hay rachas de tres o más partidos.')
        with st.expander('Rehacer equipos'):
            st.caption('Se borran la distribución y las camisetas. Los jugadores y las condiciones se conservan.')
            if st.button('Eliminar equipos confirmados', key=pref + '_eliminar'):
                guardar('eliminar', None)
        return

    st.markdown(f'### 1. Jugadores · {len(jugadores)}/10')
    if len(jugadores) != 10:
        st.info(f'Faltan {max(0, 10-len(jugadores))} jugadores.' if len(jugadores) < 10
                else 'Quitá jugadores hasta dejar diez.')
    with st.form(pref + '_roster'):
        seleccion = st.multiselect('Jugadores del partido', list(nombres), default=ids,
                                   format_func=label, key=pref + '_ids',
                                   help='Buscá por nombre para agregar. Quitá una etiqueta con × para dar de baja.')
        st.caption('Podés cargar todos desde cero. Los cambios se aplican juntos y conservan las confirmaciones de quienes siguen.')
        aplicar = st.form_submit_button('Aplicar jugadores')
    if aplicar:
        if set(seleccion) == set(ids):
            st.info('La lista no cambió.')
        else:
            guardar('roster', seleccion)
    if not jugadores:
        return

    st.markdown('### 2. Condiciones opcionales')
    st.caption('Los grupos siempre juegan juntos. Las parejas separadas siempre van a equipos opuestos.')
    for i, grupo in enumerate(grupos):
        texto, boton = st.columns([5, 1])
        texto.write('🔗 Juntos: ' + ', '.join(nombres[j] for j in grupo))
        if boton.button('Quitar', key=pref + f'_grupo_{i}'):
            guardar('reglas', ([g for k, g in enumerate(grupos) if k != i], separados))
    for i, pareja in enumerate(separados):
        texto, boton = st.columns([5, 1])
        texto.write('↔ Separados: ' + ' / '.join(nombres[j] for j in pareja))
        if boton.button('Quitar', key=pref + f'_par_{i}'):
            guardar('reglas', (grupos, [p for k, p in enumerate(separados) if k != i]))
    with st.form(pref + '_regla', clear_on_submit=True):
        tipo = st.radio('Nueva condición', ['Juegan juntos', 'Juegan separados'], horizontal=True)
        miembros = st.multiselect('Elegí integrantes', ids, format_func=label)
        agregar = st.form_submit_button('Agregar condición')
    if agregar:
        nuevos_grupos = grupos + [miembros] if tipo == 'Juegan juntos' else grupos
        nuevos_pares = separados + [miembros] if tipo == 'Juegan separados' else separados
        try:
            validar_reglas(ids, nuevos_grupos, nuevos_pares)
            if tipo == 'Juegan separados' and any(set(miembros) == set(p) for p in separados):
                raise ValueError('Esta pareja ya tiene una regla de separados.')
            if len(jugadores) == 10:
                propuesta = [dict(j, bloque=None) for j in jugadores]
                asignacion = {jid: i for i, g in enumerate(nuevos_grupos, 1) for jid in g}
                for j in propuesta:
                    j['bloque'] = asignacion.get(j['jugador_id'])
                opts, _ = opciones_validas(propuesta, nuevos_pares, core.construir_bloques, core.generar_opciones_unicas)
                if not opts:
                    raise ValueError('Estas condiciones no permiten formar dos equipos de cinco. Revisá los grupos y las separaciones.')
        except ValueError as exc:
            st.error(str(exc))
        else:
            guardar('reglas', (nuevos_grupos, nuevos_pares))

    if len(jugadores) != 10:
        return
    st.markdown('### 3. Opciones balanceadas')
    if st.button('Generar opciones' if not estado['opciones'] else 'Recalcular opciones',
                 key=pref + '_generar', type='primary'):
        try:
            opciones, diffs = opciones_validas(jugadores, separados, core.construir_bloques,
                                               core.generar_opciones_unicas)
        except ValueError as exc:
            st.error(str(exc))
        else:
            if not opciones:
                st.error('No hay armados posibles con estas condiciones. Quitá o modificá alguna regla.')
            else:
                estado.update(opciones=opciones, diffs=diffs, actual=opciones[0][:], elegida=0,
                              pagina=0, revision=estado['revision'] + 1)
    if not estado['opciones']:
        return
    _mostrar_opciones(estado, por_id, pref)
    indice = estado['elegida']
    st.caption(f"Estás ajustando la opción {indice+1}. Navegar no cambia tu armado; elegir otra opción carga su distribución original.")
    st.markdown('### 4. Ajustar y confirmar')
    st.caption('El ajuste manual es libre: podés separar grupos o juntar jugadores marcados como separados. Las condiciones se conservan para generar nuevas alternativas.')
    actual = estado['actual']
    _mostrar_equipos(actual, por_id)
    swap_key = pref + f"_swap_{estado['revision']}_{indice}_{repr(actual)}"

    def intercambiar():
        mover1 = [jid for g in st.session_state.get(swap_key + '_a', []) for jid in g]
        mover2 = [jid for g in st.session_state.get(swap_key + '_b', []) for jid in g]
        try:
            if not mover1 or len(mover1) != len(mover2):
                raise ValueError('Elegí la misma cantidad de jugadores de cada lado.')
            anterior = estado['actual']
            nuevo = ([j for j in anterior[:5] if j not in mover1] + mover2 +
                     [j for j in anterior[5:] if j not in mover2] + mover1)
            validar_equipos(nuevo[:5], nuevo[5:], ids, [], [])
        except ValueError as exc:
            estado['error_swap'] = str(exc)
        else:
            estado['actual'] = nuevo
            estado.pop('error_swap', None)

    with st.form(swap_key):
        c1, c2 = st.columns(2)
        with c1:
            st.multiselect('Mover del equipo 1', unidades_equipo(actual[:5], []),
                           format_func=lambda g: ' + '.join(nombres[j] for j in g), key=swap_key + '_a')
        with c2:
            st.multiselect('Mover del equipo 2', unidades_equipo(actual[5:], []),
                           format_func=lambda g: ' + '.join(nombres[j] for j in g), key=swap_key + '_b')
        st.form_submit_button('↔️ Intercambiar selección', on_click=intercambiar)
    if estado.get('error_swap'):
        st.error(estado['error_swap'])

    def restaurar():
        estado['actual'] = estado['opciones'][estado['elegida']][:]
        estado.pop('error_swap', None)

    c1, c2 = st.columns(2)
    c1.button('Restaurar alternativa', key=pref + '_restaurar', on_click=restaurar)
    if c2.button('✅ Confirmar equipos', key=pref + '_confirmar', type='primary'):
        guardar('confirmar', estado['actual'])


def _mostrar_opciones(estado, por_id, pref):
    """Tres tarjetas por página. Los callbacks sólo actualizan estado local."""
    opciones = estado['opciones']
    paginas = (len(opciones) + 2) // 3
    estado['pagina'] = min(max(estado.get('pagina', 0), 0), paginas - 1)
    pagina = estado['pagina']
    inicio = pagina * 3
    fin = min(inicio + 3, len(opciones))
    st.caption(f'Opciones {inicio+1}–{fin} de {len(opciones)} · Página {pagina+1}/{paginas}')
    # El marcador limita el ajuste móvil a esta fila; no afecta al editor ni al menú.
    st.markdown("""
<style>
.eq-option-title { font-size:1.1rem; font-weight:700; margin-bottom:6px; }
.eq-option-diff { font-weight:600; margin-bottom:18px; }
.eq-option-team { font-weight:650; border-left:3px solid; padding-left:8px; margin:14px 0 6px; }
.eq-option-higher { font-size:0.65rem; opacity:0.7; margin-left:5px; vertical-align:middle; }
.eq-option-names { list-style:none; padding:0; margin:0; overflow-wrap:anywhere; }
.eq-option-names li { padding:3px 0; line-height:1.4; }
@media (max-width:760px) {
    [data-testid="stHorizontalBlock"]:has([data-equipos-opcion]) { flex-direction:column; }
    [data-testid="stHorizontalBlock"]:has([data-equipos-opcion]) > [data-testid="stColumn"] {
        width:100% !important; flex:1 1 100% !important; min-width:0 !important;
    }
}
</style>
""", unsafe_allow_html=True)

    def elegir(indice):
        estado['actual'] = opciones[indice][:]
        estado['elegida'] = indice
        estado.pop('error_swap', None)

    def navegar(destino):
        estado['pagina'] = destino

    columnas = st.columns(fin - inicio)
    for indice, columna in zip(range(inicio, fin), columnas):
        opcion = opciones[indice]
        seleccionada = indice == estado['elegida']
        with columna:
            with st.container(border=True):
                titulo = f'Opción {indice+1}' + (' · En ajuste' if seleccionada else '')
                html = (f'<div data-equipos-opcion="{indice+1}">'
                        f'<div class="eq-option-title">{titulo}</div>'
                        f'<div class="eq-option-diff">Diferencia: {estado["diffs"][indice]} ELO</div>')
                totales = [int(sum(por_id[jid]['elo'] for jid in opcion[lado*5:(lado+1)*5]))
                           for lado in range(2)]
                for lado, color in enumerate(('#60a5fa', '#fb923c')):
                    equipo = opcion[lado*5:(lado+1)*5]
                    total = totales[lado]
                    indicador = ('<span class="eq-option-higher" title="Mayor ELO" aria-label="Mayor ELO">▲</span>'
                                 if total > totales[1-lado] else '')
                    html += (f'<div class="eq-option-team" style="color:{color};border-color:{color};">'
                             f'Equipo {lado+1} · {total} ELO{indicador}</div><ul class="eq-option-names">')
                    html += ''.join(f'<li>{escape(str(por_id[jid]["nombre"]))}</li>' for jid in equipo)
                    html += '</ul>'
                st.markdown(html + '</div>', unsafe_allow_html=True)
                st.button('En ajuste' if seleccionada else 'Elegir y ajustar',
                          key=pref + f"_elegir_{estado['revision']}_{indice}",
                          disabled=seleccionada, use_container_width=True,
                          on_click=elegir, args=(indice,))
    anterior, siguiente = st.columns(2)
    anterior.button('← Anteriores', key=pref + '_anteriores', disabled=pagina == 0,
                    on_click=navegar, args=(pagina-1,), use_container_width=True)
    siguiente.button('Siguientes →', key=pref + '_siguientes', disabled=pagina == paginas-1,
                     on_click=navegar, args=(pagina+1,), use_container_width=True)


def _mostrar_equipos(actual, por_id, camisetas=False, mostrar_elo=True):
    totales = [int(sum(por_id[jid]['elo'] for jid in actual[lado*5:(lado+1)*5]))
               for lado in range(2)]
    columnas = st.columns(2)
    for lado, columna in enumerate(columnas):
        equipo = actual[lado*5:(lado+1)*5]
        total = totales[lado]
        with columna:
            color = ('#60a5fa', '#fb923c')[lado]
            st.markdown(
                f'<h4 style="color:{color};border-left:4px solid {color};padding-left:12px;">'
                f'<span aria-hidden="true">●</span> Equipo {lado+1}</h4>',
                unsafe_allow_html=True)
            if mostrar_elo:
                etiqueta = (
                    '<span style="display:inline-block;margin-left:8px;padding:2px 8px;'
                    'border:1px solid currentColor;border-radius:999px;font-size:0.75rem;">'
                    'Mayor ELO</span>' if total > totales[1-lado] else '')
                st.markdown(
                    f'<div style="font-size:0.875rem;opacity:0.7;margin-bottom:16px;">'
                    f'{total} ELO{etiqueta}</div>', unsafe_allow_html=True)
            if camisetas:
                camiseta = next((por_id[jid]['camiseta'] for jid in equipo if por_id[jid]['camiseta']), 'Sin asignar')
                estilos = {
                    'clara': ('#e0e0e0', '#222', '#bdbdbd', '⚪ Camiseta Clara'),
                    'oscura': ('#222', '#fff', '#666', '⚫ Camiseta Oscura'),
                }
                fondo, texto, borde, etiqueta = estilos.get(
                    camiseta, ('#757575', '#fff', '#999', 'Camiseta sin asignar'))
                st.markdown(
                    f'<span style="display:inline-block;padding:8px 14px;'
                    f'border-radius:999px;font-weight:600;background:{fondo};'
                    f'color:{texto};border:1px solid {borde};margin:4px 0 18px;">'
                    f'{etiqueta}</span>', unsafe_allow_html=True)
                items = ''.join(f'<li style="padding:8px 0;">{escape(str(por_id[jid]["nombre"]))}</li>'
                                for jid in equipo)
                st.markdown(f'<ul style="padding-left:24px;margin:0;">{items}</ul>',
                            unsafe_allow_html=True)
                continue
            for jid in equipo:
                st.write(por_id[jid]['nombre'])
    if mostrar_elo:
        st.markdown(f'**Diferencia: {abs(totales[0]-totales[1])} ELO**')
