# Pin the package versions
checkpoint::checkpoint("2023-01-15")
library(lme4)
# Fit
m <- lmer(y ~ x + (1 | id), data = dat)
