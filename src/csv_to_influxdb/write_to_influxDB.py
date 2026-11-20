import subprocess
import os


def write_to_influxDB(FILE_INPUT: str):
    """
    Reads a .line and writes to the influxDB database through the commandline

    :param FILE_INPUT: Name of .line File
    :param BUCKET_NAME: Name of Output File
    """
    BUCKET_NAME: str = "MF13"
    FILE_PATH: str = FILE_INPUT
    HOST_NAME: str
    ORG: str
    TOKEN: str

    with open("/data/influxdb2_parameters/influxdb2-localhost-url") as file:
        HOST_NAME = file.read()
    with open("/data/influxdb2_parameters/influxdb2-org") as file:
        ORG = file.read()
        ORG = ORG.strip()
    with open("/data/influxdb2_parameters/influxdb2-admin-token") as file:
        TOKEN = file.read()
        TOKEN = TOKEN.strip()  # Should be using a constant of some kind for these

    HOST_NAME = "http://fsaelinux.mines.edu:8086"  # hard code for now :thumbsdown:

    """
    follows the command structure:
        influx write --bucket <your-bucket-name> --file /path/to/your/data.csv --org <your-organization> --token <your-token>
        influx write --precision ms --host http://localhost:8086 --bucket NEWDATATESTING --file app/data/csv/EnduranceDayData.data.line --org docs --token theTokenOfMyDreams!
    """
    command = [
        "influx",
        "write",
        "--precision",
        "ms",
        "--format",
        "lp",
        "--host",
        HOST_NAME,
        "--bucket",
        BUCKET_NAME,
        "--file",
        FILE_PATH,
        "--org",
        ORG,
        "--token",
        TOKEN,
    ]

    print("Running:", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True)
    print("STDOUT: ", result.stdout)
    print("STDERR: ", result.stderr)
