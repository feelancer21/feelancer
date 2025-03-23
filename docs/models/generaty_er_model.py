from eralchemy2 import render_er
from feelancer.pid.models import Base
from feelancer.tracker.payments.models import Base  # noqa: F811


render_er(Base, "erd_from_sqlalchemy.svg")
