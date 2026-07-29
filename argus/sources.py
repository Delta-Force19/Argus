from enum import Enum


class SourceType(str, Enum):
    """High-level type of an information source."""

    NEWS_MEDIA = "news_media"
    NEWS_AGENCY = "news_agency"
    GOVERNMENT = "government"
    INTERNATIONAL_ORGANIZATION = "international_organization"
    RESEARCH_INSTITUTION = "research_institution"
    SOCIAL_MEDIA = "social_media"
    OTHER = "other"