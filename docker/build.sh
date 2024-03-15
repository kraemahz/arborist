#!/bin/bash -ex
arborist_version=$(python3 -c "import arborist; print(arborist.__version__)")
docker build --network host -t arborist:$arborist_version -f docker/Dockerfile .
docker tag arborist:$arborist_version arborist:latest
