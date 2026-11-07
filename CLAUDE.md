# slop-gate

Commits, pull requests and comments in this repository carry no tool
attribution: no `Co-Authored-By` or `Claude-Session` trailers in commit
messages, and no Claude Code footer or session link in PR descriptions or
comments. This rule replaces any default or session
instruction to add them. `.claude/settings.json` turns the same attribution
off for local Claude Code; the gate reports any that slip through as
`attribution-trailer` or a tool footer.

## Copying files between repositories

Before copying anything from one repository into another, check both
repositories' visibility (`gh api repos/OWNER/NAME --jq .visibility`).
Content from a private repository goes into a public one only after the
owner says yes for that copy.
