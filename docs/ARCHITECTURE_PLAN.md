# Personal AI Agent Workspace — Architecture Plan

## Version: Phase 1 Analysis
## Goal: Fastest path to a working, impressive PoC

---

# 1. MINIMAL ARCHITECTURE

```
┌─────────────────────────────────────────────────────┐
│                  Docker Compose                       │
│                                                       │
│  ┌──────────────────┐    ┌──────────────────┐        │
│  │    Frontend       │    │     Backend       │        │
│  │   (Next.js)       │◄──►│    (FastAPI)      │        │
│  │   Port 3000       │    │    Port 8000      │        │
│  └──────────────────┘    └────────┬─────────┘        │
│                                   │                    │
│                          ┌────────▼─────────┐        │
│                          │     Ollama         │        │
│                          │   Port 11434      │        │
│                          └──────────────────┘        │
│                                                       │
│  ┌──────────────────┐                                 │
│  │     SQLite        │                                 │
│  │   (data/workflows.db)                               │
│  └──────────────────┘                                 │
└─────────────────────────────────────────────────────┘
```

## Design Decisions

### Why this architecture?

| Decision | Why |
|----------|-----|
| **Monolith Docker Compose** | Simplest deployment. One `docker compose up`. No orchestration. |
| **Frontend calls FastAPI directly** | No API gateway, no reverse proxy. CORS is enough for PoC. |
| **SQLite over PostgreSQL** | Zero setup. File-based. Backed up by copying one file. |
| **Zustand over Redux** | No boilerplate. Minimal API surface. React Flow integrates natively. |
| **SSE over WebSocket** | Unidirectional streaming is enough. Simpler. No reconnection complexity. |
| **CrewAI sync execution** | Sequential pipeline is the spec. No celery, no queues. |
| **No nginx** | One less service to maintain. Next.js dev server + FastAPI + Ollama is 3 containers. |

### Communication Pattern

```
Browser → FastAPI (HTTP + SSE)
Browser → FastAPI directly (via NEXT_PUBLIC_API_URL)
No Next.js API route proxying → keeps it simple
FastAPI → Ollama (HTTP, localhost:11434)
```

---

# 2. FINAL PROJECT FOLDER STRUCTURE

```
/workspace
├── frontend/                          # Next.js 14 App Router
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx             # Root layout (dark theme, sidebar+canvas+inspector)
│   │   │   ├── page.tsx               # Main workspace page
│   │   │   └── globals.css            # Tailwind imports + dark theme variables
│   │   ├── components/
│   │   │   ├── canvas/
│   │   │   │   ├── WorkflowCanvas.tsx      # React Flow wrapper
│   │   │   │   ├── AgentNode.tsx           # Custom React Flow node
│   │   │   │   └── nodeTypes.ts            # Node type registry
│   │   │   ├── sidebar/
│   │   │   │   ├── Sidebar.tsx             # Left panel container
│   │   │   │   ├── AgentPalette.tsx        # Draggable agent list
│   │   │   │   └── TemplateList.tsx        # Saved templates
│   │   │   ├── inspector/
│   │   │   │   └── InspectorPanel.tsx      # Right panel for node config
│   │   │   ├── terminal/
│   │   │   │   └── ThinkingTerminal.tsx    # Bottom panel live logs
│   │   │   ├── toolbar/
│   │   │   │   └── WorkflowToolbar.tsx     # Run, Save, Load buttons
│   │   │   └── ui/                         # Reusable UI primitives
│   │   │       ├── Button.tsx
│   │   │       └── Card.tsx
│   │   ├── stores/
│   │   │   ├── workflowStore.ts        # Zustand: nodes, edges, workflow metadata
│   │   │   ├── executionStore.ts       # Zustand: run state, logs, SSE connection
│   │   │   └── uiStore.ts             # Zustand: selected node, panel visibility
│   │   ├── hooks/
│   │   │   ├── useSSE.ts              # SSE connection hook
│   │   │   └── useWorkflowActions.ts  # Save/load/run workflow
│   │   ├── types/
│   │   │   └── index.ts               # All TypeScript interfaces
│   │   ├── lib/
│   │   │   ├── api.ts                 # Fetch wrapper for backend
│   │   │   └── constants.ts           # Agent presets, colors, etc.
│   │   └── services/
│   │       └── workflowService.ts     # API call functions
│   ├── public/
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   └── postcss.config.js
│
├── backend/                           # FastAPI
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI app, CORS, router registration
│   │   ├── config.py                  # Settings (OLLAMA_URL, DB_PATH, etc.)
│   │   ├── database.py                # SQLite connection + init
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── workflow.py           # Pydantic models for workflow
│   │   │   └── execution.py          # Pydantic models for execution/logs
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── workflows.py          # CRUD endpoints
│   │   │   ├── execution.py          # Run + stream endpoints
│   │   │   └── templates.py          # Template endpoints
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── workflow_service.py   # Workflow business logic
│   │   │   └── template_service.py   # Template management
│   │   ├── execution/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py             # Core execution loop
│   │   │   ├── crew_adapter.py       # CrewAI bridge
│   │   │   └── workflow_mapper.py    # Graph → CrewAI config
│   │   └── streaming/
│   │       ├── __init__.py
│   │       └── manager.py            # SSE event manager
│   ├── requirements.txt
│   └── Dockerfile
│
├── data/                              # SQLite + runtime files
│   └── .gitkeep
│
├── docker-compose.yml                 # Frontend + Backend + Ollama
├── .env.example
├── .gitignore
└── README.md
```

