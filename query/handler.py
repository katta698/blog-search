import json
import os
import re
import math
from datetime import datetime
import boto3


INDEX_BUCKET = os.environ["INDEX_BUCKET"]
FEEDBACK_TABLE = os.environ.get("FEEDBACK_TABLE", "")
REGION = os.environ["AWS_REGION_NAME"]
INDEX_KEY = "index/chunks.json"
SUMMARY_KEY = "index/summaries.json"
TOP_K = 4
CACHE_PATH = "/tmp/chunks.json"

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
dynamodb = boto3.client("dynamodb", region_name=REGION)

_index_cache = None     # warm Lambda reuse
_summary_cache = None   # warm Lambda reuse


def load_index():
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    obj = s3.get_object(Bucket=INDEX_BUCKET, Key=INDEX_KEY)
    _index_cache = json.loads(obj["Body"].read())
    return _index_cache


def load_summaries():
    global _summary_cache
    if _summary_cache is not None:
        return _summary_cache
    try:
        obj = s3.get_object(Bucket=INDEX_BUCKET, Key=SUMMARY_KEY)
        _summary_cache = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        _summary_cache = {}
    return _summary_cache


def embed(text):
    resp = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text}),
    )
    return json.loads(resp["body"].read())["embedding"]


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    denom = mag_a * mag_b
    return dot / denom if denom else 0.0


def search(question_vec, chunks, top_k=TOP_K):
    scored = [
        (cosine_similarity(question_vec, c["embedding"]), c)
        for c in chunks
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def latest_post_chunks(chunks):
    # Used only for explicit date-based fallback (posts with post_date set).
    # Recency questions are now handled by the synthetic metadata doc in the index,
    # which semantic search finds naturally for any phrasing.
    dated = [c for c in chunks if c.get("post_date")]
    if not dated:
        return None
    latest_url = max(dated, key=lambda c: datetime.fromisoformat(c["post_date"]))["post_url"]
    return [c for c in chunks if c["post_url"] == latest_url]


# A query like "Day 15" or "Week 6" carries almost no semantic content for
# an embedding model to latch onto — cosine similarity is built for "what's
# this about", not exact literal lookups like a post's own day/week number.
# Even with the title indexed in every chunk (see indexer/handler.py), the
# query's own embedding is too sparse to reliably win. Detect this pattern
# explicitly and match directly against post titles instead of relying on
# similarity score at all.
DAY_WEEK_PATTERN = re.compile(r"\b(day|week)\s*#?\s*(\d{1,3})\b", re.IGNORECASE)


def find_post_by_day_week(question, chunks):
    match = DAY_WEEK_PATTERN.search(question)
    if not match:
        return None
    kind, number = match.group(1), match.group(2)
    title_prefix = re.compile(rf"^{re.escape(kind)}\s*{re.escape(number)}\b", re.IGNORECASE)
    matched_url = None
    for c in chunks:
        if title_prefix.search(c.get("post_title", "")):
            matched_url = c["post_url"]
            break
    if not matched_url:
        return None
    return [c for c in chunks if c["post_url"] == matched_url]


def ask_claude(question, top_chunks):
    context = "\n\n---\n\n".join(
        f"[{c['post_title']}]\n{c['chunk_text']}" for c in top_chunks
    )
    prompt = (
        "You are a helpful assistant for Jayanth Katta's personal website. "
        "You have access to content from his blog posts and his resume. "
        "Answer the question in a friendly, conversational tone using only the content provided below. "
        "You may paraphrase, summarize, and connect ideas across posts — but do not introduce facts not present in the context. "
        "If the context does not contain enough information to answer, say so briefly and suggest what topics are covered. "
        "Keep answers concise (2-4 sentences) unless the question needs more detail.\n\n"
        f"Content:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
    resp = bedrock.invoke_model(
        modelId="amazon.nova-lite-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"max_new_tokens": 512, "temperature": 0},
        }),
    )
    body = json.loads(resp["body"].read())
    return body["output"]["message"]["content"][0]["text"].strip()


def lambda_handler(event, context):
    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")
    path = event.get("rawPath", "")
    if method == "GET" and path == "/summary":
        return handle_summary(event)
    if path == "/feedback":
        if method == "POST":
            return handle_feedback_vote(event)
        if method == "GET":
            return handle_feedback_counts(event)
    return handle_search(event)


