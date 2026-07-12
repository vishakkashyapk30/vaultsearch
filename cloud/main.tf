# VaultSearch cloud-native architecture, provisioned locally on LocalStack.
#
# The same HCL applies unchanged to real AWS: remove the localstack endpoint
# block (or drop the AWS_ENDPOINT_URL override) and supply real credentials.
#
# Usage with LocalStack (community image, no auth token required for these
# services):
#
#   docker compose --profile cloud up -d localstack
#   cd cloud && tflocal init && tflocal apply        # or: terraform, see below
#
# Without tflocal, plain terraform works because the provider below points
# every service at http://localhost:4566.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "localstack_endpoint" {
  description = "LocalStack edge endpoint; set to null when deploying to real AWS."
  type        = string
  default     = "http://localhost:4566"
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3       = var.localstack_endpoint
    sqs      = var.localstack_endpoint
    dynamodb = var.localstack_endpoint
  }
}

# ---- Storage: raw connector documents land here (slack/, drive/, tickets/) --

resource "aws_s3_bucket" "sources" {
  bucket        = "vaultsearch-sources"
  force_destroy = true
}

# Built artifacts (bm25.pkl, vectors.faiss, chunks_meta.json) for distribution.
resource "aws_s3_bucket" "artifacts" {
  bucket        = "vaultsearch-artifacts"
  force_destroy = true
}

# ---- Eventing: new/updated source documents trigger re-ingestion ------------

resource "aws_sqs_queue" "ingest" {
  name                       = "vaultsearch-ingest"
  message_retention_seconds  = 86400
  visibility_timeout_seconds = 300
}

resource "aws_sqs_queue_policy" "allow_s3" {
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_s3_bucket.sources.arn }
      }
    }]
  })
}

resource "aws_s3_bucket_notification" "sources_to_sqs" {
  bucket = aws_s3_bucket.sources.id

  queue {
    queue_arn = aws_sqs_queue.ingest.arn
    events    = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_sqs_queue_policy.allow_s3]
}

# ---- Audit: immutable, queryable record of every /api/ask -------------------
#
# Partition key = user_id, sort key = timestamp#uuid, so "everything this
# user ever asked" is a single Query. This is the artifact a security review
# starts from.

resource "aws_dynamodb_table" "audit" {
  name         = "vaultsearch-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "event_key"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "event_key"
    type = "S"
  }
}

output "sources_bucket" {
  value = aws_s3_bucket.sources.bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "audit_table" {
  value = aws_dynamodb_table.audit.name
}
