# Argus Configuration

## Purpose

Application configuration is defined in `argus/config.py`.

The configuration module contains static application settings and must not
perform filesystem, network or database operations during import.

## Project Paths

All runtime paths are derived from `PROJECT_ROOT`.

Configured paths include:

- data directory;
- database directory;
- SQLite database file;
- raw-artifact directory;
- logging directory;
- application log file;
- Alembic configuration file.

The modules responsible for using a path are also responsible for creating its
parent directory.

Importing configuration alone must not create files or directories.

Raw acquisition responses are stored beneath `data/raw_artifacts` by their
SHA-256 content address. The artifact store creates directories only when
bytes are written. Absolute filesystem paths are not persisted as artifact
identities.

Database schema management is described in
[Database Migrations](database_migrations.md).

## Telegram reader bot

The public multi-user reader bot uses runtime environment variables rather
than static configuration or command-line secret values:

- `ARGUS_TELEGRAM_BOT_TOKEN` contains the token issued by BotFather;
- `ARGUS_TELEGRAM_ADMIN_CHAT_ID` optionally identifies the legacy
  administrator whose single-user delivery cursor should be imported.

The previous `ARGUS_TELEGRAM_ALLOWED_CHAT_ID` name remains accepted as a
backwards-compatible administrator identifier. New installations should use
`ARGUS_TELEGRAM_ADMIN_CHAT_ID`.

Neither value belongs in source control, `.env` examples, logs, screenshots,
or committed shell scripts. The bot refuses to start when the token is missing
or when a supplied administrator identifier is not an integer.

The bot supports:

- `/start` to activate access immediately;
- `/latest` to read the current feed;
- `/subscribe` to enable future automatic delivery;
- `/unsubscribe` to disable automatic delivery while retaining access;
- `/forgetme` to delete the chat's subscriber and delivery state from
  Argus storage.

Any feed reader command registers a new chat automatically, so `/latest` also
works before `/start`. `/forgetme` is the exception: it deletes local state
without registering the chat again. Registration does not subscribe the user
automatically. This keeps manual access and automatic delivery as separate
choices. Repeated `/latest` requests have a per-chat cooldown of ten seconds;
it can be changed with `--latest-cooldown-seconds`.

The manual polling process is started with:

```text
python main.py telegram-bot --timezone Europe/Moscow
```

The `--once` option processes one update batch and exits. It is intended for
setup verification, not ongoing delivery.

Automatic collection and delivery is opt-in:

```text
python main.py telegram-bot \
    --timezone Europe/Moscow \
    --auto-delivery \
    --delivery-interval-minutes 60
```

Each registered subscriber has an independent delivery cursor in the
`telegram_subscribers` database table. `/subscribe` records the current
highest article identifier, so existing history is not sent as a surprise.
Articles ingested after that boundary are delivered in ingestion order. A
recipient's cursor advances only after Telegram accepts that recipient's
message. Failure for one recipient does not prevent delivery to the others.

The cursors make normal restarts repeat-safe. Telegram Bot API does not expose
an idempotency key for `sendMessage`, so a process failure after Telegram
accepts a message but before that user's cursor is saved can still produce a
repeat on the next cycle. This is an explicit at-least-once delivery boundary.

On the first start after upgrading from the single-user bot, the configured
administrator is created as an approved subscriber. If
`data/telegram_delivery_state.json` exists, its cursor is imported for that
administrator; otherwise the current ingestion boundary is used. Subscriber
state is stored in SQLite after that migration.

Argus stores only the Telegram `chat_id`, access/subscription flags, delivery
cursor, and database timestamps. It does not persist Telegram usernames,
display names, phone numbers, or command text. `/forgetme` deletes the complete
local subscriber row. It cannot delete messages or interaction records
retained by Telegram. A configured administrator that uses `/forgetme` will be
created again on the next bot start while `ARGUS_TELEGRAM_ADMIN_CHAT_ID`
remains set.

Automatic delivery runs collection and prioritizes parsing of the oldest
undelivered ingestion slice before sending. `--delivery-limit` bounds the
number of articles sent per cycle and `--auto-parse-limit` bounds parsing work
per cycle. A larger backlog is drained across later cycles instead of flooding
the chat in one run. The polling process must
remain running; `--once` and `--auto-delivery` cannot be combined.

BotFather command-menu configuration:

```text
start - Start using Argus
latest - Show latest news
subscribe - Enable automatic delivery
unsubscribe - Disable automatic delivery
forgetme - Delete my stored Argus data
```


## RSS Sources

RSS sources are represented by the immutable `RSSFeedConfig` dataclass.

Each feed currently contains:

- display name;
- stable source identifier;
- stable collection-endpoint identifier;
- source type;
- feed URL;
- language;
- country or international context.

If no explicit source identifier is configured, the display name is used as
the initial identifier. An explicit identifier is required before a display
name can be changed independently.

If no explicit endpoint identifier is configured, Argus derives
`rss:<source identifier>`. An explicit endpoint identifier is required when
one source exposes more than one RSS feed or when endpoint identity must remain
stable while its technical URL changes.

The configuration objects and the `RSS_FEEDS` collection are immutable during
application execution.

The normalized persistence model is described in
[Sources](sources.md).

## Source Metadata

Source country and language are contextual metadata.

They must not be interpreted as:

- evidence that a source is reliable;
- evidence that a claim is true or false;
- a political classification;
- a substitute for future source profiling.

Argus stores source context so that future analytical modules can compare
coverage across countries and media ecosystems.

## Current Language Limitation

The current discourse-analysis method uses the English spaCy model
`en_core_web_sm`.

The platform architecture targets Arabic, Chinese, English, French, Russian,
and Spanish. English and Russian are the first implementation targets, while
the current production discourse pipeline remains English-only.

For this reason, the active RSS configuration currently contains only
English-language feeds.

Multilingual sources may be added after language detection and versioned
language-specific processing pipelines are implemented.

## Adding a Source

Before an RSS source is added:

1. the URL must return a readable RSS or Atom document;
2. the feed must contain at least one valid entry;
3. article entries must provide a title and URL;
4. the source language and country context must be recorded;
5. the source identifier must match an existing source when several feeds
   belong to the same publisher;
6. each distinct feed must have a stable endpoint identifier;
7. the source type must describe the origin, not its reliability;
8. the source must use its own feed rather than an unattributed aggregation
   proxy.

Network availability is verified manually because unit tests must not depend on
external services.

The RSS adapter itself is tested with deterministic mocked feed data.
