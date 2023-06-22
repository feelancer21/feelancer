from enum import Enum


class TimeUnit(Enum):
    SECOND = 1
    HOUR = 3600
    DAY = 24 * 3600


class ConversionMethod(Enum):
    SIMPLE = 0
    COMPOUNDING = 1
