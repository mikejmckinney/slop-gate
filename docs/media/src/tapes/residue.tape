Output out/residue.mp4
Source tapes/common.tape
Hide
Type `export PYTHONPATH="$SLOPGATE_DIR" PS1='\[\e[38;5;245m\]\W\[\e[0m\] \[\e[38;5;71m\]$\[\e[0m\] ' && cd "$DEMOS_DIR"/residue-before && clear`
Enter
Sleep 500ms
Show
Type "cat billing.py   # looks clean in review"
Enter
Sleep 2.2s
Hide
Type "clear"
Enter
Show
Type "python -m slopgate check --base main --head HEAD --only code --report-md r.md >/dev/null"
Enter
Sleep 1.8s
Type "glow -s dark -w 112 r.md"
Enter
Sleep 4s
Hide
Type "clear; cd ../residue-after"
Enter
Show
Type "# after the fix"
Enter
Type "python -m slopgate check --base main --head HEAD --only code --report-md r.md >/dev/null"
Enter
Sleep 1.8s
Type "glow -s dark -w 112 r.md"
Enter
Sleep 2.5s
