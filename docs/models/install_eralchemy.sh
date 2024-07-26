#!/bin/bash
# Make sure that you have activated the correct python virtualenv, where also feelancer
# is installed. Otherwise it is not possible to generate the ER-Model from the
# modules

# We have to install graphviz first. Requires sudo for install

wget https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/9.0.0/graphviz-9.0.0.tar.gz
tar -xzf graphviz-9.0.0.tar.gz
cd graphviz-9.0.0
./configure
make
sudo make install

pip install eralchemy2
