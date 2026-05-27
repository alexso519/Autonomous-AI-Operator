"""

Benchmark evaluation API — suite execution, history, comparison, export.

"""



from __future__ import annotations



import logging

from typing import Any



from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from pydantic import BaseModel



from app.benchmark.benchmark_runner import BenchmarkRunner



logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])





class RunSuiteRequest(BaseModel):

    taskIds: list[str] | None = None

    categories: list[str] | None = None





class CompareRequest(BaseModel):

    runIdA: str

    runIdB: str





@router.get("/tasks")

async def list_benchmark_tasks() -> dict[str, Any]:

    return {"tasks": await BenchmarkRunner.list_tasks()}





@router.get("/history")

async def benchmark_history(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:

    return {"runs": await BenchmarkRunner.get_history(limit=limit)}





@router.get("/trends")

async def benchmark_trends(days: int = Query(30, ge=1, le=90)) -> dict[str, Any]:

    return await BenchmarkRunner.get_trends(days=days)





@router.post("/run")

async def run_benchmark_suite(

    body: RunSuiteRequest,

    background: BackgroundTasks,

    async_mode: bool = Query(False, alias="async"),

) -> dict[str, Any]:

    if async_mode:

        background.add_task(

            BenchmarkRunner.run_suite,

            task_ids=body.taskIds,

            categories=body.categories,

        )

        return {"status": "started", "message": "Benchmark suite running in background"}

    return await BenchmarkRunner.run_suite(

        task_ids=body.taskIds,

        categories=body.categories,

    )





@router.post("/run/{task_id}")

async def run_single_benchmark(task_id: str) -> dict[str, Any]:

    from app.benchmark.benchmark_tasks import get_task



    task = get_task(task_id)

    if not task:

        raise HTTPException(404, f"Benchmark task {task_id} not found")

    return await BenchmarkRunner.run_task(task)





@router.post("/replay/{run_id}")

async def replay_benchmark(run_id: str) -> dict[str, Any]:

    return await BenchmarkRunner.replay_run(run_id)





@router.post("/compare")

async def compare_benchmark_runs(body: CompareRequest) -> dict[str, Any]:

    return await BenchmarkRunner.compare_runs(body.runIdA, body.runIdB)





@router.get("/export")

async def export_benchmark_report(

    suite_id: str | None = Query(None),

    limit: int = Query(100, ge=1, le=500),

) -> dict[str, Any]:

    return await BenchmarkRunner.export_report(suite_id=suite_id, limit=limit)


