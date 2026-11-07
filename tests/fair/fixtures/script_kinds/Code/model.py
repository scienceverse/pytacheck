import os

import pandas as pd

os.chdir("/home/me/project")
df = pd.read_csv("data/scores.csv")
print(df.describe())
