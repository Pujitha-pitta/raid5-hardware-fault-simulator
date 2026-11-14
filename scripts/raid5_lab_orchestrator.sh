#!/usr/bin/env bash
###############################################################################
# raid5_lab_orchestrator.sh
# ─────────────────────────────────────────────────────────────────────────────
# Live Linux Kernel‑Mode RAID 5 Lab Orchestrator
#
# Creates 4 × 100 MB loopback block devices, assembles a 3‑disk RAID 5 array
# with 1 hot spare via mdadm, injects a disk fault, inspects kernel logs and
# array status, then performs a hot‑swap recovery.
#
# Usage:
#   sudo bash raid5_lab_orchestrator.sh              # full lab run
#   sudo bash raid5_lab_orchestrator.sh --cleanup     # tear everything down
#
# Prerequisites: Linux ≥ 4.x, mdadm, losetup, root privileges
###############################################################################
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
DISK_SIZE_MB=100
DISK_COUNT=4
IMG_DIR="/tmp/raid5_lab"
LOOP_BASE=10                           # /dev/loop10 – /dev/loop13
MD_DEV="/dev/md0"
RAID_DEVICES=3
SPARE_DEVICES=1

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; CYN='\033[0;36m'; RST='\033[0m'

log()  { echo -e "${CYN}[INFO]${RST}  $*"; }
warn() { echo -e "${YEL}[WARN]${RST}  $*"; }
err()  { echo -e "${RED}[ERR ]${RST}  $*" >&2; }
ok()   { echo -e "${GRN}[ OK ]${RST}  $*"; }

# ── Privilege check ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "This script requires root privileges.  Re‑run with sudo."
    exit 1
fi

# ── Cleanup helper ───────────────────────────────────────────────────────────
cleanup() {
    log "Starting cleanup …"

    # Stop the array if it exists
    if [[ -e "$MD_DEV" ]]; then
        mdadm --stop "$MD_DEV" 2>/dev/null || true
        ok "Stopped $MD_DEV"
    fi

    # Detach loop devices and remove images
    for i in $(seq 0 $((DISK_COUNT - 1))); do
        local loop="/dev/loop$((LOOP_BASE + i))"
        local img="${IMG_DIR}/disk${i}.img"

        if losetup "$loop" &>/dev/null; then
            # Zero the superblock before detaching
            mdadm --zero-superblock "$loop" 2>/dev/null || true
            losetup -d "$loop" 2>/dev/null || true
            ok "Detached $loop"
        fi

        [[ -f "$img" ]] && rm -f "$img" && ok "Removed $img"
    done

    [[ -d "$IMG_DIR" ]] && rm -rf "$IMG_DIR" && ok "Removed $IMG_DIR"

    log "Cleanup complete."
}

# If --cleanup flag is passed, just tear down and exit
if [[ "${1:-}" == "--cleanup" ]]; then
    cleanup
    exit 0
fi

###############################################################################
# PHASE 1 — Create virtual disk images & attach loop devices
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "PHASE 1: Creating ${DISK_COUNT} × ${DISK_SIZE_MB} MB virtual disks"
log "═══════════════════════════════════════════════════════════════"

mkdir -p "$IMG_DIR"

LOOP_DEVS=()
for i in $(seq 0 $((DISK_COUNT - 1))); do
    img="${IMG_DIR}/disk${i}.img"
    loop="/dev/loop$((LOOP_BASE + i))"

    dd if=/dev/zero of="$img" bs=1M count="$DISK_SIZE_MB" status=progress 2>&1
    losetup "$loop" "$img"
    LOOP_DEVS+=("$loop")
    ok "disk${i}.img → $loop"
done

echo ""
log "Attached loop devices:"
losetup -l | grep loop1[0-3] || true
echo ""

###############################################################################
# PHASE 2 — Assemble RAID 5 array with hot spare
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "PHASE 2: Creating RAID 5 array $MD_DEV  (3 active + 1 spare)"
log "═══════════════════════════════════════════════════════════════"

mdadm --create "$MD_DEV" \
    --level=5 \
    --raid-devices=$RAID_DEVICES \
    --spare-devices=$SPARE_DEVICES \
    "${LOOP_DEVS[@]}" \
    --force --run --assume-clean

ok "RAID 5 array created"
echo ""

log "Array detail:"
mdadm --detail "$MD_DEV"
echo ""

log "/proc/mdstat:"
cat /proc/mdstat
echo ""

###############################################################################
# PHASE 3 — Inject disk fault
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "PHASE 3: Injecting fault on /dev/loop10  (simulated drive failure)"
log "═══════════════════════════════════════════════════════════════"

mdadm --manage "$MD_DEV" --fail /dev/loop10
warn "Marked /dev/loop10 as FAULTY"
echo ""

log "Array status after fault injection:"
mdadm --detail "$MD_DEV"
echo ""

log "/proc/mdstat (expect degraded + rebuilding on spare):"
cat /proc/mdstat
echo ""

log "Recent dmesg entries related to md0:"
dmesg | tail -30 | grep -i -E "md|raid|loop|blk" || true
echo ""

# Give the spare rebuild a moment to start
sleep 2

###############################################################################
# PHASE 4 — Hot‑swap recovery
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "PHASE 4: Hot‑swap recovery — remove faulty, re‑add new disk"
log "═══════════════════════════════════════════════════════════════"

# Remove faulty drive from the array
mdadm --manage "$MD_DEV" --remove /dev/loop10
ok "Removed /dev/loop10 from array"

# Zero superblock on the old disk (simulates firmware wipe on replacement)
mdadm --zero-superblock /dev/loop10
ok "Zeroed superblock on /dev/loop10"

# Re‑create the disk image (simulates physically swapping in a new drive)
dd if=/dev/zero of="${IMG_DIR}/disk0.img" bs=1M count="$DISK_SIZE_MB" status=progress 2>&1
losetup -d /dev/loop10 2>/dev/null || true
losetup /dev/loop10 "${IMG_DIR}/disk0.img"
ok "New disk0.img attached to /dev/loop10"

# Add the fresh disk back to the array
mdadm --manage "$MD_DEV" --add /dev/loop10
ok "Added /dev/loop10 back to array as spare / rebuild target"
echo ""

log "Array detail (post hot‑swap, rebuild in progress):"
mdadm --detail "$MD_DEV"
echo ""

log "/proc/mdstat (rebuild progress):"
cat /proc/mdstat
echo ""

###############################################################################
# PHASE 5 — Wait for rebuild & final verification
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "PHASE 5: Monitoring rebuild until completion"
log "═══════════════════════════════════════════════════════════════"

while grep -q "recovery" /proc/mdstat 2>/dev/null; do
    echo -ne "\r  Rebuild progress: $(grep -oP '\d+\.\d+%' /proc/mdstat || echo 'calculating…')  "
    sleep 1
done

echo ""
ok "Rebuild complete!"
echo ""

log "Final array status:"
mdadm --detail "$MD_DEV"
echo ""
cat /proc/mdstat
echo ""

###############################################################################
# Done — prompt for cleanup
###############################################################################
log "═══════════════════════════════════════════════════════════════"
log "Lab complete.  Run with --cleanup to tear down all resources."
log "═══════════════════════════════════════════════════════════════"
