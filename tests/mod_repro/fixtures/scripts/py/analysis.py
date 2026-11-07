import csv

with open("data.csv") as f:
    rows = list(csv.reader(f))
print(len(rows))
