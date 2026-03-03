import requests
import mysql.connector
import re

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

DB_CONFIG = {
    "host": "13.232.85.218",
    "user": "oneapp_usr",
    "password": "JJ3v31YYHRGX2EC5",
    "database": "only_app",
    "port": 3306,
    "auth_plugin": "mysql_native_password",
}

OLLAMA_URL  = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "phi3"
MAX_RETRIES  = 3
HISTORY_WINDOW = 3

# ─────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────

with open("schema_final.txt") as f:
    schema_text = f.read()

# ─────────────────────────────────────────────
# PROMPT COMPONENTS
# ─────────────────────────────────────────────

SCHEMA_HINT = """
AVAILABLE TABLES AND COLUMNS (USE ONLY THESE):

  users       -> user_id (PK), user_name, email
  tasks       -> task_id (PK), user_id (FK->users.user_id), task_name,
                 status, priority, completed_at, parent_id
  user_task   -> id (PK), user_id (FK->users.user_id), task_id (FK->tasks.task_id)

VALID JOIN CONDITIONS:
  tasks     JOIN users    ON tasks.user_id     = users.user_id
  user_task JOIN users    ON user_task.user_id = users.user_id
  user_task JOIN tasks    ON user_task.task_id = tasks.task_id

COUNTING ASSIGNED TASKS — CRITICAL RULE:
  To count tasks assigned to a user you MUST use user_task, NOT tasks directly.
  CORRECT:
    SELECT COUNT(*) AS task_count
    FROM user_task ut
    JOIN users u ON ut.user_id = u.user_id
    WHERE u.user_name = 'Hari';

  WRONG (returns 0 because tasks.user_id may differ from user_task assignments):
    SELECT COUNT(*) FROM tasks t
    JOIN users u ON t.user_id = u.user_id
    WHERE u.user_name = 'Hari';

FORBIDDEN:
  - users.id in ANY join condition (must always be users.user_id)
  - Tables not listed above (e.g. assignment, task_user)
  - Joining on user_name or assigned_to strings
"""

BUSINESS_RULES = """
TASK LOGIC:
  - Main task completed: completed_at IS NOT NULL AND parent_id = 0
  - Pending tasks: completed_at IS NULL
  - High-priority: priority = 'high'

USERNAME RULES:
  - A capitalised word like "Hari" is ALWAYS a username — never a date or status.
  - Always filter with: WHERE u.user_name = '<name>'
  - NEVER treat a person's name as a date or any other value.
"""

FEW_SHOT_EXAMPLES = """
==== CORRECT EXAMPLES ====

Q: How many tasks assigned to Hari
SQL:
SELECT COUNT(*) AS task_count
FROM user_task ut
JOIN users u ON ut.user_id = u.user_id
WHERE u.user_name = 'Hari';
Explanation:
This SQL counts all tasks assigned to Hari using the user_task table, which is the
correct assignment table. It joins on the numeric user_id and filters by username.

Q: How many tasks has Hari completed?
SQL:
SELECT COUNT(*) AS completed_count
FROM user_task ut
JOIN users u ON ut.user_id = u.user_id
JOIN tasks t ON ut.task_id = t.task_id
WHERE u.user_name = 'Hari'
  AND t.completed_at IS NOT NULL
  AND t.parent_id = 0;
Explanation:
This SQL counts completed main tasks for Hari by joining user_task with both
users and tasks, filtering for completed_at IS NOT NULL and parent_id = 0.

Q: Show all tasks assigned to Hari
SQL:
SELECT t.task_name, t.status, t.priority
FROM user_task ut
JOIN users u ON ut.user_id = u.user_id
JOIN tasks t ON ut.task_id = t.task_id
WHERE u.user_name = 'Hari';
Explanation:
This SQL retrieves task details for Hari by going through the user_task assignment
table, joining both users and tasks on their numeric IDs.

Q: List pending tasks for Hari
SQL:
SELECT t.task_name, t.priority
FROM user_task ut
JOIN users u ON ut.user_id = u.user_id
JOIN tasks t ON ut.task_id = t.task_id
WHERE u.user_name = 'Hari'
  AND t.completed_at IS NULL;
Explanation:
This SQL lists all tasks not yet completed (completed_at IS NULL) assigned to Hari
via the user_task table.

==== WRONG PATTERNS — NEVER DO THESE ====

-- WRONG: joins tasks directly, misses user_task assignments, returns 0
SELECT COUNT(*) FROM tasks t JOIN users u ON t.user_id = u.user_id WHERE u.user_name = 'Hari';

-- WRONG: uses users.id instead of users.user_id
JOIN users u ON ut.user_id = u.id

-- WRONG: hallucinated table
FROM assignment a JOIN tasks t ON a.task_id = t.id

-- WRONG: name used as a date
WHERE t.created_at = 'Hari'
"""

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

