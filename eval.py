#!/usr/bin/env python3
"""
Current Affairs Eval — tests how well Claude answers questions about the last 7 days of news.

Pipeline: scrape RSS → generate questions → ask Claude → judge answers → report

Usage:
    python eval.py                        # Full pipeline with search (haiku)
    python eval.py --model sonnet         # Use a different model
    python eval.py --no-search            # Skip search tools
    python eval.py --compare              # Run with and without search side by side
    python eval.py --eval-only            # Skip scraping and generation
    python eval.py --scrape-only          # Just scrape articles
    python eval.py --generate-only        # Just generate questions
"""

import argparse, asyncio, hashlib, json, os, time, uuid
from collections import Counter, defaultdict
from datetime import date

import anthropic, feedparser
from ddgs import DDGS
from dotenv import load_dotenv
from newspaper import Article as NewsArticle, Config as NewsConfig

from config import (
    CATEGORY_KEYWORDS, CONTROVERSY_KEYWORDS, GENERATOR_MODEL, MODELS,
    REGION_KEYWORDS, RSS_FEEDS, SCORING_WEIGHTS,
)

DIR = os.path.dirname(__file__)
DATA = os.path.join(DIR, "data")
RESULTS = os.path.join(DIR, "results")

MAX_ENTRIES_TO_FETCH = 100
MAX_ARTICLES = 80
MIN_ARTICLE_LENGTH = 200
ARTICLE_TEXT_TRUNCATION = 3000
CLASSIFY_TEXT_TRUNCATION = 2000
GENERATION_BATCH_SIZE = 5
TITLE_SIMILARITY_THRESHOLD = 0.3
TITLE_STEM_LENGTH = 4
MAX_CONCURRENT_EVALS = 5
NEWS_REQUEST_TIMEOUT = 10
SCRAPE_DELAY_SECS = 0.5


# ── Helpers ──────────────────────────────────────────────────────────────

def save_json(path: str, items: list | dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(items, f, indent=2)


def load_json(path: str) -> list | dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Error: data file not found: {path}\nHint: run without --eval-only/--generate-only first to generate the required data.")


def classify(title: str, text: str, keywords_map: dict, default: str = "geopolitics") -> str:
    """Match text against keyword lists and return the best-matching key."""
    combined = (title + " " + text[:CLASSIFY_TEXT_TRUNCATION]).lower()
    best, best_count = default, 0
    for key, keywords in keywords_map.items():
        count = sum(1 for kw in keywords if kw in combined)
        if count > best_count:
            best, best_count = key, count
    return best


_STOP_WORDS = {
    "the", "a", "an", "in", "on", "to", "of", "for", "and", "is", "was",
    "are", "by", "at", "as", "it", "with", "from", "that", "this", "has",
    "have", "had", "its", "his", "her",
}


def titles_are_similar(title_a: str, title_b: str) -> bool:
    """Check if two article titles are about the same story using word stem overlap."""
    # Use first N chars of each word as a rough stem (catches Israel/Israeli, score/scores, etc.)
    stems_a = {w[:TITLE_STEM_LENGTH] for w in title_a.lower().split() if w not in _STOP_WORDS and len(w) > 2}
    stems_b = {w[:TITLE_STEM_LENGTH] for w in title_b.lower().split() if w not in _STOP_WORDS and len(w) > 2}
    if not stems_a or not stems_b:
        return False
    overlap = len(stems_a & stems_b) / min(len(stems_a), len(stems_b))
    return overlap >= TITLE_SIMILARITY_THRESHOLD


# ── Stage 1: Scrape ─────────────────────────────────────────────────────

def scrape(target_date: str) -> list[dict]:
    """Download articles from RSS feeds and save them."""
    print(f"Scraping articles for {target_date}...", flush=True)

    np_config = NewsConfig()
    np_config.request_timeout = NEWS_REQUEST_TIMEOUT
    np_config.browser_user_agent = "CurrentAffairsEval/0.1"

    # Collect RSS entries
    entries = []
    seen_urls = set()
    for feed in RSS_FEEDS:
        print(f"  Fetching {feed['name']}...", flush=True)
        try:
            parsed = feedparser.parse(feed["url"])
            for e in parsed.entries:
                url = getattr(e, "link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    entries.append({
                        "title": getattr(e, "title", ""),
                        "url": url,
                        "published": getattr(e, "published", getattr(e, "updated", "")),
                        "source": feed["name"],
                        "region": feed["region"],
                    })
        except Exception as ex:
            print(f"    Warning: {ex}", flush=True)
    print(f"  {len(entries)} unique entries", flush=True)

    # Download full article text
    articles = []
    for entry in entries[:MAX_ENTRIES_TO_FETCH]:
        try:
            a = NewsArticle(entry["url"], config=np_config)
            a.download()
            a.parse()
            if not a.text or len(a.text) < MIN_ARTICLE_LENGTH:
                time.sleep(SCRAPE_DELAY_SECS)
                continue
        except Exception:
            time.sleep(SCRAPE_DELAY_SECS)
            continue

        text_lower = a.text.lower()
        controversy = ("high" if any(kw in text_lower for kw in CONTROVERSY_KEYWORDS["high"])
                        else "medium" if any(kw in text_lower for kw in CONTROVERSY_KEYWORDS["medium"])
                        else "low")

        # Skip articles with titles too similar to ones we already have
        if any(titles_are_similar(entry["title"], existing["title"]) for existing in articles):
            time.sleep(SCRAPE_DELAY_SECS)
            continue

        articles.append({
            "id": hashlib.sha256(entry["url"].encode()).hexdigest()[:16],
            "title": entry["title"],
            "source": entry["source"],
            "url": entry["url"],
            "published": entry["published"] or target_date,
            "text": a.text,
            "category": classify(entry["title"], a.text, CATEGORY_KEYWORDS),
            "region": classify(entry["title"], a.text, REGION_KEYWORDS, entry["region"]),
            "controversy_level": controversy,
        })
        if len(articles) >= MAX_ARTICLES:
            break
        time.sleep(SCRAPE_DELAY_SECS)

    print(f"  Scraped {len(articles)} articles", flush=True)
    save_json(os.path.join(DATA, "articles", f"{target_date}.json"), articles)
    return articles


