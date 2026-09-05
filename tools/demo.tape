# termstats demo recording for https://github.com/charmbracelet/vhs
#
#   vhs tools/demo.tape        -> termstats-demo.gif
#
# --demo plays a scripted, repeatable machine (network burst, CPU spike, filling disk),
# so the recording is the same every time it is made. Nothing real is measured.

Output termstats-demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1400
Set Height 820
Set Padding 12
Set Theme "Catppuccin Mocha"

Type "termstats --demo"
Enter
Sleep 14s
Ctrl+C
Sleep 800ms
