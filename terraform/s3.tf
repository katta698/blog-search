resource "aws_s3_bucket" "index" {
  bucket = var.index_bucket_name
}

resource "aws_s3_bucket_public_access_block" "index" {
  bucket                  = aws_s3_bucket.index.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "index" {
  bucket = aws_s3_bucket.index.id
  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "index" {
  bucket = aws_s3_bucket.index.id

  rule {
    id     = "expire-old-index"
    status = "Enabled"

    filter {
      prefix = "index/"
    }

    # Keep only the last 30 days of index files
    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "index" {
  bucket = aws_s3_bucket.index.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
