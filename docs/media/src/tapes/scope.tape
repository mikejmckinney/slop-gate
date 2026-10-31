Output out/scope.mp4
Source tapes/common.tape
Hide
Type `export GIT_PAGER=cat PYTHONPATH="$SLOPGATE_DIR" PS1='\[\e[38;5;245m\]\W\[\e[0m\] \[\e[38;5;71m\]$\[\e[0m\] ' && cd "$DEMOS_DIR"/scope && clear`
Enter
Sleep 500ms
Show
Type "git diff main   # old lines on main already have slop"
Enter
Sleep 2.2s
Type "python -m slopgate check --base main --head HEAD --only prose --report-md r.md >/dev/null"
Enter
Sleep 1.8s
Hide
Type "clear"
Enter
Show
Type "glow -s dark -w 112 r.md   # only the PR's own lines"
Enter
Sleep 3s
Hide
Type "clear"
Enter
Show
Type "python -m slopgate check --all --only prose --report-md r.md >/dev/null   # whole-repo audit"
Enter
Sleep 1.8s
Type "glow -s dark -w 112 r.md"
Enter
Sleep 3.5s
