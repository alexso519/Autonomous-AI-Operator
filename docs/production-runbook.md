# Production Runbook

## Deployment profiles

| Profile | Flags |
|---------|-------|
| `local_dev` | Default — single-node SQLite |
| `single_node` | `ENABLE_DISTRIBUTED_RUNTIME=1` |
| `distributed_cluster` | Distributed + `REDIS_URL` |
| `enterprise` | RBAC + multi-tenancy + governance |
| `cloud_federated` | Mesh + autoscaling |

## Enterprise feature flags

```bash
ENABLE_RUNTIME_MESH=1
ENABLE_POLICY_ENGINE=1
ENABLE_COST_METERING=1
ENABLE_INFRA_INTELLIGENCE=1
ENABLE_AUTOSCALING=1
ENABLE_APPROVAL_WORKFLOWS=1
COMPLIANCE_MODE=enterprise
DEFAULT_EXECUTION_BUDGET=500
```

## Health checks

- Liveness: `GET /api/infrastructure/health`
- Dashboard: `GET /api/infrastructure/dashboard`
- Mesh topology: `GET /api/infrastructure/mesh`

## Zero-downtime rollout

1. Run pre-deploy: `GET /api/infrastructure/release`
2. Drain workers via deployment orchestrator
3. Apply Helm upgrade with `helm/enterprise-values.yaml`
4. Verify mesh health score > 0.8

## Incident response

- Budget exceeded → check `GET /api/infrastructure/metering`
- Policy violations → `GET /api/infrastructure/governance`
- Anomalies → `GET /api/infrastructure/intelligence`
- Emergency: `POST /api/infrastructure/dr/snapshot`
