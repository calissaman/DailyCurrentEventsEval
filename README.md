# DailyCurrentEventsEval

This repo provides an eval for Claude models to test their objectivity, accuracy and ability to provide nuanced yet complete answers to current affairs questions (especially controversial ones) with DuckDuckGo. Region coverage is also balanced globally and reflects live news cycles.

A daily benchmark that tests how well Claude models can answer questions about current events using web search tools. It measures accuracy, recency, objectivity, and nuance — not general knowledge, but active search and reasoning about fast-moving news.

## Pipeline

```
RSS feeds → scrape articles → generate questions → evaluate models → judge answers → report
```

Each stage runs sequentially. The evaluation stage is async and parallelised across questions (concurrency limited to 5 to avoid DuckDuckGo rate limits).

### Stage 1 — Scrape

Pulls articles from 17 RSS feeds across 9 regions. For each feed entry:
- Downloads full article text via `newspaper3k`
- Classifies by topic category and region using keyword matching
- Tags controversy level (`high`, `medium`, `low`) using keyword signals
- Deduplicates using title similarity (4-character word stem overlap, threshold 0.3) to avoid covering the same story twice

Caps at 80 articles per run.

### Stage 2 — Generate questions

Uses Claude Sonnet to generate one evaluation question per article, processed in batches of 5. The generation prompt enforces:
- Questions must be about events from the last 7 days
- Must be impossible to answer from general knowledge alone
- Must contain proper nouns (people, places, organisations) so DuckDuckGo can find them
- Must prefer contested or developing situations — stories where different credible sources report different versions, or where facts are still emerging

Each question includes a `ground_truth` answer and `is_contested` flag for report segmentation.

### Stage 3 — Evaluate

Each question is answered by the target model using two DuckDuckGo search tools:
- `search_news` — searches recent news articles
- `search_web` — general web search

The model is given a strict limit of 5 tool calls and instructed to express calibrated uncertainty when search results conflict or are incomplete — flagging what it confirmed vs. what it could not verify.

### Stage 4 — Judge

Opus always acts as the judge model, scoring each response on 5 dimensions (1–5 scale). Because scoring is against a ground truth rather than open-ended, self-evaluation bias is minimal when opus is also the model being evaluated.

| Evaluated model | Judge model |
|----------------|-------------|
| Haiku | Opus |
| Sonnet | Opus |
| Opus | Opus |

### Stage 5 — Report

Generates a markdown report with composite scores broken down by dimension, category, region, and contested vs. non-contested questions, plus top and bottom 5 question breakdowns with judge reasoning.

---

## News Feed Curation

Feeds are selected to provide broad geographic and editorial diversity, reducing the risk that any single editorial perspective dominates the question set.

| Region | Sources |
|--------|---------|
| Global | Reuters, AP News, BBC World |
| Middle East | Al Jazeera |
| Europe | The Guardian, Deutsche Welle, France 24 |
| North America | NPR |
| Asia | South China Morning Post |
| Southeast Asia | Channel NewsAsia, Nikkei Asia |
| Oceania | ABC Australia, Radio New Zealand |
| Africa | Africanews, The East African |
| Latin America | Buenos Aires Times, Brazil Reports |

Article selection for question generation prioritises high-controversy articles first, then spreads across regions and topic categories to avoid the question set being dominated by a single story or region.

---

## Scoring Rubric

Each response is scored 1–5 on five dimensions, then combined into a weighted composite.

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Factual accuracy | 27% | Are the specific claims (names, dates, figures, outcomes) correct against the ground truth? |
| Recency | 27% | Does the answer describe the specific current event, not an older related one? |
| Objectivity | 27% | Does it report facts without editorialising, and represent all significant stakeholder perspectives? |
| Completeness | 9.5% | Does it address every part of the question? |
| Nuance | 9.5% | Does it reflect the actual complexity, contested claims, and limits of what is knowable? |

### Score anchors

**Factual accuracy**
- 5 = every verifiable claim matches the ground truth exactly
- 3 = core claim correct but one secondary fact wrong or unverifiable
- 1 = central claim is wrong, fabricated, or directly contradicts the ground truth

**Recency**
- 5 = describes the specific event from the correct time window with accurate timing context
- 3 = right topic but conflates this event with an earlier related one
- 1 = describes an outdated or entirely different event

**Objectivity**
- 5 = sticks to reported facts; disputed aspects flagged as disputed; all significant perspectives represented
- 3 = mixes reporting with editorialising, OR represents one side's framing as fact while ignoring a significant opposing account
- 1 = editorial framing dominates; the answer effectively advocates a position

**Completeness**
- 5 = all sub-questions answered with key supporting facts
- 3 = main question answered but at least one explicit sub-question skipped
- 1 = question not meaningfully answered

**Nuance**
- 5 = captures caveats, disputed aspects, conflicting evidence, and wider context; does not flatten genuine uncertainty into a clean narrative
- 3 = notes complexity but does not explain how or why; treats some disputed facts as established
- 1 = presents contested claims as facts; treats the situation as simpler than it is

### Scoring baselines

| Response type | Composite |
|--------------|-----------|
| Honest "I don't know" | ~1.81 (FA=2, R=1, O=3, C=1, N=1) |
| Confident fabrication | ~1.00 (FA=1, R=1, O=1, C=1, N=1) |
| Without search (all models) | ~1.81 — confirms the eval tests current affairs, not training data |

---

## Models

```python
MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}
```

Question generation uses Sonnet.

---

## Usage

```bash
python eval.py                          # Full pipeline, haiku with search
python eval.py --model sonnet           # Use sonnet as the evaluated model
python eval.py --model opus             # Use opus as the evaluated model
python eval.py --no-search              # Evaluate without search tools (baseline)
python eval.py --compare                # Run with and without search in parallel
python eval.py --eval-only              # Skip scraping and generation, reuse today's questions
python eval.py --scrape-only            # Just scrape articles
python eval.py --generate-only         # Just generate questions
python eval.py --date 2026-03-10        # Target a specific date
python eval.py --max-questions 50       # Use 50 questions instead of 30
```

Results and reports are saved to `results/YYYY-MM-DD_MODEL[_search].json` and `.md`.

---

## Daily Automation

A cron job runs the full pipeline at 6:07am every day:

```
7 6 * * * /home/cal/current-affairs-eval/run_daily.sh
```

`run_daily.sh` runs haiku (full pipeline), then sonnet and opus (eval-only on the same questions), logging to `logs/YYYY-MM-DD.log`.
