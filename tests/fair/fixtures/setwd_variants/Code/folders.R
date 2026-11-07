old <- getwd()
setwd(tempdir())
setwd(old)
setwd(dirname(rstudioapi::getActiveDocumentContext()$path))
msg <- "a line that says
setwd('here') in a text"
  setwd("results")
