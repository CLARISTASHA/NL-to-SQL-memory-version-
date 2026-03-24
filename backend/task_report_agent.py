import mysql.connector
import pandas as pd
import os
import re
import matplotlib.pyplot as plt
from io import BytesIO
import base64

from dotenv import load_dotenv
load_dotenv()

from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate

from langchain_community.vectorstores import SupabaseVectorStore
from supabase import create_client

from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

import redis


# ───────────────── DATABASE ─────────────────
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "13.232.85.218"),
        user=os.getenv("DB_USER", "oneapp_usr"),
        password=os.getenv("DB_PASSWORD", "JJ3v31YYHRGX2EC5"),
        database=os.getenv("DB_NAME", "only_app")
    )


# ───────────────── REDIS CACHE ─────────────────
redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)


def get_cache(query):
    return redis_client.get(query)


def set_cache(query, result):
    redis_client.set(query, result)


# ───────────────── VECTOR STORE ─────────────────
embeddings = OllamaEmbeddings(model="nomic-embed-text")

vectorstore = FAISS.load_local(
    "../schema_index",
    embeddings,
    allow_dangerous_deserialization=True
)

retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

llm = ChatOllama(model="phi3:mini", temperature=0)


# ───────────────── SUPABASE VECTOR MEMORY ─────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

vector_memory = SupabaseVectorStore(
    client=supabase,
    embedding=embeddings,
    table_name="query_memory",
    query_name="match_query_memory"
)

supabase_retriever = vector_memory.as_retriever(search_kwargs={"k": 2})


# ───────────────── REDIS CHAT MEMORY ─────────────────
def get_redis_history(session_id):
    return RedisChatMessageHistory(
        session_id=session_id,
        url="redis://localhost:6379"
    )


# ───────────────── SQL PROMPT ─────────────────
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

    schema_docs = retriever.invoke(question)
    memory_docs = supabase_retriever.invoke(question)

    all_docs = schema_docs + memory_docs

    schema_context = "\n".join(
        d.page_content if hasattr(d, "page_content") else str(d)
        for d in all_docs
    )

    prompt = sql_prompt.format(
        schema_context=schema_context,
        question=question
    )

    response = llm.invoke(prompt)

    return response.content.strip()


def extract_sql_and_explanation(text):

    cleaned = re.sub(r"```sql|```", "", text).strip()

    sql_match = re.search(r"(SELECT\b.+?;)", cleaned, re.S | re.I)
    sql = sql_match.group(1).strip() if sql_match else ""

    exp_match = re.search(r"Explanation:\s*(.*)", cleaned, re.I)
    explanation = exp_match.group(1).split("\n")[0].strip() if exp_match else ""

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


# ───────────────── RESTORED FUNCTIONS ─────────────────
def analyze_and_summarize(df, user_query, llm, status_flag):

    if status_flag == "NO_MATCH" or df is None:
        name = re.search(r"assigned to (\w+)", user_query.lower())
        if name:
            return None, f"{name.group(1)} is not found in the database."
        return None, "No records exist for this query."

    prompt = f"""
Summarize this data in concise business language.
Highlight key numbers clearly.
Keep it 2–4 sentences.

User question:
{user_query}

Result:
{df.to_string(index=False)}
"""

    response = llm.invoke(prompt)
    return df, response.content.strip()


def generate_chart(df):

    if df is None or df.empty:
        return None

    # Do NOT create chart for single value results
    if df.shape[0] == 1 and df.shape[1] == 1:
        return None

    try:

        cols = [c.lower() for c in df.columns]

        if "priority" in cols:
            df["priority"].value_counts().plot(kind="pie", autopct="%1.1f%%")

        elif "status" in cols:
            df["status"].value_counts().plot(kind="bar")

        elif df.shape[1] == 2:
            df.plot(kind="bar", x=df.columns[0], y=df.columns[1])

        else:
            return None

        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png")
        plt.close()

        encoded = base64.b64encode(buf.getvalue()).decode()

        return f"data:image/png;base64,{encoded}"

    except Exception:
        return None


def build_report(df, summary, chart_uri):

    table_text = df.to_string(index=False) if df is not None and not df.empty else "No data"

    report = f"""
SUMMARY:
{summary}

DATA:
{table_text}
"""

    if chart_uri:
        report += f"\nCHART URI:\n{chart_uri}\n"

    return report.strip()


# ───────────────── MAIN LOOP ─────────────────
import traceback  # ✅ ADD THIS AT TOP OF FILE

# ───────────────── MAIN LOOP ─────────────────
if __name__ == "__main__":

    print("System Ready")

    user_id = input("Enter user id: ")
    session_id = input("Enter session id: ")

    while True:

        question = input("\nAsk (type exit to quit): ")

        if question.lower() == "exit":
            break

        cached = get_cache(question)

        if cached:
            print("\n[CACHE HIT]")
            print(cached)
            continue

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

        # ✅ FIXED TRY-EXCEPT BLOCK
        try:
            if sql:
                vector_memory.add_texts(
                    texts=[resolved_question],
                    metadatas=[{"sql": sql}]
                )
        except Exception as e:
            print("FULL ERROR:", str(e))
            traceback.print_exc()

        print("Result:")

        if df is not None and not df.empty:
            result_text = df.to_string(index=False)
            print(result_text)
        else:
            result_text = "No data"
            print("No data")

        set_cache(question, result_text)
        add_to_history(question, result_text)

        print("\n-------------------------")