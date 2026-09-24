# Review script for the capture runner: odd values, classless results, an error.
d <- data.frame(y = c(2.1, 3.4, 1.9, 4.2, 3.3, 2.8, 1.1, 2.4, 2.9, 3.2, 1.3, 2.2),
                g = rep(c("été", "b \"q\""), each = 6), id = rep(1:6, 2))
t.test(y ~ g, data = d, mu = 1e-300)
cor.test(d$y, d$id, method = "kendall", exact = FALSE)
chisq.test(matrix(c(1, 2, 3, 4), 2))
summary(aov(y ~ g + Error(factor(id)), data = d))
by(d$y, d$g, mean)
sapply(split(d$y, d$g), FUN = sd)
vapply(split(d$y, d$g), median, numeric(1))
aggregate(d$y, by = list(grp = d$g), FUN = "var")
tapply(d$y, d$g, function(v) mean(v))
mean <- function(x, ...) base::mean(x, ...) + 0
aggregate(y ~ g, data = d, FUN = mean)
fisher.test(matrix(c(3, 1, 1, 3), 2), alternative = "greater")
prop.test(0, 10)
summary(lm(y ~ 1, data = d))$coefficients
m <- lm(y ~ g, data = d); r <- m
summary(r)
anova(m, lm(y ~ 1, data = d))
x <- c(a = Inf, b = 1)
stop("deliberate failure é")
t.test(d$y)
