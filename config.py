from typing import TypedDict


class FeedConfig(TypedDict):
    name: str
    url: str
    region: str


# RSS feeds to scrape, grouped by region
RSS_FEEDS: list[FeedConfig] = [
    # Global
    {"name": "reuters", "url": "https://www.reutersagency.com/feed/", "region": "global"},
    {"name": "ap_news", "url": "https://rsshub.app/apnews/topics/apf-topnews", "region": "global"},
    {"name": "bbc_world", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "region": "global"},
    # Middle East
    {"name": "aljazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "region": "middle_east"},
    # Europe
    {"name": "guardian", "url": "https://www.theguardian.com/world/rss", "region": "europe"},
    {"name": "dw", "url": "https://rss.dw.com/rdf/rss-en-all", "region": "europe"},
    {"name": "france24", "url": "https://www.france24.com/en/rss", "region": "europe"},
    # North America
    {"name": "npr", "url": "https://feeds.npr.org/1001/rss.xml", "region": "north_america"},
    # Oceania
    {"name": "abc_australia", "url": "https://www.abc.net.au/news/feed/2942460/rss.xml", "region": "oceania"},
    {"name": "rnz", "url": "https://www.rnz.co.nz/rss/world.xml", "region": "oceania"},
    # Asia
    {"name": "scmp", "url": "https://www.scmp.com/rss/91/feed", "region": "asia"},
    # Southeast Asia
    {"name": "cna_asia", "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511", "region": "southeast_asia"},
    {"name": "nikkei_asia", "url": "https://asia.nikkei.com/rss", "region": "southeast_asia"},
    # Africa
    {"name": "africanews", "url": "https://www.africanews.com/feed/", "region": "africa"},
    {"name": "the_east_african", "url": "https://www.theeastafrican.co.ke/tea/rss", "region": "africa"},
    # Latin America
    {"name": "buenos_aires_times", "url": "https://www.batimes.com.ar/feed", "region": "latin_america"},
    {"name": "brazil_reports", "url": "https://brazilreports.com/feed/", "region": "latin_america"},
]

# Claude models to evaluate
MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

# Which model generates the eval questions
GENERATOR_MODEL = "claude-sonnet-4-6"

# How much each scoring dimension counts toward the final score
# Primary tier (81%): accuracy, recency, objectivity — core qualities
# Secondary tier (19%): completeness, nuance — nice to have
SCORING_WEIGHTS: dict[str, float] = {
    "factual_accuracy": 0.27,
    "recency": 0.27,
    "objectivity": 0.27,
    "completeness": 0.095,
    "nuance": 0.095,
}

# Keywords to classify articles by topic category
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "geopolitics": ["sanctions", "diplomacy", "diplomatic", "treaty", "alliance", "nato", "united nations", "g7", "g20", "summit", "bilateral", "foreign policy"],
    "domestic_politics": ["parliament", "congress", "senate", "legislation", "bill passed", "executive order", "supreme court", "ruling party", "opposition"],
    "conflicts": ["war", "military", "troops", "bombing", "airstrike", "ceasefire", "invasion", "combat", "missile", "casualties", "offensive"],
    "elections": ["election", "vote", "ballot", "polling", "candidate", "campaign", "referendum", "runoff", "primary"],
    "policy": ["regulation", "law", "policy", "reform", "mandate", "ban", "tariff", "tax", "subsidy", "healthcare", "immigration"],
    "social_movements": ["protest", "demonstration", "rally", "activist", "movement", "rights", "equality", "justice", "civil rights"],
    "economy": ["gdp", "inflation", "recession", "stock market", "trade", "unemployment", "interest rate", "economic", "central bank"],
    "technology": ["artificial intelligence", "tech company", "cybersecurity", "data privacy", "social media", "machine learning"],
    "climate_environment": ["climate", "emissions", "carbon", "renewable", "fossil fuel", "wildfire", "flood", "drought", "environmental", "pollution"],
}

# Keywords for controversy level (used to prioritize interesting articles)
CONTROVERSY_KEYWORDS: dict[str, list[str]] = {
    "high": ["war", "invasion", "genocide", "massacre", "coup", "impeach", "scandal", "corruption", "assassination", "authoritarian"],
    "medium": ["protest", "controversial", "disputed", "contested", "sanctions", "ban", "crackdown", "censorship", "extremis", "polariz"],
}

# Keywords to guess which region an article is about
REGION_KEYWORDS: dict[str, list[str]] = {
    "north_america": ["united states", "u.s.", "usa", "america", "canada", "mexico", "washington", "white house"],
    "europe": ["europe", "european union", "britain", "france", "germany", "spain", "italy", "nato", "brussels", "london", "paris", "berlin"],
    "asia": ["china", "japan", "india", "korea", "beijing", "tokyo", "delhi", "taiwan", "hong kong", "pakistan", "bangladesh"],
    "southeast_asia": ["southeast asia", "asean", "indonesia", "thailand", "vietnam", "philippines", "malaysia", "singapore", "myanmar", "cambodia"],
    "oceania": ["australia", "new zealand", "pacific", "oceania", "fiji", "papua", "samoa", "canberra", "sydney"],
    "middle_east": ["israel", "palestine", "gaza", "iran", "iraq", "syria", "saudi", "yemen", "lebanon", "jordan", "qatar", "jerusalem", "tehran"],
    "africa": ["africa", "nigeria", "kenya", "south africa", "ethiopia", "congo", "sudan", "sahel", "ghana", "tanzania", "uganda"],
    "latin_america": ["brazil", "argentina", "colombia", "venezuela", "chile", "peru", "latin america", "caribbean", "cuba", "ecuador"],
}
