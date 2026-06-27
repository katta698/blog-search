import json
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
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
RSS_FEED_URL = os.environ.get("RSS_FEED_URL", "https://jayanthkatta.com/blog/rss.xml")
RESUME_URL = os.environ.get("RESUME_URL", "https://jayanthkatta.com/resume.html")
INDEX_BUCKET = os.environ["INDEX_BUCKET"]
REGION = os.environ["AWS_REGION_NAME"]
INDEX_KEY = "index/chunks.json"

CHUNK_SIZE = 400   # target words per chunk
CHUNK_OVERLAP = 50 # words of overlap between chunks

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def fetch_all_posts():
    resp = requests.get(RSS_FEED_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    posts = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        date_el = item.find("pubDate")
        title = title_el.text if title_el is not None and title_el.text else ""
        link = link_el.text if link_el is not None and link_el.text else None
        if not link:
            continue

        post_date = None
        if date_el is not None and date_el.text:
            try:
                post_date = parsedate_to_datetime(date_el.text).isoformat()
            except (TypeError, ValueError):
                post_date = None

        page = requests.get(link, timeout=30)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        content = soup.find(id="jk-post") or soup

        text = content.get_text(separator=" ")
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            # Prepend the title so every chunk of this post carries it, not
            # just metadata attached after the fact. Without this, searching
            # by the post's own title/number ("Day 15", "Week 6") fails —
            # those words never otherwise appear in the body text, so a
            # semantic match against the embedded chunks has nothing to
            # latch onto even though the post obviously exists.
            text = f"Title: {title}. {text}" if title else text
            posts.append({"title": title, "url": link, "text": text, "date": post_date})

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

    records = []
    for post in posts + resume_docs:
        for chunk in chunk_text(post["text"]):
            vector = embed(chunk)
            records.append(
                {
                    "post_title": post["title"],
                    "post_url": post["url"],
                    "post_date": post["date"],
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
    return {"statusCode": 200, "body": f"Indexed {len(records)} chunks from {len(posts)} posts"}
