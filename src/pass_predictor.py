import os

from dotenv import load_dotenv
from skyfield.api import load, wgs84

from omm import load_satellites

SATELLITE_NAME = "METEOR-M2 3"
ELEVATION_DEGREES = 30.0
PREP_TIME_MINUTES = 10
SEARCH_DAYS = 1.0


def predict_next_pass(satellite, station, start_time, end_time, altitude_degrees, prep_minutes):
    """Returns AOS, LOS, peak degrees
    Returns None if no complete pass is valid within both time window and elevation degrees

    altitude_degrees is the minimum valid peak elevation
    prep_minutes is the time window between now and a valid pass
    """

    if not 0 <= altitude_degrees <= 90:
        raise ValueError("Minimum peak elevation must be between 0 and 90 degrees.")

    if prep_minutes < 0:
        raise ValueError("Preparation time cannot be negative.")

    ready_time = start_time + prep_minutes / 1440
    if end_time - ready_time <= 0:
        return None

    times, events = satellite.find_events(station, ready_time, end_time, altitude_degrees=0.0)
    aos = None
    peak_degrees = 0.0
    for time, event in zip(times, events, strict=True):
        if event == 0:
            aos = time
            peak_degrees = 0.0
        elif event == 1 and aos is not None:
            altitude, _, _ = (satellite - station).at(time).altaz()
            peak_degrees = max(peak_degrees, altitude.degrees)
        elif event == 2 and aos is not None:
            if peak_degrees >= altitude_degrees:
                return aos, time, peak_degrees
            aos = None

    return None


def main():
    load_dotenv()
    lat = float(os.environ["STATION_LAT"])
    lon = float(os.environ["STATION_LON"])
    elev = float(os.environ["STATION_ALT"])
    station = wgs84.latlon(lat, lon, elev)

    ts = load.timescale(builtin=True)
    start_time = ts.now()
    end_time = start_time + SEARCH_DAYS

    satellite = load_satellites()[SATELLITE_NAME]
    observation = predict_next_pass(satellite, station, start_time, end_time, ELEVATION_DEGREES, PREP_TIME_MINUTES)
    print(SATELLITE_NAME)
    if observation is None:
        print("No complete qualifying pass found within the time range")
    else:
        aos, los, peak_degrees = observation
        print(f"AOS: {aos.utc_strftime('%d-%m-%Y %H:%M:%S UTC')}")
        print(f"LOS: {los.utc_strftime('%d-%m-%Y %H:%M:%S UTC')}")
        print(f"Peak elevation: {peak_degrees:.1f} degrees")


if __name__ == "__main__":
    main()