conn   = get_connection()
cursor = conn.cursor()

# ─────────────────────────────────────────────
# SQL VALIDATION
# ─────────────────────────────────────────────

ALLOWED_TABLES = {"users", "tasks", "user_task"}

NON_SQL_PHRASES = [
    "let me", "here is", "here's", "this query",
    "note:", "please", "the following", "i will", "i'll",
    "make sure", "ensure", "remember",
]

def validate_sql(sql):
    errors = []
    sql_lower = sql.lower()

    for phrase in NON_SQL_PHRASES:
        if phrase in sql_lower:
            errors.append(f"Non-SQL text detected: '{phrase}'")

    if re.search(r'\busers\.id\b', sql, re.IGNORECASE):
        errors.append("Forbidden: used 'users.id' — must use 'users.user_id'")

    referenced = set(re.findall(r'\b(?:from|join)\s+([a-z_][a-z0-9_]*)', sql_lower))
    for tbl in referenced:
        if tbl not in ALLOWED_TABLES:
            errors.append(f"Hallucinated table: '{tbl}' does not exist")

    if len(sql.strip()) < 10:
        errors.append("SQL too short — extraction likely failed")

    if "select" not in sql_lower:
        errors.append("No SELECT found in extracted SQL")

    # Catch direct tasks join for count queries (the main zero-result bug)
    if "count" in sql_lower and "user_task" not in sql_lower and "from tasks" in sql_lower:
        errors.append(
            "Count query uses 'tasks' directly — must use 'user_task' for assigned task counts"
        )

    return errors

# ─────────────────────────────────────────────
# USERNAME DETECTION
# ─────────────────────────────────────────────

def detect_username(question):
    pattern = r'\b(?:for|to|by|of|about|assigned\s+to|assigned\s+for|belonging\s+to)\s+([A-Z][a-z]+)'

    match = re.search(pattern, question)
    if match:
        return match.group(1)

    titled = question.title()
    match = re.search(pattern, titled)
    if match:
        return match.group(1)

    SQL_KEYWORDS = {
        "Select", "From", "Where", "Join", "On", "And", "Or", "Group",
        "Order", "By", "Having", "Limit", "Count", "Show", "List", "Find",
        "Get", "Give", "What", "How", "Many", "Tasks", "Users", "Completed",
        "Pending", "High", "All", "Are", "The", "For", "Task", "Has",
        "Assigned", "Is", "In", "With", "My", "Me",
    }

    for word in question.split():
        clean = re.sub(r'[^A-Za-z]', '', word)
        if clean and clean[0].isupper() and clean not in SQL_KEYWORDS:
            return clean

    for word in titled.split():
        clean = re.sub(r'[^A-Za-z]', '', word)
        if clean and len(clean) > 2 and clean not in SQL_KEYWORDS:
            return clean

    return None

# ─────────────────────────────────────────────
# SQL + EXPLANATION EXTRACTION
# ─────────────────────────────────────────────

def extract_sql_and_explanation(llm_response):
    text = re.sub(r"<[^>]+>", "", llm_response)
    text = re.sub(r"```sql\s*", "", text, flags=re.I)
    text = re.sub(r"```\s*", "", text)

    sql, explanation = "", ""

    if re.search(r'\bSQL\s*:', text, re.I):
        parts    = re.split(r'\bSQL\s*:', text, flags=re.I)
        remainder = parts[1] if len(parts) > 1 else ""
    else:
        remainder = text

    if re.search(r'\bExplanation\s*:', remainder, re.I):
        sql_part, exp_part = re.split(r'\bExplanation\s*:', remainder, flags=re.I)
        sql = sql_part.strip()
        for stop in ["User Question:", "Note:", "Output:"]:
            if stop in exp_part:
                exp_part = exp_part.split(stop)[0]
        explanation = exp_part.strip()
    else:
        for stop in ["User Question:", "Note:", "Output:"]:
            if stop in remainder:
                remainder = remainder.split(stop)[0]
        sql = remainder.strip()

    for stop in ["User Question:", "Note:", "Output:"]:
        if stop in sql:
            sql = sql.split(stop)[0]

    return sql.strip(), explanation.strip()

# ─────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────

conversation_history = []