# ── Stage 2: Generate questions ──────────────────────────────────────────

GENERATION_PROMPT = """You are generating eval questions to test an AI's ability to research CURRENT events using search tools.

CRITICAL RULES:
1. Every question MUST be about something that happened in the last 7 days.
2. It must be IMPOSSIBLE to answer correctly from general knowledge alone.
3. Generate exactly ONE question per article.
4. Questions should be HARD and require multiple searches to piece together a full answer.
5. The information MUST exist on the public web (reported by multiple news outlets).

IMPORTANT — SEARCHABILITY:
The AI being tested will use DuckDuckGo to research its answer. The question MUST
contain enough proper nouns (people, places, organizations) that a keyword search
can find relevant articles. Do NOT write questions that can only be answered by
reading one specific article — the answer should be findable across multiple sources.

PREFER CONTESTED AND DEVELOPING SITUATIONS:
Prioritize questions where:
- Multiple stakeholders have publicly conflicting accounts (e.g. government vs. opposition, two states disputing a claim)
- The situation is evolving and key facts are still disputed or emerging
- There are meaningful open questions about causes, responsibility, or outcomes
- Different credible sources report meaningfully different versions of events

These questions are more valuable than questions with a single clean verifiable answer,
because they test whether the AI can navigate conflicting information and calibrate its uncertainty.

GOOD questions (contain searchable proper nouns, require piecing together multiple facts):
- "What record did Bam Adebayo break this week against the Washington Wizards, whose record did he surpass, and what was the final score?" (proper nouns: Adebayo, Wizards, NBA)
- "What reasons were given for resuming China-North Korea train services in March 2026, and how long had they been suspended?" (proper nouns: China, North Korea, dates)
- "What competing accounts have emerged from Israel and Hamas about the terms of the latest ceasefire proposal, and what specific points remain disputed?" (tests multi-source navigation)
- "What happened in the NSW foster children case involving convicted killer Regina Arthurell this week?" (proper nouns: NSW, Arthurell)

BAD questions (lack proper nouns, too vague to search for):
- "What landmark court ruling was handed down this week?" (no names, no country — unsearchable)
- "What strategy did the Democratic candidate use?" (no name, no state, no race)
- "What diplomatic position was communicated this week?" (no names, no specifics)

For each question provide:
- article_index (int): which article (0-indexed)
- question (str): must include proper nouns and dates to make it searchable
- ground_truth (str): factual answer with key details; for contested topics note what is disputed and what different sources say
- source_excerpt (str): key excerpt from the article containing the answer
- is_contested (bool): true if the question involves genuinely disputed facts or multiple stakeholder perspectives with conflicting accounts

Return ONLY a JSON array of objects.

ARTICLES:
{articles_text}"""


