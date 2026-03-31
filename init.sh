#!/bin/zsh

echo Initializing virtual environment...
python -m venv venv

source venv/bin/activate

echo Installing required packages...
# Install the required packages
pip install --upgrade pip
pip install -qr requirements.txt

echo Installation complete.

echo Starting application...

./run.sh
