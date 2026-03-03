import logging
import os
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from task_report_agent import (
    generate_sql,
    extract_sql_and_explanation,
    execute_sql,
    force_reporting_table,
    resolve_question_with_history,
    add_to_history,
    analyze_and_summarize,
    generate_chart,
    build_report,
    llm,
)

# ── Config ─────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("API_KEY", "my-secret-key")

# ── Logging setup ──────────────────────────────────────────────────────────────
# FIX: Use os.makedirs with exist_ok so it never crashes if dir already exists
log_dir = "/var/log/task_report_agent"
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(log_dir, "app.log")),
    ],
)
logger = logging.getLogger(__name__)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Task Report Agent",
    description="Natural language to SQL query agent for task reporting",
    version="1.0.0",
)

# ── CORS middleware ────────────────────────────────────────────────────────────
# FIX: CORS must be added BEFORE any routes are defined
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request / Response models ──────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    api_key: str
    session_id: Optional[str] = None


class QueryResponse(BaseModel):
    session_id: str
    question: str
    resolved_question: str
    generated_sql: str
    explanation: str
    summary: str
    row_count: int
    chart_uri: Optional[str] = None
    execution_time_seconds: float
    report: str


# ── Middleware: request logging ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    logger.info(
        f"{request.method} {request.url.path} "
        f"status={response.status_code} duration={duration:.1f}ms"
    )
    return response


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/")
@app.get("/health")
def health_check():
    """
    Health check endpoint.
    GET /        → used by Docker HEALTHCHECK
    GET /health  → used by monitoring tools
    FIX: Does NOT call llm.invoke() — that was too slow (~2s) for a health check
         and would cause Docker to mark container as unhealthy during normal use.
    """
    return {
        "status": "API is running",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
    }


# ── Main query endpoint ────────────────────────────────────────────────────────
@app.post("/ask", response_model=QueryResponse)
async def ask_query(request: QueryRequest):
    """
    Accepts a natural language question and returns SQL, summary, chart, report.
    Requires api_key in request body.
    """
    # API key check
    if request.api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    session_id = request.session_id or str(uuid.uuid4())
    start = time.time()

    logger.info(f"[{session_id}] Question: {request.question}")

    try:
        # Step 1: Resolve references using conversation history
        resolved = resolve_question_with_history(request.question)
        if resolved != request.question:
            logger.info(f"[{session_id}] Resolved: {resolved}")

        # Step 2: Generate SQL
        llm_output = generate_sql(resolved)
        sql, explanation = extract_sql_and_explanation(llm_output)
        sql = force_reporting_table(sql, resolved)

        logger.info(f"[{session_id}] SQL: {sql}")

        if not sql.strip():
            logger.warning(f"[{session_id}] No SQL generated")
            add_to_history(request.question, "No SQL generated")
            raise HTTPException(
                status_code=422,
                detail="Could not generate SQL for this question. Please rephrase."
            )

        # Step 3: Execute SQL
        df, status_flag = execute_sql(sql)

        # Step 4: Analyze and summarize
        result_df, summary = analyze_and_summarize(df, resolved, llm, status_flag)

        # Step 5: Generate chart
        chart_uri = generate_chart(df)

        # Step 6: Build final report
        final_report = build_report(df, summary, chart_uri)

        row_count = len(df) if df is not None and not df.empty else 0
        execution_time = round(time.time() - start, 3)

        logger.info(
            f"[{session_id}] SQL: {sql} | rows={row_count} | time={execution_time}s"
        )

        # Store short summary in conversation history
        add_to_history(request.question, summary if summary else "No result")

        return QueryResponse(
            session_id=session_id,
            question=request.question,
            resolved_question=resolved,
            generated_sql=sql,
            explanation=explanation,
            summary=summary or "No data found.",
            row_count=row_count,
            chart_uri=chart_uri,
            execution_time_seconds=execution_time,
            report=final_report,
        )

    except HTTPException:
        # Re-raise HTTP exceptions as-is (don't wrap in 500)
        raise
    except Exception as e:
        logger.error(f"[{session_id}] Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Reset conversation history ─────────────────────────────────────────────────
@app.post("/reset")
def reset_history():
    """Clears conversation history."""
    from task_report_agent import conversation_history
    conversation_history.clear()
    logger.info("Conversation history cleared")
    return {"status": "ok", "message": "Conversation history cleared."}


# ── Run directly ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
