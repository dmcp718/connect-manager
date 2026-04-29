"""Generate the aws-fargate architecture diagrams using AWS icons.

Run from this directory:

    . .venv/bin/activate     # or: uv run --project ..
    python architecture.py

Outputs three PNGs in this directory:

    01-infrastructure.png  — static AWS topology (VPC, ALB, ECS, RDS, etc.)
    02-request-flow.png    — login + load-filespaces happy path
    03-import-flow.png     — S3-to-LucidLink job lifecycle (web -> worker)

Requires graphviz on $PATH. Install with `sudo apt install graphviz`.
"""

from __future__ import annotations

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, ECS, ElasticContainerServiceContainer, Fargate
from diagrams.aws.compute import Lambda
from diagrams.aws.database import ElasticacheForRedis, RDS
from diagrams.aws.integration import SimpleNotificationServiceSns as SNS
from diagrams.aws.management import (
    Cloudwatch,
    CloudwatchAlarm,
    CloudwatchLogs,
)
from diagrams.aws.network import (
    ALB,
    InternetGateway,
    NATGateway,
    PrivateSubnet,
    PublicSubnet,
    Route53,
    VPC,
)
from diagrams.aws.security import (
    ACM,
    IAMRole,
    KMS,
    SecretsManager,
)
from diagrams.aws.storage import SimpleStorageServiceS3 as S3
from diagrams.onprem.client import User, Users
from diagrams.onprem.network import Internet
from diagrams.programming.framework import Fastapi


GRAPH_ATTR = {
    "fontsize": "16",
    "splines": "spline",
    "pad": "0.5",
    "nodesep": "0.6",
    "ranksep": "1.0",
}


def infrastructure() -> None:
    with Diagram(
        "CONNECT Manager — aws-fargate Infrastructure",
        filename="01-infrastructure",
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
    ):
        operator = User("operator")
        browser = Users("end users")

        with Cluster("AWS account 534711626568 — us-west-2"):
            r53 = Route53("connect.solution-lab.click")
            cert = ACM("ACM cert\n(DNS-01)")

            with Cluster("VPC 10.20.0.0/16"):
                igw = InternetGateway("IGW")
                with Cluster("public subnets (3 AZ)"):
                    pub = [PublicSubnet(f"public-{i}") for i in range(1, 4)]
                    nat = NATGateway("NAT")
                    alb = ALB("connect-prod-alb\n:443 HTTPS")

                with Cluster("private subnets (3 AZ)"):
                    priv = [PrivateSubnet(f"private-{i}") for i in range(1, 4)]
                    with Cluster("ECS cluster connect-prod (Fargate)"):
                        web = ElasticContainerServiceContainer("web\n+ lucidlink-api sidecar")
                        worker = ElasticContainerServiceContainer("worker\n(scale-to-zero)")
                        migrate = ElasticContainerServiceContainer("migrate\n(one-shot)")
                    rds = RDS("Postgres 16\nconnect-prod")
                    valkey = ElasticacheForRedis("Valkey 8\nTLS+AUTH")

            with Cluster("Image registry"):
                ecr_web = ECR("ECR connect-web")
                ecr_worker = ECR("ECR connect-worker")
                ecr_ll = ECR("ECR lucidlink-api\n(upstream mirror)")

            with Cluster("Secrets + KMS"):
                kms = KMS("connect-secrets\nCMK")
                sm = SecretsManager(
                    "Secrets Manager\n/connect/prod/{jwt,admin,db,valkey}"
                )

            with Cluster("Identity"):
                exec_role = IAMRole("Task execution\nrole")
                web_role = IAMRole("web Task Role")
                worker_role = IAMRole("worker Task Role")

            with Cluster("Observability"):
                logs = CloudwatchLogs("Log groups\n/aws/ecs/connect-prod/*")
                metrics = Cloudwatch("Custom metrics\nqueue_depth")
                alarms = CloudwatchAlarm("Alarms")
                topic = SNS("connect-alerts")

            qmetric = Lambda("queue-metric\n(15 min cron)")

        ll_api = Internet("LucidLink API\n(upstream)")
        cust_s3 = S3("Customer S3\n(import source)")

        # Network path
        browser >> Edge(label="HTTPS") >> r53 >> Edge(label="ALIAS") >> alb
        alb >> Edge(label="TLS terminated\nfwd HTTP :8000") >> web
        cert - Edge(style="dashed", label="cert validation") - r53
        igw - pub
        pub - nat
        nat - priv
        web - priv
        worker - priv
        rds - priv
        valkey - priv

        # Operator
        operator >> Edge(label="terraform / deploy.sh", style="dashed") >> alb

        # ECS pulls
        ecr_web >> Edge(label="image pull", style="dashed") >> web
        ecr_worker >> Edge(label="image pull", style="dashed") >> worker
        ecr_ll >> Edge(label="sidecar image", style="dashed") >> web
        exec_role >> Edge(style="dotted", label="ECR + secret read") >> [web, worker, migrate]

        # Data plane
        web >> Edge(label="async SQL") >> rds
        worker >> Edge(label="async SQL") >> rds
        migrate >> Edge(label="alembic upgrade") >> rds
        web >> Edge(label="ARQ + cache") >> valkey
        worker >> Edge(label="ARQ dequeue") >> valkey

        # Secrets
        sm >> Edge(label="ValueFrom\n(injected at task start)") >> [web, worker, migrate]
        kms - Edge(style="dashed") - sm

        # Task roles
        web_role >> Edge(style="dotted", label="boto3 chain") >> web
        worker_role >> Edge(style="dotted", label="boto3 chain") >> worker

        # Worker reaches LucidLink + customer S3
        worker >> Edge(label="HTTPS") >> ll_api
        worker >> Edge(label="GetObject") >> cust_s3
        web >> Edge(label="proxied via sidecar") >> ll_api

        # Observability
        web >> Edge(style="dashed") >> logs
        worker >> Edge(style="dashed") >> logs
        qmetric >> Edge(label="emit ARQ depth\nevery 15s") >> metrics
        valkey >> Edge(style="dashed") >> qmetric
        metrics >> alarms >> topic


