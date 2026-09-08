#!/usr/bin/env python3

import yaml
import sys


config_file = sys.argv[1]


with open(config_file) as f:
    cfg = yaml.safe_load(f)


print(cfg["top"])


print(";".join(cfg.get("source_dirs", [])))


print(";".join(cfg.get("include_dirs", [])))


print(";".join(cfg.get("exclude_patterns", [])))