from dataclasses import dataclass
from pathlib import Path
from argus.sources import SourceType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"

DATA_DIRECTORY = PROJECT_ROOT / "data"
DATABASE_DIRECTORY = DATA_DIRECTORY / "database"
DATABASE_PATH = DATABASE_DIRECTORY / "argus.db"
RAW_ARTIFACT_DIRECTORY = DATA_DIRECTORY / "raw_artifacts"

LOG_DIRECTORY = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIRECTORY / "argus.log"


@dataclass(frozen=True, slots=True)
class RSSFeedConfig:
    """Configuration of one RSS collection source."""

    name: str
    url: str
    language: str
    country: str
    source_identifier: str | None = None
    endpoint_identifier: str | None = None
    source_type: SourceType = SourceType.NEWS_MEDIA

    @property
    def effective_source_identifier(self) -> str:
        """Return the stable source identifier used for persistence."""

        return self.source_identifier or self.name

    @property
    def effective_endpoint_identifier(self) -> str:
        """Return the stable identifier of this collection endpoint."""

        return (
            self.endpoint_identifier
            or f"rss:{self.effective_source_identifier}"
        )


RSS_FEEDS: tuple[RSSFeedConfig, ...] = (
    # International and transnational sources
    RSSFeedConfig(
        name="UN News",
        url="https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        language="en",
        country="International",
    ),
    RSSFeedConfig(
        name="Euronews",
        url="https://www.euronews.com/rss?level=theme&name=news",
        language="en",
        country="International",
    ),
    RSSFeedConfig(
        name="Africanews",
        url="https://www.africanews.com/feed/rss",
        language="en",
        country="International",
    ),

    # United Kingdom
    RSSFeedConfig(
        name="BBC World",
        url="https://feeds.bbci.co.uk/news/world/rss.xml",
        language="en",
        country="UK",
    ),
    RSSFeedConfig(
        name="The Telegraph",
        url="https://www.telegraph.co.uk/rss.xml",
        language="en",
        country="UK",
    ),
    RSSFeedConfig(
        name="The Guardian World",
        url="https://www.theguardian.com/world/rss",
        language="en",
        country="UK",
    ),

    # United States
    RSSFeedConfig(
        name="NPR World",
        url="https://feeds.npr.org/1004/rss.xml",
        language="en",
        country="USA",
    ),
    RSSFeedConfig(
        name="CNN World",
        url="http://rss.cnn.com/rss/edition_world.rss",
        language="en",
        country="USA",
    ),
    RSSFeedConfig(
        name="Fox News World",
        url=(
            "https://moxie.foxnews.com/"
            "google-publisher/world.xml"
        ),
        language="en",
        country="USA",
    ),

    # Continental Europe
    RSSFeedConfig(
        name="DW World",
        url="https://rss.dw.com/rdf/rss-en-world",
        language="en",
        country="Germany",
    ),
    RSSFeedConfig(
        name="France 24",
        url="https://www.france24.com/en/rss",
        language="en",
        country="France",
    ),

    # Middle East
    RSSFeedConfig(
        name="Al Jazeera",
        url="https://www.aljazeera.com/xml/rss/all.xml",
        language="en",
        country="Qatar",
    ),
    RSSFeedConfig(
        name="The Times of Israel",
        url="https://www.timesofisrael.com/feed/",
        language="en",
        country="Israel",
    ),

    # South and Southeast Asia
    RSSFeedConfig(
        name="The Hindu International",
        url=(
            "https://www.thehindu.com/news/"
            "international/feeder/default.rss"
        ),
        language="en",
        country="India",
    ),
    RSSFeedConfig(
        name="CNA",
        url=(
            "https://www.channelnewsasia.com/api/v1/"
            "rss-outbound-feed?_format=xml"
        ),
        language="en",
        country="Singapore",
    ),

    # East Asia
    RSSFeedConfig(
        name="The Japan Times",
        url="https://www.japantimes.co.jp/feed/topstories/",
        language="en",
        country="Japan",
    ),
    RSSFeedConfig(
        name="South China Morning Post",
        url="https://www.scmp.com/rss/91/feed/",
        language="en",
        country="China",
    ),
    RSSFeedConfig(
        name="China Daily World",
        url=(
            "https://www.chinadaily.com.cn/rss/"
            "world_rss.xml"
        ),
        language="en",
        country="China",
    ),
    RSSFeedConfig(
        name="Global Times",
        url="https://www.globaltimes.cn/rss/outbrain.xml",
        language="en",
        country="China",
    ),

    # Russia and Ukraine
    RSSFeedConfig(
        name="TASS English",
        url="https://tass.com/rss/v2.xml",
        language="en",
        country="Russia",
    ),
    RSSFeedConfig(
        name="RT English",
        url="https://www.rt.com/rss/news/",
        language="en",
        country="Russia",
    ),
    RSSFeedConfig(
        name="The Kyiv Independent",
        url="https://kyivindependent.com/news-archive/rss/",
        language="en",
        country="Ukraine",
    ),

    # Oceania
    RSSFeedConfig(
        name="ABC Australia",
        url="https://www.abc.net.au/news/feed/51120/rss.xml",
        language="en",
        country="Australia",
    ),
)
