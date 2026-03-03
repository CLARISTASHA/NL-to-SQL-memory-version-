import mysql.connector
import os

print("Connecting to database...")

conn = mysql.connector.connect(
    host="13.232.85.218",
    user="oneapp_usr",
    password="JJ3v31YYHRGX2EC5",
    database="only_app",
    port=3306,
    auth_plugin="mysql_native_password"
)


cursor = conn.cursor()
print("Connected successfully!\n")

cursor.execute("SHOW TABLES")
tables = [row[0] for row in cursor.fetchall()]

print("Tables found:")
for t in tables:
    print("-", t)

schema_text = ""

for table in tables:
    schema_text += f"\nTABLE: {table}\n"

    cursor.execute(f"DESCRIBE {table}")
    for col in cursor.fetchall():
        name, col_type, nullable, key, default, extra = col
        line = f"- {name} {col_type}"
        if key:
            line += f" ({key})"
        schema_text += line + "\n"

# Save schema
with open("schema_raw.txt", "w") as f:
    f.write(schema_text)

print("\nSchema saved to schema_raw.txt")


cursor.close()
conn.close()
