import sqlite3

conn = sqlite3.connect("app/db/store_intelligence.db")
cursor = conn.cursor()

# Get table names
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
print("Tables:", cursor.fetchall())

# Count events
cursor.execute("SELECT COUNT(*) FROM events;")
print("Total events:", cursor.fetchone()[0])

# Count events by store_id
cursor.execute("SELECT store_id, COUNT(*) FROM events GROUP BY store_id;")
print("Events by store:", cursor.fetchall())

# Count events by is_staff
cursor.execute("SELECT is_staff, COUNT(*) FROM events GROUP BY is_staff;")
print("Events by is_staff:", cursor.fetchall())

# Query a few events
cursor.execute("SELECT event_id, store_id, visitor_id, event_type, is_staff FROM events LIMIT 5;")
print("Sample events:")
for row in cursor.fetchall():
    print(row)

conn.close()
