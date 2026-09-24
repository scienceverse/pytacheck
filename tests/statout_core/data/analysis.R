# Example analysis script used to generate R console output fixtures.
d <- sleep
head(d)
t.test(extra ~ group, data = d)
t.test(extra ~ group, data = d, var.equal = TRUE)
with(d, t.test(extra[group == 1], extra[group == 2], paired = TRUE))
t.test(d$extra, mu = 0)
cor.test(mtcars$mpg, mtcars$wt)
cor.test(mtcars$mpg, mtcars$wt, method = "spearman", exact = FALSE)
cor.test(mtcars$mpg, mtcars$wt, method = "kendall", exact = FALSE)
chisq.test(matrix(c(12, 5, 7, 9), nrow = 2))
prop.test(c(15, 25), c(50, 50))
shapiro.test(mtcars$mpg)
wilcox.test(mpg ~ am, data = mtcars, exact = FALSE)
kruskal.test(mpg ~ cyl, data = mtcars)
fisher.test(matrix(c(3, 1, 1, 3), nrow = 2))
binom.test(7, 20)
bartlett.test(mpg ~ cyl, data = mtcars)
var.test(mpg ~ am, data = mtcars)
m <- lm(mpg ~ wt + hp, data = mtcars)
summary(m)
anova(m)
fit <- aov(mpg ~ factor(cyl), data = mtcars)
summary(fit)
g <- glm(am ~ wt, data = mtcars, family = binomial)
summary(g)
for (v in c("mpg", "wt")) print(t.test(mtcars[[v]] ~ mtcars$am))
s <- summary(m)
s$coefficients[2, , drop = FALSE]
res <- t.test(extra ~ group,
              data = d)
res
aggregate(mpg ~ cyl, data = mtcars, FUN = mean)
tapply(mtcars$mpg, mtcars$cyl, sd)
r2<-cor(mtcars$mpg,mtcars$wt)^2
r2
x <- 5
