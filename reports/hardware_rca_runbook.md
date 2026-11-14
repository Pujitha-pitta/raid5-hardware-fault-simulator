# AWS DCO Hardware Root Cause Analysis (RCA) & Hot-Swap Runbook

> **Generated:** 2026-11-14T07:15:51.169466Z  
> **Host:** `aws-dco-bms-prod-0142.us-east-1.metal.internal`  
> **Overall Severity:** `CRITICAL`

---

## 1. RAID Array Status

### Simulated `/proc/mdstat` Output
```
Personalities : [raid6] [raid5] [raid4]
md0 : active raid5 loop10[0](F) loop11[1] loop12[2] loop13[3](S)
      204800 blocks super 1.2 level 5, 512k chunk, algorithm 2 [UU_]
      [======>..............]  recovery = 34.7% (71065/204800) finish=0.2min speed=12800K/sec

unused devices: <none>
```

### Simulated `mdadm --detail /dev/md0`
```
        Version : 1.2
  Creation Time : Sat Nov 14 07:10:22 2026
     Raid Level : raid5
     Array Size : 204800 (200.00 MiB)
  Used Dev Size : 102400 (100.00 MiB 104.86 MB)
   Raid Devices : 3
  Total Devices : 4
         State  : degraded
 Active Devices : 2
Working Devices : 3
 Failed Devices : 1
  Spare Devices : 1

    0     /dev/loop10    faulty
    1     /dev/loop11    active sync
    2     /dev/loop12    active sync
    3     /dev/loop13    spare rebuilding
```

**Bitmap Analysis:** `[UU_]` → 2 active, 1 degraded

## 2. S.M.A.R.T. Disk Health Telemetry

### 🔴 `/dev/loop10` — WDC WUH721818ALE6L4 (S/N: WMC2K1R4F)

- **Health:** FAILED
- **Firmware:** 83.00A83
- **Temperature:** 52°C
- **Power-On Hours:** 34,819

| ID | Attribute | Value | Worst | Thresh | Raw | Status |
|---:|-----------|------:|------:|-------:|----:|--------|
| 1 | Raw_Read_Error_Rate | 160 | 133 | 51 | 2814 | ⚠️ WARNING |
| 3 | Spin_Up_Time | 170 | 165 | 21 | 4812 | OK |
| 4 | Start_Stop_Count | 100 | 100 | 0 | 42 | OK |
| 5 | Reallocated_Sector_Ct | 132 | 132 | 140 | 544 | 🔴 CRITICAL |
| 7 | Seek_Error_Rate | 183 | 175 | 51 | 1247 | ⚠️ WARNING |
| 9 | Power_On_Hours | 72 | 72 | 0 | 34819 | OK |
| 10 | Spin_Retry_Count | 100 | 100 | 51 | 0 | OK |
| 12 | Power_Cycle_Count | 100 | 100 | 0 | 67 | OK |
| 194 | Temperature_Celsius | 95 | 88 | 0 | 52 | OK |
| 197 | Current_Pending_Sector | 196 | 196 | 0 | 48 | 🔴 CRITICAL |
| 198 | Offline_Uncorrectable | 196 | 196 | 0 | 48 | ⚠️ WARNING |
| 199 | UDMA_CRC_Error_Count | 198 | 198 | 0 | 7 | 🔴 CRITICAL |

### 🟢 `/dev/loop11` — WDC WUH721818ALE6L4 (S/N: WMC5G3N7P)

- **Health:** PASSED
- **Firmware:** 83.00A83
- **Temperature:** 34°C
- **Power-On Hours:** 12,847

| ID | Attribute | Value | Worst | Thresh | Raw | Status |
|---:|-----------|------:|------:|-------:|----:|--------|
| 1 | Raw_Read_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 3 | Spin_Up_Time | 176 | 175 | 21 | 4375 | OK |
| 4 | Start_Stop_Count | 100 | 100 | 0 | 18 | OK |
| 5 | Reallocated_Sector_Ct | 200 | 200 | 140 | 0 | OK |
| 7 | Seek_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 9 | Power_On_Hours | 97 | 97 | 0 | 12847 | OK |
| 10 | Spin_Retry_Count | 100 | 100 | 51 | 0 | OK |
| 12 | Power_Cycle_Count | 100 | 100 | 0 | 14 | OK |
| 194 | Temperature_Celsius | 113 | 103 | 0 | 34 | OK |
| 197 | Current_Pending_Sector | 200 | 200 | 0 | 0 | OK |
| 198 | Offline_Uncorrectable | 200 | 200 | 0 | 0 | OK |
| 199 | UDMA_CRC_Error_Count | 200 | 200 | 0 | 0 | OK |

