import json
import os
import re
import math
from datetime import datetime
import boto3

INDEX_BUCKET = os.environ["INDEX_BUCKET"]
REGION = os.environ["AWS_REGION_NAME"]
INDEX_KEY = "index/chunks.json"
TOP_K = 4
CACHE_PATH = "/tmp/chunks.json"

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

_index_cache = None  # warm Lambda reuse


def load_index():
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    obj = s3.get_object(Bucket=INDEX_BUCKET, Key=INDEX_KEY)
    _index_cache = json.loads(obj["Body"].read())
    return _index_cache


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


# Pure semantic search has no concept of "recency" — a question like
# "what's your latest post" gets matched by *meaning*, not by date, so it
# can easily surface an older post that happens to talk about being new or
# recent. Detect that question intent explicitly (a recency word AND a
# post-word present anywhere, regardless of order) and answer it from the
# indexed post_date field instead of similarity score.
RECENCY_WORDS = re.compile(r"\b(latest|newest|most recent|recently|recent|last)\b", re.IGNORECASE)
POST_WORDS = re.compile(r"\b(post|blog|article|wrote|written|publish|published|writing)\b", re.IGNORECASE)


def is_recency_question(question):
    return bool(RECENCY_WORDS.search(question) and POST_WORDS.search(question))


def latest_post_chunks(chunks):
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
    try:
        body = json.loads(event.get("body") or "{}")
        question = (body.get("query") or body.get("question") or "").strip()
        if not question:
            return _resp(400, {"error": "question is required"})

        chunks = load_index()

        top_chunks = None
        if is_recency_question(question):
            top_chunks = latest_post_chunks(chunks)
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


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
