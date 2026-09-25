dat <- read.csv("raw/survey.csv")
t.test(rt ~ cond, dat)