### 🟢 `/dev/loop12` — WDC WUH721818ALE6L4 (S/N: WMC5G3N7P)

- **Health:** PASSED
- **Firmware:** 83.00A83
- **Temperature:** 34°C
- **Power-On Hours:** 12,847

| ID | Attribute | Value | Worst | Thresh | Raw | Status |
|---:|-----------|------:|------:|-------:|----:|--------|
| 1 | Raw_Read_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 3 | Spin_Up_Time | 176 | 175 | 21 | 4375 | OK |
| 4 | Start_Stop_Count | 100 | 100 | 0 | 18 | OK |
| 5 | Reallocated_Sector_Ct | 200 | 200 | 140 | 0 | OK |
| 7 | Seek_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 9 | Power_On_Hours | 97 | 97 | 0 | 12847 | OK |
| 10 | Spin_Retry_Count | 100 | 100 | 51 | 0 | OK |
| 12 | Power_Cycle_Count | 100 | 100 | 0 | 14 | OK |
| 194 | Temperature_Celsius | 113 | 103 | 0 | 34 | OK |
| 197 | Current_Pending_Sector | 200 | 200 | 0 | 0 | OK |
| 198 | Offline_Uncorrectable | 200 | 200 | 0 | 0 | OK |
| 199 | UDMA_CRC_Error_Count | 200 | 200 | 0 | 0 | OK |

### 🟢 `/dev/loop13` — WDC WUH721818ALE6L4 (S/N: WMC5G3N7P)

- **Health:** PASSED
- **Firmware:** 83.00A83
- **Temperature:** 34°C
- **Power-On Hours:** 12,847

| ID | Attribute | Value | Worst | Thresh | Raw | Status |
|---:|-----------|------:|------:|-------:|----:|--------|
| 1 | Raw_Read_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 3 | Spin_Up_Time | 176 | 175 | 21 | 4375 | OK |
| 4 | Start_Stop_Count | 100 | 100 | 0 | 18 | OK |
| 5 | Reallocated_Sector_Ct | 200 | 200 | 140 | 0 | OK |
| 7 | Seek_Error_Rate | 200 | 200 | 51 | 0 | OK |
| 9 | Power_On_Hours | 97 | 97 | 0 | 12847 | OK |
| 10 | Spin_Retry_Count | 100 | 100 | 51 | 0 | OK |
| 12 | Power_Cycle_Count | 100 | 100 | 0 | 14 | OK |
| 194 | Temperature_Celsius | 113 | 103 | 0 | 34 | OK |
| 197 | Current_Pending_Sector | 200 | 200 | 0 | 0 | OK |
| 198 | Offline_Uncorrectable | 200 | 200 | 0 | 0 | OK |
| 199 | UDMA_CRC_Error_Count | 200 | 200 | 0 | 0 | OK |

### ⚠️  S.M.A.R.T. Threshold Alerts

- **/dev/loop10** — `Reallocated_Sector_Ct` (ID 5): value=132, threshold=140, raw=544 → **CRITICAL**
- **/dev/loop10** — `Current_Pending_Sector` (ID 197): value=196, threshold=0, raw=48 → **CRITICAL**
- **/dev/loop10** — `UDMA_CRC_Error_Count` (ID 199): value=198, threshold=0, raw=7 → **CRITICAL**

## 3. Kernel Log Analysis (`dmesg`)