def generate(target_date: str, max_questions: int = 30) -> list[dict]:
    """Use Claude to create questions from the scraped articles."""
    articles = load_json(os.path.join(DATA, "articles", f"{target_date}.json"))
    client = anthropic.Anthropic()

    # Select up to max_questions diverse articles — one question each
    # Spread across regions and categories, prioritize controversial topics
    regions_seen, cats_seen = set(), set()
    selected = []
    # First pass: pick articles that add at least one unseen region or category
    for a in sorted(articles, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["controversy_level"], 2)):
        if a["region"] not in regions_seen or a["category"] not in cats_seen:
            selected.append(a)
            regions_seen.add(a["region"])
            cats_seen.add(a["category"])
        if len(selected) >= max_questions:
            break
    # Second pass: fill remaining slots with unused articles
    selected_ids = {a["id"] for a in selected}
    for a in articles:
        if a["id"] not in selected_ids:
            selected.append(a)
            selected_ids.add(a["id"])
        if len(selected) >= max_questions:
            break

    print(f"Generating questions from {len(selected)} articles (1 question each)...", flush=True)

    questions = []
    # Process in batches
    for i in range(0, len(selected), GENERATION_BATCH_SIZE):
        batch = selected[i:i + GENERATION_BATCH_SIZE]
        articles_text = ""
        for j, a in enumerate(batch):
            articles_text += f"\n--- ARTICLE {j} ---\nTitle: {a['title']}\nSource: {a['source']}\nPublished: {a['published']}\nText: {a['text'][:ARTICLE_TEXT_TRUNCATION]}\n"

        try:
            resp = client.messages.create(
                model=GENERATOR_MODEL, max_tokens=4096,
                messages=[{"role": "user", "content": GENERATION_PROMPT.replace("{articles_text}", articles_text)}],
            )
            text = next((b.text for b in resp.content if b.type == "text"), "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end != -1:
                text = text[start:end + 1]
            raw = json.loads(text)
        except Exception as ex:
            batch_num = i // GENERATION_BATCH_SIZE + 1
            print(f"  Warning: batch {batch_num} failed: {ex}", flush=True)
            continue

        # Take only 1 question per article
        seen_articles = set()
        for q in raw:
            if "question" not in q or "ground_truth" not in q:
                continue
            idx = max(0, min(q.get("article_index", 0), len(batch) - 1))
            if idx in seen_articles:
                continue
            seen_articles.add(idx)
            a = batch[idx]
            questions.append({
                "id": uuid.uuid4().hex[:12],
                "article_id": a["id"],
                "question": q["question"],
                "ground_truth": q["ground_truth"],
                "category": a["category"],
                "region": a["region"],
                "controversy_level": a["controversy_level"],
                "source_excerpt": q.get("source_excerpt", ""),
                "is_contested": bool(q.get("is_contested", False)),
            })

    print(f"  Generated {len(questions)} questions", flush=True)
    save_json(os.path.join(DATA, "questions", f"{target_date}.json"), questions)
    return questions


# ── Stage 3: Evaluate (async) ────────────────────────────────────────────

MAX_SEARCH_CALLS = 5

SEARCH_TOOLS = [
    {"name": "search_news", "description": "Search recent news articles.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "search_web", "description": "Search the web for information.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]

SYSTEM_WITH_SEARCH = (
    "You are a helpful assistant with web and news search tools. "
    "You MUST search before answering — never rely on training data for current events.\n\n"
    "RULES:\n"
    f"- You have a STRICT LIMIT of {MAX_SEARCH_CALLS} tool calls. Plan carefully.\n"
    "- Start with search_news for recent events.\n"
    "- After getting good results, STOP searching and answer immediately.\n"
    "- Base your answer ONLY on search results. Do not guess.\n"
    "- If search doesn't find it, say so honestly.\n\n"
    "HANDLING UNCERTAINTY AND CONFLICTING SOURCES:\n"
    "- If different sources report different versions of events, say so explicitly: name the sources and what each claims.\n"
    "- If a fact is unverified or disputed, flag it as such ('Source A says X; Source B says Y; this remains contested').\n"
    "- If your searches found partial information, clearly distinguish what you confirmed from what you could not verify.\n"
    "- Expressing calibrated partial confidence is correct behavior — do not paper over gaps with confident-sounding prose.\n"
    "- A response that honestly says 'I found X but could not confirm Y' is better than one that presents everything with equal confidence.\n\n"
    "SEARCH QUERY TIPS (DuckDuckGo works best with keywords, not natural language):\n"
    "- USE: specific proper nouns, place names, year/month ('March 2026')\n"
    "- USE: key factual terms from the question (e.g. 'sentencing', 'phone call', 'election')\n"
    "- AVOID: filler words like 'this week', 'latest', 'recent', 'what happened'\n"
    "- AVOID: long natural language phrases — keep queries to 4-8 keywords\n"
    "- If first search returns nothing, REFORMULATE with different keywords, not the same words rearranged.\n"
    "- Example: instead of 'Kenya court ruling attack gay men this week'\n"
    "  try 'Kenya prison sentence gay men robbery 2026'"
)

SYSTEM_WITHOUT_SEARCH = (
    "You are a helpful assistant answering questions about very recent current events. "
    "If you do not have specific, concrete information about the event, "
    "say clearly that you don't have this information. Do NOT guess or provide "
    "generic analysis as a substitute for specific current facts.\n\n"
    "CALIBRATION: If you have partial information — for example you know background context "
    "but not the specific recent development — say so explicitly. Distinguish between "
    "what you know with confidence, what you're uncertain about, and what you simply "
    "don't have. A response that honestly partitions its own confidence is more useful "
    "than one that presents partial knowledge as complete knowledge."
)

JUDGE_PROMPT = """Evaluate this AI response to a current affairs question.

QUESTION: __QUESTION__
GROUND TRUTH: __GROUND_TRUTH__
SOURCE EXCERPT: __SOURCE_EXCERPT__
AI RESPONSE: __RESPONSE__

SCORING:
- Fabricated/wrong info → factual_accuracy=1, objectivity=1
- Honest "I don't know" → factual_accuracy=2, objectivity=3
- Both non-answers and wrong answers → recency=1, completeness=1, nuance=1

Score 1-5 on each:
1. factual_accuracy: are the specific claims (names, dates, figures, outcomes) correct against the ground truth?
   5=every verifiable claim matches the ground truth exactly
   4=all major claims correct; minor imprecision in a peripheral detail (e.g. slightly wrong date, paraphrased figure)
   3=core claim is correct but one secondary fact is wrong or unverifiable
   2=mixed — some correct facts alongside at least one materially wrong claim
   1=central claim is wrong, fabricated, or directly contradicts the ground truth

2. recency: does the answer describe the specific event in question, not an older related event?
   5=describes the specific event from the correct time window with accurate timing context
   4=identifies the right event but is vague on timing or misses a same-week development
   3=answer is about the right topic but conflates this event with an earlier related one
   2=draws primarily on background or historical context rather than the current event
   1=describes an outdated or entirely different event, or has no time-awareness at all

3. objectivity: does the answer report what happened without editorializing, and does it fairly represent all significant stakeholder perspectives?
   5=sticks to reported facts; disputed aspects are clearly flagged as disputed; where multiple parties have stated positions, each is accurately represented
   4=predominantly factual with one minor instance of unattributed framing or an omitted perspective that is not central
   3=mixes factual reporting with editorializing, OR represents one side's framing as fact while ignoring a significant opposing account
   2=answer is structured around a viewpoint; facts are selectively used to support it; a major stakeholder perspective is absent or caricatured
   1=editorial framing dominates; reported facts are secondary or absent; the answer effectively advocates a position

4. completeness: does the answer address every part of the question?
   5=all sub-questions answered with the key supporting facts included
   4=all sub-questions addressed but one supporting detail is thin or missing
   3=the main question is answered but at least one explicit sub-question is skipped
   2=only the most obvious part of the question is answered; rest is ignored or vague
   1=question is not meaningfully answered; response is off-topic or a refusal

5. nuance: does the answer reflect the actual complexity of the situation, including contested claims and the limits of what is knowable?
   5=captures relevant caveats, disputed aspects, conflicting evidence, and wider context that affect how the facts should be read; does not flatten genuine uncertainty into a clean narrative
   4=acknowledges complexity in most areas but flattens one aspect that matters, or presents one genuinely contested claim as settled
   3=notes that the situation is complex but does not explain how or why; treats some disputed facts as established
   2=presents a clean narrative that obscures meaningful uncertainty or disagreement between sources
   1=treats the situation as simpler than it is in a way that distorts understanding; presents contested claims as facts

Return ONLY JSON: {"factual_accuracy": N, "recency": N, "objectivity": N, "completeness": N, "nuance": N, "reasoning": "..."}"""


def do_search(query: str, news: bool = False) -> dict:
    """Run a DuckDuckGo search and return results."""
    try:
        results = DDGS().news(query, max_results=5) if news else DDGS().text(query, max_results=5)
        return {"results": [
            {"title": r.get("title", ""), "url": r.get("url", r.get("href", "")),
             "snippet": r.get("body", ""), "date": r.get("date", "")}
            for r in results
        ]}
    except Exception as e:
        return {"error": str(e), "results": []}


async def ask_with_search(client: anthropic.Anthropic, model: str, question_text: str) -> tuple[str, list]:
    """Ask Claude a question, letting it use search tools to find the answer."""
    messages = [{"role": "user", "content": question_text}]
    search_queries = []
    total_tool_calls = 0

    while total_tool_calls < MAX_SEARCH_CALLS:
        resp = await asyncio.to_thread(
            client.messages.create,
            model=model, max_tokens=1024, system=SYSTEM_WITH_SEARCH,
            tools=SEARCH_TOOLS, messages=messages,
        )
        if resp.stop_reason != "tool_use":
            answer = "\n".join(b.text for b in resp.content if b.type == "text")
            return answer, search_queries

        # Run the searches Claude requested and send results back
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                if total_tool_calls >= MAX_SEARCH_CALLS:
                    break
                query = block.input.get("query", "")
                search_type = "news" if block.name == "search_news" else "web"
                result = await asyncio.to_thread(
                    do_search, query, block.name == "search_news"
                )
                search_queries.append({"type": search_type, "query": query, "results": len(result.get("results", []))})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
                total_tool_calls += 1
        messages.append({"role": "user", "content": tool_results})

    return "(Max search calls reached)", search_queries


async def ask_without_search(client: anthropic.Anthropic, model: str, question_text: str) -> str:
    """Ask Claude a question directly, with no search tools."""
    resp = await asyncio.to_thread(
        client.messages.create,
        model=model, max_tokens=1024, system=SYSTEM_WITHOUT_SEARCH,
        messages=[{"role": "user", "content": question_text}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


async def judge(client: anthropic.Anthropic, judge_model: str, question: dict, response: str) -> dict:
    """Have a judge model score the response on each dimension in SCORING_WEIGHTS."""
    prompt = (JUDGE_PROMPT
              .replace("__QUESTION__", question["question"])
              .replace("__GROUND_TRUTH__", question["ground_truth"])
              .replace("__SOURCE_EXCERPT__", question.get("source_excerpt", ""))
              .replace("__RESPONSE__", response))
    resp = await asyncio.to_thread(
        client.messages.create,
        model=judge_model, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Extract JSON object if there's surrounding prose
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    try:
        scores = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  Warning: judge returned invalid JSON: {e}\n  Raw text: {text[:200]}", flush=True)
        return {k: 3 for k in SCORING_WEIGHTS} | {"composite": 3.0, "reasoning": "parse error"}
    for key in SCORING_WEIGHTS:
        scores[key] = max(1, min(5, round(float(scores[key]))))
    scores["composite"] = round(sum(scores[k] * SCORING_WEIGHTS[k] for k in SCORING_WEIGHTS), 2)
    scores["reasoning"] = scores.get("reasoning", "")
    return scores


async def eval_one(idx: int, total: int, question: dict, client: anthropic.Anthropic, model: str, judge_model: str, use_search: bool) -> dict | None:
    """Evaluate a single question: ask Claude, then judge the answer."""
    label = "search" if use_search else "no-search"
    try:
        if use_search:
            answer, search_queries = await ask_with_search(client, model, question["question"])
        else:
            answer = await ask_without_search(client, model, question["question"])
            search_queries = []

        scores = await judge(client, judge_model, question, answer)
        q_text = question['question']
        q_preview = q_text[:60] + ("..." if len(q_text) > 60 else "")
        print(f"  [{label}] [{idx+1}/{total}] {scores['composite']:.2f} — {q_preview}", flush=True)
        return {"question_id": question["id"], "response": answer, "search_queries": search_queries, **scores}
    except Exception as e:
        print(f"  [{label}] [{idx+1}/{total}] ERROR: {e}", flush=True)
        return None


async def evaluate(target_date: str, model_alias: str = "haiku", use_search: bool = True, max_questions: int = 30, semaphore: asyncio.Semaphore | None = None) -> tuple[str, float]:
    """Run the full evaluation: ask all questions in parallel, judge, save results."""
    model = MODELS[model_alias]
    # Always use opus as judge — it's the most capable and scores against a ground truth,
    # so self-evaluation bias is minimal even when evaluating opus itself.
    judge_model = MODELS["opus"]
    client = anthropic.Anthropic()

    questions = load_json(os.path.join(DATA, "questions", f"{target_date}.json"))[:max_questions]
    label = "with search" if use_search else "without search"
    print(f"Evaluating {len(questions)} questions {label} ({model_alias})...", flush=True)

    # Limit concurrency to avoid DuckDuckGo rate limits.
    # Caller may pass a shared semaphore (e.g. --compare runs two evaluations in parallel
    # and must share the limit to avoid doubling effective DDG concurrency).
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_EVALS)
    async def limited_eval(i, q):
        async with semaphore:
            return await eval_one(i, len(questions), q, client, model, judge_model, use_search)

    tasks = [limited_eval(i, q) for i, q in enumerate(questions)]
    raw_results = await asyncio.gather(*tasks)
    results = [r for r in raw_results if r is not None]

    # Save results and report
    suffix = f"{model_alias}_search" if use_search else model_alias
    save_json(os.path.join(RESULTS, f"{target_date}_{suffix}.json"), results)

    report = make_report(results, questions, suffix, target_date)
    with open(os.path.join(RESULTS, f"{target_date}_{suffix}_report.md"), "w") as f:
        f.write(report)

    avg = sum(r["composite"] for r in results) / len(results) if results else 0
    print(f"  Score: {avg:.2f}/5.00 ({len(results)} questions)", flush=True)
    return suffix, avg


# ── Report ───────────────────────────────────────────────────────────────

def make_report(results: list[dict], questions: list[dict], label: str, target_date: str) -> str:
    """Generate a markdown report summarizing the eval results."""
    if not results:
        return "# No results\n"

    q_map = {q["id"]: q for q in questions}
    n = len(results)
    avg = sum(r["composite"] for r in results) / n

    lines = [
        f"# Current Affairs Eval — {target_date}",
        f"**Model**: {label} | **Questions**: {n} | **Composite**: {avg:.2f}/5.00", "",
        "| Dimension | Avg | Min | Max |", "|---|---|---|---|",
    ]
    for dim in SCORING_WEIGHTS:
        vals = [r[dim] for r in results]
        lines.append(f"| {dim} | {sum(vals)/len(vals):.2f} | {min(vals)} | {max(vals)} |")

    # Scores by category
    by_cat = defaultdict(list)
    for r in results:
        q = q_map.get(r["question_id"])
        if q:
            by_cat[q.get("category", "other")].append(r["composite"])
    if by_cat:
        lines += ["", "| Category | Avg | Count |", "|---|---|---|"]
        for cat in sorted(by_cat):
            s = by_cat[cat]
            lines.append(f"| {cat} | {sum(s)/len(s):.2f} | {len(s)} |")

    # Scores by region
    by_region = defaultdict(list)
    for r in results:
        q = q_map.get(r["question_id"])
        if q:
            by_region[q["region"]].append(r["composite"])
    if by_region:
        lines += ["", "| Region | Avg | Count |", "|---|---|---|"]
        for region in sorted(by_region):
            s = by_region[region]
            lines.append(f"| {region} | {sum(s)/len(s):.2f} | {len(s)} |")

    # Scores by contested status
    by_contested = defaultdict(list)
    for r in results:
        q = q_map.get(r["question_id"])
        if q:
            label_c = "contested" if q.get("is_contested") else "non-contested"
            by_contested[label_c].append(r["composite"])
    if len(by_contested) > 1:
        lines += ["", "| Question Type | Avg | Count |", "|---|---|---|"]
        for ctype in sorted(by_contested):
            s = by_contested[ctype]
            lines.append(f"| {ctype} | {sum(s)/len(s):.2f} | {len(s)} |")

    # Bottom 5
    sorted_results = sorted(results, key=lambda r: r["composite"])
    lines += ["", "**Bottom 5:**", ""]
    for r in sorted_results[:5]:
        q = q_map.get(r["question_id"])
        lines.append(f"- **{r['composite']:.2f}** — {q['question'][:100] if q else '?'}")
        lines.append(f"  - Scores: FA={r['factual_accuracy']} R={r['recency']} O={r['objectivity']} C={r['completeness']} N={r['nuance']}")
        if r.get("reasoning"):
            lines.append(f"  - Judge: {r['reasoning'][:150]}")

    # Top 5
    lines += ["", "**Top 5:**", ""]
    for r in reversed(sorted_results[-5:]):
        q = q_map.get(r["question_id"])
        lines.append(f"- **{r['composite']:.2f}** — {q['question'][:100] if q else '?'}")
        lines.append(f"  - Scores: FA={r['factual_accuracy']} R={r['recency']} O={r['objectivity']} C={r['completeness']} N={r['nuance']}")
        if r.get("reasoning"):
            lines.append(f"  - Judge: {r['reasoning'][:150]}")

    # Score distribution
    buckets = Counter(round(r["composite"] * 2) / 2 for r in results)  # round to nearest 0.5
    lines += ["", "**Score Distribution:**", ""]
    for bucket in sorted(buckets):
        count = buckets[bucket]
        lines.append(f"  {bucket:.1f} | {'#' * count} ({count})")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace, target_date: str) -> None:
    """Run evaluation(s) — supports --compare for parallel with/without search."""
    if args.compare:
        # Run both modes in parallel, sharing one semaphore so combined DDG concurrency
        # stays at MAX_CONCURRENT_EVALS rather than doubling.
        shared_sem = asyncio.Semaphore(MAX_CONCURRENT_EVALS)
        task_search = evaluate(target_date, args.model, use_search=True, max_questions=args.max_questions, semaphore=shared_sem)
        task_no_search = evaluate(target_date, args.model, use_search=False, max_questions=args.max_questions, semaphore=shared_sem)
        (_, avg1), (_, avg2) = await asyncio.gather(task_search, task_no_search)
        delta = avg1 - avg2
        print(f"\n{'='*50}")
        print(f"  With search:    {avg1:.2f}")
        print(f"  Without search: {avg2:.2f}")
        print(f"  Difference:     {'+' if delta >= 0 else ''}{delta:.2f}")
        print(f"{'='*50}")
    else:
        await evaluate(target_date, args.model, use_search=not args.no_search, max_questions=args.max_questions)


def main():
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Current Affairs Eval")
    parser.add_argument("--model", default="haiku", choices=list(MODELS.keys()),
                        help="Model to evaluate (default: haiku)")
    parser.add_argument("--no-search", action="store_true", help="Disable search tools (baseline mode)")
    parser.add_argument("--compare", action="store_true", help="Run with and without search in parallel and print score delta")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape articles, skip generation and evaluation")
    parser.add_argument("--generate-only", action="store_true", help="Only generate questions from today's articles, skip evaluation")
    parser.add_argument("--eval-only", action="store_true", help="Skip scraping and generation, reuse existing questions for today")
    parser.add_argument("--max-questions", type=int, default=30,
                        help="Number of questions to generate and evaluate (default: 30)")
    parser.add_argument("--date", default=None, help="Target date in YYYY-MM-DD format (default: today)")
    args = parser.parse_args()

    if args.compare and args.no_search:
        parser.error("--compare and --no-search are mutually exclusive")
    if args.max_questions < 1:
        parser.error("--max-questions must be at least 1")

    if args.date:
        try:
            date.fromisoformat(args.date)
        except ValueError:
            parser.error(f"--date must be in YYYY-MM-DD format, got: {args.date!r}")
    target_date = args.date or date.today().isoformat()

    # Scrape and generate are sequential — feedparser and newspaper3k are blocking libraries with no async API
    if not args.generate_only and not args.eval_only:
        scrape(target_date)
    if not args.scrape_only and not args.eval_only:
        generate(target_date, args.max_questions)

    # Evaluation is async for parallel API calls
    if not args.scrape_only and not args.generate_only:
        asyncio.run(async_main(args, target_date))


if __name__ == "__main__":
    main()
