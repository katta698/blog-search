import hashlib
import json
import os
import re
from datetime import datetime, timezone
import boto3
import requests
from bs4 import BeautifulSoup

# Migrated off Blogger (2026-06-26): Blogger no longer receives new posts —
# katta698.github.io/posts/ is the only source of truth now (see
# project-blog-sync-arch memory). The old BLOGGER_FEED_URL fetch silently
# went stale the moment Blogger was retired, since it would just keep
# re-fetching the same frozen set of posts forever. RSS_FEED_URL gives the
# current list of posts; the indexer then fetches each post's actual page
# for full text, since the RSS <description> is only a short excerpt.
BLOG_INDEX_URL = os.environ.get("BLOG_INDEX_URL", "https://jayanthkatta.com/blog/")
RESUME_URL = os.environ.get("RESUME_URL", "https://jayanthkatta.com/resume.html")
INDEX_BUCKET = os.environ["INDEX_BUCKET"]
REGION = os.environ["AWS_REGION_NAME"]
INDEX_KEY = "index/chunks.json"
SUMMARY_KEY = "index/summaries.json"
SUMMARY_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # inference profile ID — Claude Haiku 4.5 requires cross-region inference, not direct on-demand invoke (verified live, 2026-07-24)
SUMMARY_MAX_CHARS = 6000  # enough context for a short post-level summary without unbounded token cost on long posts

