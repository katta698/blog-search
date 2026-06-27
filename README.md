# blog-search

RAG-powered "Ask my blog" widget backend for jayanthkatta.com. Two Lambda
functions behind an API Gateway HTTP API, fronted by a small JS widget
embedded in the portfolio/blog pages (see `katta698.github.io`).

**Correction (2026-06-26, later the same day):** this repo was originally
created believing no source existed for this project anywhere — the Lambda
code was pulled fresh from AWS because of that assumption. That was wrong.
A source repo (`blog-infra`, formerly referred to locally as `Blogger`)
already had both `lambdas/{indexer,query}/handler.py` *and* the full
Terraform (`main.tf`, `iam.tf`, `lambda.tf`, `s3.tf`, `api_gateway.tf`,
`eventbridge.tf`, `outputs.tf`, `variables.tf`) — just an older, partially
uncommitted version that predated several fixes (including today's). To
stop this project being split across two repos with silently-diverging
copies, everything has now been consolidated here: the Terraform moved
into `terraform/`, and this folder's `indexer/`/`query/` handlers (already
the more current version, since they include today's fixes) are now the
single source of truth. The old `lambdas/` and `terraform/` folders were
deleted from `blog-infra` in the same session.

**This repo (not AWS, not `blog-infra`) is now the source of truth** for
both the Lambda code and the infrastructure. To deploy changes, build a zip
and either `terraform apply` (after building `dist/indexer.zip` and
`dist/query.zip` — see `terraform/lambda.tf` for the exact expected paths)
or `aws lambda update-function-code` directly (see Redeploy commands
below) for a quick code-only change without a full Terraform run.

## Other files moved here during consolidation

- `scripts/build_lambdas.sh` / `.ps1` — packages `indexer/` and `query/` into
  `dist/indexer.zip` / `dist/query.zip` for Terraform to deploy. Paths
  updated for this repo's flat layout (was `lambdas/$name`, now just
  `$name`, since there's no `lambdas/` parent folder here).
- `scripts/attach_permissions.ps1` — one-time IAM bootstrap for the
  `terraform-user` that applies this Terraform.
- `widget/search.css` / `search.js` — **not the live widget.** This is an
  earlier prototype that was never actually wired up (the real "Ask my
  blog" widget on jayanthkatta.com is embedded directly in
  `katta698.github.io/index.html` as an inline terminal-style overlay, not
  loaded from these files). Kept for reference only.

## Architecture

```
Visitor types a question in the widget
  → POST https://37arp5b92a.execute-api.us-east-1.amazonaws.com/search
  → API Gateway (HTTP API, id 37arp5b92a) → Lambda: blog-search-query
      1. Loads index/chunks.json from S3 (jay-blog-search-index-v2)
      2. Embeds the question (Bedrock amazon.titan-embed-text-v2:0)
      3. Cosine-similarity search, top 4 chunks
      4. Asks Bedrock amazon.nova-lite-v1:0 to answer using only those chunks
  ← {"answer": "...", "sources": [...]}

EventBridge rule "blog-search-reindex" (rate(1 day))
  → Lambda: blog-search-indexer
      1. Fetches https://jayanthkatta.com/blog/rss.xml for the post list
      2. Fetches each post's actual page, extracts #jk-post text
      3. Also fetches /resume.html
      4. Chunks every doc (400 words, 50-word overlap), embeds each chunk
      5. Writes the whole thing to s3://jay-blog-search-index-v2/index/chunks.json
```

## Key files

- `indexer/handler.py` — `blog-search-indexer` Lambda. Builds the index.
- `query/handler.py` — `blog-search-query` Lambda. Answers questions.

## "Day N" / "Week N" questions (e.g. "Day 15", "Week 7")

Fixed 2026-06-27. A query like "Day 15" carries almost no semantic content
for an embedding model to latch onto — cosine similarity is built for
"what's this about", not exact literal lookups by number. Two-part fix:
- `indexer/handler.py` prepends `"Title: {title}. "` to each post's text
  before chunking, so the title is part of every embedded chunk, not just
  attached as metadata afterward. This alone wasn't enough — the query's
  own embedding is too sparse to reliably win even when the title is
  indexed.
- `query/handler.py` now detects this pattern explicitly
  (`find_post_by_day_week()` — a `day|week` word followed by a number) and
  matches directly against `post_title` instead of relying on similarity
  score. Same general approach as the recency-detection fix below: when a
  query has a literal, checkable structure, don't make an embedding model
  guess at something exact-match search already does perfectly.
- Verified live: "Day 15" and "Week 7" both now correctly return their
  respective posts; unrelated semantic questions ("how does cosine
  similarity work") still work normally — no regression.

## Recency questions ("what's your latest post")

Fixed 2026-06-26. Pure semantic similarity search has no concept of dates —
it only matches by meaning, so "latest post" could return an older post that
happened to talk about being recent. Now:
- `indexer/handler.py` reads each post's `<pubDate>` from the RSS feed and
  stores it as `post_date` (ISO 8601) on every indexed chunk.
- `query/handler.py` detects recency intent (`is_recency_question()` — a
  recency word like "latest"/"newest"/"recent"/"last" AND a post-word like
  "post"/"blog"/"published", present anywhere in the question regardless of
  order) and, when detected, answers directly from the chronologically
  newest `post_date` instead of running semantic search.
- Verified live: "latest post", "most recent blog post" → correctly return
  Week 6. Non-recency questions ("how does cosine similarity work", "tell me
  about week 3") still go through normal semantic search, confirmed no
  false-positive triggering.

## History

- **2026-06-26**: Found `indexer/handler.py` was still fetching from
  `BLOGGER_FEED_URL` (Blogger's JSON Atom feed) — a feed that stopped
  receiving new posts the moment Blogger was retired from the publishing
  pipeline (see `project-blog-sync-arch` memory in the `katta698.github.io`
  project). This meant the index could never include any post published
  after that point, including Week 6. Fixed by switching to
  `RSS_FEED_URL` (`https://jayanthkatta.com/blog/rss.xml`, generated by
  `katta698.github.io/scripts/sync_blog.py`'s `build_rss_feed()`), then
  fetching each post's actual page for full text (the RSS `<description>`
  is just a short excerpt, not enough for good embeddings). Verified Week 6
  is retrievable after the fix; manually re-invoked the indexer once to
  rebuild the index immediately rather than waiting for the next scheduled
  run. Removed the now-stale `BLOGGER_FEED_URL` env var, added `RSS_FEED_URL`
  explicitly. Same day, also fixed the "latest post" recency gap described
  above (post_date field + recency-intent detection).
- **2026-06-26 (later same day)**: `build_rss_feed()` in `sync_blog.py` was
  capping the feed at the 20 most recent posts (originally fine — that feed
  was only used for the profile README's "latest posts" list). Once the
  indexer started reading from this same feed, the cap became a real
  regression: the other 38 older posts silently became unsearchable, not
  just absent from a recency list. Removed the cap (now all posts), waited
  for the rss.xml redeploy, re-invoked the indexer, and verified an older
  post ("Day 5 - Terraform Variables") is retrievable again.
- Also validated the original "I Built a RAG-Powered Search..." blog post
  against reality and fixed several stale claims: it said "Blogger JSON
  feed" (now RSS), "Nova Micro" (the deployed model is actually Nova Lite —
  this was wrong even before today's changes), and "implemented with numpy"
  (the real code never used numpy). Post count stats updated 54 → 58. The
  post's Terraform claim was double-checked and is accurate — see the note
  above about where that source actually lives.

## Redeploy commands

Indexer (after editing `indexer/handler.py` locally):
```bash
cd indexer
# pull current deployed deps once if you don't have them locally:
#   aws lambda get-function --function-name blog-search-indexer --query Code.Location --output text
#   (download the zip from that URL, extract the bs4/requests/etc packages alongside handler.py)
python -c "
import zipfile, os
zf = zipfile.ZipFile('../indexer_deploy.zip', 'w', zipfile.ZIP_DEFLATED)
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if not d.endswith('.dist-info')]
    for f in files:
        full = os.path.join(root, f)
        zf.write(full, os.path.relpath(full, '.'))
zf.close()
"
cd ..
aws lambda update-function-code --function-name blog-search-indexer --zip-file fileb://indexer_deploy.zip
```

Manually trigger a reindex right now (don't wait for the daily schedule):
```bash
aws lambda invoke --function-name blog-search-indexer --cli-read-timeout 120 --payload "{}" /tmp/result.json
cat /tmp/result.json
```

Query Lambda redeploy follows the same pattern, targeting `blog-search-query`.

## Test the live API directly

```bash
curl -s -X POST "https://37arp5b92a.execute-api.us-east-1.amazonaws.com/search" \
  -H "Content-Type: application/json" \
  -d '{"question":"What did Week 6 build?"}'
```

## Infra reference

| Resource | Value |
|---|---|
| API Gateway (HTTP API) | `37arp5b92a` — `POST /search` |
| Indexer Lambda | `blog-search-indexer` (python3.12, 512MB, 300s timeout) |
| Query Lambda | `blog-search-query` (python3.12, 256MB, 30s timeout) |
| Index bucket | `s3://jay-blog-search-index-v2/index/chunks.json` |
| Reindex schedule | EventBridge rule `blog-search-reindex`, `rate(1 day)` |
| Embedding model | `amazon.titan-embed-text-v2:0` (Bedrock) |
| Answer model | `amazon.nova-lite-v1:0` (Bedrock) |
| AWS account | 684346483786, us-east-1 |
