# Analysis script for study 1
library(dplyr)
setwd("C:/Users/lisa/project")
install.packages("lme4")
dat <- read.csv("data.csv")
raw <- read.csv("C:/Users/lisa/project/raw.csv")
x <- 1
y <- 2
z <- 3
w <- 4
library(ggplot2)
res <- readRDS("results/model_fit.rds")
