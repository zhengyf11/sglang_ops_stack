# Production deployment

This compose stack runs SGLang Ops Stack with a FastAPI web process, a Celery worker,
Redis, and PostgreSQL. It is intended for a trusted private network only.

## Security boundary

- Do not expose the web service directly to the public Internet. Bind it to localhost or a
  trusted internal load balancer/VPN and terminate TLS at the trusted edge.
- Use a unique high-entropy `SGLANG_OPS_AUTH_SECRET_KEY` in every environment. Rotating this
  key invalidates existing JWT sessions; perform rotation during a maintenance window and ask
  users to log in again.
- SSH passwords remain request/session credentials for job execution. They are not persisted by
  the application. In Celery mode, task payloads carry only application-encrypted short-lived
  credential material derived from `SGLANG_OPS_AUTH_SECRET_KEY`; plaintext passwords are never sent
  to Redis. The provided compose file disables Redis AOF/RDB persistence so broker/backend payloads
  are not written to disk. If a future deployment integrates a credential vault, store only encrypted
  ciphertext or opaque vault references and never return plaintext secrets from the API.
- Docker `--privileged`, host networking, host IPC, root container users, and all-GPU exposure are
  high-risk settings. The API surfaces warnings and audit records for deployment changes; approve
  these settings only on trusted hosts.
- Application logs, job logs, and audit summaries are redacted for passwords, tokens, API keys,
  Authorization headers, private keys, and sensitive URL query parameters.

## Required environment variables

Create a local `.env` file next to this README before running compose:

```env
POSTGRES_PASSWORD=<generate-a-long-random-password>
SGLANG_OPS_AUTH_SECRET_KEY=<generate-at-least-32-random-bytes>
SGLANG_OPS_BOOTSTRAP_ADMIN_USERNAME=<initial-admin-name>
SGLANG_OPS_BOOTSTRAP_ADMIN_PASSWORD=<initial-admin-password>
```

Optional variables:

- `POSTGRES_DB` (default: `sglang_ops_stack`)
- `POSTGRES_USER` (default: `sglang_ops`)
- `SGLANG_OPS_REQUIRE_HTTPS` (default: `true`)
- `SGLANG_OPS_DOCS_ENABLED` (default: `false`)

Remove the bootstrap admin password from `.env` after the first successful login and user
creation workflow. Existing users remain in PostgreSQL with PBKDF2 password hashes.

## Start and migrate

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d postgres redis
docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm web alembic upgrade head
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d web worker
```

Check health:

```bash
curl -fsS http://127.0.0.1:8000/health
```

## Worker queue

Set `SGLANG_OPS_TASK_QUEUE_MODE=celery` in production. API routes persist `Job` rows and enqueue
long-running SSH, environment, deployment, operation, and redeploy tasks to Redis. The worker uses
late acknowledgements and a single prefetch multiplier so interrupted tasks can be retried by the
broker/worker policy; failed task execution updates persisted job status when the task runner catches
errors.

## Backup and restore

Back up PostgreSQL regularly:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

Restore into an empty database:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T postgres \
  psql -U "$POSTGRES_USER" "$POSTGRES_DB" < backup.sql
```

Back up Redis only if queued tasks must survive a full stack outage. PostgreSQL is the durable source
for users, hosts, deployments, jobs, job logs, monitoring config, and audit logs.

## Key rotation

1. Announce a maintenance window.
2. Stop `web` and `worker`.
3. Update `SGLANG_OPS_AUTH_SECRET_KEY` to a new high-entropy value.
4. Restart services.
5. Require users to authenticate again.

If an external credential vault is introduced, rotate vault encryption keys according to the vault's
re-encryption workflow and verify the API still returns only redacted references.
