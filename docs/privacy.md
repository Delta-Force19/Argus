# Privacy and operator separation

Argus minimizes personal data in the public Telegram reader and keeps runtime
credentials outside version control. These controls reduce accidental
disclosure; they do not make an operator anonymous to Telegram, Git hosting,
infrastructure providers, or a determined investigator.

## Telegram reader data

Argus persists only:

- Telegram `chat_id`;
- access and subscription state;
- the last delivered article identifier;
- database creation and update timestamps.

Argus does not persist Telegram usernames, display names, phone numbers, or
command text. Delivery errors are logged without the raw `chat_id`.
`/forgetme` deletes the complete local subscriber row. Telegram controls its
own retention of messages, bot updates, network metadata, and account records.

## Secrets and local state

The Telegram bot token and optional administrator chat identifier are runtime
environment variables. They must not be committed, pasted into issue reports,
stored in screenshots, or included in shell history. Local databases, logs,
raw artifacts, IDE metadata, virtual environments, and `.env` files are
excluded by `.gitignore`.

Before any public release, inspect both the working tree and Git history for
credentials, email addresses, personal paths, account names, and infrastructure
identifiers. Removing a value from the current file does not remove it from
existing Git history.

## Project identity

Repository ownership, commit author fields, SSH or signing keys, bot ownership,
hosting accounts, domains, payments, and public contact addresses can link a
project to its operator. A pseudonymous publication should use a separate
project identity and new root Git history rather than a GitHub transfer or
fork of a previously linked repository.

The development, public repository, bot ownership, and production
infrastructure may be separated operationally. That separation must be
maintained outside the codebase as well as within it.
