#!/bin/zsh

export PYTHONPATH=$PYTHONPATH:util/
export UV_LINK_MODE=copy

uv run streamlit run home.py --server.port 4090