CHUNK_SIZE = 400   # target words per chunk
CHUNK_OVERLAP = 50 # words of overlap between chunks

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def fetch_all_posts():
    # Read every post URL from blog/index.html post cards — this is the single
    # source of truth for all posts (Blogger-era and new direct-to-GitHub posts
    # alike). RSS is no longer used: Blogger was retired and rss.xml is frozen.
    resp = requests.get(BLOG_INDEX_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    entries = []
    for tag in soup.find_all("a", class_=lambda c: c and "post-card" in c):
        href = tag.get("href", "")
        if not href or not href.startswith("/blog/"):
            continue
        url = "https://jayanthkatta.com" + href
        title = tag.get("data-title", "") or tag.get_text(separator=" ", strip=True)[:80]
        entries.append({"url": url, "title": title})

    print(f"blog/index.html: found {len(entries)} post cards")

    posts = []
    for rank, entry in enumerate(entries):
        url, title = entry["url"], entry["title"]
        try:
            page = requests.get(url, timeout=30)
            page.raise_for_status()
        except Exception as e:
            print(f"  skipping {url}: {e}")
            continue
        psoup = BeautifulSoup(page.text, "html.parser")
        content = psoup.find(id="jk-post") or psoup
        text = content.get_text(separator=" ")
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            text = f"Title: {title}. {text}" if title else text
            posts.append({"title": title, "url": url, "text": text, "date": None, "rank": rank})

    return posts


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += size - overlap
    return chunks


def embed(text):
    body = json.dumps({"inputText": text})
    resp = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    return json.loads(resp["body"].read())["embedding"]


def slug_from_url(url):
    # Post URLs are always https://jayanthkatta.com/blog/<slug>/ — matches
    # the slug sync_blog.py bakes into each page's summary-widget mount
    # point, so the two sides key on the same string with no translation.
    return url.rstrip("/").rsplit("/", 1)[-1]


def load_existing_summaries():
    try:
        obj = s3.get_object(Bucket=INDEX_BUCKET, Key=SUMMARY_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {}
    except Exception as e:
        print(f"Could not load existing summaries (starting fresh): {e}")
        return {}


# "At a glance" widget, replacing the old build-time keyword-extraction
# version (removed 2026-07-23 — it force-labeled sentences as Problem/Build/
# Catch regardless of fit, which broke on the 53-of-65 posts that aren't
# shaped like a weekly build post: troubleshooting posts, a day-N tutorial
# series, career posts). Generic Overview/Key detail/Takeaway labels are
# deliberately universal so ONE prompt/shape fits every post type — no
# per-post-type branching needed, and the model (not a keyword-frequency
# heuristic) decides what's actually worth saying about THIS post.
SUMMARY_PROMPT = """Below is the full text of a blog post. Write a 3-line "at a glance" \
summary for a reader deciding whether to read the full post. Be specific — name the actual \
technology, error, or technique involved, not a generic category. Respond with ONLY a JSON \
object, no other text, in exactly this shape:
{{"overview": "<one sentence: what this post is about>", \
"key_detail": "<one sentence: the single most important specific mechanism, decision, or fact>", \
"takeaway": "<one sentence: the most useful lesson or result for the reader>"}}

Title: {title}

POST TEXT:
{text}
"""


def generate_summary(title, text):
    prompt = SUMMARY_PROMPT.format(title=title, text=text[:SUMMARY_MAX_CHARS])
    try:
        resp = bedrock.invoke_model(
            modelId=SUMMARY_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 300,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
        # Model sometimes wraps JSON in a ```json fence despite instructions.
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        data = json.loads(raw)
        for key in ("overview", "key_detail", "takeaway"):
            if not data.get(key):
                raise ValueError(f"missing '{key}' in model output")
        return {"overview": data["overview"], "key_detail": data["key_detail"], "takeaway": data["takeaway"]}
    except Exception as e:
        print(f"  summary generation failed: {e}")
        return None


def build_summaries(posts):
    """Generate (or reuse cached) at-a-glance summaries, one Bedrock call per
    NEW or CHANGED post only — a content-hash cache means re-running the
    indexer on unchanged posts costs nothing extra."""
    existing = load_existing_summaries()
    updated = dict(existing)
    generated, reused, failed = 0, 0, 0

    for post in posts:
        slug = slug_from_url(post["url"])
        content_hash = hashlib.sha256(post["text"].encode("utf-8")).hexdigest()[:16]
        cached = existing.get(slug)
        if cached and cached.get("content_hash") == content_hash:
            reused += 1
            continue

        result = generate_summary(post["title"], post["text"])
        if result is None:
            failed += 1
            continue
        updated[slug] = {
            **result,
            "content_hash": content_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": SUMMARY_MODEL,
        }
        generated += 1

    print(f"Summaries: {generated} generated, {reused} reused (unchanged), {failed} failed")
    return updated


def fetch_resume():
    resp = requests.get(RESUME_URL, timeout=30)
    resp.raise_for_status()
    text = BeautifulSoup(resp.text, "html.parser").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return [{"title": "Jayanth Katta — Resume", "url": "https://jayanthkatta.com/resume", "text": text, "date": None}]


def lambda_handler(event, context):
    print("Fetching posts from RSS feed...")
    posts = fetch_all_posts()
    print(f"Fetched {len(posts)} posts")

    print("Fetching resume...")
    resume_docs = fetch_resume()
    print("Resume fetched")

    print("Building at-a-glance summaries...")
    summaries = build_summaries(posts)  # resume has no post page to attach a summary widget to — posts only
    s3.put_object(
        Bucket=INDEX_BUCKET,
        Key=SUMMARY_KEY,
        Body=json.dumps(summaries),
        ContentType="application/json",
    )
    print(f"Wrote {len(summaries)} summaries to s3://{INDEX_BUCKET}/{SUMMARY_KEY}")

    # Synthetic metadata document describing post order — picked up by semantic
    # search for any recency phrasing ("today's post", "new post", "what did you
    # publish", etc.) without relying on a brittle keyword list in the query Lambda.
    top5 = posts[:5]
    recency_lines = "\n".join(
        f"{i+1}. {p['title']} — {p['url']}" for i, p in enumerate(top5)
    )
    recency_text = (
        f"Blog post publication order (most recent first). "
        f"The latest post, today's post, and the newest post is: {top5[0]['title']} at {top5[0]['url']}. "
        f"Recent posts in order:\n{recency_lines}"
    )
    recency_doc = {
        "title": "Blog post index — most recent first",
        "url": top5[0]["url"],
        "text": recency_text,
        "date": None,
        "rank": -1,  # sentinel: this is a metadata doc, not a real post
    }

    records = []
    for post in [recency_doc] + posts + resume_docs:
        for chunk in chunk_text(post["text"]):
            vector = embed(chunk)
            records.append(
                {
                    "post_title": post["title"],
                    "post_url": post["url"],
                    "post_date": post["date"],
                    "post_rank": post.get("rank"),
                    "chunk_text": chunk,
                    "embedding": vector,
                }
            )

    print(f"Writing {len(records)} chunks to s3://{INDEX_BUCKET}/{INDEX_KEY}")
    s3.put_object(
        Bucket=INDEX_BUCKET,
        Key=INDEX_KEY,
        Body=json.dumps(records),
        ContentType="application/json",
    )
    return {"statusCode": 200, "body": f"Indexed {len(records)} chunks from {len(posts)} posts, {len(summaries)} summaries cached"}
