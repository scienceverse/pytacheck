key <- Sys.getenv("OPENAI_API_KEY")
if (!nzchar(key)) stop("OPENAI_API_KEY is not set")
