#!/usr/bin/env python3
"""
Weekly roundup of public-institution job openings (EU, UN, OECD, OSCE, NATO,
IMO, Council of Europe, ...), filtered for:

  * Brussels and the surrounding commuting area (preferred), then
  * fully-remote / home-based roles, then
  * any duty station within `radius_km` of the origin (commute up to a few
    times a month).

The script is STATELESS: every run pulls everything posted in the last
`lookback_days` and emails the matches. Run it weekly (see
.github/workflows/weekly-roundup.yml). No database, no server, no admin
rights required.

Sources, in order of reliability: #
  1. ReliefWeb v2 JSON API   -> works out of the box (free, no key)
  2. RSS feeds               -> add the exact feed URLs in config.yaml
  3. (extension point)       -> HTML scrapers for portals with no feed

Delivery: SMTP (e.g. a Gmail app password). If no SMTP creds are present it
does a local dry run and writes roundup.html instead, so you can iterate.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import smtplib
import sys
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from math import asin, cos, radians, sin, sqrt

import feedparser
import requests
import yaml

UA = "brussels-io-roundup/1.0 (+https://github.com/cdtrich)"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    title: str
    org: str
    location: str
    url: str
    source: str
    posted: dt.date | None = None
    deadline: str | None = None
    desc: str = ""
    # filled in by classify()
    tier: str = ""
    score: int = 0
    distance_km: float | None = None
    remote: bool = False


# --------------------------------------------------------------------------- #
# Geo helpers
# --------------------------------------------------------------------------- #
def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def locate(text: str, cfg: dict) -> tuple[float | None, bool]:
    """
    Map a free-text location/title to (distance_km_from_origin, is_remote).
    distance is None when no known duty station is recognised in the text.
    """
    t = (text or "").lower()
    remote = any(term in t for term in cfg["remote_terms"])
    origin = (cfg["origin"]["lat"], cfg["origin"]["lon"])
    best: float | None = None
    for city, (lat, lon) in cfg["duty_stations"].items():
        if city.lower() in t:
            d = haversine(origin, (lat, lon))
            best = d if best is None else min(best, d)
    return best, remote


# --------------------------------------------------------------------------- #
# Classification / ranking
# --------------------------------------------------------------------------- #
def recent(job: Job, cfg: dict) -> bool:
    if job.posted is None:
        return True  # keep undated items rather than silently dropping them
    return (dt.date.today() - job.posted).days <= cfg["lookback_days"]


def classify(job: Job, cfg: dict) -> bool:
    """Return True if the job is a keeper; sets job.tier / .score / .distance_km."""
    blob = f"{job.title} {job.location} {job.desc}".lower()

    if any(k in blob for k in cfg["keywords_exclude"]):
        return False

    dist, remote = locate(blob, cfg)
    job.remote = remote
    job.distance_km = dist
    boost = sum(1 for k in cfg["keywords_boost"] if k in blob)

    if dist is not None and dist <= 30:
        job.tier, job.score = "Brussels", 100 + boost
    elif dist is not None and dist <= 120:
        job.tier, job.score = "Near Brussels (\u2264120 km)", 80 + boost
    elif remote:
        job.tier, job.score = "Fully remote / home-based", 70 + boost
    elif dist is not None and dist <= cfg["radius_km"]:
        job.tier = f"Within {cfg['radius_km']} km (occasional commute)"
        job.score = max(40, int(60 - dist / 20)) + boost
    else:
        return False  # unknown location and not remote -> drop
    return True


# --------------------------------------------------------------------------- #
# Source 1: ReliefWeb v2 JSON API  (free, no key; humanitarian + a lot of UN)
# --------------------------------------------------------------------------- #
def fetch_reliefweb(cfg: dict) -> list[Job]:
    rc = cfg.get("reliefweb", {})
    if not rc.get("enabled"):
        return []

    since = (dt.date.today() - dt.timedelta(days=cfg["lookback_days"])).isoformat()
    base = "https://api.reliefweb.int/v2/jobs"
    params = {"appname": rc.get("appname", "brussels-io-roundup")}
    fields = [
        "title", "url", "source.name", "country.name", "city.name",
        "date.created", "date.closing", "body", "career_categories.name",
    ]

    def query(conditions, text=None) -> list[dict]:
        payload = {
            "limit": 300,
            "sort": ["date.created:desc"],
            "fields": {"include": fields},
            "filter": {"operator": "AND", "conditions": conditions},
        }
        if text:
            payload["query"] = {"value": text, "fields": ["title", "body"]}
        r = requests.post(base, params=params, json=payload,
                          headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])

    rows: list[dict] = []
    # (a) anything posted recently in a country within the radius
    rows += query([
        {"field": "date.created", "value": {"from": since}},
        {"field": "country.name", "value": rc["countries"], "operator": "OR"},
    ])
    # (b) remote / home-based anywhere
    rows += query(
        [{"field": "date.created", "value": {"from": since}}],
        text="home-based OR remote OR telework OR teleworking",
    )
    return [_parse_rw(d) for d in rows]


def _parse_rw(d: dict) -> Job:
    f = d.get("fields", {})

    def first_name(key):
        v = f.get(key)
        return v[0]["name"] if isinstance(v, list) and v else ""

    created = (f.get("date", {}) or {}).get("created")
    posted = dt.date.fromisoformat(created[:10]) if created else None
    closing = (f.get("date", {}) or {}).get("closing")
    loc = ", ".join(x for x in (first_name("city"), first_name("country")) if x)

    return Job(
        title=f.get("title", "(no title)"),
        org=first_name("source"),
        location=loc or "Unspecified",
        url=f.get("url", ""),
        source="ReliefWeb",
        posted=posted,
        deadline=closing[:10] if closing else None,
        desc=(f.get("body") or "")[:2000],
    )


# --------------------------------------------------------------------------- #
# Source 2: RSS feeds (UNjobs, UNjoblist, UN Careers, UNDP, agency feeds, ...)
# --------------------------------------------------------------------------- #
def fetch_rss(feed: dict, cfg: dict) -> list[Job]:
    parsed = feedparser.parse(feed["url"], agent=UA)
    out: list[Job] = []
    for e in parsed.entries:
        posted = None
        if getattr(e, "published_parsed", None):
            posted = dt.date(*e.published_parsed[:3])
        out.append(Job(
            title=getattr(e, "title", "(no title)"),
            org=feed.get("org", ""),
            # feeds vary: location may live in the title or summary, which
            # classify() scans anyway. location_hint is a fallback.
            location=getattr(e, "location", "") or feed.get("location_hint", ""),
            url=getattr(e, "link", ""),
            source=feed["name"],
            posted=posted,
            desc=getattr(e, "summary", "")[:2000],
        ))
    return out


# --------------------------------------------------------------------------- #
# Optional: LLM relevance pass (off unless cfg.llm.enabled and key present)
# --------------------------------------------------------------------------- #
def llm_filter(jobs: list[Job], cfg: dict) -> list[Job]:
    if not jobs:
        return jobs
    profile = cfg["llm"]["profile"]
    listing = "\n".join(f"{i}. {j.title} \u2014 {j.org} \u2014 {j.location}"
                        for i, j in enumerate(jobs))
    prompt = (f"My profile: {profile}\n\nJob openings:\n{listing}\n\n"
              "Return ONLY a JSON array of the indices that are a plausible fit "
              "for my profile. No prose, no code fences.")
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        txt = "".join(b.get("text", "") for b in r.json()["content"])
        keep = set(json.loads(txt[txt.index("["): txt.rindex("]") + 1]))
        return [j for i, j in enumerate(jobs) if i in keep]
    except Exception as e:  # never let the optional layer break the run
        print("LLM filter skipped:", e, file=sys.stderr)
        return jobs


# --------------------------------------------------------------------------- #
# Dedup + email
# --------------------------------------------------------------------------- #
def dedup(jobs: list[Job]) -> list[Job]:
    seen, out = set(), []
    for j in jobs:
        key = j.url.split("?")[0].rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append(j)
    return out


def _tier_rank(t: str) -> int:
    order = {
        "Brussels": 0,
        "Near Brussels (\u2264120 km)": 1,
        "Fully remote / home-based": 2,
    }
    return order.get(t, 3)


def build_email(jobs: list[Job]) -> str:
    groups: dict[str, list[Job]] = {}
    for j in jobs:
        groups.setdefault(j.tier, []).append(j)

    parts = [
        f"<h2>Public-institution job roundup \u2014 {dt.date.today():%d %b %Y}</h2>",
        f"<p>{len(jobs)} matching openings this week.</p>",
    ]
    for tier in sorted(groups, key=_tier_rank):
        items = sorted(groups[tier], key=lambda x: -x.score)
        parts.append(f"<h3>{html.escape(tier)} ({len(items)})</h3><ul>")
        for j in items:
            meta = " \u00b7 ".join(filter(None, [
                html.escape(j.org),
                html.escape(j.location),
                f"posted {j.posted:%d %b}" if j.posted else "",
                f"deadline {j.deadline}" if j.deadline else "",
                (f"{int(j.distance_km)} km" if j.distance_km is not None
                 else ("remote" if j.remote else "")),
            ]))
            parts.append(
                f'<li><a href="{html.escape(j.url)}">{html.escape(j.title)}</a>'
                f'<br><small>{meta} \u00b7 {html.escape(j.source)}</small></li>'
            )
        parts.append("</ul>")
    return "\n".join(parts)


def send_email(html_body: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    to = os.environ["EMAIL_TO"]
    frm = os.environ.get("EMAIL_FROM", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"IO job roundup \u2014 {dt.date.today():%d %b %Y}"
    msg["From"] = frm
    msg["To"] = to
    msg.attach(MIMEText("This digest is HTML; view it in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.sendmail(frm, [t.strip() for t in to.split(",")], msg.as_string())


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main() -> None:
    with open("config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    jobs: list[Job] = []

    try:
        jobs += fetch_reliefweb(cfg)
    except Exception as e:
        print("ReliefWeb error:", e, file=sys.stderr)

    for feed in cfg.get("rss_feeds", []):
        if not feed.get("enabled", True):
            continue
        try:
            jobs += fetch_rss(feed, cfg)
        except Exception as e:
            print(f"RSS '{feed.get('name')}' error:", e, file=sys.stderr)

    jobs = [j for j in jobs if recent(j, cfg)]
    jobs = dedup(jobs)
    jobs = [j for j in jobs if classify(j, cfg)]

    if cfg.get("llm", {}).get("enabled") and os.environ.get("ANTHROPIC_API_KEY"):
        jobs = llm_filter(jobs, cfg)

    jobs.sort(key=lambda j: -j.score)
    print(f"{len(jobs)} matching openings")

    body = build_email(jobs) if jobs else "<p>No matching openings this week.</p>"

    if os.environ.get("SMTP_USER"):
        send_email(body)
        print("Email sent.")
    else:
        with open("roundup.html", "w", encoding="utf-8") as fh:
            fh.write(body)
        print("No SMTP creds set \u2014 wrote roundup.html for local preview.")


if __name__ == "__main__":
    main()
