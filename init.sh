#!/bin/zsh

echo Initializing virtual environment...
# python3 -m venv .venv
# check us intalled
uv venv --python 3.14.1

source .venv/bin/activate

echo Installing required packages...
# Install the required packages
uv pip install --upgrade pip
uv pip install -r requirements.txt

echo Installation complete.

echo Starting application...

./run.sh
