# Sleep and mood in first-year students

This is an example data package that ships with metacheck, so that its data package
checks can be tried without a package of your own. The study, the people and the numbers
in it are made up.

## About the study

Forty first-year students kept a sleep diary for two weeks and rated their mood each
evening. The analysis asks whether nights with less sleep are followed by a lower mood.

- **Authors:** A. Example and B. Example
- **Contact:** a.example@example.org
- **Collected:** September to October 2025

## Files

| File | What it holds |
|------|---------------|
| `data/raw/sleep_survey.csv` | The answers as they were collected |
| `data/processed/sleep_mood.csv` | One row per student, ready for the analysis |
| `docs/codebook.csv` | What each column of the processed data means |
| `code/01_clean.R` | Turns the raw answers into the processed data |
| `code/02_analysis.R` | The analysis in the paper |

## How to reproduce

Run `code/01_clean.R`, then `code/02_analysis.R`, in R 4.4 or later.
