import pandas as pd

"""
@author Will Turchin
Removes any recorded gps values outside North America.
Eliminates 'cold start' gps values from data.
"""


def filter_gps(file_name: str):
    """
    :param file_name: Name of csv file with GPS values to correct
    """
    LAT_MIN = 32  # Southern tip of Arizona
    LAT_MAX = 46  # Northern tip of Michigan
    LONG_MIN = -118 # Western tip Los Angeles
    LONG_MAX = -73 # Eastern Tip New York

    df = pd.read_csv(file_name)

    latitude = df.loc[df["Sensor"] == "Latitude"].copy()
    longitude = df.loc[df["Sensor"] == "Longitude"].copy()

    latitude["Value"] = pd.to_numeric(latitude["Value"], errors="coerce")
    longitude["Value"] = pd.to_numeric(longitude["Value"], errors="coerce")

    latitude = latitude.loc[latitude["Value"].between(LAT_MIN, LAT_MAX)]
    longitude = longitude.loc[longitude["Value"].between(LONG_MIN, LONG_MAX)]

    non_gps = df.loc[~df["Sensor"].isin(["Latitude", "Longitude"])]
    filtered_df = pd.concat([non_gps, latitude, longitude], ignore_index=False)
    filtered_df = filtered_df.sort_index().reset_index(drop=True)

    filtered_df.to_csv(file_name, index=False)
    return
