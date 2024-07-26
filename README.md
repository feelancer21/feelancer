# feelancer

A fee adjustment tool using a PID Controller. Details about the algo are
explained [here](docs/math.md)

**⚠️ Warning:** This project is still in development and has not been used by the
creator on mainnet. Currently, it only stores the results in a database.

## Installation

Install the requirements and the project with pip, e.g. in your virtualenv.
```
pip install -r requirements.in .
```

## Getting Started

Adjust the feelancer.toml for your needs. Especially choose a database in
`sqlalchemy.url` section. An example of the configuration file can be found
[here](docker_demo/app/feelancer.toml).


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
