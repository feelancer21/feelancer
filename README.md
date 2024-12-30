# feelancer

A fee adjustment tool using a PID Controller for liquidity spreads and a mean
reversion controller for a margin. 

**⚠️ Warning:** This project is still very early. Changes to the next version can
be breaking in context of model, configuration and database scheme without providing
migration scripts. [Documentation](docs/math.md) little outdated due to recent
changes.

## Application Areas of the Tool

Currently, it is still unclear for which areas of fee setting the tool can
be used. The tool adjusts fees according to predefined parameters. These
parameters primarily determine the speed of fee adjustments. The parameters
must be specified by the user, which means that, at present, an understanding
of the economic and mathematical model behind the tool is required to
configure them.

At this stage, it can be assumed that the tool is best suited for nodes
whose fees are generally in a good state but need regular adjustments to
ensure optimal liquidity flow and counteract the depletion of channels. A
good analogy is the autopilot of an airplane, which assists pilots during
normal conditions but is not activated during takeoff or in severe turbulence.
Similarly, this tool is not expected to deliver immediate positive results
if simply activated on nodes with fees that are currently not market-
appropriate. Additionally, it will be necessary to regularly validate the
parameters used by the tool, as the algorithm cannot respond to all changes
in the network. For new channels, it will also be necessary to manually set
fees externally, as the tool does not provide a good initial estimate but
instead applies a configured value meant only to prevent depletion.

The tool is also capable of setting inbound discounts. However, this does
not eliminate the need for rebalancing in cases of significant demand sinks.
The tool can support finding a suitable fee level that simultaneously
represents an opportunity for rebalancing.

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
