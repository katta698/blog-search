variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}

variable "index_bucket_name" {
  description = "S3 bucket name for the search index"
  type        = string
  default     = "jay-blog-search-index-v2"
}

variable "blogger_feed_url" {
  description = "Blogger JSON feed URL"
  type        = string
  default     = "https://blog.jayanthkatta.com/feeds/posts/default?alt=json&max-results=500"
}

variable "allowed_origins" {
  description = "CORS allowed origins for the API"
  type        = list(string)
  default     = ["https://jayanthkatta.com", "https://blog.jayanthkatta.com"]
}

variable "indexer_schedule_enabled" {
  description = "Whether to enable the EventBridge schedule for auto re-indexing"
  type        = bool
  default     = true
}

variable "indexer_schedule_expression" {
  description = "EventBridge schedule expression for re-indexing"
  type        = string
  default     = "rate(1 day)"
}
