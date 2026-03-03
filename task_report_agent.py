import mysql.connector
import pandas as pd
import os
import re
import matplotlib.pyplot as plt
from io import BytesIO
import base64

from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate


# ───────────────── DATABASE ─────────────────
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "13.232.85.218"),
        user=os.getenv("DB_USER", "oneapp_usr"),
        password=os.getenv("DB_PASSWORD", "JJ3v31YYHRGX2EC5"),
        database=os.getenv("DB_NAME", "only_app")
    )


# ───────────────── VECTOR STORE ─────────────────
embeddings = OllamaEmbeddings(model="phi3:mini")

vectorstore = FAISS.load_local(
    "schema_index",
    embeddings,
    allow_dangerous_deserialization=True
)

retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

llm = ChatOllama(model="phi3:mini", temperature=0)


# ───────────────── SQL PROMPT (STRICT VERSION) ─────────────────
sql_prompt = PromptTemplate(
    input_variables=["schema_context", "question"],
    template="""
You are a MySQL query generator for the table: user_task

COLUMNS: id, company_id, company_name, user_id, user_name, device, designation,
project_id, project_name, list_id, parent_id, list_name, title, description,
priority, note, due_date, status, internal_status, tag, assigned_by, assigned_to,
started_at, completed_at, cron_run

RULES — follow every rule exactly:
1. Always filter by name using exact match: assigned_to = 'Name'
2. assigned_to = person task is assigned TO
3. assigned_by = person who created/assigned the task
4. priority values: 'High', 'Medium', 'Low'
5. status values: 'completed', 'pending', 'Open'
6. COUNT questions → SELECT COUNT(*) FROM user_task WHERE ...
7. SHOW questions → SELECT * FROM user_task WHERE ... LIMIT 100
8. NEVER use JOIN or other tables
9. NEVER invent columns
10. Always include LIMIT for SELECT * queries
11. Ambiguous question → SELECT * FROM user_task LIMIT 20

Respond EXACTLY:

SQL:
<query>

Explanation:
<one sentence>

Schema:
{schema_context}

Question:
{question}
"""
)


# ───────────────── CONVERSATION MEMORY ─────────────────
conversation_history = []
MAX_HISTORY = 4


def add_to_history(question, answer):
    short_answer = str(answer).strip()[:150] if answer else "No result"
    conversation_history.append({"question": question, "answer": short_answer})
    if len(conversation_history) > MAX_HISTORY:
        conversation_history.pop(0)


def resolve_question_with_history(current_question):
    if not conversation_history:
        return current_question

    reference_triggers = [
    "only", "those", "that person",
    "same person", "for him",
    "for her", "their tasks",
    "filter by",
    "status",
    "priority"
   ]

    if not any(t in current_question.lower() for t in reference_triggers):
        return current_question

    history_text = ""
    for i, h in enumerate(conversation_history):
        history_text += f"Q{i+1}: {h['question']}\nA{i+1}: {h['answer']}\n\n"

    resolution_prompt = f"""
Rewrite the new question to be self-contained.

Conversation History:
{history_text}

New Question: {current_question}

Rewritten Question:
"""

    resolved = llm.invoke(resolution_prompt).content.strip()

    if len(resolved) > 120:
        return current_question
    if "\n" in resolved[:60]:
        return current_question

    return resolved


# ───────────────── SQL GENERATION ─────────────────
def generate_sql(question):
    docs = retriever.invoke(question)
    schema_context = "\n".join(d.page_content for d in docs)
    prompt = sql_prompt.format(schema_context=schema_context, question=question)
    return llm.invoke(prompt).content.strip()


def extract_sql_and_explanation(text):
    cleaned = re.sub(r"```sql|```", "", text).strip()

    sql_match = re.search(r"SQL:\s*(.*?)(Explanation:|$)", cleaned, re.S | re.I)
    exp_match = re.search(r"Explanation:\s*(.*)", cleaned, re.S | re.I)

    sql = sql_match.group(1).strip() if sql_match else ""
    explanation = exp_match.group(1).strip() if exp_match else ""

    if not sql:
        fallback = re.search(r"(SELECT\b.+?;)", cleaned, re.S | re.I)
        if fallback:
            sql = fallback.group(1).strip()

    if sql and not sql.endswith(";"):
        sql += ";"

    return sql, explanation


