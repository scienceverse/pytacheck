dir <- "raw"
dat <- read.csv(sprintf("%s/data.csv", dir))
extra <- read.csv(paste0(dir, "/nothere.csv"))
