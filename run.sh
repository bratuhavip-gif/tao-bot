#!/bin/bash
export $(cat .env | xargs)
python3 bot.py