---

# 3. FRONTEND / BACKEND RESPONSIBILITY SPLIT

| Concern | Owned By | Details |
|---------|----------|---------|
| **Canvas rendering** | Frontend | React Flow handles all visual graph rendering |
| **Node state** | Frontend | Zustand `workflowStore` manages nodes/edges locally |
| **Drag-and-drop** | Frontend | HTML5 drag from sidebar → React Flow drop zone |
| **Node configuration** | Frontend | Inspector panel edits node props → updates store |
| **Workflow persistence** | Backend | POST/GET/PUT/DELETE `/api/workflows` |
| **Workflow execution** | Backend | CrewAI sequential pipeline |
| **Live logs** | Backend → Frontend | SSE stream from execution engine to terminal |
| **Approval flow** | Both | Backend pauses → Frontend shows approve button → Backend resumes |
| **Template storage** | Backend | CRUD for workflow templates |
| **LLM interaction** | Backend → Ollama | Backend calls Ollama HTTP API |
| **UI polish** | Frontend | Animations, transitions, loading states |
| **Workflow validation** | Both | Frontend basic validation, Backend deep validation |

---

# 4. DATA FLOW

```
┌──────────────────────────────────────────────────────────┐
│                      DATA FLOW                             │
│                                                           │
│  CREATE WORKFLOW:                                         │
│  ┌────────┐    drag nodes    ┌────────┐                  │
│  │Palette │ ──────────────► │ Canvas │                  │
│  └────────┘                  └────────┘                  │
│                                  │                        │
│                          update Zustand                   │
│                                  │                        │
│                          [workflowStore]                  │
│                                  │                        │
│                    Save (POST /api/workflows)             │
│                                  │                        │
│                              ┌────▼────┐                 │
│                              │ SQLite  │                 │
│                              └─────────┘                 │
│                                                           │
│  RUN WORKFLOW:                                           │
│  [Run Button] ──► POST /api/workflows/{id}/run           │
│                           │                               │
│                      Execution Engine                     │
│                           │                               │
│                    ┌───────┴───────┐                     │
│                    │  CrewAI Loop  │                     │
│                    │  node1 → node2│                     │
│                    │  → node3 ...  │                     │
│                    └───────┬───────┘                     │
│                            │ (per step)                  │
│                    ┌───────▼───────┐                     │
│                    │  SSE Stream   │                     │
│                    │  manager.push │                     │
│                    └───────┬───────┘                     │
│                            │                             │
│              GET /api/workflows/{id}/stream              │
│                            │                             │
│                    ┌───────▼───────┐                     │
│                    │ Thinking      │                     │
│                    │ Terminal (UI) │                     │
│                    └───────────────┘                     │
└──────────────────────────────────────────────────────────┘
```

---

# 5. SIMPLIFIED EXECUTION ENGINE DESIGN

## Core Concept

```
Workflow = ordered list of nodes (topologically sorted by edges)
Execution = for each node → create CrewAI agent → run task → pass context to next
```

