d <- read.csv("data/raw_scores.csv")
saveRDS(d, "processed/clean.rds")
x <- readxl::read_excel(path = "Stimuli.xlsx")
load("workspace.RData")