def handle_summary(event):
    try:
        params = event.get("queryStringParameters") or {}
        slug = (params.get("slug") or "").strip()
        if not slug:
            return _resp(400, {"error": "slug is required"})
        summary = load_summaries().get(slug)
        if not summary:
            return _resp(404, {"error": "no summary for this post"})
        return _resp(200, {
            "overview": summary["overview"],
            "key_detail": summary["key_detail"],
            "takeaway": summary["takeaway"],
        })
    except Exception as e:
        print(f"Error (summary): {e}")
        return _resp(500, {"error": "Internal server error"})


def handle_search(event):
    try:
        body = json.loads(event.get("body") or "{}")
        question = (body.get("query") or body.get("question") or "").strip()
        if not question:
            return _resp(400, {"error": "question is required"})

        chunks = load_index()

        top_chunks = None
        if not top_chunks:
            top_chunks = find_post_by_day_week(question, chunks)

        if not top_chunks:
            q_vec = embed(question)
            top_chunks = search(q_vec, chunks)

        answer = ask_claude(question, top_chunks)

        # deduplicate sources
        seen = set()
        sources = []
        for c in top_chunks:
            if c["post_url"] not in seen:
                seen.add(c["post_url"])
                sources.append({"title": c["post_title"], "url": c["post_url"]})

        return _resp(200, {"answer": answer, "sources": sources})

    except Exception as e:
        print(f"Error: {e}")
        return _resp(500, {"error": "Internal server error"})


# ── Post feedback ─────────────────────────────────────────────
# Deliberately stores no free text and nothing identifying: a slug, a vote, an
# optional reason drawn from a fixed list, and a timestamp. That is not a
# privacy gesture for its own sake -- it means there is no moderation queue, no
# PII to handle, and nothing an abusive submitter can put on the page. Readers
# with something specific to say get a mailto link in the widget instead, which
# routes the detail to a human without routing it through this table.
VOTES = ("up", "down")
REASONS = ("too-shallow", "too-long", "not-what-i-expected", "something-is-wrong")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")


def handle_feedback_vote(event):
    try:
        if not FEEDBACK_TABLE:
            return _resp(503, {"error": "feedback is not configured"})
        body = json.loads(event.get("body") or "{}")
        slug = (body.get("slug") or "").strip()
        vote = (body.get("vote") or "").strip()
        reason = (body.get("reason") or "").strip()

        # Validate against fixed sets rather than sanitising. Anything that is
        # not one of the known values is a bug or a probe; either way it should
        # not reach the table.
        if not SLUG_RE.match(slug):
            return _resp(400, {"error": "invalid slug"})
        if vote not in VOTES:
            return _resp(400, {"error": "vote must be up or down"})
        if reason and reason not in REASONS:
            return _resp(400, {"error": "unknown reason"})

        item = {
            "slug": {"S": slug},
            # Random suffix so two votes in the same millisecond cannot
            # overwrite each other -- the sort key is the only thing keeping
            # them apart.
            "voted_at": {"S": datetime.utcnow().isoformat(timespec="milliseconds")
                              + "Z#" + os.urandom(4).hex()},
            "vote": {"S": vote},
        }
        if reason:
            item["reason"] = {"S": reason}

        dynamodb.put_item(TableName=FEEDBACK_TABLE, Item=item)
        return _resp(200, {"ok": True})
    except Exception as e:
        print(f"Error (feedback vote): {e}")
        return _resp(500, {"error": "Internal server error"})


def handle_feedback_counts(event):
    """Aggregate counts for one post. Returns totals only, never individual
    votes, so the endpoint cannot be used to reconstruct who said what when."""
    try:
        if not FEEDBACK_TABLE:
            return _resp(503, {"error": "feedback is not configured"})
        params = event.get("queryStringParameters") or {}
        slug = (params.get("slug") or "").strip()
        if not SLUG_RE.match(slug):
            return _resp(400, {"error": "invalid slug"})

        counts = {"up": 0, "down": 0}
        reasons = {}
        kwargs = {
            "TableName": FEEDBACK_TABLE,
            "KeyConditionExpression": "slug = :s",
            "ExpressionAttributeValues": {":s": {"S": slug}},
            "ProjectionExpression": "vote, reason",
        }
        while True:
            page = dynamodb.query(**kwargs)
            for it in page.get("Items", []):
                v = it.get("vote", {}).get("S")
                if v in counts:
                    counts[v] += 1
                r = it.get("reason", {}).get("S")
                if r:
                    reasons[r] = reasons.get(r, 0) + 1
            token = page.get("LastEvaluatedKey")
            if not token:
                break
            kwargs["ExclusiveStartKey"] = token

        return _resp(200, {"slug": slug, "up": counts["up"],
                           "down": counts["down"], "reasons": reasons})
    except Exception as e:
        print(f"Error (feedback counts): {e}")
        return _resp(500, {"error": "Internal server error"})


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
