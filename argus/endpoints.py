from enum import Enum


class EndpointType(str, Enum):
    """High-level protocol or catalog type of a collection endpoint."""

    RSS = "rss"
    ATOM = "atom"
    REST_API = "rest_api"
    OAI_PMH = "oai_pmh"
    IIIF = "iiif"
    SDMX = "sdmx"
    SPARQL = "sparql"
    SITEMAP = "sitemap"
    WEB_ARCHIVE = "web_archive"
    DATASET_CATALOG = "dataset_catalog"
    OTHER = "other"


class EndpointConfigurationConflict(ValueError):
    """Raised when a stable endpoint identity conflicts with stored data."""
