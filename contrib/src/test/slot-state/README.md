Test build for the `slot-state` layer. Proves that units with `ConditionNeedsUpdate=/etc` or `ConditionNeedsUpdate=/var` run on the first boot of a slot and on any change of image, newer or older, and that a slot's `/var` is reset only when its own image changes.

The image is `trixie-minbase-ab.yaml` with Raspberry Pi Connect Lite and Remote Update, plus a custom `slot-state-probe` layer, which adds:

* `slot-state-probe-etc` and `slot-state-probe-var`: oneshot units with `ConditionNeedsUpdate=/etc` and `ConditionNeedsUpdate=/var` that log `IMAGE_VERSION` and `IG_SOURCE_DATE_EPOCH`.
* `slot-state-check`: shows the running image, whether the update units ran on this boot, the update stamps, and whether the slot's `/var` was reset.
* A hold on `systemd-update-done` while `/persistent/slot-state-hold` exists.

## Build

`SOURCE_DATE_EPOCH` can be set, but images are differentiated by it, so it must be different across each build. Left unset, it's the build time. Testing requires usage of `sudo` because it manipulates files on `/persistent` which is owned by root. Set your build config preferences for user/sudo/passwd as you like, adding them on the end of the following commands (or in the config file):

```bash
rpi-image-gen build -S ./contrib/src/test/slot-state -c slot-state-test.yaml -- IGconf_artefact_version=1.0.0
rpi-image-gen build -S ./contrib/src/test/slot-state -c slot-state-test.yaml -- IGconf_artefact_version=2.0.0
```

If the device is registered with a Raspberry Pi Connect for Organisations account, it signs in on first boot automatically and needs no auth key or manual signin. An artefact can be deployed as soon as it comes online.

## Test

Flash the 1.0.0 base image, then follow the steps in order. After every boot, run `sudo slot-state-check`. Deploying an artefact is assumed to automatically reboot the device.

`slot-state-check` leaves a marker in each slot's `/var`. If the marker is gone, the running slot's `/var` was reset since the last check.

| Step | Running | probe-etc | probe-var | slot-var-reset | Marker |
|---|---|---|---|---|---|
| 1. First boot after flashing | A, 1.0.0 | yes | yes | yes | n/a |
| 2. Reboot | A, 1.0.0 | no | no | no | kept |
| 3. Deploy 2.0.0 | B, 2.0.0 | yes | yes | yes | gone |
| 4. Reboot | B, 2.0.0 | no | no | no | kept |
| 5. Deploy 2.0.0 | A, 2.0.0 | no | yes | yes | gone |
| 6. Deploy 1.0.0 (older) | B, 1.0.0 | yes | yes | yes | gone |
| 7. Reboot | B, 1.0.0 | no | no | no | kept |

`yes` is `ConditionResult=yes`. After every boot, `/etc/.updated`, `/persistent/common/etc/.updated` and `/var/.updated` have the same time as `/usr`.

To see the runs covering all boots:

```bash
journalctl -o short -u slot-state-probe-etc -u slot-state-probe-var -u slot-var-reset
```

### Rollback

From step 7, tryboot back into slot A, and don't commit: `sudo reboot '0 tryboot'`.

| Step | Running | probe-etc | probe-var | slot-var-reset | Marker |
|---|---|---|---|---|---|
| 8. Tryboot | A, 2.0.0 | yes | no | no | kept |
| 9. Reboot | B, 1.0.0 | yes | no | no | kept |

### Interrupted update

While `/persistent/slot-state-hold` exists, `systemd-update-done` doesn't run, as if each boot lost power before the stamps were written.

`sudo touch /persistent/slot-state-hold`, then deploy a new build, eg 3.0.0. A slot's `/var` is only reset when its own image changes.

| Step | probe-etc | probe-var | slot-var-reset | systemd-update-done | Marker |
|---|---|---|---|---|---|
| 1. Deploy | yes | yes | yes | no | gone |
| 2. Reboot | yes | yes | yes | no | gone |
| 3. `sudo rm /persistent/slot-state-hold`, reboot | yes | yes | yes | yes | gone |
| 4. Reboot | no | no | no | no | kept |

In steps 1 and 2, `/etc/.updated` and `/var/.updated` should be missing, and `/persistent/common/etc/.updated` has the previous image's `SOURCE_DATE_EPOCH` as its time.

## Initramfs

```bash
journalctl -b -u rpi-etc-overlay
```
