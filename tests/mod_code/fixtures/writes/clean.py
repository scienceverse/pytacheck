"""Clean the data and write the results."""
rows = open("trials.csv").read()
with open("clean.csv", "w") as out:
    out.write(rows)
with open("log.txt", mode="a") as log:
    log.write("done")
with open("lookup.txt", "r+") as keep:
    keep.read()
with open("missing_input.dat") as inp, open("summary.txt", "wb") as res:
    res.write(inp.read())
