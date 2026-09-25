"""Clean the raw data."""
import pandas as pd
import numpy as np

df = pd.read_csv("/home/lisa/data/trials.csv")
out = df.dropna()
out.to_csv("clean.csv")
