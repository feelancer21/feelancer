from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Type, TypeVar

import tomli


# GenericConf Class für typing only
@dataclass
class GenericConf:
    pass


T = TypeVar("T", bound=GenericConf)


def defaults_from_type(
    defaults: Type[T], conf: dict | None, exclude: list[str] | None = None
) -> T:
    if not conf:
        return defaults()

    conf_copy = deepcopy(conf)
    if exclude:
        for key in exclude:
            del conf_copy[key]

    return defaults(**conf_copy)


def defaults_from_instance(
    defaults: T, conf: dict | None, exclude: list[str] | None = None
) -> T:
    if not conf:
        return defaults

    conf_copy = deepcopy(conf)
    res = deepcopy(defaults)
    if exclude:
        for key in exclude:
            del conf_copy[key]

    field_names = [f.name for f in fields(res)]

    for key, value in conf_copy.items():
        if key not in field_names:
            raise KeyError(f"{key}")
        setattr(res, key, value)

    return res


def get_peers_config(cls: Type[T], conf: dict) -> dict[str, T]:
    res: dict[str, T] = {}

    res["default"] = default = defaults_from_type(cls, conf.get("default"))

    for peer in conf.keys() - ["default"]:
        for pub_key in conf[peer]["pubkeys"]:
            res[pub_key] = defaults_from_instance(default, conf[peer], ["pubkeys"])

    return res


def read_config_file(file_name: str) -> dict:
    config_path = os.path.expanduser(file_name)

    if not os.path.exists(config_path):
        raise FileExistsError(f"Config file '{file_name}' does not exist")

    with open(config_path, "rb") as config_file:
        res = tomli.load(config_file)

    return res
