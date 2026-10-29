"""Pruebas SQL sin credenciales, Streamlit ni acceso a Turso."""
import ast
import sqlite3
import unittest
from pathlib import Path


def load_helpers(connection):
    source = Path(__file__).resolve().parents[1] / 'jugador_panel.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    names = {'_row_to_dict', '_rows_to_dicts', '_datos_partidos',
             '_jugadores_en_partido', '_waitlist_get', '_waitlist_is_in',
             '_equipos_estan_generados', '_cancha_label'}
    module = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name in names], type_ignores=[])
    env = {'get_connection': lambda: connection, 'CUPO_PARTIDO': 10}
    exec(compile(module, str(source), 'exec'), env)
    return env


class BatchQueriesTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE partidos(id INTEGER PRIMARY KEY, cancha_id INTEGER);
            CREATE TABLE canchas(id INTEGER PRIMARY KEY, nombre TEXT, direccion TEXT);
            CREATE TABLE jugadores(id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE partido_jugadores(partido_id, jugador_id,
                confirmado_por_jugador, equipo, ingreso_desde_espera, camiseta);
            CREATE TABLE lista_espera(partido_id, jugador_id, created_at);
            INSERT INTO canchas VALUES(1, 'Cancha A', ' Calle 1 ');
        ''')
        self.conn.executemany('INSERT INTO partidos VALUES(?, ?)',
                              [(i, 1 if i % 2 else None) for i in range(1, 402)])
        self.conn.executemany('INSERT INTO jugadores VALUES(?, ?)',
                              [(i, f'Jugador {i:02}') for i in range(1, 14)])
        self.conn.executemany('INSERT INTO partido_jugadores VALUES(1, ?, 1, ?, 0, ?)',
                              [(i, 1 if i <= 5 else 2, 'clara') for i in range(1, 11)])
        self.conn.execute("INSERT INTO lista_espera VALUES(1, 12, '2026-10-29 12:01')")
        self.conn.execute("INSERT INTO lista_espera VALUES(1, 11, '2026-10-29 12:00')")
        self.conn.commit()
        self.helpers = load_helpers(self.conn)
        self.queries = []
        self.conn.set_trace_callback(self.queries.append)

    def tearDown(self):
        self.conn.close()

    def test_batch_matches_original_queries(self):
        data = self.helpers['_datos_partidos']([{'id': 1}, {'id': 2}])
        self.assertEqual(len(self.queries), 3)
        for pid in (1, 2):
            original = self.helpers['_jugadores_en_partido'](pid)
            self.assertEqual([{k: v for k, v in r.items() if k != 'partido_id'}
                              for r in data[pid]['inscritos']], original)
            self.assertEqual(data[pid]['espera'], self.helpers['_waitlist_get'](pid))
            self.assertEqual(data[pid]['equipos'], self.helpers['_equipos_estan_generados'](pid))
            self.assertEqual(data[pid]['cancha'], self.helpers['_cancha_label'](1 if pid == 1 else None))
        self.assertTrue(data[1]['equipos'])
        self.assertFalse(data[2]['equipos'])

    def test_empty_and_large_batches(self):
        self.assertEqual(self.helpers['_datos_partidos']([]), {})
        self.assertEqual(self.queries, [])
        data = self.helpers['_datos_partidos']([{'id': i} for i in range(1, 402)])
        self.assertEqual(len(data), 401)
        self.assertEqual(len(self.queries), 6)

    def test_reads_are_fresh_after_write(self):
        self.helpers['_datos_partidos']([{'id': 1}])
        self.conn.execute('DELETE FROM partido_jugadores WHERE jugador_id=1')
        self.conn.commit()
        data = self.helpers['_datos_partidos']([{'id': 1}])
        self.assertEqual(len(data[1]['inscritos']), 9)
        self.assertFalse(data[1]['equipos'])


if __name__ == '__main__':
    unittest.main()