def force_reporting_table(sql, question):
    sql = re.sub(r"\bFROM\s+tasks\b", "FROM user_task", sql, flags=re.I)
    sql = re.sub(r"\bJOIN\s+tasks\b", "", sql, flags=re.I)
    return sql


def sanitize_sql(sql):
    if not sql:
        return ""
    if re.search(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE)\b", sql, re.I):
        return ""
    if re.search(r"\bFROM\s+(?!user_task)\w+", sql, re.I):
        return ""
    return sql


# ───────────────── EXECUTE SQL ─────────────────
def execute_sql(sql_query):
    if not sql_query:
        return None, "Empty SQL query"

    sql_query = sanitize_sql(sql_query)
    if not sql_query:
        return None, "Unsafe SQL blocked"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql_query)

        if cursor.description:
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()

            if len(rows) == 0:
                return pd.DataFrame(), "NO_MATCH"

            return pd.DataFrame(rows, columns=columns), None

        return None, None

    except mysql.connector.Error as err:
        return None, str(err)

    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass


# ───────────────── ANALYSIS ─────────────────
def analyze_and_summarize(df, user_query, llm, status_flag):

    if status_flag == "NO_MATCH" or df is None or (isinstance(df, pd.DataFrame) and df.empty):
        name = re.search(r"assigned(?:\s+to)?\s+(\w+)", user_query.lower())
        if name:
            return None, f"No tasks found for '{name.group(1)}'."
        return None, "No matching records found."

    if status_flag and status_flag != "NO_MATCH":
        return None, f"Query failed: {status_flag}"

    if len(df) == 1 and len(df.columns) == 1:
        count = df.iloc[0, 0]
        return df, f"Total count: {count}."

    row_count = len(df)
    return df, f"{row_count} records found."


# ───────────────── CHART ─────────────────
def generate_chart(df):
    if df is None or df.empty:
        return None

    try:
        cols_lower = [c.lower() for c in df.columns]

        if "priority" in cols_lower:
            col = df.columns[cols_lower.index("priority")]
            df[col].value_counts().plot(kind="pie", autopct="%1.1f%%")

        elif "status" in cols_lower:
            col = df.columns[cols_lower.index("status")]
            df[col].value_counts().plot(kind="bar")

        elif df.shape[1] == 2:
            df.plot(kind="bar", x=df.columns[0], y=df.columns[1], legend=False)

        else:
            return None

        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png")
        plt.close()

        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

    except Exception:
        plt.close()
        return None


# ───────────────── REPORT ─────────────────
def build_report(df, summary, chart_uri):
    table_text = df.to_string(index=False) if df is not None and not df.empty else "No data"
    report = f"SUMMARY:\n{summary}\n\nDATA:\n{table_text}"
    if chart_uri:
        report += f"\n\nCHART URI:\n{chart_uri}"
    return report.strip()


# ───────────────── MAIN LOOP ─────────────────
if __name__ == "__main__":
    print("System Ready")

    while True:
        question = input("\nAsk (type exit to quit): ")
        if question.lower() == "exit":
            break

        resolved_question = resolve_question_with_history(question)
        if resolved_question != question:
            print(f"\n[Resolved Question]: {resolved_question}")

        llm_output = generate_sql(resolved_question)
        sql, explanation = extract_sql_and_explanation(llm_output)
        sql = force_reporting_table(sql, resolved_question)

        print("\nGenerated SQL:")
        print(sql if sql else "(no SQL generated)")
        print("\nExplanation:", explanation)

        if not sql.strip():
            print("\n[WARNING] No SQL generated.")
            add_to_history(question, "No SQL generated")
            continue

        print("\nExecuting SQL...\n")

        df, status_flag = execute_sql(sql)
        result, summary = analyze_and_summarize(df, resolved_question, llm, status_flag)

        print("Result:")
        if df is not None and not df.empty:
            print(df.to_string(index=False))
        else:
            print("No data")

        print("\nResult Summary:")
        print(summary)

        chart_uri = generate_chart(df)

        print("\nFinal Report:")
        print(build_report(df, summary, chart_uri))

        add_to_history(question, summary if summary else "No result")
        print("\n-------------------------")