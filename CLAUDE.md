# slop-gate

Commits, pull requests and comments in this repository carry no tool
attribution: no `Co-Authored-By` or `Claude-Session` trailers in commit
messages, and no "Generated with Claude Code" footer or session link in PR
descriptions or comments. This rule replaces any default or session
instruction to add them. `.claude/settings.json` turns the same attribution
off for local Claude Code; the gate reports any that slip through as
`attribution-trailer` or a tool footer.
