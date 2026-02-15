# EFS - Persistent storage for SQLite DB + encrypted secrets
resource "aws_efs_file_system" "data" {
  encrypted = true

  lifecycle_policy {
    transition_to_ia = "AFTER_14_DAYS"
  }

  tags = { Name = "${var.project_name}-data" }
}

resource "aws_efs_mount_target" "data" {
  count = 2

  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# S3 - Deploy bucket for application artifacts
resource "aws_s3_bucket" "deploy" {
  bucket = "${var.project_name}-deploy-${data.aws_caller_identity.current.account_id}"

  tags = { Name = "${var.project_name}-deploy" }
}

resource "aws_s3_bucket_versioning" "deploy" {
  bucket = aws_s3_bucket.deploy.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "deploy" {
  bucket = aws_s3_bucket.deploy.id

  rule {
    id     = "cleanup-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "deploy" {
  bucket = aws_s3_bucket.deploy.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
