# Runtime Stabilization Analysis

## Table of Contents
1. [Critical Runtime Risks](#1-critical-runtime-risks)
2. [Memory Leak Analysis](#2-memory-leak-analysis)
3. [Async Lifecycle Problems](#3-async-lifecycle-problems)
4. [SSE Cleanup Risks](#4-sse-cleanup-risks)
5. [Execution Corruption Risks](#5-execution-corruption-risks)
6. [SQLite Concurrency Risks](#6-sqlite-concurrency-risks)
7. [Stuck Execution Risks](#7-stuck-execution-risks)
8. [Frontend Desync Risks](#8-frontend-desync-risks)
9. [Stabilization Roadmap](#9-stabilization-roadmap)
10. [Runtime Safety Layer Design](#10-runtime-safety-layer-design)
11. [Execution Lifecycle Improvements](#11-execution-lifecycle-improvements)
12. [Cleanup Strategy](#12-cleanup-strategy)
13. [Cancellation Architecture](#13-cancellation-architecture)
14. [Timeout Architecture](#14-timeout-architecture)
15. [Crash Recovery Strategy](#15-crash-recovery-strategy)

---

## 1. Critical Runtime Risks

### 1.1 Uncontrolled async background tasks
**File**: [`backend/app/routes/workflows.py`](backend/app/routes/workflows.py:279)

```python
asyncio.create_task(
    execute_workflow(...)
)
```

The execution engine is launched as a fire-and-forget `asyncio.create_task`. There is:
- **No task reference stored** → cannot cancel, inspect, or await the task
- **No cancellation mechanism** → once launched, it runs until completion
- **No tracking** → task leaks on server restart or if it gets garbage-collected mid-execution
- **Duplicate execution risk** → calling POST /run twice concurrently starts two tasks for the same workflow

**Severity**: CRITICAL — this is the single largest runtime risk.

### 1.2 No execution cancellation endpoint
There is no `DELETE /api/executions/{id}/cancel` endpoint. If a user starts a long-running workflow:
- They cannot stop it
- They must restart the entire backend server
- The Ollama call blocks the thread indefinitely (see 1.3)

**Severity**: CRITICAL — user-facing lock-in.

### 1.3 CrewAI `agent.execute_task` is synchronous, blocks event loop
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:520)

```python
output = await asyncio.to_thread(agent.execute_task, task)
```

While `asyncio.to_thread` moves it off the event loop, the thread is:
- **Uncancellable** — `asyncio.to_thread` cannot be cancelled once the thread is running
- **Unbound** — no timeout on the thread execution
- **Blocking cleanup** — if the task hangs, the cleanup path never runs
- **Resource leak** — each agent creates a new `ChatOpenAI` LLM connection

**Severity**: HIGH — Ollama hangs translate to stuck threads and unresponsive system.

### 1.4 No timeout anywhere
The entire codebase has zero timeout configuration:
- No execution-level timeout
- No per-agent timeout
- No Ollama request timeout
- No SSE stream idle timeout (the 15s timeout is only between events)
- No database operation timeout

An Ollama model that hangs (e.g., loading a large model for the first time) blocks the execution indefinitely.

**Severity**: HIGH — a single slow Ollama response stalls the entire system.

### 1.5 Duplicate execution ID on replay
**File**: [`backend/app/routes/executions.py`](backend/app/routes/executions.py:417)

```python
asyncio.ensure_future(
    execute_workflow(...)
)
```

The rerun endpoint calls `execute_workflow` directly, which **generates a new execution ID internally**. But the frontend doesn't know the new ID until the SSE stream starts. There is a window where:
- The rerun response returns a placeholder ID
- The frontend doesn't know the real execution ID
- The SSE stream may be missed entirely

Also, `asyncio.ensure_future` is the lower-level API — `asyncio.create_task` is preferred for clarity.

**Severity**: MEDIUM — leads to frontend desync and missed stream connections.

---

## 2. Memory Leak Analysis

### 2.1 SSE queues not cleaned on engine crash
**File**: [`backend/app/streaming/event_manager.py`](backend/app/streaming/event_manager.py:183)

The `cleanup()` method is called from the SSE generator's `finally` block in [`stream.py`](backend/app/routes/stream.py:160). If:
- The execution engine crashes before the SSE stream subscribes
- The stream was never opened (no client connected)
- The task is cancelled

...the queue stays in `_queues` forever. There is no guard that cleans up orphaned queues belonging to terminated executions.

**Severity**: MEDIUM — over many runs, queues accumulate in memory.

### 2.2 CrewAI agent instances accumulate
**File**: [`backend/app/execution/agent_factory.py`](backend/app/execution/agent_factory.py:105)

Each agent node creates a new `CrewAgent` instance with a new `ChatOpenAI` LLM connection. These are:
- Stored in local `agent` variable during execution
- Held by the CrewAI `Task` object
- Garbage-collected when the execution completes

However, if an execution hangs or crashes, these objects may persist in memory until the task is garbage-collected. With long-running hung executions, memory grows unboundedly.

**Severity**: MEDIUM — exacerbates the stuck execution problem.

### 2.3 Log arrays grow unboundedly during execution
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:359-360)

```python
logs: list[dict[str, Any]] = []
steps: list[dict[str, Any]] = []
```

These lists accumulate every event during execution. For workflows with many agents or verbose output, these lists grow in memory. They are returned at the end and then discarded, but during a long execution, memory usage grows proportionally.

**Severity**: LOW — acceptable for personal use, but worth noting.

### 2.4 UUID generation for every log entry creates object churn
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:376)

Every log/step/event generates 1-3 UUIDs, log dicts, and timestamps. During normal execution this is fine, but demonstrates the pattern of disposable object creation that contributes to GC pressure.

**Severity**: LOW — not a practical concern.

---

## 3. Async Lifecycle Problems

### 3.1 No task registry or lifecycle tracking
The execution engine is launched as an orphaned task. There is no central registry that:
- Maps execution IDs to their `asyncio.Task`
- Tracks task state (running/pending/cancelled)
- Allows task enumeration or cleanup
- Prevents duplicate execution

Without this, the runtime cannot:
- Cancel a running execution
- Detect duplicate runs
- Implement grace period shutdown
- Report which executions are currently active

**Severity**: CRITICAL — foundational missing piece.

### 3.2 Execution record created in two places
Execution records are created in both:
1. [`routes/workflows.py:269`](backend/app/routes/workflows.py:269) — run endpoint
2. [`engine.py:348`](backend/app/execution/engine.py:348) — execute_workflow

The first creates a "stub" record with `status='running'`, then passes to `execute_workflow` which immediately creates a **second** `INSERT` that fails (or silently overwrites?). This is a race:
- The route creates an execution record with `id = uuid4().hex[:12]`
- `execute_workflow` creates another execution record with a different `id` (also `uuid4().hex[:12]`)
- The route returns `execution_id` to the frontend
- The engine uses its own `execution_id` for everything internally
- When the frontend connects to `/api/executions/{route_execution_id}/stream`, the stream is listening for `engine_execution_id`

**This is a BUG** — the frontend connects to one ID, the engine emits events under a different ID. The SSE stream will never receive events because the queue is created for a different ID than what the frontend subscribes to.

Let me verify this by re-reading the flow:

In [`routes/workflows.py:266-301`](backend/app/routes/workflows.py:266):
- `execution_id = uuid.uuid4().hex[:12]`
- `INSERT INTO executions (id, ...) VALUES ({execution_id}, ...)`
- `asyncio.create_task(execute_workflow(...))` — **does NOT pass execution_id**
- Returns `{executionId: execution_id}`

In [`engine.py:329-353`](backend/app/execution/engine.py:329):
- `execution_id = uuid.uuid4().hex[:12]` — generates a **different** ID
- `INSERT INTO executions (id, ...) VALUES ({this_execution_id}, ...)`
- But the route already inserted a record with the route's ID!
- The second INSERT will either:
  - PRIMARY KEY violation if IDs collide (unlikely with hex[:12])
  - Create a **second** execution record with a different ID
- `event_manager.create_stream(this_execution_id)` — queue created with engine's ID
- Frontend connects to `{route_execution_id}/stream` — no queue exists for this ID

**Severity**: CRITICAL — the entire SSE streaming pipeline is broken if both code paths run.

Wait — the route handler creates the execution record and launches the task, but `execute_workflow` also creates its own execution record. These are two different records. The frontend is given the route's `execution_id`, but the engine creates events under its own `execution_id`. The frontend connects to the route's ID, but the queue exists for the engine's ID.

Actually wait, let me re-read `routes/workflows.py` more carefully. The route handler:
1. Creates an execution record with its own ID
2. Launches `execute_workflow` as a task — **without passing execution_id**

Then `engine.py: execute_workflow` signature:
```python
async def execute_workflow(
    workflow_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    workflow_name: str = "Untitled Workflow",
    workflow_description: str = "",
) -> dict[str, Any]:
```

No `execution_id` parameter! It generates its own. So YES, this is a bug — two execution records, two different IDs, the SSE stream connects to the wrong one.

**Severity**: CRITICAL — the run → stream pipeline is non-functional for this reason alone.

Hmm wait, but the system appears to work since it's running. Let me re-think...

Actually, maybe I'm misunderstanding. The route creates a task that calls `execute_workflow()`. Let me check if there's been some change. Looking at the code again:

In `execute_workflow`, line 329: `execution_id = uuid.uuid4().hex[:12]`
In `workflows.py`, line 266: `execution_id = uuid.uuid4().hex[:12]`

Yes, they create different UUIDs. The frontend gets the one from `workflows.py`, but `execute_workflow` uses its own. The queue is created for the engine's ID.

This would mean SSE streaming NEVER works from the `run` endpoint. But the user says the system is "feature-complete". Let me check if the code path might have been updated... no, this is the current code.

Actually, maybe there IS a mechanism that handles this. Let me look at the `_end_stream` function and how the subscriber gets the right queue... No, looking at `event_manager.subscribe()`, it takes `execution_id` as the key to look up the queue. If the frontend connects with the wrong ID, `subscribe` returns no queue.

But wait — the system was said to work. So either:
1. My analysis is wrong about the bug
2. The run endpoint has been updated differently
3. Or there's something else going on

Let me look more carefully at the run endpoint response. It returns `streamUrl: "/api/executions/{execution_id}/stream"`. The `execution_id` here is the one generated in the route handler. The frontend's `executionStreamService` opens `EventSource` to this URL. The stream handler looks up the queue by this ID. But the queue was created with the engine's own ID.

Unless... the execution engine creates a SECOND execution record, and the second INSERT silently fails because it's a duplicate key? No, `uuid4().hex[:12]` is a 12-char hex string - 16^12 = 2.8 * 10^14 possibilities. Collision is effectively impossible.

I think this IS a real bug. The system may appear to work because maybe `execute_workflow`'s INSERT also succeeds (different ID), and someone might be looking at the wrong execution record in the history. But the SSE streaming would NOT work for the frontend's execution ID.

Actually, upon even deeper reflection - maybe the `workflows.py` run endpoint creates the record as a "stub" and the engine overwrites it. Let me look again...

No, `engine.py` does `INSERT INTO executions (id, ...)` with a new random ID. It does NOT use the one from the route. So there are definitely TWO records. The question is whether the SSE streaming works...

Hmm, but maybe the engine has been modified and the file I read doesn't reflect the latest. Let me check if there's a version where `execute_workflow` accepts an `execution_id` parameter... No, the signature clearly shows no `execution_id` parameter.

This is a significant finding regardless — the fact that `execute_workflow` doesn't accept an `execution_id` means the route's ID is IGNORED. The entire SSE streaming pipeline has a disconnect between route-generated ID and engine-generated ID.

OK let me move on and note this as a critical finding, then produce the document.

### 3.3 Resume duplicates the execution loop
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:762-861)

The `resume_workflow` function contains an almost exact copy of the execution loop from `execute_workflow` (lines 384-670). This is ~200 lines of duplicated logic. Any bug fix in one must be manually applied to the other.

**Severity**: HIGH — maintenance burden and bug multiplication.

### 3.4 No backpressure on event emission
**File**: [`backend/app/streaming/event_manager.py`](backend/app/streaming/event_manager.py:88-107)

Events are pushed into an unbounded `asyncio.Queue`. If the SSE consumer is slow or disconnected, the queue grows unboundedly. The `emit_nowait` method (line 109) shows awareness of this but provides no relief — it falls back to `await queue.put()`.

**Severity**: MEDIUM — unbounded memory during stream starvation.

---

## 4. SSE Cleanup Risks

### 4.1 Queue orphaned when engine crashes before SSE connects
The typical lifecycle is:
1. `POST /run` → creates task (engine starts, creates queue)
2. Frontend connects to stream → subscribes to queue
3. Engine emits events → SSE consumes

If step 2 never happens (frontend doesn't connect), or happens late, the queue sits in memory. The engine finishes → stream ends → but cleanup only happens when someone subscribes to the queue.

**Severity**: MEDIUM — orphaned queues accumulate.

### 4.2 Queue orphaned on task cancellation
If the execution task is cancelled (future cancellation feature), the `_queues` entry persists because no SSE subscriber is running to trigger cleanup. The queue must be cleaned up by the cancellation handler, but none exists.

**Severity**: HIGH — cancellation creates leaks.

### 4.3 SSE generator cleanup only runs on client disconnect
**File**: [`backend/app/routes/stream.py`](backend/app/routes/stream.py:158-160)

```python
finally:
    await event_manager.cleanup(execution_id)
```

This `finally` block runs when the SSE generator exits, which happens when:
- The client disconnects
- The queue returns None sentinel
- The timeout fires

But if the engine crashes or is cancelled, and no client is connected, cleanup never runs.

**Severity**: MEDIUM — cleanup is conditional on client presence.

### 4.4 No cleanup on app shutdown
**File**: [`backend/app/main.py`](backend/app/main.py:36-61)

The lifespan handler closes the database but does NOT call `event_manager.cleanup_all()`. While `cleanup_all` exists, it's never invoked during shutdown.

**Severity**: LOW — queues die with the process, but signals missed cleanup.

---

## 5. Execution Corruption Risks

### 5.1 Race condition on concurrent approval/reject
**File**: [`backend/app/routes/approvals.py`](backend/app/routes/approvals.py:32-77)

Both `approve` and `reject` check `status == 'waiting_approval'` and then proceed. If both are called simultaneously (e.g., double-click on frontend):
1. Both pass the status check
2. `approve` starts `resume_workflow()` → sets status to 'running'
3. `reject` sets status to 'rejected' (overwrites 'running')
4. Execution is now in a corrupted state: a partially running execution marked as 'rejected'

**Severity**: HIGH — state corruption from concurrent approval/reject.

### 5.2 No optimistic locking on database updates
Throughout the codebase, database updates use read-then-write patterns without any locking:

```python
# approvals.py:44-59
row = await cursor.fetchone()  # READ
status = dict(row)["status"]
if status == "waiting_approval":
    await resume_workflow()  # WRITE (different transaction?)
```

Without `BEGIN IMMEDIATE` or row-level locking, concurrent requests can interleave.

**Severity**: HIGH — race conditions in state transitions.

### 5.3 Checkpoint payload can become stale
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:136-173)

The checkpoint stores the entire node list, context, logs, and steps as a JSON blob. If the workflow is edited after pausing but before resuming, the checkpoint contains stale workflow data. When resumed, the engine uses the stale data:
- Old node configuration
- Old edge definitions
- Old workflow metadata

This could lead to executing nodes that no longer exist in the updated workflow, or missing nodes that were added.

**Severity**: MEDIUM — stale checkpoint on workflow edit during pause.

### 5.4 Executor not reentrant
The engine has no guard against re-entrant execution. If `resume_workflow` is called while the engine is still processing (e.g., timeout + retry), two executions run for the same ID:
- Two queues created for the same ID (the second replaces the first)
- Two DB updates interleaving
- Duplicate events emitted

**Severity**: HIGH — corruption from re-entrant execution.

---

## 6. SQLite Concurrency Risks

### 6.1 Single shared connection, no connection pooling
**File**: [`backend/app/database/database.py`](backend/app/database/database.py:22-30)

```python
_db: aiosqlite.Connection | None = None

async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await _create_connection()
    return _db
```

Single connection shared across all async tasks. When one task executes a long-running query, all other tasks wait. With WAL mode reads can proceed concurrently, but writes are serialized.

**Severity**: MEDIUM — throughput bottleneck, not critical for personal use.

### 6.2 Transactions are not explicitly managed
The codebase uses `await db.commit()` after each write, but there's no explicit `BEGIN` or `BEGIN IMMEDIATE`. This means:
- SQLite wraps each standalone statement in an implicit transaction
- Multi-statement operations are NOT atomic
- Partial writes can occur if the process crashes mid-operation
- The execution record might be created without its associated steps

For example in [`engine.py:348-353`](backend/app/execution/engine.py:348):
```python
await db.execute("INSERT INTO executions ...")
await db.commit()
# ... later ...
await db.execute("INSERT INTO execution_steps ...")
await db.commit()
```

If the process crashes between these two commits, an execution record exists with no steps.

**Severity**: MEDIUM — data inconsistency risk.

### 6.3 No retry on SQLite locked/busy
SQLite can return `SQLITE_BUSY` when concurrent writers conflict. There's no retry logic anywhere in the database layer.

**Severity**: LOW — rare with single-user, but can happen with concurrent approval/resume.

### 6.4 Database file locked on Windows
On Windows, SQLite holds exclusive file locks. The WAL journal mode helps, but schema changes (`ALTER TABLE`) during app startup can conflict with concurrent database access.

**Severity**: LOW — startup race only.

---

## 7. Stuck Execution Risks

### 7.1 Ollama hang → thread hang → execution stuck
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:520)

```python
output = await asyncio.to_thread(agent.execute_task, task)
```

If Ollama hangs (model loading, OOM, deadlock in llama.cpp), the thread blocks indefinitely:
- `asyncio.to_thread` returns a `Future` that never completes
- The `await` never resolves
- The execution loop never proceeds to the next agent
- No timeout to break free
- No mechanism to cancel the running thread

Since the event loop is not blocked (the thread is), other requests work, but the specific execution is stuck forever.

**Severity**: CRITICAL — no recovery path for Ollama hangs.

### 7.2 No cancellation mechanism for running agents
Even if we implement cancellation (`Task.cancel()`), `asyncio.to_thread` creates a `concurrent.futures.ThreadPoolExecutor` thread. Cancelling the `asyncio.Task` wrapping the `to_thread` does NOT cancel the underlying thread. The thread continues running until:
- The Ollama call completes
- The process terminates

**Severity**: CRITICAL — cancellation is unsafe without thread management.

### 7.3 Approval pause never times out
**File**: [`backend/app/execution/engine.py`](backend/app/execution/engine.py:391-414)

When execution hits an approval gate, it pauses indefinitely. There is no:
- Approval timeout
- Auto-reject after timeout
- Orphan detection

A paused execution stays in `waiting_approval` forever if the user never acts.

**Severity**: MEDIUM — orphaned executions accumulate.

### 7.4 SSE stream timeout is misleading
**File**: [`backend/app/streaming/event_manager.py`](backend/app/streaming/event_manager.py:159)

```python
event = await asyncio.wait_for(queue.get(), timeout=timeout)
```

The 15-second timeout is between events, not a total stream timeout. A long-running agent that takes 5 minutes to respond will stay connected. The timeout only fires when the engine is truly idle.

**Severity**: LOW — intentional design, but worth documenting.

---

## 8. Frontend Desync Risks

### 8.1 EventSource auto-reconnect causes duplicate event processing
**File**: [`frontend/src/services/executionStreamService.ts`](frontend/src/services/executionStreamService.ts:116-126)

```typescript
this.eventSource.onerror = (error: Event) => {
```

The browser's `EventSource` automatically reconnects on connection loss. If the backend has already cleaned up the queue, the reconnect creates a new EventSource that immediately gets an error (404 or no events), but the frontend may interpret this as a second execution attempt.

**Severity**: MEDIUM — recovery not clean.

### 8.2 Approval resume race: disconnect → reconnect window
**File**: [`frontend/src/stores/executionStore.ts`](frontend/src/stores/executionStore.ts:169-189)

```typescript
setTimeout(() => {
    store.disconnectStream();
    executionStreamService.connect(executionId, callbacks);
}, 200);
```

After approval, there's a 200ms delay before reconnecting. If the backend has already completed the resumed execution by then, the frontend misses all events and shows a stale state.

**Severity**: MEDIUM — race on fast post-approval executions.

### 8.3 No heartbeat mechanism
There is no heartbeat/keepalive from the backend (the 15s timeout is internal). If the backend process dies, the frontend's EventSource doesn't detect it until the browser's TCP timeout fires (which can be minutes).

**Severity**: MEDIUM — slow failure detection.

### 8.4 Rerun returns incorrect execution ID
**File**: [`backend/app/routes/executions.py`](backend/app/routes/executions.py:437)

```python
return {
    "executionId": f"rerun-{execution_id[:8]}",
    ...
    "streamUrl": "",
}
```

The rerun endpoint returns a synthetic execution ID (`rerun-xxx`) that doesn't match the actual execution ID generated inside `execute_workflow`. The frontend can't connect to the stream because:
1. It receives a fake ID
2. No stream URL is provided
3. The actual execution ID is never communicated back

**Severity**: HIGH — rerun is effectively broken for SSE streaming.

### 8.5 Frontend has no orphaned execution recovery
If the frontend page is refreshed during execution:
1. The execution store resets to `idle`
2. The running execution continues on the backend
3. The frontend has no mechanism to reconnect to an in-flight execution
4. The backend execution becomes an orphan

**Severity**: MEDIUM — page refresh during execution loses connection.

---

## 9. Stabilization Roadmap

### Phase 1: Critical Fixes (DO FIRST)
These are blocking issues that make the system unreliable.

| # | Issue | Fix |
|---|-------|-----|
| 1 | **Double execution ID bug** — route and engine create different IDs | Pass `execution_id` from route to `execute_workflow` |
| 2 | **No task registry** — orphaned background tasks | Create `ExecutionManager` with task registry |
| 3 | **No cancellation** — stuck executions unrecoverable | Implement `Task.cancel()` path + cancel endpoint |
| 4 | **No timeout** — Ollama hang blocks forever | Add per-agent timeout config |

### Phase 2: Safety Layer
Protect against common failure modes.

| # | Issue | Fix |
|---|-------|-----|
| 5 | **Thread safety** — `asyncio.to_thread` can't cancel | Use `concurrent.futures` with proper shutdown |
| 6 | **Approval race** — concurrent approve/reject | Optimistic locking with status check + atomic update |
| 7 | **Re-entrant execution** — duplicate resume | Guard with `asyncio.Lock` per execution |
| 8 | **SSE cleanup** — orphaned queues | Queue TTL / cleanup on completion everywhere |

### Phase 3: Lifecycle Management
Make execution lifecycle predictable and observable.

| # | Issue | Fix |
|---|-------|-----|
| 9 | **Resume code duplication** — duplicate loop in resume | Extract shared execution step into helper |
| 10 | **Rerun broken** — fake execution ID | Return actual execution ID from rerun |
| 11 | **Approval timeout** — pause never expires | Add configurable approval TTL |
| 12 | **Graceful shutdown** — running tasks on server stop | Cancel running tasks in lifespan shutdown |

### Phase 4: Resilience
Recover from crashes, disconnects, and failures.

| # | Issue | Fix |
|---|-------|-----|
| 13 | **Ollama failure recovery** — model errors, connection refused | Retry with backoff, emit structured error |
| 14 | **Stream disconnect recovery** — client drops mid-execution | Reconnect on resume with event replay |
| 15 | **Crash recovery** — server dies during execution | Mark orphaned executions as 'orphaned' on startup |
| 16 | **Heartbeat** — client detects dead backend | SSE heartbeat every 30s |

### Phase 5: Cleanup
Prevent leaks and ensure resource cleanup.

| # | Issue | Fix |
|---|-------|-----|
| 17 | **Queue cleanup on shutdown** | Add to lifespan shutdown handler |
| 18 | **Memory bounds** — log/step list growth | Optional cap on in-memory log size |
| 19 | **DB transaction safety** — partial writes risk | Explicit `BEGIN IMMEDIATE` for multi-step ops |
| 20 | **Orphan cleanup** — stale queues, hanging threads | Periodic sweep of orphaned resources |

---

## 10. Runtime Safety Layer Design

### 10.1 ExecutionManager — Central Task Registry

```
ExecutionManager
├── _tasks: dict[str, asyncio.Task]       # execution_id → task
├── _locks: dict[str, asyncio.Lock]       # execution_id → mutex
├── _cancel_events: dict[str, asyncio.Event]  # execution_id → cancel signal
├── register(execution_id, task) → None
├── cancel(execution_id) → bool           # Cancel and clean up
├── get_status(execution_id) → TaskStatus
├── list_active() → list[execution_id]
├── cancel_all() → None                   # On shutdown
└── is_running(execution_id) → bool
```

Key behaviors:
- **Duplicate prevention**: `register()` raises if task already registered and running
- **Cancellation propagation**: `cancel()` sets a flag, cancels the task, cleans up queues
- **Locking**: Per-execution `asyncio.Lock` prevents re-entrant execution
- **Observability**: `list_active()` reports all running executions

### 10.2 ExecutionGuard — Per-Execution Mutual Exclusion

```
ExecutionGuard
├── _lock: asyncio.Lock
├── _owner_id: str | None             # Currently executing owner
├── acquire(execution_id) → bool      # True if acquired
├── release(execution_id) → None
├── is_owned_by(execution_id) → bool
└── is_locked() → bool
```

Used to prevent:
- Concurrent execution of the same workflow
- Concurrent `approve`/`reject` on the same execution
- Re-entrant `resume_workflow`

### 10.3 AgentTimeout — Per-Agent Execution Time Bound

```
AgentTimeout
├── default_timeout: int = 300        # 5 minutes default
├── per_agent_overrides: dict[str, int]
├── get_timeout_for(agent_name) → int
├── wrap_with_timeout(coro, timeout) → coro  # asyncio.wait_for wrapper
└── on_timeout(execution_id, agent_name) → None  # Emit timeout event
```

Applied to each `asyncio.to_thread` call. On timeout:
1. Emit `agent_timeout` event
2. Mark agent step as `failed` with `timeout` reason
3. Continue to next agent OR fail workflow (configurable)

---

## 11. Execution Lifecycle Improvements

### 11.1 Fix: Single Execution ID Flow

```
POST /api/workflows/{id}/run
  │
  ├─ Generate execution_id in route handler
  ├─ Create execution record in DB
  ├─ Register task with ExecutionManager
  └─ Pass execution_id to execute_workflow(execution_id=...)
        │
        ├─ Use the same execution_id for:
        │   ├─ DB records
        │   ├─ Queue creation
        │   ├─ Event emission
        │   └─ SSE stream key
        │
        └─ Frontend receives execution_id → opens SSE with same ID
```

### 11.2 Fix: Shared Execution Step Logic

Extract the per-agent execution logic into a shared helper:

```python
async def _execute_agent_step(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    ctx: ContextManager,
    agent_count: int,
    logs: list,
    steps: list,
    cancel_event: asyncio.Event,
    timeout: int,
) -> tuple[str, str | None]:  # Returns (status, error)
```

Called by both `execute_workflow` and `resume_workflow`, eliminating 200+ lines of duplication.

### 11.3 Fix: Explicit Lifecycle States

Define a state machine for execution status:

```
           ┌─────────┐
           │  idle   │
           └────┬────┘
                │ run
           ┌────▼────┐
           │ running │
           └┬──┬──┬──┘
            │  │  │
   approve  │  │  │  cancel
   reached  │  │  │
       ┌────▼──┘  │
       │ paused   │
       └────┬─────┘
            │ approve/reject
       ┌────▼────┐   ┌──────────┐
       │ running │   │ rejected │
       └────┬────┘   └──────────┘
            │ complete
       ┌────▼──────┐   ┌────────┐
       │ completed │   │ failed │
       └───────────┘   └────────┘

Also:
  running ──cancel──→ cancelled
  paused  ──cancel──→ cancelled
  running ──timeout─→ failed
  idle    ──timeout─→ failed (approval timeout)
```

---

## 12. Cleanup Strategy

### 12.1 Deterministic Cleanup Points

Every execution MUST clean up at one of these exit points:

| Exit Point | Trigger | Cleanup Actions |
|------------|---------|-----------------|
| Normal completion | All agents done | Remove from task registry, close queue, update DB |
| Agent failure | Exception in agent | Same as normal + set failed status |
| Cancellation | User cancels | Cancel task, remove queue, set cancelled status |
| Approval timeout | Timer expires | Set failed, close queue, remove from registry |
| Shutdown | Server stops | Cancel all tasks, clear all queues, close DB |
| Client disconnect | SSE closes | Cleanup queue (already done in stream.py finally) |

### 12.2 Queue Lifecycle Enforcement

```
create_stream() → queue created, tracked in _queues
emit()          → engine pushes events
subscribe()     → SSE pulls events
cleanup()       → queue removed, tracked in _queues
```

Enforcement rules:
1. `cleanup()` MUST be called when execution task finishes (engine's responsibility)
2. `cleanup()` MUST be called when SSE client disconnects (stream.py finally)
3. `cleanup()` MUST be called on task cancellation (cancellation handler)
4. Orphan sweep: periodic check for queues with no active task

### 12.3 Orphan Sweep

```python
async def _orphan_sweep(self):
    """Periodically clean up queues for executions with no active task."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        active = self._execution_manager.list_active()
        async with self._lock:
            orphaned = [eid for eid in self._queues if eid not in active]
            for eid in orphaned:
                self._queues.pop(eid, None)
                logger.warning("Swept orphaned queue: %s", eid)
```

---

## 13. Cancellation Architecture

### 13.1 User-Facing Cancellation Flow

```
Frontend                   Backend
   │                         │
   │  POST /executions/{id}/cancel
   │────────────────────────►│
   │                         ├─ ExecutionManager.cancel(id)
   │                         │   ├─ Set cancel_event for execution
   │                         │   ├─ task.cancel()  # raises CancelledError
   │                         │   ├─ Cleanup queue
   │                         │   ├─ Update DB status → 'cancelled'
   │                         │   └─ Return success
   │                         │
   │  ←─────────────────────│  { status: "cancelled" }
   │                         │
```

### 13.2 Thread Cancellation Strategy

Since `asyncio.to_thread` can't cancel running threads:

1. **Use `concurrent.futures.ThreadPoolExecutor`** with a dedicated pool
2. **Send cancellation signal** via a shared flag
3. **On timeout**: cancel the asyncio task, but the thread continues in background
4. **Mark thread as "zombie"**: track it, log warning, ignore its result
5. **Pool recycling**: periodic shutdown/recreate of thread pool to clean up zombie threads

```python
class ThreadManager:
    _executor: ThreadPoolExecutor
    _zombie_threads: set[Future]
    
    async def run_with_cancellation(self, fn, cancel_event, timeout):
        """Run fn in thread with cancellation support."""
        future = self._executor.submit(fn)
        try:
            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, future.result),
                timeout=timeout
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._zombie_threads.add(future)
            raise
```

### 13.3 Agent-Level Cancellation Checkpoints

Within the execution loop, check for cancellation signals:

```python
for idx, node in enumerate(sorted_nodes, start=1):
    if cancel_event.is_set():
        await _handle_cancellation(execution_id, ...)
        return
    
    # Before each DB operation
    if cancel_event.is_set():
        ...
    
    # Before each agent execution
    await _emit(execution_id, "thinking", ...)
    
    # Check again before the long-running call
    if cancel_event.is_set():
        ...
    
    output = await run_agent_with_timeout(agent, task, cancel_event, timeout)
```

---

## 14. Timeout Architecture

### 14.1 Timeout Configuration

```python
@dataclass
class TimeoutConfig:
    # Per-agent execution timeout (seconds)
    agent_timeout: int = 300  # 5 minutes default
    
    # Total workflow timeout (seconds), 0 = unlimited
    workflow_timeout: int = 3600  # 1 hour default
    
    # SSE idle timeout (seconds between events)
    stream_idle_timeout: int = 30
    
    # Approval timeout (seconds), 0 = never
    approval_timeout: int = 0  # Disabled by default
    
    # Ollama request timeout (seconds)
    ollama_request_timeout: int = 120  # 2 minutes
    
    # Database operation timeout (seconds)
    db_timeout: int = 10
```

### 14.2 Timeout Hierarchy

```
workflow_timeout (total wall clock)
  └── agent_timeout (per agent, checked before each agent)
        └── asyncio.wait_for(agent_timeout) wraps to_thread
              └── ollama_request_timeout (ChatOpenAI request timeout)
                    └── HTTP request timeout to Ollama server
```

### 14.3 Timeout Behavior

| Timeout Type | What Happens | Recovery |
|-------------|-------------|----------|
| Agent timeout | Emit `agent_timeout` event, mark step failed | Cont to next agent OR abort (configurable) |
| Workflow timeout | Emit `workflow_timeout`, cancel all agents | Abort, set status to `failed` |
| Approval timeout | Auto-reject, emit `approval_timeout` | Set status to `rejected` |
| Ollama timeout | Retry with backoff (up to 3 retries) | If all retries fail, mark as failed |
| Stream idle timeout | Close SSE connection gracefully | Client can reconnect |

---

## 15. Crash Recovery Strategy

### 15.1 Startup Recovery

On application startup, detect and clean up orphaned executions:

```python
async def _recover_orphaned_executions():
    """On startup, mark any 'running' or 'waiting_approval' executions as orphaned."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status FROM executions WHERE status IN ('running', 'waiting_approval')"
    )
    orphans = await cursor.fetchall()
    
    now = datetime.now(timezone.utc).isoformat()
    for row in orphans:
        execution_id = row["id"]
        await db.execute(
            "UPDATE executions SET status = 'orphaned', completed_at = ?, error = ? WHERE id = ?",
            (now, "Server restarted during execution", execution_id),
        )
        logger.warning("Marked orphaned execution: %s (%s)", execution_id, row["status"])
    
    await db.commit()
    logger.info("Recovered %d orphaned execution(s)", len(orphans))
```

### 15.2 Stream Reconnection on Resume

When a client reconnects to a stream after approval, they should receive a "catch-up" burst of any missed events:

```python
async def subscribe(execution_id, timeout=30.0, last_event_id=None):
    """Subscribe with optional replay from last_event_id."""
    if last_event_id:
        # Replay missed events from database
        missed = await _fetch_events_since(execution_id, last_event_id)
        for event in missed:
            yield event
    
    # Continue with live stream
    async for event in _live_subscribe(execution_id, timeout):
        yield event
```

### 15.3 Checkpoint Validation on Resume

Before resuming from a checkpoint, validate integrity:

```python
async def _validate_checkpoint(checkpoint: dict) -> bool:
    """Validate checkpoint integrity before resume."""
    required_keys = ["current_idx", "workflow_id", "nodes", "context_outputs"]
    if not all(k in checkpoint for k in required_keys):
        return False
    
    # Validate node indices are within range
    nodes = checkpoint.get("nodes", [])
    idx = checkpoint.get("current_idx", 0)
    if idx < 0 or idx >= len(nodes):
        return False
    
    return True
```

### 15.4 Graceful Shutdown

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await get_db()
    await _recover_orphaned_executions()
    
    yield  # App runs
    
    # Shutdown
    logger.info("Shutting down...")
    
    # Cancel all running executions
    active = execution_manager.list_active()
    for execution_id in active:
        logger.warning("Cancelling running execution on shutdown: %s", execution_id)
        await execution_manager.cancel(execution_id)
    
    # Clean up all SSE queues
    await event_manager.cleanup_all()
    
    # Close database
    await close_db()
    logger.info("Shutdown complete (cancelled %d execution(s))", len(active))
```

---

## Summary of Findings

### Criticalimmediate fixes needed)

| # | Issue | Impact |
|---|-------|--------|
| 1 | Route and engine use different execution IDs | SSE streaming broken |
| 2 | No task registry — tasks are orphaned | Cannot cancel/track executions |
| 3 | No cancellation mechanism | Stuck execution unrecoverable |
| 4 | No timeout on agent execution | Ollama hang blocks forever |
| 5 | Rerun returns fake execution ID | Rerun SSE streaming broken |

### High Priority (must fix for stability)

| # | Issue | Impact |
|---|-------|--------|
| 6 | Resume code ~200 lines duplicated from execute | Bug fixes don't propagate |
| 7 | No optimistic locking on state transitions | Race on approve/reject |
| 8 | SSE queue orphaned on task cancellation | Memory leak |
| 9 | Approval timeouts not enforced | Orphaned paused executions accumulate |
| 10 | Thread cancellation doesn't stop threads | Zombie threads on cancel |

### Medium Priority (should fix)

| # | Issue | Impact |
|---|-------|--------|
| 11 | Single SQLite connection | Minor throughput bottleneck |
| 12 | No explicit transactions | Partial write risk |
| 13 | Stale checkpoints if workflow edited during pause | Resume uses stale data |
| 14 | No startup recovery for orphaned executions | DB shows "running" for dead executions |
| 15 | EventSource auto-reconnect not handled | Duplicate event processing |
| 16 | No SSE heartbeat | Slow failure detection |
| 17 | No cleanup on shutdown | Leaked queues |
| 18 | Frontend page refresh loses connection | Orphaned backend execution |

---

## Recommended Implementation Order

```
Phase 1 ─── Critical (week 1)
  ├── Fix execution ID flow (route → engine)
  ├── Build ExecutionManager with task registry
  ├── Add cancellation endpoint + logic
  └── Add agent execution timeout

Phase 2 ─── Safety (week 2)
  ├── Add optimistic locking (approve/reject race)
  ├── Add per-execution execution guard (no re-entrancy)
  ├── Fix SSE queue cleanup in all paths
  └── Add startup orphan recovery

Phase 3 ─── Lifecycle (week 3)
  ├── Extract shared execution step logic
  ├── Fix rerun to return actual execution ID
  ├── Add approval timeout
  └── Add graceful shutdown with task cancellation

Phase 4 ─── Resilience (week 4)
  ├── Add Ollama failure retry with backoff
  ├── Add SSE heartbeat
  ├── Add checkpoint validation on resume
  └── Add stream replay on reconnect