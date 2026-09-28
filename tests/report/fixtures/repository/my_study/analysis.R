library(dplyr)

d <- read.csv("data.csv")
summary(d)
d |> filter(!is.na(age)) |> summarise(mean_score = mean(score))
