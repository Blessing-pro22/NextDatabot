import sqlite3

conn = sqlite3.connect("blessings_data_hub_users.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    username TEXT NOT NULL
)
""")

conn.commit()
conn.close()


fetch_Data_query = """
    SELECT id, name FROM users
"""
rows = []

with sqlite3.connect("blessings_data_hub_users.db") as conn:
    cursor = conn.cursor()
    cursor.execute(fetch_Data_query)
    rows = cursor.fetchall()

# for row in rows:
#     print(f"ID: {row[0]}, Name: {row[1]}")
