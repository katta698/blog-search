locals {
  indexer_zip = "${path.module}/../dist/indexer.zip"
  query_zip   = "${path.module}/../dist/query.zip"
}

# ── Indexer Lambda ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "indexer" {
  function_name    = "blog-search-indexer"
  role             = aws_iam_role.indexer.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = local.indexer_zip
  source_code_hash = filebase64sha256(local.indexer_zip)
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      INDEX_BUCKET     = aws_s3_bucket.index.id
      BLOGGER_FEED_URL = var.blogger_feed_url
      AWS_REGION_NAME  = var.aws_region
    }
  }
}

resource "aws_cloudwatch_log_group" "indexer" {
  name              = "/aws/lambda/${aws_lambda_function.indexer.function_name}"
  retention_in_days = 7
}

# ── Query Lambda ────────────────────────────────────────────────────────────

resource "aws_lambda_function" "query" {
  function_name    = "blog-search-query"
  role             = aws_iam_role.query.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = local.query_zip
  source_code_hash = filebase64sha256(local.query_zip)
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      INDEX_BUCKET    = aws_s3_bucket.index.id
      AWS_REGION_NAME = var.aws_region
    }
  }
}

resource "aws_cloudwatch_log_group" "query" {
  name              = "/aws/lambda/${aws_lambda_function.query.function_name}"
  retention_in_days = 7
}

# ── API Gateway permission to invoke query Lambda ───────────────────────────

resource "aws_lambda_permission" "api_gateway_query" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.search.execution_arn}/*/*/search"
}
