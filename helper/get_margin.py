import os
from feelancer.utils import read_config_file
from feelancer.data.db import FeelancerDB
from feelancer.pid.data import PidStore
import json


config_file = os.environ.get("FEELANCER_CONFIG")
if not config_file:
    raise ValueError("Env variable 'FEELANCER_CONFIG' not provided.")

config = read_config_file(config_file)

pubkey = os.environ.get("FEELANCER_PUBKEY")
if not pubkey:
    raise ValueError("Env variable 'FEELANCER_PUBKEY' not provided.")

if "sqlalchemy" in config:
    db = FeelancerDB.from_config_dict(config["sqlalchemy"]["url"])
else:
    raise ValueError("'sqlalchemy' section is not included in config-file")


def get_margin(db: FeelancerDB, pubkey: str) -> None:
    """
    Fetches the margin of the last run from the database and returns a json
    string.
    """

    store = PidStore(db, pubkey)

    last_run_id, _ = store.pid_run_last()
    if not last_run_id:
        raise ValueError("'last_run_id' not found.")

    last_params = store.mr_params_by_run(last_run_id)
    if not last_params:
        raise ValueError("'last_params' not found.")

    dict_out = {"margin": int(last_params.control_variable)}
    print(json.dumps(dict_out, indent=1))


if __name__ == "__main__":
    get_margin(db, pubkey)
