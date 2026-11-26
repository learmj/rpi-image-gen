#!/bin/sh
# Mount an overlay on /sysroot/etc. Its upper layer is on /run, which carries
# across switch-root, and holds the persisted files restored from storage.

set -eu

manifest=/sysroot/usr/lib/rpi-image-gen/slot-state.manifest
src="/sysroot<PERSISTENT_DIR>/common/etc"
run=/run/rpi-etc-overlay

# Remove a stamp left by a different image, so units with
# ConditionNeedsUpdate= run whether this image is newer or older.
stale() {
   if [ -f "$1" ] && [ "$(stat -c %Y "$1")" != "$(stat -c %Y /sysroot/usr)" ]; then
      rm -f "$1"
   fi
}

mkdir -p "${run}/upper" "${run}/work"

# The EarlyPersistFile= paths in the manifest, relative to /etc
files() {
   while read -r op path _; do
      case "$op" in
         EarlyPersistFile) echo "${path#/etc/}" ;;
      esac
   done < "$manifest"
}

if [ -f "$manifest" ] && [ -d "$src" ]; then
   files | rsync -a --numeric-ids --ignore-missing-args --files-from=- "${src}/" "${run}/upper/"
fi
stale "${run}/upper/.updated"

slot=$(blkid -s PARTLABEL -o value -- /dev/disk/by-slot/active/system || true)
var="/sysroot<PERSISTENT_DIR>/slots/${slot}/var"
if [ -n "$slot" ] && [ -d "$var" ]; then
   stale "${var}/.updated"
fi

mount -t overlay overlay \
   -o "lowerdir=/sysroot/etc,upperdir=${run}/upper,workdir=${run}/work" /sysroot/etc
