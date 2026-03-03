import sys
import os
import time
import re

# task_report_agent.py is in the PARENT folder (phase/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_questions import TEST_QUESTIONS
from task_report_agent import (
    generate_sql,
    extract_sql_and_explanation,
    execute_sql,
    force_reporting_table,
    resolve_question_with_history,
    add_to_history,
    analyze_and_summarize,
    llm,
)


def is_sql_obviously_wrong(sql, question=""):
    """Returns (is_wrong: bool, reason: str)"""
    if not sql or not sql.strip():
        return True, "Empty SQL"
    if re.search(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE)\b", sql, re.I):
        return True, "Dangerous mutation statement"
    if ";" in sql.strip().rstrip(";"):
        return True, "Multiple statements detected"
    if re.search(r"\bFROM\s+tasks\b", sql, re.I):
        return True, "Wrong table: should be user_task"
    if re.search(r"assigned_(?:to|by)\s+LIKE\b", sql, re.I):
        return True, "LIKE on assigned_to/by (should be exact match)"
    if re.search(r"'specific_id'|= 'X'|example\.com", sql, re.I):
        return True, "Placeholder value in SQL"
    return False, ""


def run_tests():
    total_time = 0
    passed = 0
    flagged = 0
    skipped = 0

    for i, question in enumerate(TEST_QUESTIONS, 1):
        print("=" * 60)
        print(f"Test {i}: {question}")

        start = time.time()

        resolved_question = resolve_question_with_history(question)
        if resolved_question != question:
            print(f"  [Resolved] -> {resolved_question}")

        raw_llm_output = generate_sql(resolved_question)
        sql, explanation = extract_sql_and_explanation(raw_llm_output)
        sql = force_reporting_table(sql, resolved_question)

        duration = time.time() - start
        total_time += duration

        print("\nGenerated SQL:")
        if sql:
            print(sql)
        else:
            print("  (no SQL extracted -- raw LLM output:)")
            print("-" * 40)
            print(raw_llm_output[:400])
            print("-" * 40)

        if not sql.strip():
            print("\n[SKIP] No SQL generated.")
            add_to_history(question, "No SQL generated")
            print(f"Response Time: {duration:.2f}s")
            skipped += 1
            continue

        print("\nExecution Result:")
        df, status = execute_sql(sql)
        result_df, summary = analyze_and_summarize(df, resolved_question, llm, status)

        if status and status != "NO_MATCH":
            print(f"  DB Error: {status}")
        elif df is not None and not df.empty:
            display_df = df.iloc[:5, :6]
            print(display_df.to_string(index=False))
            if len(df) > 5:
                print(f"  ... ({len(df)} total rows)")
        else:
            print("  No matching records found.")

        print(f"\nSummary: {summary}")

        add_to_history(question, summary if summary else explanation)

        wrong, reason = is_sql_obviously_wrong(sql, question)
        if wrong:
            print(f"FLAG: {reason}")
            flagged += 1
        else:
            passed += 1

        print(f"Response Time: {duration:.2f}s")

    print("\n" + "=" * 60)
    print("TEST RUN COMPLETE")
    print(f"  Total    : {len(TEST_QUESTIONS)}")
    print(f"  Passed   : {passed}")
    print(f"  Flagged  : {flagged}")
    print(f"  Skipped  : {skipped}")
    print(f"  Avg Time : {total_time / len(TEST_QUESTIONS):.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()