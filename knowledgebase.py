import mysql.connector

conn = mysql.connector.connect(
    host="13.232.85.218",
    user="oneapp_usr",
    password="JJ3v31YYHRGX2EC5",
    database="only_app"
)

cur = conn.cursor()

cur.execute("SELECT DISTINCT title FROM user_task")
titles = [r[0] for r in cur.fetchall()]

cur.execute("SELECT DISTINCT assigned_to FROM user_task")
users = [r[0] for r in cur.fetchall()]

with open("values.txt", "w", encoding="utf-8") as f:
    f.write("Task Titles:\n")
    for t in titles:
        f.write(f"- {t}\n")

    f.write("\nUsers:\n")
    for u in users:
        f.write(f"- {u}\n")

cur.close()
conn.close()