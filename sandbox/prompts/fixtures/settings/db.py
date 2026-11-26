import os
import sqlite3

DB_PATH = os.path.join("data", "app.db")


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists notes (id integer primary key, text text)")
    return conn


def all_notes(path=DB_PATH):
    with connect(path) as conn:
        return conn.execute("select id, text from notes").fetchall()
