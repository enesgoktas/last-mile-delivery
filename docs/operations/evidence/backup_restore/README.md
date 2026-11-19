# Backup and Restore Evidence

Store restore rehearsal evidence here.

Minimum evidence for a valid rehearsal:
- timestamp
- source environment/database
- backup artifact checksum
- restored database name
- Alembic version
- confirmation that table counts matched between source and restore
- operator initials or automation identifier

Use [infra/scripts/rehearse_postgres_restore.py](/Users/dorukyalcin/Desktop/LastMileDelivery/infra/scripts/rehearse_postgres_restore.py) for local PostgreSQL restore drills.
