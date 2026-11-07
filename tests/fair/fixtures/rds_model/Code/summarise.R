d <- read.csv("../data/scores.csv")
write.csv(summary(d), "results_table.csv")