```
[   84291.000] err: blk_update_request: critical medium error, dev sdb, sector 4096 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 0
[   84291.001] err: md/raid:md0: Disk failure on loop10, disabling device.
[   84291.002] warning: md/raid:md0: Operation continuing on 2 devices.
[   84291.210] info: md: recovery of RAID array md0
[   84291.211] info: md: minimum _guaranteed_ speed: 1000 KB/sec/disk.
[   84291.212] info: md: using maximum available idle IO bandwidth for recovery.
[   84291.213] info: md: md0: recovery done.
```

## 4. Thermal, Fan & Memory (EDAC) Status

### CPU & System Temperatures (`lm-sensors`)

| Sensor | Component | Temp (°C) | High (°C) | Crit (°C) | Status |
|--------|-----------|----------:|----------:|----------:|--------|
| coretemp-isa-0000 | CPU Package | 62.0 | 90.0 | 100.0 | OK |
| coretemp-isa-0000 | Core 0 | 58.0 | 90.0 | 100.0 | OK |
| coretemp-isa-0000 | Core 1 | 61.0 | 90.0 | 100.0 | OK |
| nct6798-isa-0290 | System Temp | 38.0 | 75.0 | 85.0 | OK |

### Fan Speeds

- **CPU_FAN:** 3,200 RPM
- **SYS_FAN1:** 1,850 RPM
- **SYS_FAN2:** 1,830 RPM
- **SYS_FAN3:** 1,810 RPM

## 5. Root Cause Analysis Summary

### Diagnosis

The RAID 5 array `/dev/md0` has entered a **degraded** state due to a physical 
media failure on `/dev/loop10` (mapped to drive bay **Slot 0**).  S.M.A.R.T. 
pre-failure attributes confirm an imminent drive failure:

- **Reallocated_Sector_Ct (ID 5):** 544 reallocated sectors — exceeds threshold (140).
- **Current_Pending_Sector (ID 197):** 48 sectors awaiting reallocation.
- **UDMA_CRC_Error_Count (ID 199):** 7 interface CRC errors — possible cable/backplane issue.

The kernel log confirms `blk_update_request: critical medium error` on the affected 
device, and mdadm has automatically begun rebuilding from the hot spare `/dev/loop13`.

## 6. DCO Hot-Swap & Remediation Runbook

### 6.1 Pre-Swap Safety Checks

- [ ] **ESD Safety:** Don anti-static wrist strap; connect to rack ground point.
- [ ] **Identify drive bay:** Activate locator LED via `sesutil locate /dev/loop10 on` or BMC/IPMI.
- [ ] **Verify array state:** `cat /proc/mdstat` — confirm `[UU_]` degraded status.
- [ ] **Verify spare rebuild started:** `mdadm --detail /dev/md0` — spare shows `rebuilding`.
- [ ] **Notify NOC:** Open DCO ticket; include server hostname, array ID, and slot number.

### 6.2 Physical Drive Replacement

1. **Remove faulty drive:**
   ```bash
   mdadm --manage /dev/md0 --remove /dev/loop10
   ```
2. **Physically extract** the drive from bay Slot 0 (wait for locator LED).
3. **Inspect backplane connector** for bent pins, dust, or corrosion.
4. **Insert replacement drive** (same model/capacity: WDC WUH721818ALE6L4 18TB).
5. **Zero the superblock** on the new drive:
   ```bash
   mdadm --zero-superblock /dev/loop10
   ```
6. **Add new drive to array:**
   ```bash
   mdadm --manage /dev/md0 --add /dev/loop10
   ```

### 6.3 Post-Swap Verification

- [ ] **Monitor rebuild progress:**
  ```bash
  watch -n 2 cat /proc/mdstat
  ```
- [ ] **Verify full sync:** `mdadm --detail /dev/md0` — state shows `active` / `[UUU]`.
- [ ] **Run S.M.A.R.T. self-test on new drive:**
  ```bash
  smartctl -t long /dev/loop10
  ```
- [ ] **Check IPMI SEL (System Event Log)** for additional hardware events.
- [ ] **Deactivate locator LED:** `sesutil locate /dev/loop10 off`.
- [ ] **Update DCO ticket** with rebuild completion time and S.M.A.R.T. health status.

---

*This runbook was automatically generated by the AWS DCO Hardware Health Monitor.*  
*Simulation scenario: `CRITICAL` severity at `2026-11-14T07:15:51.169466Z`*
