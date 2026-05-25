# Autonomous AI Workspace Architecture

## Vision

This project is evolving from a manual workflow builder into an autonomous AI operating system.

The final goal is:

User gives one high-level task.
The system:

* analyzes the task
* decomposes the task
* dynamically creates suitable agents
* dynamically builds execution workflows
* selects tools automatically
* executes autonomously
* self-corrects failures
* optionally asks for approval only for dangerous actions

The system should feel similar to:

* Devin
* Manus
* OpenHands
* AutoGen
* Claude Code Agent
* OpenAI Deep Research

But simplified for:

* single-user usage
* local-first deployment
* local Ollama models
* lightweight architecture
* fast iteration

---

# Current Architecture

Frontend:

* Next.js
* React Flow workflow canvas
* SSE streaming terminal
* Tailwind UI

Backend:

* FastAPI
* CrewAI
* SQLite
* Ollama
* Docker Compose

Current capabilities:

* workflow execution engine
* node execution
* event streaming
* approval system
* workflow persistence
* multi-agent execution

Current limitation:

* workflows are mostly manually built
* agents are hardcoded
* user involvement is too high
* execution planning is static

---

# Target Architecture

The future architecture should be:

User Task
↓
Task Analyzer
↓
Planner Agent
↓
Task Decomposition
↓
Dynamic Agent Generator
↓
Workflow Graph Builder
↓
Execution Engine
↓
Tool Execution Layer
↓
Reflection / Self-Correction
↓
Final Output

---

# Tool Execution Layer

The next major subsystem is the Tool Execution Layer.
This layer enables agents to perform safe operational actions without changing the existing execution engine.
It sits below the agent runtime and above tool implementations.

Core components:

* ToolRegistry — deterministic catalog of available tools, explicit schemas, and permission metadata.
* ToolExecutionService — validation, sandboxed execution, approval gating, and persistence.
* Safe Tool Sandbox — executes tool adapters in a controlled manner.
* Shared memory persistence — records every tool action and output for later reasoning.

Goals:

* Keep tools deterministic, inspectable, and modular.
* Enforce explicit tool permissions before execution.
* Persist tool inputs, outputs, and metadata into shared memory.
* Gate dangerous actions with approval checkpoints.
* Avoid unrestricted shell or arbitrary Python execution.

Important rules:

* Tool execution must not require modifying the existing workflow execution engine.
* ToolRegistry must expose names, categories, input schemas, output schemas, permissions, and descriptions.
* Safe tools execute automatically; dangerous tools require approval.
* All tool actions are persisted as structured shared-memory records.
* No unbounded runtime code evaluation or injection of arbitrary code.

## ToolRegistry

ToolRegistry is the deterministic source of truth for tool metadata.
Each tool is defined with an explicit schema and safety profile.
The registry should support:

* Tool name and stable identifier.
* Category: safe or approval-required.
* Input schema: typed JSON contract for tool arguments.
* Output schema: typed JSON contract for results.
* Permission level: safe, restricted, approval-required.
* Description and inspector-friendly metadata.

Initial tool categories:

Safe tools:

* web_search
* file_read
* markdown_generation
* structured_data_extraction
* calculator
* json_transformation

Approval-required tools:

* shell_command_execution
* file_write
* file_delete
* http_post_request
* system_modification_action

## ToolExecutionService

ToolExecutionService sits between agents and actual tool implementations.
Its responsibilities are:

* Validate requested tool names against ToolRegistry.
* Validate input payloads against tool input schemas.
* Enforce deterministic permission rules.
* Decide whether execution is allowed, paused for approval, or denied.
* Execute tools inside a safe sandbox adapter.
* Persist tool call records into shared memory and event logs.
* Return normalized structured results to the agent runtime.

Execution flow:

1. Agent runtime emits a structured tool request.
2. ToolExecutionService looks up the tool in ToolRegistry.
3. The request is schema-validated.
4. Permission and approval rules are evaluated.
5. If safe, the tool is executed in the sandbox.
6. If approval is required, an approval node is inserted or the workflow pauses.
7. Tool inputs, outputs, status, and metadata are stored in shared memory.
8. The agent receives a normalized tool result.

## Approval-Gated Dangerous Tools

Dangerous tools must be explicitly gated by the approval system.
Approval should be required for:

* any shell command execution beyond a narrow built-in command set.
* file modifications, deletions, or writes.
* outbound HTTP POST requests.
* system modification actions that change state.

Approval gating rules:

