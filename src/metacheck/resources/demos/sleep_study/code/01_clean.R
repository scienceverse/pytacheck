# Turn the raw answers into the processed data: drop the names, e-mail addresses and bedtimes.
raw <- read.csv("data/raw/sleep_survey.csv")
clean <- raw[, c("participant_id", "age", "gender", "sleep_hours", "mood_score")]
write.csv(clean, "data/processed/sleep_mood.csv", row.names = FALSE)
