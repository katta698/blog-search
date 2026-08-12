resource "aws_apigatewayv2_api" "search" {
  name          = "blog-search-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "query" {
  api_id                 = aws_apigatewayv2_api.search.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "search" {
  api_id    = aws_apigatewayv2_api.search.id
  route_key = "POST /search"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

# At-a-glance summary widget (added 2026-07-24) — same query Lambda, which
# dispatches on method+path internally (see query/handler.py lambda_handler),
# so this reuses the existing integration rather than deploying a new function.
resource "aws_apigatewayv2_route" "summary" {
  api_id    = aws_apigatewayv2_api.search.id
  route_key = "GET /summary"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

# Post feedback (added 2026-08-12) — same query Lambda again, dispatched on
# method+path, same reasoning as /summary above: one more route beats one more
# cold-starting function for an endpoint that takes a handful of hits a day.
resource "aws_apigatewayv2_route" "feedback_vote" {
  api_id    = aws_apigatewayv2_api.search.id
  route_key = "POST /feedback"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_route" "feedback_counts" {
  api_id    = aws_apigatewayv2_api.search.id
  route_key = "GET /feedback"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.search.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format          = "$context.requestId $context.status $context.httpMethod $context.path"
  }
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/blog-search"
  retention_in_days = 7
}
