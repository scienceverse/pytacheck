"""Clean the data."""
import pandas as pd


def clean(df):
    """Drop missing rows."""
    return df.dropna()
