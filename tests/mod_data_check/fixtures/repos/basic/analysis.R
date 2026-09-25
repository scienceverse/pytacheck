d <- read.csv('data/study.csv')
summary(lm(score ~ age, d))
