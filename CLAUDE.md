# slop-gate

Commits, pull requests and comments in this repository carry no tool
attribution: no `Co-Authored-By` or `Claude-Session` trailers in commit
messages, and no Claude Code footer or session link in PR descriptions or
comments. This rule replaces any default or session
instruction to add them. `.claude/settings.json` turns the same attribution
off for local Claude Code; the gate reports any that slip through as
`attribution-trailer` or a tool footer.

## Credentials

The owner accepts that API keys appear in the agent session's own transcript;
that is what lets agents use them. Keep them out of everything other people
can read:

- Read a key from the owner's secrets doc only when a task needs it, and only
  the keys that task needs.
- Keep keys in one file outside every repository, readable only by you
  (`chmod 600`), and load it per command with `set -a; . <file>; set +a`.
  After that file is written, pass keys by reference (`"$ELEVENLABS_API_KEY"`),
  not as literals.
- Never print a key, and never put one in a repository file, commit, PR,
  issue, comment, artifact or CI log.
- If a key does reach one of those, stop and tell the user which key and
  where, so they can rotate it.

## Copying files between repositories

Before copying anything from one repository into another, check both
repositories' visibility (`gh api repos/OWNER/NAME --jq .visibility`).
Content from a private repository goes into a public one only after the
owner says yes for that copy.