## Engine Pseudocode

```python
# backend/app/execution/engine.py

class ExecutionEngine:
    """Simplest possible sequential execution engine."""
    
    async def execute(self, workflow_id: str, stream_manager: SSEStreamManager):
        workflow = self._load_workflow(workflow_id)
        nodes = self._topological_sort(workflow.nodes, workflow.edges)
        
        context = {}  # accumulated outputs
        
        for node in nodes:
            # 1. Signal start
            await stream_manager.push({
                "type": "agent_start",
                "agent": node.data.name,
                "role": node.data.role,
                "node_id": node.id
            })
            
            # 2. Create CrewAI agent
            agent = self._create_agent(node, context)
            task = self._create_task(node, context)
            
            # 3. Execute with streaming logs
            async for log in self._run_crew(agent, task):
                await stream_manager.push({
                    "type": "log",
                    "agent": node.data.name,
                    "content": log,
                    "node_id": node.id
                })
            
            # 4. Check approval if node requires it
            if node.data.approval_required:
                await stream_manager.push({
                    "type": "approval_request",
                    "agent": node.data.name,
                    "output": result,
                    "node_id": node.id
                })
                approved = await self._wait_for_approval(node.id)
                if not approved:
                    await stream_manager.push({
                        "type": "agent_rejected",
                        "agent": node.data.name,
                        "node_id": node.id
                    })
                    break
            
            # 5. Store output in context
            context[node.id] = result
            
            await stream_manager.push({
                "type": "agent_complete",
                "agent": node.data.name,
                "output": result,
                "node_id": node.id
            })
        
        await stream_manager.push({
            "type": "workflow_complete",
            "output": context
        })
```

## Key Simplifications

| Complexity | We Skip | We Do Instead |
|------------|---------|---------------|
| **Parallel execution** | Multi-threaded agents | Sequential `for node in nodes` |
| **DAG scheduling** | Full graph engine | Topological sort (simple) |
| **Task queue** | Celery, Redis | In-process async execution |
| **State persistence** | Event sourcing | SQLite append-only logs |
| **Retry logic** | Exponential backoff | Simple retry x3 with delay |

---

# 6. STATE MANAGEMENT STRATEGY

## Zustand Store Architecture

```typescript
// Three small stores, no selectors, no middleware

// stores/workflowStore.ts
interface WorkflowStore {
  // Workflow metadata
  workflowId: string | null;
  workflowName: string;
  
  // React Flow state
  nodes: Node<AgentNodeData>[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  
  // Actions
  setWorkflow: (nodes: Node[], edges: Edge[]) => void;
  addNode: (type: string, position: XYPosition) => void;
  removeNode: (id: string) => void;
  updateNodeData: (id: string, data: Partial<AgentNodeData>) => void;
  loadWorkflow: (id: string) => Promise<void>;
  saveWorkflow: () => Promise<void>;
  resetCanvas: () => void;
}

// stores/executionStore.ts
interface ExecutionStore {
  isRunning: boolean;
  currentAgent: string | null;
  logs: LogEntry[];
  
  // Actions
  startExecution: (workflowId: string) => Promise<void>;
  appendLog: (entry: LogEntry) => void;
  clearLogs: () => void;
  setRunning: (running: boolean) => void;
}

// stores/uiStore.ts
interface UIStore {
  selectedNodeId: string | null;
  showSidebar: boolean;
  showInspector: boolean;
  
  // Actions
  selectNode: (id: string | null) => void;
  toggleSidebar: () => void;
  toggleInspector: () => void;
}
```

## Why Three Stores

- **workflowStore**: Vanilla React Flow state + persistence. Changes on every drag. Keep isolated.
- **executionStore**: Completely separate lifecycle. Only active during runs. High-frequency updates.
- **uiStore**: UI concerns only. Never touches workflow data.

No derived stores. No selectors. No computed values. Direct mutation with Zustand's `set`.

---

# 7. STREAMING ARCHITECTURE (SSE)

## Backend (FastAPI)

