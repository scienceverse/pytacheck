library(dplyr)
dat <- read.csv("data.csv")

summary(dat$x > 2)
