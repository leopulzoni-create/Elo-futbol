import sqlite3
from db import get_connection

DB_NAME = "elo_futbol.db"

def get_connection():
    conn = get_connection()

    return conn