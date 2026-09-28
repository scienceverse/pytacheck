raw <- read.csv("data/raw_data.csv")
clean <- raw[!is.na(raw$score), ]
write.csv(clean, "clean_data.csv", row.names = FALSE)
