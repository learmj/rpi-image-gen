#!/bin/bash

set -u

msg() {
   date +"[%T] $*"
}

err (){
   >&2 msg "$*"
}


die (){
   err "$*"
   exit 1
}

usage()
{
cat <<-EOF >&2
Usage
  $(basename "$0") [options]

Simple provisioner that uses an rpi-image-gen JSON image description and the
pi-gen-micro fastboot gadget to provision a device with the corresponding
software image.

Options:

-f <json> [-- args] Path to the file created with image2json. Remaing args
                    will be passed to fastboot.
EOF
}


JSON=
FARGS=
while getopts "f:" flag ; do
   case "$flag" in
      f)
         JSON="$OPTARG"
         ;;
      *|?)
         usage ; exit 1
         ;;
   esac 
done
[[ -z $JSON ]] && { usage ; die "Require path to JSON file" ; }
realpath -e $JSON > /dev/null 2>&1 || die "$JSON is invalid"

shift $((OPTIND - 1))
FARGS="$@"


# Dependencies
progs=()
progs+=(jq)
progs+=(fastboot)
for p in "${progs[@]}" ; do
   if ! command -v $p 2>&1 >/dev/null ; then
      die "$p is not installed"
   fi
done


JQ() {
   jq "$@" "$JSON" || die "jq: error running $@ [JSON=$JSON]"
}


FB() {
   if [ -z "$FARGS" ] ; then
      fastboot "$@"
   else
      fastboot "$FARGS" "$@"
   fi
   [[ $? -eq 0 ]] || die "fastboot: error running $FARGS $@"
}


ASSET_DIR=$(JQ -r '.IGmeta.IGconf_sys_outputdir')
[[ -d $ASSET_DIR ]] || die "JSON specified asset dir $ASSET_DIR is invalid"

# Do it

msg "Staging description.."
FB stage $JSON 2>&1 || die "Unable to transfer description to device"

msg "Checking if provisioning is possible.."
FB oem idpinit 2>&1 || die "Pre-provision checks failed"

msg "Initiating provisioning.."
FB oem idpwrite || die "Error writing to device"

while true; do
    output=$(FB oem idpgetblk 2>&1)

    bootloader_lines=$(echo "$output" | grep '^(bootloader)')

    # If there are no (bootloader) lines, we're done
    if [[ -z "$bootloader_lines" ]]; then
        break
    fi

    # Write each image into the appointed block device
    while read -r line; do
        if [[ "$line" =~ ^\(bootloader\)\ ([^:]+):(.+)$ ]]; then
            dev="${BASH_REMATCH[1]}"
            image="${BASH_REMATCH[2]}"
            FB flash $dev ${ASSET_DIR}/$image || die "Error writing $image to $dev"
        fi
    done <<< "$bootloader_lines"
done

msg "Complete"

FB oem idpdone || die "Error cleaning up"
