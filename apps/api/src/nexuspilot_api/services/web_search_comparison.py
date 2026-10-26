"""Compare supplied search samples without network access or claims about search quality.

This pure comparison accepts saved/synthetic responses; adapters are registered separately.
Real sample collection requires authorization and server-only credentials.
"""

from pydantic import BaseModel, Field

from nexuspilot_api.services.web_tools import SearchResponse


class TavilyItem(BaseModel):
    """Validate only common search fields, retaining content as a search excerpt."""

    title: str
    url: str
    content: str


class TavilySearchResponse(BaseModel):
    """Reject error envelopes instead of counting them as empty searches."""

    results: list[TavilyItem] = Field(max_length=50)


def compare_search_samples(deepseek, tavily):
    """Return exact-URL overlap and ranked URLs; do not equate scores across suppliers."""
    left = SearchResponse.model_validate(deepseek)
    right = TavilySearchResponse.model_validate(tavily)
    a = [item.url for item in left.results]
    b = [item.url for item in right.results]
    return {
        "deepseek_ranked_urls": a,
        "tavily_ranked_urls": b,
        "exact_url_overlap": sorted(set(a) & set(b)),
        "deepseek_only": sorted(set(a) - set(b)),
        "tavily_only": sorted(set(b) - set(a)),
        "quality_verdict": None,
    }