```python
# backend/app/streaming/manager.py

class SSEStreamManager:
    """Manages per-workflow SSE event queues."""
    
    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
    
    async def subscribe(self, workflow_id: str) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            if workflow_id not in self._queues:
                self._queues[workflow_id] = []
            self._queues[workflow_id].append(queue)
        return queue
    
    async def unsubscribe(self, workflow_id: str, queue: asyncio.Queue):
        async with self._lock:
            if workflow_id in self._queues:
                self._queues[workflow_id].remove(queue)
    
    async def push(self, workflow_id: str, event: dict):
        async with self._lock:
            queues = self._queues.get(workflow_id, [])
            for q in queues:
                await q.put(event)

# Route
@router.get("/api/workflows/{workflow_id}/stream")
async def stream_workflow(workflow_id: str, manager: SSEStreamManager = Depends()):
    queue = await manager.subscribe(workflow_id)
    
    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "workflow_complete":
                    break
        finally:
            await manager.unsubscribe(workflow_id, queue)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

## Frontend (SSE Hook)

```typescript
// hooks/useSSE.ts
export function useSSE(workflowId: string | null) {
  const appendLog = useExecutionStore(s => s.appendLog);
  const setRunning = useExecutionStore(s => s.setRunning);
  
  useEffect(() => {
    if (!workflowId) return;
    
    const eventSource = new EventSource(
      `${API_URL}/api/workflows/${workflowId}/stream`
    );
    
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case 'log':
          appendLog(data);
          break;
        case 'agent_start':
          useExecutionStore.getState().setCurrentAgent(data.agent);
          break;
        case 'workflow_complete':
          setRunning(false);
          eventSource.close();
          break;
      }
    };
    
    return () => eventSource.close();
  }, [workflowId]);
}
```

## SSE Event Types

```typescript
type SSEEvent =
  | { type: 'agent_start'; agent: string; role: string; node_id: string; timestamp: string }
  | { type: 'log'; agent: string; content: string; node_id: string; timestamp: string }
  | { type: 'tool_use'; agent: string; tool: string; input: string; timestamp: string }
  | { type: 'agent_complete'; agent: string; output: string; node_id: string; timestamp: string }
  | { type: 'approval_request'; agent: string; output: string; node_id: string; timestamp: string }
  | { type: 'workflow_complete'; output: Record<string, string>; timestamp: string }
  | { type: 'workflow_error'; error: string; node_id: string; timestamp: string };
```

---

# 8. DATABASE SCHEMA (MINIMAL)

```sql
-- Only 4 tables. No migrations. SQLite creates on startup.

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT NOT NULL DEFAULT 'Untitled Workflow',
    description TEXT DEFAULT '',
    nodes TEXT NOT NULL DEFAULT '[]',       -- JSON array of node objects
    edges TEXT NOT NULL DEFAULT '[]',       -- JSON array of edge objects
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    nodes TEXT NOT NULL,                     -- JSON array of node objects
    edges TEXT NOT NULL,                     -- JSON array of edge objects
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed | rejected
    started_at TEXT,
    completed_at TEXT,
    result TEXT,                             -- JSON final output
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    node_id TEXT,
    agent_name TEXT,
    log_type TEXT NOT NULL,                  -- log | output | error | approval_request | tool_use
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (execution_id) REFERENCES executions(id)
);

-- Index for fast log retrieval
CREATE INDEX IF NOT EXISTS idx_execution_logs_execution_id 
    ON execution_logs(execution_id);
```

## Why This Schema

| Table | Purpose | Why Minimal |
|-------|---------|-------------|
| `workflows` | Store full graph as JSON blobs | No need for normalized nodes/edges tables. JSON is queryable enough. |
| `templates` | Same schema as workflows, but read-only presets | Simple copy-on-save pattern. |
| `executions` | Track each run | Enables history view later. |
| `execution_logs` | Append-only log of all events | Single table for all event types. `log_type` column discriminates. |

No foreign key constraints enforced (SQLite pragma). No cascade deletes. No triggers.
Datetime as TEXT (ISO 8601) — simpler than timestamp types.

---

# 9. DEVELOPMENT PHASE BREAKDOWN

```
Week 1 ──────────────────────────────────────────────────────
│
├─ Phase 1 (Days 1-2): BOOTSTRAP
│     Frontend scaffold + Docker + basic layout + health check
│     → "Everything boots, you can see the layout"
│
├─ Phase 2 (Days 3-4): CANVAS + INSPECTOR
│     React Flow + drag-drop nodes + edit agent config
│     → "You can build and edit a workflow visually"
│
├─ Phase 3 (Day 5): BACKEND EXECUTION
│     FastAPI run + CrewAI integration + Ollama connection
│     → "Agents actually execute something"
│
├─ Phase 4 (Day 6): LIVE TERMINAL
│     SSE streaming + thinking terminal UI + agent activity
│     → "You see AI thinking in real-time"
│
└─ Phase 5 (Day 7): APPROVAL + TEMPLATES
      Approval nodes + pause/resume + save/load templates
      → "Complete workflow with human supervision"

