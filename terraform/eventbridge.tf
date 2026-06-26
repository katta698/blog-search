resource "aws_cloudwatch_event_rule" "reindex" {
  name                = "blog-search-reindex"
  description         = "Trigger the indexer Lambda on a schedule"
  schedule_expression = var.indexer_schedule_expression
  state               = var.indexer_schedule_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "reindex" {
  rule      = aws_cloudwatch_event_rule.reindex.name
  target_id = "IndexerLambda"
  arn       = aws_lambda_function.indexer.arn
}

resource "aws_lambda_permission" "eventbridge_indexer" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.indexer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reindex.arn
}
