import os
import sys

import storage

MAX_RETRIES = 5
TIMEOUT_SECONDS = 30
API_KEY = os.environ["SERVICE_API_KEY"]
REGION = os.environ.get("SERVICE_REGION", "eu-west-1")


def main():
    print("region", REGION, "retries", MAX_RETRIES)
    storage.save("started")


if __name__ == "__main__":
    main()
