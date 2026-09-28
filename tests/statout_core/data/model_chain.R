dat <- mtcars
dat$g <- factor(dat$am, labels = c("a", "b"))
m <- lm(mpg ~ g, data = dat)
anova(m)
s <- summary(m)
s$coefficients[2, , drop = FALSE]
m |> summary()
r2<-cor(dat$mpg,dat$wt)^2
cor.test(dat$mpg, dat$wt)
x <- c(2.1, 3.4, 1.9, 4.2, 3.3, 2.8)
y <- c(1.1, 2.4, 2.9, 3.2, 1.3, 2.2)
tt <- t.test(x, y)
tt
for (k in 1:2) print(t.test(x + k, y))
print(head(dat, 2))
tail(dat[, 1:3], 2)
str(x)
aggregate(mpg ~ g, data = dat, FUN = mean)
cor(dat$mpg, dat$wt)
