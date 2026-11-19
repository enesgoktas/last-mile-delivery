# PostgreSQL Restore Rehearsal

- Generated at: `2026-03-26T21:23:09.209856+00:00`
- Operator: `dorukyalcin`
- Source host: `localhost`
- Source port: `5432`
- Source database: `delivery_db`
- Restore database: `delivery_db_restore_rehearsal_20260326212307`
- Dump file: `/var/folders/m5/q1xh0vmx47907d7tzfvj74yh0000gn/T/restore-rehearsal-9mjgt41c/delivery_db.dump`
- Dump SHA-256: `ebc735f420eae16eff30c050ba343a590ee1ec2d72ae07e25a32e6ec5def016f`
- Dump size (bytes): `168014`
- Alembic version: `20260314_0015`
- Verified public tables: `22`
- Result: `PASS`

## Notes
- Postgres is treated as the authoritative transactional store.
- Redis is not restored by this rehearsal because it is treated as rebuildable runtime state (cache, broker, revocation, rate limits).
- POD asset recovery is covered separately by S3 versioning and bucket controls in Terraform.

## Table parity sample

| table | source rows | restored rows |
|---|---:|---:|
| `alembic_version` | 1 | 1 |
| `api_clients` | 0 | 0 |
| `audit_logs` | 17 | 17 |
| `couriers` | 3 | 3 |
| `depots` | 1 | 1 |
| `import_jobs` | 0 | 0 |
| `inventory_events` | 10 | 10 |
| `location_snapshots` | 4 | 4 |
| `notifications` | 24 | 24 |
| `optimization_runs` | 50 | 50 |
| `order_status_history` | 62 | 62 |
| `orders` | 19 | 19 |

Full parity across all public tables was verified by the rehearsal script.
