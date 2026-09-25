summarize price mpg weight
regress price mpg weight
foreach v of varlist price mpg { quietly summarize `v' }
logit foreign mpg, nolog
tabulate rep78
display "ababab AA       xy|  ab   |z    |-1.5x"
display "only text, no table"
graph export "figure1.png", replace
log close
