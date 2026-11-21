#!/bin/bash

BASE_DIR=/home/tho881p/project/data_pipeline/lotto_parser
LOG_DIR=/mnt/200gb/logs/lotto

cd $BASE_DIR

$BASE_DIR/venv/bin/python -m main.lotto_main \
>> $LOG_DIR/lotto_$(date +%Y%m%d).log 2>&1

