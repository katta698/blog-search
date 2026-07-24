data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── Indexer role ────────────────────────────────────────────────────────────

resource "aws_iam_role" "indexer" {
  name               = "blog-search-indexer-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "indexer_basic_execution" {
  role       = aws_iam_role.indexer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "indexer_permissions" {
  statement {
    sid     = "S3WriteIndex"
    effect  = "Allow"
    actions = ["s3:PutObject", "s3:GetObject"]
    resources = [
      "${aws_s3_bucket.index.arn}/index/*"
    ]
  }

  statement {
    sid     = "BedrockEmbeddings"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
    ]
  }

  # Claude Haiku 4.5 (at-a-glance summary generation, added 2026-07-24) only
  # supports cross-region inference, not direct on-demand invoke by base
  # model ID — confirmed live (ValidationException without this). Needs BOTH
  # the inference-profile ARN (what the code actually calls) AND the
  # underlying per-region foundation-model ARNs it's allowed to route
  # through (verified via `aws bedrock get-inference-profile` — the
  # us.anthropic.* profile spans exactly these 3 regions).
  statement {
    sid     = "SummaryClaudeHaiku"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel", "bedrock:UseInferenceProfile"]
    resources = [
      "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
    ]
  }
}

resource "aws_iam_role_policy" "indexer_permissions" {
  name   = "blog-search-indexer-policy"
  role   = aws_iam_role.indexer.id
  policy = data.aws_iam_policy_document.indexer_permissions.json
}

# ── Query role ──────────────────────────────────────────────────────────────

resource "aws_iam_role" "query" {
  name               = "blog-search-query-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "query_basic_execution" {
  role       = aws_iam_role.query.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "query_permissions" {
  statement {
    sid     = "S3ReadIndex"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.index.arn}/index/*"
    ]
  }

  statement {
    sid     = "BedrockEmbeddingsAndClaude"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0",
      "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.nova-lite-v1:0"
    ]
  }
}

resource "aws_iam_role_policy" "query_permissions" {
  name   = "blog-search-query-policy"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query_permissions.json
}
