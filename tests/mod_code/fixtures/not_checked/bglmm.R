model{
  for (i in 1:N) { y[i] ~ dnorm(mu, tau) }
  mu ~ dnorm(0, 0.001)
}
