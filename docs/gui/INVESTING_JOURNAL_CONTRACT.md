# Investing Journal Contract

## Scope

The morning journal is a local, provider-free projection of the retained
Dashboard payload and retained morning brief. It never reads or renders the
payload's account section, balances, holdings, credentials, or identifiers.

The vault location is local configuration. The writer reads `journal_dir` from
`artifacts/local_user/web_settings.json`; tracked code contains no vault path.
If that directory is absent, the writer warns and performs no write.

## Schedule and target

The target is `<journal_dir>/YYYY-MM-DD 투자.md`. Saturdays, Sundays, and KRX
holidays are skipped using the shared exchange-calendar service. A payload
build failure is a non-zero operation and creates no journal. Missing individual
metrics render as `표시 불가` without failing the draft.

## Ownership boundary

For a new file, the writer creates the agreed headings and `tags:
[pk/investing]`. In an existing marked file, machine ownership is limited to:

- frontmatter values for `date`, `regime`, and `source`;
- the `- 대시보드 국면:` line under `## 오늘 국면 판단`;
- content between `<!-- auto:start -->` and `<!-- auto:end -->`.

All other bytes, including user frontmatter keys and tags, remain unchanged.
An existing file without one unique marker pair is treated as a legacy note and
left untouched. Journal and long-brief writes use same-directory atomic replace.

## Brief projection

The retained input is
`artifacts/local_user/briefs/YYYY-MM-DD-morning.md`. For a body of at most 12
lines, the first three non-empty lines are embedded as bullets. A longer body is
atomically written to `<journal_dir>/../브리핑/YYYY-MM-DD 브리핑.md` with
`date`, `tags: [pk/investing]`, and `source: auto-draft`, then linked from the
journal. That brief file is entirely machine-owned and may be overwritten.

## Runtime integration

`scripts/maintenance/write_investing_journal.py` is the direct entry point and
supports `--date`, `--project-root`, and no-write `--dry-run`. The Telegram
bridge invokes the writer only after a morning report was both sent and
persisted. Journal failures are warning-only and cannot change the Telegram
result.
