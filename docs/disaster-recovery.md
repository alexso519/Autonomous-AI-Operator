# Disaster Recovery

## Snapshot API

```bash
curl -X POST http://localhost:8000/api/infrastructure/dr/snapshot
```

Creates a point-in-time snapshot of `persistent_executions` and `worker_nodes`.

## Execution export

Use `DisasterRecovery.export_execution(execution_id)` programmatically to export checkpoint lineage for replay.

## Kubernetes backup CronJob

Apply `k8s/disaster-recovery/backup-cronjob.yaml` for nightly automated snapshots.

## Recovery procedure

1. Verify backup: security hardening `backup_validation`
2. Import execution payload via `DisasterRecovery.import_execution`
3. Restore from checkpoint: `POST /api/infrastructure/recovery/{id}/restore`
4. Enable emergency degraded mode if partial failure persists

## RTO / RPO targets (starter)

| Tier | RPO | RTO |
|------|-----|-----|
| Enterprise | 1h | 4h |
| Regulated | 15m | 1h |
