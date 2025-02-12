#!/bin/sh

set -eu

rootfs=$1
genimg_in=$2

cat genimage.cfg.in.$IGconf_image_rootfs_type | sed \
   -e "s|<IMAGE_DIR>|$IGconf_image_outputdir|g" \
   -e "s|<IMAGE_NAME>|$IGconf_image_name|g" \
   -e "s|<IMAGE_SUFFIX>|$IGconf_image_suffix|g" \
   -e "s|<FW_SIZE>|$IGconf_image_boot_part_size|g" \
   -e "s|<ROOT_SIZE>|$IGconf_image_root_part_size|g" \
   -e "s|<SETUP>|'$(readlink -ef setup.sh)'|g" \
   -e "s|<MKE2FSCONF>|'$(readlink -ef mke2fs.conf)'|g" \
   > ${genimg_in}/genimage.cfg
