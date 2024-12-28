# feelancer

A fee adjustment tool using a PID Controller for liquidity spreads and a mean
reversion controller for a margin. 

**⚠️ Warning:** This project is still very early. Changes to the next version can
be breaking in context of model, configuration and database scheme without providing
migration scripts. [Documentation](docs/math.md) little outdated due to recent
changes.

## Installation

⚠️  Tested with Python 3.9 only.

Install the requirements and the project with pip, e.g. in your virtualenv.
```
pip install -r requirements.txt .
```

## Getting Started

Adjust the feelancer.toml for your needs. Especially config your database in
`sqlalchemy.url` section. For productive use postgres is recommended. 
An example of the configuration file can be found [here](docker_demo/app/feelancer.toml).


```
feelancer --config [CONFIG_FILE]
```

## How set up the pid_controller params?

It's hard to say. First, learn how the model works. You can find an xlsx-example
in the doc directory. In my humble opinion, a good first step is to set $k_p$ to
a value that is not equal to 0.

## What's next?
- Writing more tests and more documentation. Testing has been more explorative
until now.
- The dependencies between the model parameters must be researched, and it must
be investigated whether all parameters are required. Perhaps a generally 
different modeling approach is also preferable.
- Maybe building more analytical tasks.

## Developer hits

For development you need to install

```
pip install -r dev-requirements.txt -e .
```
