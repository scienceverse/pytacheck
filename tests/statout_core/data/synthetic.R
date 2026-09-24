library(stats)
cohen.d(x, g)
cohens_d(y ~ g, data = d)
FR_No <- t.test(x, y)
FR_No
aov_car(score ~ cond + Error(id/time), data = d)
d %>% t_test(score ~ group)
hedges_g(y ~ g, data = d)
repeated_measures_d(y ~ g | id, data = d)
glass_delta(y ~ g, data = d)
eta_squared(mc_model$anova_table$F[1])
emmeans(specs = "team", object = mc_model)
summary(update(m_vid, . ~ . - x))
r2_vid <- r.squaredGLMM(m_vid)[1,2]
CI.Rsq(r2_vid, n = 60, k = 2)
mc_model
statsmodels_print()