* Dangerous tools are not executed automatically.
* The system should generate a precise approval request with tool metadata, arguments, and purpose.
* The approval layer must be able to reject or approve the action before execution continues.
* Safe tools continue without approval unless the task risk profile explicitly requires human review.

## Safe Sandboxed Execution

Safe tools must execute in a controlled adapter environment.
Sandbox rules include:

* No arbitrary Python execution within tools.
* No unrestricted shell access.
* All command or network execution is mediated by explicit adapters.
* Tool implementations may only use deterministic algorithms or tightly constrained libraries.
* Any external data access must be read-only unless approved.

Preferred implementation pattern:

* `ToolRegistry` defines adapter metadata.
* `SafeToolSandbox` provides a narrow execution wrapper.
* Tool adapters are pure functions or controlled services.
* Tool outputs are always JSON-compatible and structured.

## Persistent Tool Result Memory

Every tool execution must be persisted into the shared memory graph.
Persisted records should include:

* tool name
* category and permission level
* input arguments
* output payload
* execution timestamp
* execution status (success/failure/blocked)
* approval metadata when applicable

This enables later reflection, replanning, and execution auditing.

## Integration with Existing Engine

The Tool Execution Layer must integrate without replacing the current engine.
The existing workflow runtime remains responsible for:

* workflow node sequencing
* approval checkpoints
* agent execution boundaries
* event streaming

The new layer is an internal service called by agents and by workflow nodes when a tool call is needed.

Suggested integration pattern:

* Agent prompt or runtime emits a structured tool call event.
* The tool call is handled by ToolExecutionService.
* The tool result is returned to the agent as structured context.
* Workflow nodes continue to treat tool-driven steps as part of the execution stream.

## Autonomous Tool Usage Principles

Tool usage should be:

* explicit rather than implied.
* constrained by a deterministic tool contract.
* observable via metadata and logs.
* safe by default, with dangerous actions isolated.
* extensible through ToolRegistry rather than ad hoc code.

Keep the focus on safe operational capabilities first.
Avoid advanced autonomous computer control until the safe tool layer is stable.

---

# Design Philosophy

Always prioritize:

* simplicity
* modularity
* readability
* extensibility
* autonomous behavior
* local-first design
* minimal human interaction

Avoid:

* enterprise complexity
* unnecessary abstractions
* premature optimization
* cloud dependencies
* Kubernetes
* microservices
* hardcoded workflows
* giant god classes

Prefer:

* composition
* event-driven execution
* async APIs
* runtime configuration
* dynamic agent creation

---

# Agent System Rules

Agents should:

* be generated dynamically at runtime
* have clearly scoped responsibilities
* use tools automatically
* communicate through structured outputs
* support retries and reflection

Avoid hardcoded agents like:

* ResearcherAgent
* WriterAgent
* ReviewerAgent

Instead prefer:

* runtime-generated role definitions
* dynamic goals
* dynamic tools
* dynamic prompts

---

# Planner System Rules

The planner is the core intelligence layer.

The planner should:

* analyze user intent
* decompose tasks
* estimate required capabilities
* generate workflow graphs
* generate suitable agents
* determine execution order
* support parallel execution

Planner outputs should be JSON-compatible with the existing workflow engine.

---

# Workflow Generation Rules

Workflow graphs should:

* be generated dynamically
* support branching
* support retries
* support approvals
* support resumability

The workflow engine should remain compatible with:

* existing execution manager
* existing SSE event system
* existing persistence layer

---

# Human Approval Philosophy

Default mode:

* autonomous execution

Approval required only for:

* file deletion
* external API calls
* email sending
* payments
* dangerous shell commands

Do NOT require approval for normal reasoning tasks.

---

# Memory & Context

Future architecture should support:

* long-term memory
* vector memory
* execution history
* agent reflection
* resumable execution
* context injection

But these are secondary to the planner MVP.

---

# MVP Priorities

Priority order:

1. Planner Agent
2. Task Decomposition
3. Dynamic Agent Generator
4. Auto Workflow Builder
5. Autonomous Execution
6. Reflection System
7. Long-term Memory

Do NOT redesign the whole system at once.

Implement incrementally.

---

# Coding Standards

Backend:

* async Python
* FastAPI
* strongly typed models
* modular services
* minimal coupling

Frontend:

* clean modern UI
* terminal-first UX
* execution visibility
* avoid UI clutter

General:

* explain architectural reasoning
* document important decisions
* prefer maintainability
* avoid hidden magic

---

# Important Goal

The most important transformation is:

FROM:
manual workflow builder

TO:
autonomous AI operating system
