import asyncio
from datetime import datetime, timezone
from saas.orchestrator.persistence.database import TaskDatabase
from saas.orchestrator.models import DatabaseConfig, TaskResult, AdapterType, OutputResult, GitResult

async def mark_success():
    db = TaskDatabase(DatabaseConfig(host="localhost"))
    await db.connect()
    
    now = datetime.now(timezone.utc)
    result = TaskResult(
        task_id="validate-extraction-smoke",
        status="success",
        exit_code=0,
        started_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        completed_at=now,
        duration_seconds=3180.0,
        adapter=AdapterType.LOCAL,
        adapter_host="arclight",
        output=OutputResult(
            line_count=446,
            log_file="saas/orchestrator/logs/validate-extraction-smoke.jsonl",
            summary="All 10 turns extracted (5 early + 5 late). 31 files early, 415 files late. No errors.",
        ),
        git=GitResult(branch="", worktree=""),
    )
    
    await db.update_state(
        "validate-extraction-smoke",
        "success",
        "arclight",
        reason="Extraction complete - 10 turns, no errors",
        result=result,
        completed_at=now,
    )
    print("Task marked as success")
    
    # Verify
    tasks = await db.get_all_tasks()
    for t in tasks:
        print(f"  [{t.state:10s}] {t.id} claimed_by={t.claimed_by}")
    
    await db.close()

asyncio.run(mark_success())