def ask_ollama(question, error_feedback=""):
    conversation_history.append(question)
    context = "\n".join(conversation_history[-HISTORY_WINDOW:])

    detected_name = detect_username(question)
    name_hint = (
        f"\nDETECTED USERNAME: '{detected_name}'\n"
        f"-> Use: WHERE u.user_name = '{detected_name}'\n"
        f"-> Do NOT treat '{detected_name}' as a date, status, or anything else.\n"
        if detected_name else ""
    )

    correction_block = (
        f"\nPREVIOUS ATTEMPT ERRORS (fix all before responding):\n{error_feedback}\n"
        if error_feedback else ""
    )

    prompt = f"""You are a strict MySQL expert. Output ONLY the format shown below — nothing else.

{SCHEMA_HINT}

{BUSINESS_RULES}

{FEW_SHOT_EXAMPLES}

Full schema:
{schema_text}

Recent conversation:
{context}
{name_hint}{correction_block}
OUTPUT FORMAT (follow exactly):

SQL:
<SQL query>

Explanation:
<1-2 plain English sentences describing what the query does>

User Question: {question}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]

# ─────────────────────────────────────────────
# SQL EXECUTION
# ─────────────────────────────────────────────

def execute_sql(sql):
    global conn, cursor
    try:
        if not conn.is_connected():
            conn   = get_connection()
            cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description] if cursor.description else []
        return cols, rows
    except mysql.connector.Error as e:
        return None, f"SQL Error: {e}"

# ─────────────────────────────────────────────
# OUTPUT FORMATTING  (clean, no dividers)
# ─────────────────────────────────────────────

def format_raw_result(cols, rows):
    if isinstance(rows, str):
        return rows
    if not rows:
        return "(no rows returned)"

    lines = ["  " + " | ".join(str(c) for c in cols)]
    for row in rows[:10]:
        lines.append("  " + " | ".join("NULL" if v is None else str(v) for v in row))
    if len(rows) > 10:
        lines.append(f"  ... and {len(rows) - 10} more row(s).")
    return "\n".join(lines)


def format_summary(cols, rows, question):
    if isinstance(rows, str):
        return rows
    if not rows:
        return "No matching records found."

    name = detect_username(question) or "The user"
    q    = question.lower()

    if len(rows) == 1 and len(rows[0]) == 1:
        count     = rows[0][0]
        col_label = cols[0] if cols else "result"

        if "complet" in q:
            sentence = f"{name} has completed {count} task(s)."
        elif "pending" in q:
            sentence = f"{name} currently has {count} pending task(s)."
        elif "high" in q and "priorit" in q:
            sentence = f"{name} has {count} high-priority task(s)."
        elif "assign" in q or "total" in q or "how many" in q:
            sentence = f"{name} has a total of {count} assigned task(s)."
        else:
            sentence = f"The query returned a value of {count}."

        return f"Result: {col_label}: {count}\nResult Summary: {sentence}"

    total    = len(rows)
    sentence = (
        f"Found {total} record(s) for {name}."
        if name != "The user"
        else f"Found {total} record(s) matching your query."
    )
    return f"Result: {total} row(s) returned\nResult Summary: {sentence}"

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("NL to SQL Assistant")
    print("System Ready\n")

    while True:
        question = input("Ask question (type exit to quit): ").strip()

        if question.lower() in {"exit", "quit", "q"}:
            print("Goodbye!")
            break

        if not question:
            continue

        sql              = None
        explanation      = ""
        validation_errors = []

        # Retry loop
        for attempt in range(1, MAX_RETRIES + 1):
            error_feedback = "\n".join(validation_errors)

            if attempt > 1:
                print(f"Retrying (attempt {attempt}/{MAX_RETRIES})...")

            try:
                llm_raw = ask_ollama(question, error_feedback)
            except requests.RequestException as e:
                print(f"Could not reach Ollama: {e}")
                break

            sql, explanation = extract_sql_and_explanation(llm_raw)
            validation_errors = validate_sql(sql)

            if not validation_errors:
                break

            print(f"Validation issues (attempt {attempt}):")
            for err in validation_errors:
                print(f"  - {err}")

        # Print output in the requested format
        print()
        print("Generated SQL:")
        print(sql if sql else "(none)")

        print()
        print("Explanation:")
        print(explanation if explanation else "(No explanation generated.)")

        if validation_errors:
            print()
            print(f"Could not produce valid SQL after {MAX_RETRIES} attempts.")
            print("Try rephrasing your question.")
            print()
            continue

        print()
        print("Executing SQL...")

        cols, rows = execute_sql(sql)

        print()
        print("Result:")
        print(format_raw_result(cols, rows))

        print()
        print(format_summary(cols, rows, question))
        print()

    try:
        cursor.close()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()