Week 2 ──────────────────────────────────────────────────────
│
└─ Phase 6: POLISH
      Animations, loading states, error handling, demo workflows, UX fixes
      → "Impressive personal AI workspace"
```

---

# 10. WHAT WE SHOULD NOT BUILD YET

| Feature | Reason to Skip | Build When |
|---------|---------------|------------|
| **User authentication** | Single-user PoC | Never for PoC |
| **Multiple users/teams** | Not the product | Not needed |
| **RBAC/permissions** | Single user owns everything | Not needed |
| **PostgreSQL** | SQLite is sufficient | If data > 1GB |
| **Celery/Redis queues** | Sequential is synchronous | If agents run > 5 min |
| **Kubernetes** | Docker Compose is fine | Never |
| **Auto-layout engine** | Manual layout is fine | If users complain |
| **Undo/redo history** | Adds complexity | Phase 7 if needed |
| **Vector memory** | Not needed for sequential workflows | Phase 8+ |
| **Plugin system** | Not needed | Phase 9+ |
| **Mobile support** | Desktop only | Not needed |
| **Comprehensive error handling** | Basic errors only | Phase 6 polish |
| **Unit tests** | Not for PoC speed | Before demo |
| **CI/CD pipeline** | Manual deploy is fine | Never |
| **WebSocket** | SSE is simpler | If bidirectional needed |
| **Docker volumes for Ollama models** | Manual pull is okay | Phase 3 |

---

# 11. PHASE 1 — EXACT IMPLEMENTATION ROADMAP

## Goal
Project boots locally with `docker compose up`. Dark theme layout visible. Health check passes.

## Day 1.1 — Project Scaffold

### Files to create:

```
docker-compose.yml
.env.example
.gitignore
README.md
backend/requirements.txt
backend/Dockerfile
backend/app/__init__.py
backend/app/main.py
backend/app/config.py
backend/app/database.py
frontend/package.json
frontend/next.config.js
frontend/tsconfig.json
frontend/tailwind.config.ts
frontend/postcss.config.js
frontend/src/app/globals.css
```

### Tasks:
1. Create [`docker-compose.yml`](docker-compose.yml) with 3 services: `frontend`, `backend`, `ollama`
2. Create [`backend/app/main.py`](backend/app/main.py) with FastAPI app, CORS, health endpoint
3. Create [`backend/app/config.py`](backend/app/config.py) with settings from environment
4. Create [`backend/app/database.py`](backend/app/database.py) with SQLite init + table creation
5. Create [`backend/requirements.txt`](backend/requirements.txt) with: `fastapi uvicorn pydantic aiosqlite crewai ollama python-dotenv`
6. Create [`backend/Dockerfile`](backend/Dockerfile) with Python 3.11, requirements install
7. Create [`frontend/package.json`](frontend/package.json) with: next, react, react-dom, tailwindcss, zustand, reactflow, typescript, @types/react, @types/node, autoprefixer, postcss
8. Create config files for Next.js, TypeScript, Tailwind
9. Create [`frontend/src/app/globals.css`](frontend/src/app/globals.css) with Tailwind directives + dark theme variables

## Day 1.2 — Basic Layout

### Files to create:

```
frontend/src/app/layout.tsx
frontend/src/app/page.tsx
frontend/src/stores/workflowStore.ts
frontend/src/stores/uiStore.ts
frontend/src/types/index.ts
frontend/src/lib/constants.ts
frontend/src/lib/api.ts
frontend/src/components/canvas/WorkflowCanvas.tsx
frontend/src/components/sidebar/Sidebar.tsx
frontend/src/components/inspector/InspectorPanel.tsx
frontend/src/components/terminal/ThinkingTerminal.tsx
```

### Tasks:
1. Create [`frontend/src/app/layout.tsx`](frontend/src/app/layout.tsx) with:
   - Dark theme (`class="dark"` on `<html>`)
   - Inter font import
   - `globals.css` import
   - Basic meta tags

2. Create [`frontend/src/app/page.tsx`](frontend/src/app/page.tsx) with layout grid:
   ```
   ┌─────────┬────────────────────┬──────────┐
   │ Sidebar │  Workflow Canvas   │ Inspector│
   │ 240px   │  flex-1            │ 280px    │
   ├─────────┴────────────────────┴──────────┤
   │  Thinking Terminal (200px min-height)    │
   └──────────────────────────────────────────┘
   ```

3. Create [`frontend/src/stores/workflowStore.ts`](frontend/src/stores/workflowStore.ts):
   - Zustand store with `nodes`, `edges`, React Flow handlers
   - `addNode`, `removeNode`, `updateNodeData` actions
   - `loadWorkflow`, `saveWorkflow` (stubs)
   - `setWorkflow`, `resetCanvas`

4. Create [`frontend/src/types/index.ts`](frontend/src/types/index.ts):
   - `AgentNodeData` interface
   - `LogEntry` interface
   - `Workflow` interface
   - Agent preset type

5. Create [`frontend/src/components/canvas/WorkflowCanvas.tsx`](frontend/src/components/canvas/WorkflowCanvas.tsx):
   - React Flow with dark theme
   - Default empty view
   - Drop handler for new nodes

6. Create placeholder components for sidebar, inspector, terminal

## Day 1.3 — Backend Routes

### Files to create/modify:

```
backend/app/__init__.py
backend/app/routes/__init__.py
backend/app/routes/health.py
backend/app/models/__init__.py
backend/app/models/workflow.py
```

### Tasks:
1. Create health route: `GET /api/health` → `{"status": "ok", "version": "0.1.0"}`
2. Create Pydantic models for workflow (id, name, nodes, edges)
3. Register routes in [`backend/app/main.py`](backend/app/main.py)

## Day 1.4 — Verify Everything Works

### Tasks:
1. Run `docker compose build` — verify no errors
2. Run `docker compose up` — verify all 3 containers start
3. Verify `http://localhost:3000` shows the workspace layout
4. Verify `http://localhost:8000/api/health` returns OK
5. Verify `http://localhost:11434` (Ollama) responds
6. Commit the working state