def request_flow() -> None:
    with Diagram(
        "CONNECT Manager — Request Flow (login + Settings → Load filespaces)",
        filename="02-request-flow",
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
    ):
        user = User("user\n(browser)")
        r53 = Route53("connect.\nsolution-lab.click")
        alb = ALB("ALB :443")

        with Cluster("Fargate web task"):
            web = Fastapi("web container\n(uvicorn :8000)")
            ll_sidecar = ElasticContainerServiceContainer(
                "lucidlink-api\n:3003 (localhost only)"
            )

        sm = SecretsManager("/connect/prod/jwt\n/connect/prod/admin")
        rds = RDS("Postgres\nusers, user_settings,\ndatastore_credentials")
        valkey = ElasticacheForRedis("Valkey\n(JWT session\nrevocation list)")
        ll_upstream = Internet("LucidLink upstream\n(Solutions Eng)")

        # Login + first page hit
        user >> Edge(label="1. POST /api/auth/login") >> r53 >> alb >> web
        web >> Edge(label="2. validate password\n(bcrypt)", color="darkgreen") >> rds
        web >> Edge(label="3. issue JWT cookie\n(HS256, JWT_SECRET_KEY\nfrom Secrets Manager)", color="darkgreen") >> user
        sm >> Edge(style="dotted", label="loaded at task start") >> web

        # Load filespaces
        user >> Edge(label="4. POST /api/load-filespaces\n(token in form, JWT cookie)") >> alb
        web >> Edge(label="5. proxy on localhost:3003") >> ll_sidecar
        ll_sidecar >> Edge(label="6. GET /filespaces") >> ll_upstream

        # Persist token
        web >> Edge(
            label="7. Fernet-encrypt token\nstore in user_settings",
            color="firebrick",
        ) >> rds
        web >> Edge(label="8. read api_host", style="dashed") >> rds


def import_flow() -> None:
    with Diagram(
        "CONNECT Manager — Import Job Flow (S3 → LucidLink)",
        filename="03-import-flow",
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
    ):
        user = User("user")
        alb = ALB("ALB")

        with Cluster("Fargate web task"):
            web = Fastapi("web\n(/api/import/folder)")

        valkey = ElasticacheForRedis("Valkey\n(ARQ queue)")

        with Cluster("Fargate worker task\n(autoscaled by queue depth)"):
            worker = Fastapi("worker\n(arq worker process)")

        rds = RDS("Postgres\njobs, processed_jobs,\ndatastore_credentials")
        cust_s3 = S3("Customer S3 bucket")
        ll_upstream = Internet("LucidLink External\nData Store API")
        qmetric = Lambda("queue-metric\nLambda")
        cw = Cloudwatch("CloudWatch\ncustom metric")

        # Enqueue
        user >> Edge(label="1. POST /api/import/folder") >> alb >> web
        web >> Edge(label="2. INSERT jobs row") >> rds
        web >> Edge(label="3. enqueue ARQ job") >> valkey

        # Autoscaling
        valkey >> Edge(label="4. SCAN queue depth", style="dashed") >> qmetric
        qmetric >> Edge(label="5. PutMetricData") >> cw
        cw >> Edge(
            label="6. ECS service auto-scale\n(target tracking)",
            style="dashed",
            color="darkorange",
        ) >> worker

        # Worker dequeues + processes
        valkey >> Edge(label="7. dequeue") >> worker
        worker >> Edge(
            label="8. load datastore creds\n(Fernet decrypt) +\nLL token (Fernet decrypt)",
            color="firebrick",
        ) >> rds
        worker >> Edge(label="9. ListObjectsV2 / GetObject") >> cust_s3
        worker >> Edge(label="10. POST imports\n(direct to upstream)") >> ll_upstream
        worker >> Edge(label="11. UPDATE jobs.status\n+ processed_jobs dedup") >> rds


if __name__ == "__main__":
    infrastructure()
    request_flow()
    import_flow()
    print("generated 01-infrastructure.png, 02-request-flow.png, 03-import-flow.png")
