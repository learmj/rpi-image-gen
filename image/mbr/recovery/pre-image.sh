#!/bin/bash

set -eu

fs=$1
genimg_in=$2

cat genimage.cfg.in | sed \
   -e "s|<IMAGE_NAME>|$IGconf_image_name|g" \
   > "${genimg_in}/genimage.cfg"
