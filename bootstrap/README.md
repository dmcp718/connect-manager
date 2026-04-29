# connect-bootstrap

Textual TUI that walks an operator through deploying the CONNECT Manager `aws-fargate` stack from scratch.

## Run

`uv` resolves the venv + deps on first invocation; no separate `pip install` step:

```bash
cd bootstrap
uv run connect-bootstrap            # launches the wizard
```

Or from the repo root (no `cd`):

```bash
uv run --project bootstrap connect-bootstrap
```

For testing against ministack (no real AWS):

```bash
AWS_ENDPOINT_URL=http://localhost:4566 \
AWS_ACCESS_KEY_ID=test \
AWS_SECRET_ACCESS_KEY=test \
uv run --project bootstrap connect-bootstrap
```

If you don't have `uv` installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`. The
older `pip install -e .` path still works but uv is the supported flow.

## Wizard flow

| Step | Screen | Bead |
|---|---|---|
| 1 | Dependency check (aws, terraform, jq, gh) | awsk-8b6.1 |
| 2 | AWS authentication picker (sso login / role-assume / static creds) | awsk-8b6.2 |
| 3 | `terraform.tfvars` form | awsk-8b6.3 |
| 4 | `terraform plan` + confirm + `terraform apply` (live output) | awsk-8b6.4 |
| 5 | Secrets Manager seeder (jwt, admin, …) | awsk-8b6.5 |
| 6 | ECS cluster verification | awsk-8b6.6 |
| 7 | CONNECT install (ECS service deploy + ALB target health) | awsk-8b6.8 |
| 8 | Status dashboard | awsk-8b6.9 |

The wizard is forward-only by default (each step depends on the previous one's apply state), but operators can re-enter any step from the main menu after the initial run.

## Reuse of `app/services/aws.py`

`connect_bootstrap.aws_helper` adds `../app` to `sys.path` and imports `services.aws` for boto3 client construction. This means ministack-side credentials (`AWS_ENDPOINT_URL`, env-var creds) work the same way as in the web app.

## Architecture

```
bootstrap/
├── pyproject.toml
├── README.md
├── connect_bootstrap/
│   ├── __init__.py
│   ├── __main__.py          # entry point
│   ├── app.py               # main App + screen navigation
│   ├── aws_helper.py        # bridge to app/services/aws.py
│   ├── shell.py             # subprocess.Popen helpers + live-stream
│   ├── tfvars.py            # terraform.tfvars read/write
│   ├── screens/
│   │   ├── __init__.py
│   │   ├── deps.py
│   │   ├── auth.py
│   │   ├── tfvars_form.py
│   │   ├── apply.py
│   │   ├── secrets.py
│   │   ├── cluster.py
│   │   ├── deploy.py
│   │   └── status.py
│   └── widgets/
│       └── __init__.py
└── tests/
```

Each screen is a `textual.screen.Screen` subclass; `app.py` registers them and pushes/pops on Next/Back. State that needs to flow between screens (cluster name, region, etc.) lives on the `App.state` attribute as a plain dataclass.
