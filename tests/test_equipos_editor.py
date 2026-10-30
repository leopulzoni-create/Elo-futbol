import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import equipos_store as store
from db import ConnProxy
from equipos_logic import opciones_validas, validar_equipos, validar_reglas, grupos_actuales
from equipos_fixtures import load_core, roster

ROOT = Path(__file__).resolve().parents[1]
CORE = load_core((ROOT / 'equipos.py').read_text(encoding='utf-8'))


class GeneratorTest(unittest.TestCase):
    def test_legacy_results_unchanged(self):
        cases = json.loads((ROOT / 'tests/equipos_baseline.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(groups=grupos_actuales(case['jugadores'])):
                opciones, diffs = opciones_validas(case['jugadores'], [], CORE['construir_bloques'], CORE['generar_opciones_unicas'])
                nombres = {j['jugador_id']: j['nombre'] for j in case['jugadores']}
                self.assertEqual([[nombres[j] for j in opt] for opt in opciones], case['opciones'])
                self.assertEqual(diffs, case['diffs'])

    def test_separations_applied_before_top_twelve(self):
        jugadores = roster()
        # Este cruce es muy desbalanceado y no figura en las mejores doce originales.
        separados = [(a, b) for a in range(1, 6) for b in range(6, 11)]
        opciones, _ = opciones_validas(jugadores, separados, CORE['construir_bloques'], CORE['generar_opciones_unicas'])
        self.assertEqual(len(opciones), 1)
        self.assertEqual({frozenset(opciones[0][:5]), frozenset(opciones[0][5:])},
                         {frozenset(range(1, 6)), frozenset(range(6, 11))})

    def test_invalid_rules_and_swaps(self):
        with self.assertRaises(ValueError):
            validar_reglas(range(1, 11), [[1, 2]], [(1, 2)])
        with self.assertRaises(ValueError):
            validar_reglas(range(1, 11), [[1, 2], [2, 3]], [])
        with self.assertRaises(ValueError):
            validar_equipos([1, 3, 4, 5, 6], [2, 7, 8, 9, 10], range(1, 11), [[1, 2]], [])
        with self.assertRaises(ValueError):
            validar_equipos(list(range(1, 6)), list(range(6, 11)), range(1, 11), [], [(1, 2)])
        validar_equipos(list(range(1, 6)), list(range(6, 11)), range(1, 11), [[1, 2]], [(1, 6)])

    def test_impossible_separations(self):
        opciones, _ = opciones_validas(roster(), [(1, 2), (2, 3), (1, 3)],
                                       CORE['construir_bloques'], CORE['generar_opciones_unicas'])
        self.assertEqual(opciones, [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        conn = sqlite3.connect(self.path)
        conn.executescript('''
            CREATE TABLE partidos(id PRIMARY KEY, tipo, ganador, diferencia_gol, equipos_generados_por);
            INSERT INTO partidos VALUES(1, 'abierto', NULL, NULL, NULL);
            CREATE TABLE jugadores(id PRIMARY KEY, nombre, elo_actual, estado);
            CREATE TABLE partido_jugadores(id INTEGER PRIMARY KEY, partido_id, jugador_id,
                confirmado_por_jugador, bloque, equipo, camiseta);
            CREATE TABLE historial_elo(jugador_id, elo_antes, elo_despues);
            INSERT INTO historial_elo VALUES(1, 1000, 1100);
        ''')
        conn.executemany('INSERT INTO jugadores VALUES(?, ?, ?, ?)',
                          [(j['jugador_id'], j['nombre'], j['elo'], 'activo') for j in roster()])
        conn.commit()
        conn.close()
        self.patcher = patch.object(store, 'get_connection', self.connect)
        self.patcher.start()
        store.ensure_schema()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return ConnProxy(conn)

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def save(self, accion, datos, snapshot=None):
        jugadores, separados, _ = store.cargar(1) if snapshot is None else snapshot
        store.guardar(1, jugadores, separados, accion, datos, 'admin')

    def test_create_from_empty_and_confirm_preserves_elo(self):
        self.save('roster', list(range(1, 11)))
        self.save('reglas', ([[1, 2]], [(1, 6)]))
        self.save('confirmar', list(range(1, 11)))
        jugadores, separados, _ = store.cargar(1)
        self.assertEqual([j['equipo'] for j in jugadores], [1]*5 + [2]*5)
        self.assertEqual([j['elo'] for j in jugadores], [700+i*77 for i in range(10)])
        self.assertEqual(separados, [(1, 6)])
        with closing(self.connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT elo_antes, elo_despues FROM historial_elo')
            self.assertEqual(tuple(cur.fetchone().values()), (1000, 1100))
            cur.fetchall()
        self.save('camisetas', None)
        self.assertEqual(store.cargar(1)[0][0]['camiseta'], 'oscura')
        self.save('eliminar', None)
        self.assertTrue(all(j['equipo'] is None for j in store.cargar(1)[0]))

    def test_stale_snapshot_rejected(self):
        snapshot = store.cargar(1)
        self.save('roster', [1, 2])
        with self.assertRaisesRegex(ValueError, 'cambió'):
            self.save('roster', [3, 4], snapshot)
        self.assertEqual({j['jugador_id'] for j in store.cargar(1)[0]}, {1, 2})

    def test_rules_survive_reload_and_removed_members_are_cleaned(self):
        self.save('roster', [1, 2, 3, 4])
        self.save('reglas', ([[1, 2]], [(2, 3)]))
        self.save('roster', [1, 3, 4])
        jugadores, separados, _ = store.cargar(1)
        self.assertEqual(separados, [])
        self.assertEqual(grupos_actuales(jugadores), [])

    def test_invalid_confirmation_rolls_back(self):
        self.save('roster', list(range(1, 11)))
        self.save('reglas', ([], [(1, 2)]))
        with self.assertRaises(ValueError):
            self.save('confirmar', list(range(1, 11)))
        self.assertTrue(all(j['equipo'] is None for j in store.cargar(1)[0]))

    @unittest.skipUnless(__import__('importlib.util', fromlist=['find_spec']).find_spec('streamlit'),
                         'Requiere Streamlit para probar la pantalla')
    def test_streamlit_editor_flow(self):
        from streamlit.testing.v1 import AppTest
        import equipos as core
        with closing(self.connect()) as conn:
            cur = conn.cursor()
            cur.execute('ALTER TABLE partidos ADD COLUMN numero_publico INTEGER')
            cur.execute('ALTER TABLE partidos ADD COLUMN fecha TEXT')
            cur.execute('ALTER TABLE partidos ADD COLUMN hora INTEGER')
            cur.execute('ALTER TABLE partidos ADD COLUMN cancha_id INTEGER')
            cur.execute("UPDATE partidos SET numero_publico=1, fecha='2026-10-29', hora=1900")
            cur.execute('CREATE TABLE canchas(id PRIMARY KEY, nombre, direccion)')
            conn.commit()
        with patch.object(core, 'db_get_connection', self.connect):
            app = AppTest.from_string("import equipos_ui; equipos_ui.panel_generacion()", default_timeout=15)
            app.run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(app.multiselect), 1)
            app.multiselect[0].set_value(list(range(1, 11)))
            next(b for b in app.button if b.label == 'Aplicar jugadores').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(store.cargar(1)[0]), 10)
            app.multiselect[1].set_value([1, 2])
            next(b for b in app.button if b.label == 'Agregar condición').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(grupos_actuales(store.cargar(1)[0]), [[1, 2]])
            next(b for b in app.radio if b.label == 'Nueva condición').set_value('Juegan separados')
            app.multiselect[1].set_value([1, 6])
            next(b for b in app.button if b.label == 'Agregar condición').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(store.cargar(1)[1], [(1, 6)])
            next(b for b in app.button if b.label == 'Generar opciones').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(any(b.label == '✅ Confirmar equipos' for b in app.button))
            # Cambiar de alternativa y hacer un intercambio válido usando grupos completos.
            selector = next(x for x in app.selectbox if x.label.startswith('Elegir alternativa'))
            if len(selector.options) > 1:
                selector.set_value(1).run()
            self.assertFalse(app.exception, str(app.exception))
            estado = app.session_state['equipos_editor_1']
            anterior = estado['actual'][:]
            from equipos_logic import unidades_equipo
            grupos = grupos_actuales(store.cargar(1)[0])
            unidades1 = unidades_equipo(anterior[:5], grupos)
            unidades2 = unidades_equipo(anterior[5:], grupos)
            seleccion_swap = None
            for a in unidades1:
                for b in unidades2:
                    if len(a) != len(b):
                        continue
                    candidato = [j for j in anterior[:5] if j not in a] + list(b)
                    candidato += [j for j in anterior[5:] if j not in b] + list(a)
                    try:
                        validar_equipos(candidato[:5], candidato[5:], range(1, 11), grupos, [(1, 6)])
                    except ValueError:
                        continue
                    seleccion_swap = (a, b, candidato)
                    break
                if seleccion_swap:
                    break
            self.assertIsNotNone(seleccion_swap)
            a, b, candidato = seleccion_swap
            next(x for x in app.multiselect if x.label == 'Mover del equipo 1').set_value([a])
            next(x for x in app.multiselect if x.label == 'Mover del equipo 2').set_value([b])
            next(x for x in app.button if x.label == '↔️ Intercambiar selección').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(app.session_state['equipos_editor_1']['actual'], candidato)
            self.assertTrue(all(j['equipo'] is None for j in store.cargar(1)[0]))
            next(b for b in app.button if b.label == '✅ Confirmar equipos').click().run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(all(j['equipo'] in (1, 2) for j in store.cargar(1)[0]))
            with closing(self.connect()) as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO partidos(id, tipo, numero_publico, fecha, hora) VALUES(2, 'abierto', 2, '2026-10-30', 1900)")
                conn.commit()
            app.run()
            next(x for x in app.selectbox if x.label == 'Partido').set_value(2).run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(len(app.multiselect), 1)
            self.assertFalse(any(b.label == '✅ Confirmar equipos' for b in app.button))
            self.assertEqual(app.session_state['equipos_editor_2']['opciones'], [])

    @unittest.skipUnless(__import__('importlib.util', fromlist=['find_spec']).find_spec('streamlit'),
                         'Requiere Streamlit para probar la pantalla')
    def test_local_interactions_do_not_query_database(self):
        from streamlit.testing.v1 import AppTest
        # Probar el fragmento aislado: cualquier consulta es un error.
        app = AppTest.from_string("""
from equipos_ui import _editor
jugadores = [dict(pj_id=i, jugador_id=i, nombre=f'Jugador {i}', elo=800+i*50,
                 bloque=None, confirmado=0, equipo=None, camiseta=None) for i in range(1,11)]
_editor(1, jugadores, [], [])
""", default_timeout=15)
        with patch.object(store, 'get_connection', side_effect=AssertionError('Consulta durante edición local')):
            app.run()
            self.assertFalse(app.exception, str(app.exception))
            next(b for b in app.button if b.label == 'Generar opciones').click().run()
            self.assertFalse(app.exception, str(app.exception))
            next(x for x in app.selectbox if x.label.startswith('Elegir alternativa')).set_value(1).run()
            self.assertFalse(app.exception, str(app.exception))
            estado = app.session_state['equipos_editor_1']
            a, b = estado['actual'][0], estado['actual'][5]
            next(x for x in app.multiselect if x.label == 'Mover del equipo 1').set_value([(a,)])
            next(x for x in app.multiselect if x.label == 'Mover del equipo 2').set_value([(b,)])
            next(x for x in app.button if x.label == '↔️ Intercambiar selección').click().run()
            self.assertFalse(app.exception, str(app.exception))
            next(x for x in app.button if x.label == 'Restaurar alternativa').click().run()
            self.assertFalse(app.exception, str(app.exception))

    def test_shirt_streaks_use_one_connection_and_existing_roster(self):
        self.save('roster', list(range(1, 11)))
        with closing(self.connect()) as conn:
            cur = conn.cursor()
            cur.execute('ALTER TABLE partidos ADD COLUMN fecha TEXT')
            cur.execute("UPDATE partidos SET fecha='2026-10-29'")
            cur.execute("UPDATE partido_jugadores SET camiseta='clara' WHERE jugador_id=1")
            cur.executemany("INSERT INTO partidos(id, fecha) VALUES(?, ?)",
                            [(2, '2026-10-27'), (3, '2026-10-25'), (4, '2026-01-01'), (5, '2026-11-01')])
            cur.executemany('INSERT INTO partido_jugadores(partido_id, jugador_id, camiseta) VALUES(?, 1, ?)',
                            [(2, 'clara'), (3, 'clara'), (4, 'oscura'), (5, 'oscura')])
            conn.commit()
        jugadores, _, _ = store.cargar(1)
        conexiones = []
        def connect():
            conexiones.append(1)
            return self.connect()
        with patch.dict(CORE, get_connection=connect):
            resultado = CORE['calcular_rachas_camiseta'](1, CORE['datetime'](2026, 10, 29), jugadores)
        self.assertEqual(len(conexiones), 1)
        self.assertEqual(resultado, [dict(nombre='Jugador 01', camiseta='clara', veces=3)])

    def test_roster_limits(self):
        for ids in ([1, 1], list(range(1, 12)), [123]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.save('roster', ids)
        self.assertEqual(store.cargar(1)[0], [])


@unittest.skipUnless(__import__('importlib.util', fromlist=['find_spec']).find_spec('libsql'),
                     'Requiere libsql para verificar el controlador de Turso')
class LibsqlStoreTest(StoreTest):
    def connect(self):
        import libsql
        return ConnProxy(libsql.connect(self.path))


if __name__ == '__main__':
    unittest.main()
