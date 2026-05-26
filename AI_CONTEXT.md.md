# Project Vision

This project is an Autonomous AI Operating System.

Core architecture:
- FastAPI backend
- Next.js frontend
- CrewAI orchestration
- Ollama local models
- SQLite persistence
- Docker Compose runtime

System capabilities:
- TaskAnalyzer
- WorkflowPlanner
- DynamicAgentFactory
- RuntimeStateMachine
- SharedExecutionMemory
- Reflection/Replanning
- ToolExecutionLayer
- ApprovalSystem
- ToolInvocationRuntime

Architecture rules:
- Keep existing execution engine
- Keep deterministic planning
- Local-first only
- Single-user only
- No enterprise complexity
- No distributed execution
- No Kubernetes

Current priority:
Build a usable autonomous AI operator experience.

Main UX target:
User enters one task.
System autonomously:
- analyzes
- plans
- creates agents
- uses tools
- reflects
- replans
- completes work

Current focus:
Chat-first autonomous UI.