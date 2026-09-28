# Analyse des données (étude 1)
dat <- read.csv("data/donnees_etude1.csv")
res <- readRDS(file = "out/modèle.rds")
write.csv(res, "resultats.csv")
