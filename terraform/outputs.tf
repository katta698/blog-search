output "api_gateway_url" {
  description = "HTTP API endpoint URL — set this in widget/search.js"
  value       = "${aws_apigatewayv2_api.search.api_endpoint}/search"
}

output "index_bucket_name" {
  description = "S3 bucket holding the search index"
  value       = aws_s3_bucket.index.id
}

output "indexer_lambda_name" {
  description = "Invoke this manually to trigger a re-index"
  value       = aws_lambda_function.indexer.function_name
}

output "query_lambda_name" {
  description = "Lambda backing the search API"
  value       = aws_lambda_function.query.function_name
}
