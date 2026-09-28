library(stats)
dat <- read.csv("clean_data.csv")
t.test(score ~ group, data = dat)
missing <- read.csv("not_shared.csv")
