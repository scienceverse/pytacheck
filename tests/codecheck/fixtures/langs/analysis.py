"""Module docstring."""
import os, sys
import pandas as pd  # data
from scipy.stats import ttest_ind

# load data
df = pd.read_csv("data/trials.csv")
with open('notes.txt') as f:
    notes = f.read()
print("100% # not a comment")
