import json
import os
import re
import math
from datetime import datetime
import boto3


INDEX_BUCKET = os.environ["INDEX_BUCKET"]
FEEDBACK_TABLE = os.environ.get("FEEDBACK_TABLE", "")
FEEDBACK_TOPIC = os.environ.get("FEEDBACK_TOPIC", "")
REGION = os.environ["AWS_REGION_NAME"]
INDEX_KEY = "index/chunks.json"
SUMMARY_KEY = "index/summaries.json"
TOP_K = 4
CACHE_PATH = "/tmp/chunks.json"

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
dynamodb = boto3.client("dynamodb", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)

# Warm-container caches, keyed on the S3 object's ETag rather than held for the
# life of the container.
#
# Holding them unconditionally is what made "At a glance" appear broken on every
# newly published post. Traced on 2026-08-19: the indexer wrote a summary for
# gcp-architecture-project-lifecycle at 12:30:16, and the API returned 404 for it
# for the next twenty minutes because one container -- started 12:26:34, four
# minutes before that write -- served all 55 requests in the period from the copy
# it had read once at start-up. The post was correct, S3 was correct, and the API
# was confidently serving a file from before the post existed.
#
# It looked like a per-cloud problem because the Azure post published the same day
# made that container's snapshot by 33 seconds and the GCP one missed it by four
# minutes. Nothing about the two posts differed; whichever published either side
# of a container start-up won.
#
# A HEAD is a few milliseconds and no data transfer, against a GET of a ~100 KB
# index on every request. The ETag changes whenever the indexer rewrites the
# object, so a publish is visible on the next request rather than whenever AWS
# happens to recycle the container -- which on a quiet site is hours.
_index_cache = None
_index_etag = None
_summary_cache = None
_summary_etag = None


def _current_etag(key):
    """ETag of an index object, or None if it is missing or S3 is unreachable.

    Never raises: a HEAD failure must not take down search or the summary
    endpoint when a perfectly good cached copy is already in memory.
    """
    try:
        return s3.head_object(Bucket=INDEX_BUCKET, Key=key)["ETag"]
    except Exception:                                          # noqa: BLE001
        return None


def load_index():
    global _index_cache, _index_etag
    etag = _current_etag(INDEX_KEY)
    if _index_cache is not None and (etag is None or etag == _index_etag):
        return _index_cache
    obj = s3.get_object(Bucket=INDEX_BUCKET, Key=INDEX_KEY)
    _index_cache = json.loads(obj["Body"].read())
    _index_etag = obj.get("ETag", etag)
    return _index_cache


def load_summaries():
    global _summary_cache, _summary_etag
    etag = _current_etag(SUMMARY_KEY)
    if _summary_cache is not None and (etag is None or etag == _summary_etag):
        return _summary_cache
    try:
        obj = s3.get_object(Bucket=INDEX_BUCKET, Key=SUMMARY_KEY)
        _summary_cache = json.loads(obj["Body"].read())
        _summary_etag = obj.get("ETag", etag)
    except s3.exceptions.NoSuchKey:
        _summary_cache = {}
        _summary_etag = None
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
# The browser-generated voter id. Fixed-length hex, so a caller cannot use this
# field to write arbitrary keys or unbounded data into the table.
VOTER_RE = re.compile(r"^[0-9a-f]{16,32}$")


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

        voter = (body.get("voter") or "").strip()
        if not VOTER_RE.match(voter):
            return _resp(400, {"error": "invalid voter"})

        # One row per voter per post, keyed on (slug, voter), so this PutItem
        # overwrites rather than appends. That single fact covers three cases
        # that each used to create a spurious extra vote: changing your mind,
        # picking a reason chip a moment after the thumb, and clicking twice.
        item = {
            "slug": {"S": slug},
            "voter": {"S": voter},
            "vote": {"S": vote},
            # Kept as a plain attribute rather than part of the key. The key
            # answers "who", the attribute answers "when", and only the first
            # of those should decide whether a row is new.
            "voted_at": {"S": datetime.utcnow().isoformat(
                timespec="milliseconds") + "Z"},
        }
        if reason:
            item["reason"] = {"S": reason}

        dynamodb.put_item(TableName=FEEDBACK_TABLE, Item=item)
        # Always mail the vote itself, even though a reason may follow and mail
        # again. Most people never pick a reason, so suppressing this one to
        # avoid the occasional duplicate would silently drop the majority of
        # down-votes -- the opposite of the point.
        notify(slug, vote, reason)
        return _resp(200, {"ok": True})
    except Exception as e:
        print(f"Error (feedback vote): {e}")
        return _resp(500, {"error": "Internal server error"})


def notify(slug, vote, reason):
    """Mail the vote. Never let a failure here cost the vote itself.

    The put_item has already succeeded by the time this runs, so the record is
    safe; a notification that does not go out is an annoyance, while an
    exception raised here would turn a stored vote into a 500 and tell the
    reader their click failed when it did not.
    """
    if not FEEDBACK_TOPIC:
        return
    word = "👍 useful" if vote == "up" else "👎 not useful"
    lines = [
        "%s\n" % word,
        "Post:   %s" % slug,
        "Link:   https://jayanthkatta.com/blog/%s/" % slug,
    ]
    if reason:
        lines.append("Reason: %s" % reason.replace("-", " "))
    try:
        sns.publish(
            TopicArn=FEEDBACK_TOPIC,
            Subject=("Blog feedback: %s" % slug)[:100],
            Message="\n".join(lines),
        )
    except Exception as e:
        print(f"Error (feedback notify): {e}")


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
