# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import json
from pprint import pprint

import requests

URL = "http://172.17.10.70:8001/gates"

mapping = {}
response = requests.get(URL)
if response.status_code == 200:
    port_list = json.loads(response.text)
    for entry in port_list:
        number = entry["number"]
        name = entry["name"]
        if number in mapping:
            print(f"WARNING: Overwriting existing port mapping for number {number} = {mapping[number]}")
        mapping[number] = name

    print("\n\n")
    print("RFID_NEIGHBOUR_MAPPING = ", end="")
    pprint(mapping)
else:
    print(f"ERROR: Unable to retrieve port mapping from {URL}!")
