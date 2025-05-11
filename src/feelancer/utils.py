from __future__ import annotations

import base64
import datetime
import os
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Protocol, TypeVar

import pytz
import tomli


# GenericConf Class für typing only
@dataclass
class GenericConf:
    pass


T = TypeVar("T", bound=GenericConf)
U = TypeVar("U")


def defaults_from_type(
    defaults: type[T], conf: dict | None, exclude: list[str] | None = None
) -> T:
    if conf is None:
        return defaults()

    conf_copy = deepcopy(conf)
    if exclude is not None:
        for key in exclude:
            del conf_copy[key]

    return defaults(**conf_copy)


def defaults_from_instance(
    defaults: T, conf: dict | None, exclude: list[str] | None = None
) -> T:
    if conf is None:
        return defaults

    conf_copy = deepcopy(conf)
    res = deepcopy(defaults)
    if exclude is not None:
        for key in exclude:
            del conf_copy[key]

    field_names = [f.name for f in fields(res)]

    for key, value in conf_copy.items():
        if key not in field_names:
            raise KeyError(f"{key}")
        setattr(res, key, value)

    return res


def get_peers_config(cls: type[T], conf: dict) -> dict[str, T]:
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


def first_some(value1: U | None, value2: U) -> U:
    """Returns the first value which is not None"""

    return value1 if value1 is not None else value2


class SupportsStr(Protocol):
    def __str__(self) -> str: ...


def ns_to_datetime(ns: int) -> datetime.datetime:
    """
    Convert UNIX nanoseconds to a timezone-aware datetime (UTC).
    """
    return datetime.datetime.fromtimestamp(ns / 1e9, tz=pytz.utc)


def sec_to_datetime(sec: int) -> datetime.datetime:
    """
    Convert UNIX seconds to a timezone-aware datetime (UTC).
    """
    return datetime.datetime.fromtimestamp(sec, tz=pytz.utc)


def bytes_to_str(bytes: bytes) -> str:
    return base64.b16encode(bytes).decode("utf-8").lower()
