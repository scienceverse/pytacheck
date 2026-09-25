* comment
use data.dta, clear // load
/* block */
merge 1:1 id using "other file.dta"
insheet using raw.csv
ssc install estout
cd "C:/project"
