#!/bin/bash

BASE_DIR=/home/tho881p/project/data_pipeline/lotto_parser
LOG_DIR=/home/tho881p/project/data_pipeline/logs

cd $BASE_DIR

$BASE_DIR/venv/bin/python -m main.lotto_main \
>> $LOG_DIR/lotto_$(date +%Y%m%d).log 2>&1

