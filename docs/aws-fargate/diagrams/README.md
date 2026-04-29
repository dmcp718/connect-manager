# aws-fargate diagrams

Three diagrams of the CONNECT Manager `aws-fargate` stack, generated with the
[diagrams](https://diagrams.mingrammer.com) Python library against the AWS
Architecture Icons set bundled with that package (matches the AWS-published
SVGs at d1.awsstatic.com).

| File | Scope |
|---|---|
| `01-infrastructure.png` | Static topology — VPC subnets, ALB, ECS Fargate, RDS, ElastiCache, ECR, Secrets Manager + KMS, IAM roles, CloudWatch, queue-metric Lambda |
| `02-request-flow.png` | Login + `Settings → Load filespaces` happy path: browser → Route53 → ALB → web → bcrypt against RDS, JWT signed with the secret in Secrets Manager, then the token persists Fernet-encrypted to `user_settings` (v0.1.5+) |
| `03-import-flow.png` | S3 → LucidLink job lifecycle: web enqueues into Valkey/ARQ, queue-metric Lambda emits depth → CloudWatch → ECS auto-scale, worker dequeues, decrypts datastore creds + LL token, calls customer S3 + LucidLink upstream, marks job complete |

## Regenerating

```bash
cd docs/aws-fargate/diagrams
uv venv .venv          # one-time
. .venv/bin/activate
uv pip install diagrams
python architecture.py
```

Requires graphviz on `$PATH` (`sudo apt install graphviz` on Ubuntu/Debian,
`brew install graphviz` on macOS). The `.venv` directory is gitignored.

## Editing

Edit `architecture.py` and re-run. Each top-level function (`infrastructure`,
`request_flow`, `import_flow`) produces one PNG. Layout direction (`LR`/`TB`)
and graph attrs are at the top of the file.

The diagrams use AWS-prefixed classes from `diagrams.aws.*` so the icons match
the AWS Architecture Icons reference set. Browse the catalogue at
<https://diagrams.mingrammer.com/docs/nodes/aws>.
