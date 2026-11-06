# slop-gate

Commits, pull requests and comments in this repository carry no tool
attribution: no `Co-Authored-By` or `Claude-Session` trailers in commit
messages, and no Claude Code footer or session link in PR descriptions or
comments. This rule replaces any default or session
instruction to add them. `.claude/settings.json` turns the same attribution
off for local Claude Code; the gate reports any that slip through as
`attribution-trailer` or a tool footer.

## Credentials

Never write a credential's value into a command, file, commit, PR, issue,
comment or log, including a scratch file outside the repository. Commands
and their arguments are kept in session transcripts, so a value typed into
one is exposed even if the output is redacted. Read credentials only by
reference: an environment variable (`"$GH_PAT"`) or a file the value is
already in. If a value does leak, stop and tell the user which credential it
was and where it went, so they can rotate it.

## Copying files between repositories

Before copying anything from one repository into another, check both
repositories' visibility (`gh api repos/OWNER/NAME --jq .visibility`).
Content from a private repository goes into a public one only after the
owner says yes for that copy.
