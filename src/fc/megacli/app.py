import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

import megacli
import terminaltables

PCI_SCSI_PATTERN = re.compile(
    "^pci-[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]-scsi-"
    "[0-9a-f]:[0-9a-f]:([0-9a-f][0-9a-f]*):[0-9a-f]$"
)


def size(nbytes):
    suffixes = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while nbytes >= 1024 and i < len(suffixes) - 1:
        nbytes /= 1024.0
        i += 1
    f = ("%.2f" % nbytes).rstrip("0").rstrip(".")
    return "%s %s" % (f, suffixes[i])


def summary():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--megacli_path",
        default=shutil.which("MegaCli64"),
        help="Path to MegaCli or MegaCli64 (default %(default)s)",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        parser.error("Must run as root!")

    # connect to Avago/LSI adapter
    cli = megacli.MegaCLI(args.megacli_path)
    logicaldrives = cli.logicaldrives()
    physicaldrives = cli.physicaldrives()

    # define summary table layout
    table_data = [
        [
            "Linux\nDevice",
            "Linux\nMountpoints",
            "LSI LD",
            "LSI\nLD\nStatus",
            "LSI PDs\n[E:S] DID Size (Inquiry data)",
            "LSI\nPD\nStatus",
        ]
    ]

    # create LD to PD mapping
    logical_to_physical = subprocess.check_output(
        [args.megacli_path, "-LdPdInfo", "-aALL"]
    ).decode("ascii")

    ld_to_pd = {}
    for line in logical_to_physical.splitlines():
        match = re.search(
            "^Virtual Drive: ([0-9][0-9]*).*$|^Enclosure Device ID: ([0-9][0-9]*).*$|^Slot Number: ([0-9][0-9]*).*$",
            line,
        )
        if match:
            if match.group(1):
                current_ld = match.group(1)
                if current_ld not in ld_to_pd:
                    ld_to_pd[current_ld] = []
            elif match.group(3):
                ld_to_pd[current_ld].append(match.group(3))

    def get_physicaldrive_by_param(physicaldrives, param, value):
        for drive in physicaldrives:
            if str(drive[param]) == value:
                return drive
        raise KeyError(param, value)

    def get_logicaldrive_param(logicaldevice_id, param):
        for ld in logicaldrives:
            if logicaldevice_id == str(ld["id"]):
                return ld.get(param)
        raise KeyError(logicaldevice_id, param)

    def get_smart_data(did):
        c = subprocess.Popen(
            [
                "smartctl",
                "--all",
                "/dev/bus/0",
                "-d",
                "megaraid,{}".format(did),
            ],
            stdout=subprocess.PIPE,
            encoding="ascii",
        )
        stdout, stderr = c.communicate()
        fast_errors, slow_errors = None, None
        for line in stdout.splitlines():
            if (
                line.startswith("read: ")
                or line.startswith("write: ")
                or line.startswith("verify: ")
            ):
                result = line.split()
                fast_errors = (fast_errors or 0) + int(result[1])
                slow_errors = (slow_errors or 0) + int(result[2])
        return fast_errors, slow_errors

    def get_mountpoints(device):
        mountpoints_found = {}
        lsblk_mountpoints = subprocess.check_output(
            ["lsblk", "-l", "-n", "-o", "TYPE,MOUNTPOINT,KNAME,PKNAME"]
        ).decode("ascii")
        for line in lsblk_mountpoints.splitlines():
            line_as_list = " ".join(line.split()).split(" ")
            # Only parse devices that are actually mounted
            if len(line_as_list) == 4:
                part_type = line_as_list[0]
                mountpoint = line_as_list[1]
                kname = line_as_list[2]
                pkname = line_as_list[3]
                if "lvm" in part_type:
                    if device in pkname:
                        if pkname in mountpoints_found:
                            mountpoints_found[pkname] += (
                                ",\n       {}:{}".format(kname, mountpoint)
                            )
                        else:
                            mountpoints_found[pkname] = "{}:{}".format(
                                kname, mountpoint
                            )
                else:
                    if device in kname:
                        mountpoints_found[kname] = mountpoint
        return mountpoints_found

    def search_nested_structure(structure, searchstring):
        for k in structure:
            for v in structure:
                if searchstring in v:
                    return k
        return None

    # gather summary table data
    for path in Path("/dev/disk/by-path").iterdir():
        logicaldevice_id = ""
        ld_raid_level = ""
        ld_state = ""
        ld_bad_block_exist = ""
        ld_status = ""
        device_name = ""
        device_mountspoints = ""
        member_disks = []
        member_params = ""
        member_status = ""
        if not (match := PCI_SCSI_PATTERN.match(path.name)):
            continue
        logicaldevice_id = match.group(1)
        # get the corresponding Linux device name
        device_name = path.readlink().name
        # deal with non-LSI devices
        if not logicaldevice_id and device_name:
            for part, mounts in get_mountpoints(device_name).items():
                device_mountspoints += "{} @ {}\n".format(part, mounts)
            if not search_nested_structure(table_data, device_name):
                table_data.append(
                    [
                        device_name,
                        device_mountspoints,
                        "non-LSI",
                        "N/A",
                        "N/A",
                        "N/A",
                    ]
                )
            continue
        if not logicaldevice_id:
            breakpoint()
        # gather data about LD
        ld_raid_level = get_logicaldrive_param(logicaldevice_id, "raid_level")
        ld_state = get_logicaldrive_param(logicaldevice_id, "state")
        ld_bad_block_exist = get_logicaldrive_param(
            logicaldevice_id, "bad_blocks_exist"
        )
        if ld_state.strip() != "optimal" or ld_bad_block_exist:
            ld_status = "LD state: {}\nBad blocks: {}".format(
                ld_state, ld_bad_block_exist
            )
        else:
            ld_status = "OK"
        # avoid duplicates (partitions, respecively. E.g. sda1, sda2, etc.
        # are essentially all sda)
        if not search_nested_structure(table_data, device_name):
            for slot in ld_to_pd[logicaldevice_id]:
                member_disks.append(
                    get_physicaldrive_by_param(
                        physicaldrives, "slot_number", slot
                    )
                )
            for disk in member_disks:
                fast_errors, slow_errors = get_smart_data(disk["device_id"])
                member_params += (
                    "[{}:{}] {} {} ({})\n{} fast, {} slow\n".format(
                        disk["enclosure_id"],
                        disk["slot_number"],
                        disk["device_id"],
                        size(disk["raw_size"]),
                        " ".join(disk["inquiry_data"].upper().split()),
                        fast_errors,
                        slow_errors,
                    )
                )
                if (
                    disk["firmware_state"].strip() != "online, spun up"
                    or disk["media_error_count"]
                    or disk["drive_has_flagged_a_smart_alert"]
                ):
                    member_status += (
                        "FW state: {}\nMedia Errors: {}\n"
                        "SMART alert: {}".format(
                            disk["firmware_state"],
                            disk["media_error_count"],
                            disk["drive_has_flagged_a_smart_alert"],
                        )
                    )
                else:
                    member_status += "OK\n"

            for part, mounts in get_mountpoints(device_name).items():
                device_mountspoints += "{} @ {}\n".format(part, mounts)

            table_data.append(
                [
                    device_name,
                    device_mountspoints,
                    "{} (R{})".format(logicaldevice_id, ld_raid_level),
                    ld_status,
                    member_params,
                    member_status,
                ]
            )

    # gather unconfigured drives and add them to the table, too
    for drive in physicaldrives:
        if "unconfigured" in drive["firmware_state"].strip():
            member_params = "[{}:{}] {} {} ({})\n".format(
                drive["enclosure_id"],
                drive["slot_number"],
                drive["device_id"],
                size(drive["raw_size"]),
                " ".join(drive["inquiry_data"].upper().split()),
            )
            member_status = (
                "FW state: {}\nMedia Errors: {}\n" "SMART alert: {}".format(
                    drive["firmware_state"],
                    drive["media_error_count"],
                    drive["drive_has_flagged_a_smart_alert"],
                )
            )
            table_data.append(
                ["N/A", "N/A", "N/A", "N/A", member_params, member_status]
            )

    print(
        "\n\n\n=======\nWARNING\n=======\nfc-megacli expects exactly 1 LSI "
        "controller and 1 enclosure!\nIn case you have multiple "
        "controllers and/or enclosures installed,\nDO NOT TRUST fc-megacli "
        "output!\n\n\n"
    )
    table = terminaltables.SingleTable(table_data)
    print(table.table)
