import sqlite3
from db import get_connection

DB_NAME = "elo_futbol.db"

def get_connection():
    from db import get_connection as _gc
    return _gc()

    return conn