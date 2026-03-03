TEST_QUESTIONS = [

    # Basic reporting
    "how many tasks assigned to hari",
    "show all high priority tasks assigned to hari",
    "show completed tasks assigned to hari",

    # Memory reuse
    "show only high priority",
    "show pending",

    # No match case
    "how many tasks assigned to unknownperson",

    # Apostrophe test (SQL injection safety)
    "how many tasks assigned to O'Connor",

    # Long output
    "show all tasks assigned to hari",

    # Ambiguous
    "show tasks",
    "what is the status",

    # Needs JOIN
    "show tasks assigned to hari with user email",

    # Edge case
    "how many tasks assigned by suresh",

    # Mixed condition
    "count completed high priority tasks assigned to hari",

    # Very long column list (stresses schema retrieval — all columns exist in user_task directly)
    "show project name, list name, company name, user name, device, priority, status, due date for tasks assigned to hari",
]