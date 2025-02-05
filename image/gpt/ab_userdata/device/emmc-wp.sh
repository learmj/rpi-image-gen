#!/bin/sh

case $1 in
   prereqs)
      echo ""
      exit 0
      ;;
esac

# wp the first 8M
mmc writeprotect user set pwron 0 16384 /dev/mmcblk0
mmc writeprotect user get /dev/mmcblk0
