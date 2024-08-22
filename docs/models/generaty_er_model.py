from eralchemy2 import render_er
from feelancer.pid.models import Base

render_er(Base, "erd_from_sqlalchemy.svg")
