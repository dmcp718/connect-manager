# terraform

Terraform that provisions the AWS substrate for the `aws-kubernetes` branch: VPC, EKS Auto Mode, RDS Postgres, ElastiCache for Valkey, IAM (Pod Identity), ACM, ECR, Secrets Manager, and CloudWatch alarms.

Each concern is a separately-destroyable module under `modules/`. The root composition in `main.tf` wires modules together; remote state is configured in `backend.tf`.

See `SPEC.md` §4 for the module shape, inputs/outputs, and state layout.
