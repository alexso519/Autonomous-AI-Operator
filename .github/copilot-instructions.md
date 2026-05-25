# Personal AI Workspace Rules

You are building a local-first AI multi-agent workspace.

Architecture:
- Frontend: Next.js
- Backend: FastAPI
- AI Runtime: Ollama
- Agent Framework: CrewAI
- Database: SQLite
- Container: Docker Compose

Rules:
- Prefer simple solutions over enterprise complexity
- Avoid unnecessary abstractions
- Keep everything single-user focused
- Avoid cloud dependencies
- Prefer local Ollama models
- Never introduce Kubernetes
- Keep code modular and readable
- Prefer async Python APIs
- Use Tailwind for UI styling
- Keep UI modern and minimal

Agent Behavior:
- User inputs one task
- System auto-generates suitable agents
- Agents execute automatically
- Human approval only for dangerous actions
- Show real-time thinking logs
- Show execution graph visually

Coding Style:
- Strong typing
- Clean architecture
- Avoid duplicated logic
- Prefer composition over inheritance