## Phase 1 Deliverables Checklist

```
[ ] docker-compose.yml with frontend + backend + ollama
[ ] Frontend boots at localhost:3000
[ ] Backend boots at localhost:8000
[ ] Ollama is accessible
[ ] Dark theme layout visible
[ ] Sidebar panel (placeholder)
[ ] Canvas panel (React Flow, empty)
[ ] Inspector panel (placeholder)
[ ] Terminal panel (placeholder, says "Ready")
[ ] API health check works
[ ] SQLite database initializes
[ ] All Docker builds succeed
[ ] Git initialized with .gitignore
```

---

# 12. RISK MANAGEMENT

| Risk | Impact | Mitigation |
|------|--------|------------|
| Ollama not running in Docker | Execution fails | Use mock execution for dev, fallback to mock mode |
| CrewAI not compatible with Python version | Runtime errors | Pin Python 3.11 in Docker, use exact CrewAI version |
| React Flow SSR issues | Build fails | Use `next/dynamic` with `ssr: false` for canvas component |
| SSE connection drops in dev | Lost logs | Auto-reconnect in SSE hook (EventSource handles this) |
| Zustand + React Flow conflicts | Stale state | Use `useReactFlow` hook for imperative updates |
| Docker networking issues | Can't connect services | Use `depends_on` + health checks in compose |

---

# 13. IMMEDIATE NEXT STEPS

1. Accept this architecture plan
2. Create [`docker-compose.yml`](docker-compose.yml) (the foundation)
3. Initialize frontend project structure
4. Initialize backend project structure
5. Build and verify locally
6. Proceed to Phase 2

---

*This document is a living plan. Update it as decisions change during implementation.*